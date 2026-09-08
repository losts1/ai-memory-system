"""
Learn sync: parse memory files and ingest learned topics as Fact nodes (Phase 4 RLM).

Pure parsing functions work without Neo4j.
Neo4j write functions (sync_facts, rebuild_graph) require a running Neo4j instance.

Public API:
  parse_learned_topics(content, filepath)         — parse markdown for learned sections
  extract_words(name, min_length)                 — word tokenisation (still used by is_topic_saturated)
  normalize_name(name)                            — lowercase + strip punctuation
  is_topic_saturated(name, existing, threshold)   — deduplication guard
  sync_facts(topics, workspace, assistant)        — MERGE Fact nodes + Word index + edge maintenance
  rebuild_graph(workspace)                        — nightly full RELATED_TO edge rebuild (wordindex.rebuild_edges)
  maintain_edges_after_write(session, name)       — on-write edge maintenance for one Fact (also used by scripts/neo4j_sync.py)
"""
import dataclasses
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Set

from neo4j.exceptions import TransientError

from ai_memory._config import get_driver
from ai_memory.embed import build_embed_subquery, embed_params, embed_text, fact_embed_text, text_sha
from ai_memory.provenance import Provenance
from ai_memory.retrieval_config import load_retrieval_config
from ai_memory.wordindex import load_edge_config, maintain_edges_for, tokenize

log = logging.getLogger("ai_memory.learn")


# -------------------------------------------------------------------------
# Constants
# -------------------------------------------------------------------------

SHORT_WORDS: Set[str] = {'ai', 'ml', 'sql', 'gpu', 'nlp', 'rl', 'api', 'cli', 'qa', 'etl'}

STOP_WORDS: Set[str] = {
    'utc', 'edt', 'est', 'pst', 'pdt', 'cst', 'cdt', 'gmt',
    '2024', '2025', '2026', '2027',
    'jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec',
    'session', 'learner', 'learned', 'daily', 'log', 'notes',
    'the', 'and', 'for', 'with', 'from', 'via', 'per',
    'market', 'making', 'trading', 'systems', 'theory',
}

_NORMALIZE_CHARS = '()&,.:—-'


# -------------------------------------------------------------------------
# Pure functions (no Neo4j)
# -------------------------------------------------------------------------

def normalize_name(name: str) -> str:
    """Lowercase and strip punctuation from a fact name."""
    result = name.lower()
    for char in _NORMALIZE_CHARS:
        result = result.replace(char, ' ')
    return result


def extract_words(name: str, min_length: int = 3) -> List[str]:
    """Extract unique words from a fact name for the Word index.

    Preserves short important tokens (AI, ML, SQL, etc.) and filters
    metadata noise (timezones, year numbers, common session words).
    """
    normalized = normalize_name(name)
    words = [w.strip() for w in normalized.split() if w.strip()]
    result = []
    for w in words:
        if w in STOP_WORDS:
            continue
        if len(w) >= min_length or w in SHORT_WORDS:
            result.append(w)
    return list(set(result))


def is_topic_saturated(
    topic_name: str, existing_names: Set[str], threshold: int = 3
) -> bool:
    """Return True if this topic's specific keywords already dominate the graph.

    Extracts words >4 chars from topic_name and counts how many existing names
    contain ALL of them. Returns True if count >= threshold.
    """
    words = extract_words(topic_name)
    specific = [w for w in words if len(w) > 4]
    if not specific:
        return False
    matches = sum(
        1 for name in existing_names if all(w in name.lower() for w in specific)
    )
    return matches >= threshold


