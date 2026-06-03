"""
Learn sync: parse memory files and ingest learned topics as Fact nodes (Phase 4 RLM).

Pure parsing functions work without Neo4j.
Neo4j write functions (sync_facts, rebuild_graph) require a running Neo4j instance.

Public API:
  parse_learned_topics(content, filepath)         — parse markdown for learned sections
  extract_words(name, min_length)                 — word tokenisation for the Word index
  normalize_name(name)                            — lowercase + strip punctuation
  is_topic_saturated(name, existing, threshold)   — deduplication guard
  link_related_facts(tx, max_df_ratio, min_shared)— rebuild RELATED_TO via Word index
  cleanup_orphaned_words(tx)                      — delete Word nodes with no Facts
  sync_facts(topics, workspace, assistant)        — MERGE Fact nodes + Word index
  rebuild_graph(workspace)                        — rebuild RELATED_TO edges
"""
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Set

from ai_memory._config import get_driver


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
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith('-') or re.match(r'^\d+\.', line):
            point = re.sub(r'^[-\d\.]+\s*', '', line).strip()
            if point:
                key_points.append(point)
            in_list = True
        elif in_list and not line.startswith('#') and not line.startswith('**'):
            if not line.startswith('|'):
                summary_lines.append(line)
        else:
            summary_lines.append(line)
            in_list = False
    summary = ' '.join(summary_lines[:3])
    if len(summary) > 500:
        summary = summary[:497] + '...'
    return key_points, summary


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

def link_related_facts(tx, max_df_ratio: float = 0.3, min_shared: int = 1) -> None:
    """Rebuild RELATED_TO edges between Facts that share Word index entries.

    Deletes all existing RELATED_TO edges first to prevent stale data.
    Uses Word index for O(n × avg_words) instead of O(n²) cartesian product.
    """
    tx.run("""
        MATCH (w:Word)<-[:HAS_WORD]-(f:Fact)
        WITH w, count(DISTINCT f) AS df
        SET w.df = df
    """)
    result = tx.run("MATCH (f:Fact) RETURN count(f) AS total")
    total_facts = result.single()['total']
    max_df = int(total_facts * max_df_ratio)
    tx.run("MATCH ()-[r:RELATED_TO]->() DELETE r")
    tx.run("""
        MATCH (f1:Fact)-[:HAS_WORD]->(w:Word)<-[:HAS_WORD]-(f2:Fact)
        WHERE elementId(f1) < elementId(f2) AND (w.df IS NULL OR w.df <= $max_df)
        WITH f1, f2, collect(DISTINCT w.text) AS shared
        WHERE size(shared) >= $min_shared
        MERGE (f1)-[r:RELATED_TO]->(f2)
        SET r.shared_keywords = shared,
            r.shared_count = size(shared)
    """, max_df=max_df, min_shared=min_shared)


def cleanup_orphaned_words(tx) -> None:
    """Delete Word nodes that have no associated Fact nodes."""
    tx.run("MATCH (w:Word) WHERE NOT (()-[:HAS_WORD]->(w)) DELETE w")


# -------------------------------------------------------------------------
# Neo4j write functions
# -------------------------------------------------------------------------

def _sync_fact_tx(tx, topic: dict, assistant: Optional[str] = None) -> bool:
    """Transaction: MERGE a Fact node + Word index edges."""
    try:
        words = extract_words(topic['name'])
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
        result = tx.run(f"""
            MERGE (f:Fact {{name: $name}})
            {set_clause}
            WITH f
            OPTIONAL MATCH (f)-[old:HAS_WORD]->(:Word)
            DELETE old
            WITH f
            MERGE (s:Source {{name: $source_file}})
            MERGE (f)-[:FROM_SOURCE]->(s)
            RETURN f.name as name
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
    except Exception as e:
        print(f"Error syncing topic '{topic.get('name', 'unknown')}': {e}", file=sys.stderr)
        return False


def sync_facts(
    topics: List[dict],
    *,
    workspace=None,
    assistant: Optional[str] = None,
) -> int:
    """MERGE topic dicts as Fact nodes + Word index in Neo4j.

    Runs post-sync maintenance: word frequencies, RELATED_TO rebuild,
    orphaned word cleanup. Returns count of successfully synced facts.
    Returns 0 immediately if topics is empty.
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
            for topic in topics:
                if session.execute_write(_sync_fact_tx, topic, assistant):
                    synced += 1
            session.execute_write(link_related_facts, max_df_ratio=0.1, min_shared=2)
            session.execute_write(cleanup_orphaned_words)
        return synced
    finally:
        if driver is not None:
            driver.close()


def rebuild_graph(*, workspace=None) -> int:
    """Rebuild all RELATED_TO edges from the current Word index.

    Does not create or modify Fact nodes. Returns the resulting edge count.
    """
    driver = None
    try:
        driver = get_driver(workspace)
        with driver.session() as session:
            session.execute_write(link_related_facts, max_df_ratio=0.1, min_shared=2)
            session.execute_write(cleanup_orphaned_words)
            result = session.run("MATCH ()-[r:RELATED_TO]->() RETURN count(r) AS cnt")
            rec = result.single()
            return rec['cnt'] if rec else 0
    finally:
        if driver is not None:
            driver.close()