def _date_from_filepath(filepath: Path) -> str:
    m = re.match(r'(\d{4}-\d{2}-\d{2})(?:[_-](\d{2})[_-](\d{2}))?', filepath.name)
    if m:
        date = m.group(1)
        hour = m.group(2) or '00'
        minute = m.group(3) or '00'
        return f"{date}T{hour}:{minute}:00Z"
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _clean_learned_title(title: str) -> str:
    title = re.sub(r'^(?:Learned|Learner Session):?\s*', '', title)
    title = re.sub(r'\s*\(Learner Cron[^)]*\)\s*$', '', title)
    title = re.sub(r'\s*\(\d+:\d+\s*[AP]M\s*EDT\)\s*$', '', title)
    title = re.sub(r'\s*\(\d+:\d+\s*[AP]M\)\s*$', '', title)
    title = re.sub(r'\s*\(\d+:\d+\s*UTC\)\s*$', '', title)
    title = re.sub(r'\s*—\s*\d{4}-\d{2}-\d{2}\s+\d+:\d+.*$', '', title)
    title = re.sub(r'\s*\(\d{4}-\d{2}-\d{2}\)\s*$', '', title)
    title = re.sub(r'\s*\(Learner\s*$', '', title)
    return title.strip()


def _parse_key_points_and_summary(body: str):
    lines = body.split('\n')
    key_points = []
    summary_lines = []
    in_list = False
    in_fence = False
    fence_marker = None
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(('```', '~~~')):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif fence_marker == marker:
                in_fence = False
                fence_marker = None
            continue
        if in_fence or not stripped:
            continue
        if stripped.startswith('-') or re.match(r'^\d+\.\s', stripped):
            point = re.sub(r'^[-\d\.]+\s*', '', stripped).strip()
            if point:
                key_points.append(point)
            in_list = True
        elif in_list and not stripped.startswith('#') and not stripped.startswith('**'):
            if not stripped.startswith('|'):
                summary_lines.append(stripped)
        else:
            summary_lines.append(stripped)
            in_list = False
    summary = ' '.join(summary_lines[:3])
    if len(summary) > 500:
        summary = summary[:497] + '...'
    return key_points, summary


# Match frontmatter, tolerating a leading UTF-8 BOM (editors that prepend ﻿).
_FRONTMATTER_RE = re.compile(r'^﻿?---\s*\n(.*?)\n---\s*\n', re.DOTALL)


def _parse_yaml_frontmatter(content: str) -> Optional[dict]:
    """Parse a simple key:value YAML frontmatter block.

    Only flat string values are supported (no lists, nested maps, or anchors).
    This intentionally avoids a PyYAML dependency — memory frontmatter in
    practice uses flat key:value pairs.

    Returns the parsed dict, or None if the file has no frontmatter.
    """
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return None
    fm = {}
    for line in m.group(1).splitlines():
        line = line.rstrip()
        if not line or line.lstrip().startswith('#'):
            continue
        if ':' not in line:
            continue
        key, _, val = line.partition(':')
        fm[key.strip()] = val.strip()
    return fm


def _strip_frontmatter(content: str) -> str:
    return _FRONTMATTER_RE.sub('', content, count=1)


def _parse_yaml_inline_list(val: str) -> list:
    """Parse a YAML inline list '[a, b, c]' to a Python list of strings."""
    val = val.strip()
    if val.startswith('[') and val.endswith(']'):
        return [x.strip().strip("'\"") for x in val[1:-1].split(',') if x.strip()]
    return [val] if val else []


def _parse_provenance_frontmatter(content: str) -> Optional[Provenance]:
    """Extract the nested 'provenance:' block from YAML frontmatter.

    Handles one level of indentation under 'provenance:' and parses
    inline lists for the 'signals' field (e.g. signals: [a, b]).
    Tolerates blank lines inside the provenance block.
    Returns None if no frontmatter or no provenance block is found.
    """
    fm_match = _FRONTMATTER_RE.match(content)
    if not fm_match:
        return None
    fm_body = fm_match.group(1)

    # Collect indented lines that follow a bare 'provenance:' line,
    # tolerating blank/whitespace-only lines within the block.
    prov_lines: list = []
    in_prov = False
    for line in fm_body.splitlines():
        if line.strip() == 'provenance:':
            in_prov = True
            continue
        if in_prov:
            if line and line[0] not in (' ', '\t'):
                break  # reached a new top-level key
            prov_lines.append(line)

    if not prov_lines:
        return None

    raw: dict = {}
    for line in prov_lines:
        stripped = line.strip()
        if not stripped or ':' not in stripped:
            continue
        key, _, val = stripped.partition(':')
        key = key.strip()
        val = val.strip()
        if key == 'signals':
            raw[key] = _parse_yaml_inline_list(val)
        elif key == 'risk_score':
            try:
                raw[key] = int(val)
            except ValueError:
                pass
        elif val:  # skip empty string values — they create untypeable trust/source states
            raw[key] = val

    if not raw.get('source'):
        return None
    try:
        return Provenance.from_dict(raw)
    except (TypeError, ValueError):
        return None


def _extract_bullets(body: str, max_points: int = 10) -> List[str]:
    """Extract markdown bullets ('- x', '* x', '1. x') as key_points.

    Skips bullets inside fenced code blocks (``` or ~~~). The numbered-list
    pattern requires whitespace after the period so version strings like
    `1.2.3 foo` are not mistaken for list items.
    """
    bullets = []
    in_fence = False
    fence_marker: Optional[str] = None
    for line in body.splitlines():
        stripped = line.strip()
        # Toggle fence on ``` or ~~~ markers; track which one to allow nesting tolerance.
        if stripped.startswith(('```', '~~~')):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif fence_marker == marker:
                in_fence = False
                fence_marker = None
            continue
        if in_fence or not stripped:
            continue
        if stripped.startswith(('-', '*')) or re.match(r'^\d+\.\s', stripped):
            point = re.sub(r'^[-*\d.]+\s*', '', stripped).strip()
            if point:
                bullets.append(point)
                if len(bullets) >= max_points:
                    break
    return bullets


def parse_frontmatter_topic(content: str, filepath: Path) -> List[dict]:
    """Extract a single topic from a YAML-frontmatter-headed memory file.

    Format::

        ---
        name: Short title
        description: One-line summary
        type: feedback
        ---
        Body content (bullets become key_points).

    The frontmatter's ``description`` field is preferred for ``summary``
    because it's a hand-curated one-liner. If absent, the first paragraph
    of the body is used (truncated at 500 chars). Bullet lines in the body
    become ``key_points``.

    Returns a one-element list on success so the return type matches
    ``parse_learned_topics``. Returns ``[]`` if the file has no frontmatter
    or no ``name`` field.
    """
    fm = _parse_yaml_frontmatter(content)
    if not fm or 'name' not in fm:
        return []

    body = _strip_frontmatter(content).strip()
    name = fm['name']
    description = fm.get('description', '')

    if description:
        summary = description
    else:
        first_para = body.split('\n\n', 1)[0].strip() if body else ''
        summary = first_para[:497] + '...' if len(first_para) > 500 else first_para

    prov = _parse_provenance_frontmatter(content)
    return [{
        'name': name,
        'summary': summary,
        'key_points': _extract_bullets(body),
        'source_file': filepath.name,
        'created_at': _date_from_filepath(filepath),
        'provenance': prov,
    }]


def parse_learned_topics(content: str, filepath: Path) -> List[dict]:
    """Extract learned topics from a memory file.

    Looks for '## Learned:', '## Learner Session:', or '# Learner Session:' sections.
    Returns list of topic dicts: {name, summary, key_points, source_file, created_at}.
    """
    topics = []
    session_header = r'#{1,2} (?:~[\d:]+\s+—\s+)?(?:Learned|Learner Session):'
    pattern = (
        r'^' + session_header + r'\s*([^\n]+)\n'
        r'(.*?)'
        r'(?=\n' + session_header + r'|\Z)'
    )
    matches = re.findall(pattern, content, re.MULTILINE | re.DOTALL)
    for title, body in matches:
        title = title.strip()
        body = body.strip()
        if not body:
            continue
        key_points, summary = _parse_key_points_and_summary(body)
        title = _clean_learned_title(title)
        if not title:
            title = 'Untitled Topic'
        topics.append({
            'name': title,
            'summary': summary,
            'key_points': key_points[:10],
            'source_file': filepath.name,
            'created_at': _date_from_filepath(filepath),
        })
    return topics


# -------------------------------------------------------------------------
# Neo4j transaction helpers (public — used by sync_facts and CLI wrapper)
# -------------------------------------------------------------------------

# -------------------------------------------------------------------------
# Neo4j write functions
# -------------------------------------------------------------------------

def write_fact(
    topic: dict,
    *,
    assistant: Optional[str] = None,
    driver=None,
    workspace=None,
    embed_fn=embed_text,
) -> bool:
    """Write a single Fact node to Neo4j.

    ``topic`` must have keys: name, summary, key_points, source_file, created_at.
    An optional ``provenance`` key (Provenance instance) writes provenance_* props.

    Returns True on success, False on any error (including unreachable Neo4j) and
    when the existing Fact of that name is tagged with another ``assistant`` — that
    write is refused outright (see ``owner_blocks_write``). Never raises.
    """
    owns_driver = driver is None
    try:
        if owns_driver:
            driver = get_driver(workspace)
        with driver.session() as session:
            cfg = _load_cfg(session)
            embed, tokens = _prepare_embed(session, topic, cfg, embed_fn)
            result = session.execute_write(_sync_fact_tx, topic, assistant, embed=embed, tokens=tokens)
            if result:
                maintain_edges_after_write(session, topic['name'])
            return bool(result)
    except Exception:
        return False
    finally:
        if owns_driver and driver is not None:
            driver.close()


def _load_cfg(session):
    """RetrievalConfig or None; a writer that cannot read it stores text and skips the vector (§4)."""
    try:
        return load_retrieval_config(session)
    except Exception as e:  # noqa: BLE001
        print(f"RetrievalConfig unavailable ({e}); writing text without embedding", file=sys.stderr)
        return None


def _load_edge_cfg(session):
    """Edge-layer config (spec §7.4) or None when unavailable/not yet built; mirrors _load_cfg —
    a writer that cannot read it just skips edge maintenance for this write."""
    try:
        return load_edge_config(session)
    except Exception as e:  # noqa: BLE001
        log.warning("edge config unavailable (%s); skipping edge maintenance", e)
        return None


def maintain_edges_after_write(session, name: str) -> None:
    """Load the edge config and run on-write maintenance for one Fact. Never lets a failure
    here abort the caller's write — this only ever runs after the Fact write already succeeded.

    Public — also called by scripts/neo4j_sync.py after its own Fact writes."""
    edge_cfg = _load_edge_cfg(session)
    if edge_cfg is None:
        return
    try:
        result = maintain_edges_for(session, name, edge_cfg)
        log.debug("edge maintenance for %r: %s", name, result)
    except Exception as e:  # noqa: BLE001 — the nightly rebuild repairs edges; never abort the write
        log.warning("edge maintenance failed for %r: %s", name, e)


_maintain_edges = maintain_edges_after_write  # legacy private name, kept as an alias


def _prepare_embed(session, topic, cfg, embed_fn):
    """Prepare the canonical text (spec §4) and its Word-index tokens, and — outside the write
    transaction, so a stalled Ollama can never hold a Fact lock — embed it when possible.

    Tokens follow `cfg`, not `embed_fn`: whenever a RetrievalConfig is available, tokens are
    computed from the full prepared text (name + summary + key_points + the Fact's existing
    `content`, with real boilerplate stripped) — matching rebuild_edges' fact_embed_text(...)
    exactly — even when `embed_fn` is None (the scripts/rlm/neo4j_learn_sync.py production path,
    which embeds separately itself). Only a missing `cfg` falls back to empty boilerplate with
    no `content` read (there is nothing to strip boilerplate against, and no config version to
    validate a CAS write with anyway).

    Returns ``(embed, tokens)``: ``embed`` is the embed params dict (incl. cas_content) or None
    when no vector is available (no cfg, no embed_fn, empty text, or the embed call itself
    returning nothing); ``tokens`` (from the same prepared text) is always a list.
    """
    if cfg is None:
        text = fact_embed_text(topic['name'], topic['summary'], topic['key_points'], None, ())
        return None, tokenize(text, topic['name'])
    rec = session.run("OPTIONAL MATCH (f:Fact {name: $name}) RETURN f.content AS content", name=topic['name']).single()
    content_seen = rec['content'] if rec else None
    text = fact_embed_text(topic['name'], topic['summary'], topic['key_points'], content_seen, cfg.boilerplate)
    tokens = tokenize(text, topic['name'])
    if embed_fn is None or not text.strip():
        return None, tokens
    vec = embed_fn(text)
    if not vec:
        return None, tokens
    return embed_params(vec, text_sha(text, cfg.version), cfg.version, cas={"content": content_seen}), tokens


_OWNER_READ = "OPTIONAL MATCH (f:Fact {name: $name}) RETURN f.assistant AS owner"


def owner_blocks_write(owner, writer: "str | None") -> "str | None":
    """Reason ``writer`` may not update a Fact tagged ``owner``, or None when it may.

    Mirrors the grok client's ``_owner_blocks_write``
    (grok/skills/neo4j-memory/scripts/neo4j_memory.py) with one deliberate
    relaxation: an **untagged** Fact (NULL/blank ``assistant``) is the library's
    own inherited memory and any writer may update — and thereby claim — it,
    where grok refuses without ``--force-assistant``. A Fact tagged with another
    mind is refused outright, with no force escape on either side; a writer that
    passes no assistant is a *different* writer, not a wildcard.

    Public — also used by scripts/neo4j_sync.py.
    """
    if owner is None or (isinstance(owner, str) and not owner.strip()):
        return None
    if owner != writer:
        return f"owned by {owner!r}"
    return None


def refuse_owner_conflict(name: str, owner, writer: "str | None") -> None:
    """One stderr line naming the Fact, its owner and the writer that was refused."""
    who = repr(writer) if writer else "untagged"
    print(f"Refusing to overwrite Fact {name!r} owned by {owner!r} (writer: {who})", file=sys.stderr)


def _sync_fact_tx(tx, topic: dict, assistant: "str | None" = None, *, embed: "dict | None" = None,
                  tokens=None) -> bool:
    """Transaction: MERGE a Fact node + Word index edges; when `embed` (built by `_prepare_embed`
    outside this transaction) is provided, the same statement sets the vector and its provenance
    (CAS on `content`, the one text field this writer does not own — spec §4). `tokens` (also
    from `_prepare_embed`) replaces the old name-only word list; when omitted (direct callers),
    falls back to `extract_words(topic['name'])`.

    Reads the existing Fact's `assistant` first, in the same transaction, and refuses
    the whole write when it is another mind's (`owner_blocks_write`) — MERGE-by-name is
    an in-place overwrite, and no library writer may silently clobber Nova/Weft content.
    A refused Fact returns False, so callers neither count it nor maintain its edges."""
    try:
        existing = tx.run(_OWNER_READ, name=topic['name']).single()
        owner = existing.get("owner") if existing is not None else None
        if owner_blocks_write(owner, assistant):
            refuse_owner_conflict(topic['name'], owner, assistant)
            return False
        words = tokens if tokens is not None else extract_words(topic['name'])
        params = {
            'name': topic['name'],
            'summary': topic['summary'],
            'key_points': topic['key_points'],
            'source_file': topic['source_file'],
            'created_at': topic['created_at'],
        }
        set_clause = """
            SET f.summary = $summary,
                f.key_points = $key_points,
                f.source_file = $source_file,
                f.created_at = coalesce(f.created_at, $created_at),
                f.updated_at = $created_at
        """
        if assistant:
            set_clause += ", f.assistant = $assistant"
            params['assistant'] = assistant
        if topic.get('provenance') is not None:
            # Iterate ALL known fields (not just non-None ones from to_dict) so that
            # re-writing a Fact with updated provenance clears previously set fields.
            # e.g. risk_score going from 47 → None must write NULL, not leave 47.
            prov_dict = topic['provenance'].to_dict()
            for f in dataclasses.fields(topic['provenance']):
                param_key = f'prov_{f.name}'
                set_clause += f', f.provenance_{f.name} = ${param_key}'
                params[param_key] = prov_dict.get(f.name)  # None for cleared fields
        embed_block, embed_return = "", ""
        if embed is not None:
            embed_block = "WITH DISTINCT f\n" + build_embed_subquery(["content"]) + "\n"
            embed_return = ", embedded"
            params.update(embed)
        result = tx.run(f"""
            MERGE (f:Fact {{name: $name}})
            {set_clause}
            WITH f
            OPTIONAL MATCH (f)-[old:HAS_WORD]->(:Word)
            DELETE old
            WITH f
            MERGE (s:Source {{name: $source_file}})
            MERGE (f)-[:FROM_SOURCE]->(s)
            {embed_block}RETURN f.name AS name{embed_return}
        """, **params)
        if not result.single():
            return False
        if assistant:
            tx.run("""
                MERGE (a:Assistant {id: $assistant})
                ON CREATE SET a.name = $assistant, a.created_at = datetime()
            """, assistant=assistant)
        if words:
            tx.run("""
                MATCH (f:Fact {name: $name})
                UNWIND $words AS word
                MERGE (w:Word {text: word})
                MERGE (f)-[:HAS_WORD]->(w)
            """, name=topic['name'], words=words)
        return True
    except TransientError:
        raise                      # let execute_write's managed retry handle deadlocks / leader switches
    except Exception as e:
        print(f"Error syncing topic '{topic.get('name', 'unknown')}': {e}", file=sys.stderr)
        return False


def sync_facts(
    topics: List[dict],
    *,
    workspace=None,
    assistant: Optional[str] = None,
    embed_fn=embed_text,
) -> int:
    """MERGE topic dicts as Fact nodes + Word index in Neo4j.

    After each successfully-synced Fact, runs on-write edge maintenance
    (wordindex.maintain_edges_for) when the edge layer has been built; a failure there
    never aborts the Fact write (the nightly rebuild repairs edges). Returns count of
    successfully synced facts. Returns 0 immediately if topics is empty.

    A Fact whose name is already tagged with a different ``assistant`` is refused
    (``owner_blocks_write``): it is neither counted nor edge-maintained.
    """
    if not topics:
        return 0
    driver = None
    try:
        driver = get_driver(workspace)
        synced = 0
        with driver.session() as session:
            session.run(
                "CREATE CONSTRAINT word_text_unique IF NOT EXISTS "
                "FOR (w:Word) REQUIRE w.text IS UNIQUE"
            )
            cfg = _load_cfg(session)
            edge_cfg = _load_edge_cfg(session)
            for topic in topics:
                embed, tokens = _prepare_embed(session, topic, cfg, embed_fn)
                if session.execute_write(_sync_fact_tx, topic, assistant, embed=embed, tokens=tokens):
                    synced += 1
                    if edge_cfg is not None:
                        try:
                            result = maintain_edges_for(session, topic['name'], edge_cfg)
                            log.debug("edge maintenance for %r: %s", topic['name'], result)
                        except Exception as e:  # noqa: BLE001 — the nightly rebuild repairs edges
                            log.warning("edge maintenance failed for %r: %s", topic['name'], e)
        return synced
    finally:
        if driver is not None:
            driver.close()


def rebuild_graph(*, workspace=None) -> int:
    """Nightly full rebuild of the word index + RELATED_TO edge layer (spec §7.5).

    Does not create or modify Fact nodes. Returns the resulting edge count.
    """
    driver = None
    try:
        driver = get_driver(workspace)
        from ai_memory.wordindex import rebuild_edges
        report = rebuild_edges(driver)
        return report["edges_written"]
    finally:
        if driver is not None:
            driver.close()
