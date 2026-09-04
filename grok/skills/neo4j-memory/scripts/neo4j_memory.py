#!/usr/bin/env python3
"""Grok ↔ Neo4j memory CLI.

Reads credentials from ~/.grok/.env.neo4j (or $GROK_HOME/.env.neo4j).
Writes Facts tagged assistant=Grok. Refuses to overwrite another mind's Fact.

Search is hybrid by default: Lucene fulltext + Ollama nomic-embed-text against
the Neo4j vector index. Ollama down or a missing index → fulltext only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from concurrent.futures import Future, wait
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values
from neo4j import GraphDatabase, Query
from neo4j.exceptions import DriverError, Neo4jError

_BOLT_FAIL = (Neo4jError, DriverError)

GROK_HOME = Path(os.environ.get("GROK_HOME", Path.home() / ".grok"))
ENV_FILE = GROK_HOME / ".env.neo4j"
HITS_FILE = GROK_HOME / "neo4j-hits.md"
SESSION_FILE = GROK_HOME / "neo4j-session.md"
INBOX_FILE = GROK_HOME / "neo4j-inbox.jsonl"
ASSISTANT = "Grok"
DEFAULT_MAX = 5
TEASER = 220
EMBED_DIM = 768
EMBED_MODEL = "nomic-embed-text"
OLLAMA_HOST = "http://127.0.0.1:11434"
KEEP_ALIVE = "30m"
RRF_K = 60
# Vector-only hybrid (Lucene empty): drop KNN neighbors below this cosine.
# Measured 2026-08-31: true match ~0.88 (llm-router review); KeePassXC noise
# ~0.75 (Nova WebSocket/asyncio). RRF path (both legs hit) is unfiltered.
VECTOR_ONLY_MIN_SCORE = 0.80
HOOK_EMBED_TIMEOUT = 3.0
HOOK_BOLT_TIMEOUT = 4.0
HOOK_CONNECT_TIMEOUT = 1.5
HOOK_DEADLINE = 5.0
SEARCH_EMBED_TIMEOUT = 15.0
SEARCH_BOLT_TIMEOUT = 15.0
WRITE_EMBED_TIMEOUT = 30.0
EMBED_FAIL_ABORT = 3
FACT_EMBED_CHARS = 2000

_LUCENE_SPECIAL = re.compile(r'[\+\-\&\|\!\(\)\{\}\[\]\^\"\~\*\?\:\/\\]')
_WORD = re.compile(r"[a-z0-9]{3,}")
_STOP = {
    "the", "and", "for", "with", "from", "that", "this", "are", "was", "were",
    "you", "your", "have", "has", "not", "but", "can", "how", "what", "when",
    "why", "who", "into", "about", "just", "like", "then", "than", "them",
}


def _pick(raw: dict, key: str, default: str) -> str:
    v = os.environ.get(key)
    if v is not None and str(v).strip():
        return str(v).strip()
    v = raw.get(key)
    if v is not None and str(v).strip():
        return str(v).strip()
    return default


def _cfg() -> dict:
    if not ENV_FILE.exists():
        raise SystemExit(f"missing {ENV_FILE}")
    raw = dotenv_values(ENV_FILE)
    uri = _pick(raw, "NEO4J_URI", "bolt://localhost:7687")
    user = (
        os.environ.get("NEO4J_USERNAME")
        or os.environ.get("NEO4J_USER")
        or raw.get("NEO4J_USERNAME")
        or raw.get("NEO4J_USER")
        or "neo4j"
    )
    password = os.environ.get("NEO4J_PASSWORD") or raw.get("NEO4J_PASSWORD")
    if not password:
        raise SystemExit(f"NEO4J_PASSWORD unset in {ENV_FILE}")
    dim_raw = _pick(raw, "NEO4J_EMBED_DIM", str(EMBED_DIM))
    try:
        dim = int(dim_raw)
    except ValueError:
        dim = EMBED_DIM
    timeout_raw = _pick(raw, "OLLAMA_EMBED_TIMEOUT", str(SEARCH_EMBED_TIMEOUT))
    try:
        embed_timeout = float(timeout_raw)
    except ValueError:
        embed_timeout = SEARCH_EMBED_TIMEOUT
    return {
        "uri": uri,
        "user": user,
        "password": password,
        "vector": _pick(raw, "NEO4J_VECTOR_INDEX", "factEmbeddingIndex"),
        "fulltext": _pick(raw, "NEO4J_FULLTEXT_INDEX", "fact_content"),
        "fulltext_kp": _pick(raw, "NEO4J_FULLTEXT_KP_INDEX", "fact_key_points"),
        "ollama": _pick(raw, "OLLAMA_HOST", OLLAMA_HOST).rstrip("/"),
        "embed_model": _pick(raw, "OLLAMA_EMBED_MODEL", EMBED_MODEL),
        "embed_dim": dim,
        "embed_timeout": embed_timeout,
        "keep_alive": _pick(raw, "OLLAMA_KEEP_ALIVE", KEEP_ALIVE),
    }


def _driver(connect_timeout: float | None = None):
    c = _cfg()
    kw = {}
    if connect_timeout is not None:
        kw["connection_timeout"] = connect_timeout
        kw["connection_acquisition_timeout"] = connect_timeout
    return GraphDatabase.driver(
        c["uri"], auth=(c["user"], c["password"]), **kw,
    ), c


def _daemon_submit(fn, *args) -> Future:
    fut: Future = Future()

    def run() -> None:
        if not fut.set_running_or_notify_cancel():
            return
        try:
            fut.set_result(fn(*args))
        except Exception as e:
            if not fut.cancelled():
                fut.set_exception(e)

    threading.Thread(target=run, name="neo4j-mem", daemon=True).start()
    return fut


def _escape_lucene(q: str) -> str:
    return _LUCENE_SPECIAL.sub(lambda m: "\\" + m.group(), q.strip())


def _teaser(text, n=TEASER) -> str:
    if not text:
        return ""
    s = " ".join(str(text).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _words(text: str) -> list[str]:
    seen = []
    for w in _WORD.findall(text.lower()):
        if w in _STOP or w in seen:
            continue
        seen.append(w)
    return seen[:12]


def _fact_text(name, summary=None, content=None, key_points=None) -> str:
    parts = [name or "", summary or "", content or ""]
    if key_points:
        parts.extend(str(p) for p in key_points if p)
    return " ".join(" ".join(parts).split())[:FACT_EMBED_CHARS]


def parse_embed_body(body: dict) -> list[float] | None:
    """Accept /api/embed (`embeddings`) or /api/embeddings (`embedding`)."""
    if not isinstance(body, dict):
        return None
    embs = body.get("embeddings")
    if isinstance(embs, list) and embs:
        first = embs[0]
        if isinstance(first, list) and first and all(isinstance(x, (int, float)) for x in first):
            return [float(x) for x in first]
    emb = body.get("embedding")
    if isinstance(emb, list) and emb and all(isinstance(x, (int, float)) for x in emb):
        return [float(x) for x in emb]
    return None


def _http_json(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _retry_legacy_embed(exc: BaseException) -> bool:
    """Retry /api/embeddings only when /api/embed is missing, not on timeout."""
    if isinstance(exc, TimeoutError):
        return False
    if isinstance(exc, json.JSONDecodeError):
        return False
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (404, 405)
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, TimeoutError):
            return False
        if isinstance(reason, urllib.error.HTTPError):
            return reason.code in (404, 405)
        return True
    if isinstance(exc, ConnectionError):
        return True
    return False


def ollama_embed(text: str, cfg: dict | None = None, timeout: float | None = None) -> list[float] | None:
    """Embed `text` via local Ollama. None if down, empty, or wrong dim."""
    if not text or not text.strip():
        return None
    c = cfg or _cfg()
    model = c["embed_model"]
    dim = c["embed_dim"]
    host = c["ollama"]
    keep = c["keep_alive"]
    t = c["embed_timeout"] if timeout is None else timeout
    payload_embed = {"model": model, "input": text[:FACT_EMBED_CHARS], "keep_alive": keep}
    payload_legacy = {"model": model, "prompt": text[:FACT_EMBED_CHARS], "keep_alive": keep}
    try:
        body = _http_json(f"{host}/api/embed", payload_embed, t)
    except Exception as e:
        if not _retry_legacy_embed(e):
            return None
        try:
            body = _http_json(f"{host}/api/embeddings", payload_legacy, t)
        except Exception:
            return None
    vec = parse_embed_body(body or {})
    if not vec or len(vec) != dim:
        return None
    return vec


def _hit_from_record(r) -> dict:
    teaser = _teaser(r["text"])
    status = r["status"] if "status" in r.keys() else None
    if status and status != STATUS_ACTIVE:
        teaser = f"[{status}] {teaser}" if teaser else f"[{status}]"
    topic = r["topic"] if "topic" in r.keys() else None
    return {
        "name": r["name"],
        "assistant": r["assistant"],
        "score": float(r["score"] or 0),
        "teaser": teaser,
        "key_points": (r["key_points"] or [])[:3],
        "via": "",
        "status": status,
        "topic": topic,
    }


def _query(cypher: str, timeout: float | None):
    return cypher if timeout is None else Query(cypher, timeout=timeout)


def search_fulltext(
    session, query: str, index: str, limit: int, bolt_timeout: float | None = None,
) -> list[dict]:
    lucene = _escape_lucene(query)
    if not lucene or limit <= 0:
        return []
    rows = session.run(
        _query(
            """
            CALL db.index.fulltext.queryNodes($index, $q)
            YIELD node, score
            RETURN node.name AS name,
                   coalesce(node.summary, node.content) AS text,
                   node.assistant AS assistant,
                   node.key_points AS key_points,
                   node.status AS status,
                   node.topic AS topic,
                   score
            ORDER BY score DESC
            LIMIT $limit
            """,
            bolt_timeout,
        ),
        index=index,
        q=lucene,
        limit=limit,
    )
    return [_hit_from_record(r) | {"via": "ft"} for r in rows]


def search_vector(
    session, embedding: list[float], index: str, limit: int,
    bolt_timeout: float | None = None,
) -> list[dict]:
    if not embedding or limit <= 0:
        return []
    rows = session.run(
        _query(
            """
            CALL db.index.vector.queryNodes($index, $k, $embedding)
            YIELD node, score
            RETURN node.name AS name,
                   coalesce(node.summary, node.content) AS text,
                   node.assistant AS assistant,
                   node.key_points AS key_points,
                   node.status AS status,
                   node.topic AS topic,
                   score
            ORDER BY score DESC
            LIMIT $k
            """,
            bolt_timeout,
        ),
        index=index,
        k=limit,
        embedding=embedding,
    )
    return [_hit_from_record(r) | {"via": "vec"} for r in rows]


def merge_rrf(
    ranked_lists: list[tuple[str, list[dict]]],
    limit: int,
    k: int = RRF_K,
) -> list[dict]:
    """Reciprocal-rank fusion. `ranked_lists` is [(origin, hits), ...]."""
    scores: dict[str, float] = {}
    meta: dict[str, dict] = {}
    origins: dict[str, set[str]] = {}
    for origin, hits in ranked_lists:
        for rank, h in enumerate(hits):
            name = h.get("name")
            if not name:
                continue
            scores[name] = scores.get(name, 0.0) + 1.0 / (k + rank + 1)
            origins.setdefault(name, set()).add(origin)
            prev = meta.get(name)
            if prev is None:
                meta[name] = dict(h)
                continue
            merged = dict(prev)
            ht, pt = h.get("teaser") or "", merged.get("teaser") or ""
            if len(ht) > len(pt):
                merged["teaser"] = h["teaser"]
            hk, pk = h.get("key_points") or [], merged.get("key_points") or []
            if hk and (not pk or len(hk) > len(pk)):
                merged["key_points"] = hk
            meta[name] = merged
    ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))[: max(limit, 0)]
    out = []
    for name, sc in ranked:
        h = dict(meta[name])
        h["score"] = round(sc, 4)
        h["via"] = "+".join(sorted(origins[name]))
        out.append(h)
    return out


def _pool_size(limit: int) -> int:
    return max(int(limit) * 4, 16)


def _demote_inactive(hits: list[dict]) -> list[dict]:
    """Keep score order, but put superseded/removed Facts after live ones."""
    live, rest = [], []
    for h in hits:
        if h.get("status") in (STATUS_SUPERSEDED, STATUS_REMOVED):
            rest.append(h)
        else:
            live.append(h)
    return live + rest


def _collapse_superseded_siblings(hits: list[dict]) -> list[dict]:
    """Drop a superseded/removed hit when an active sibling of the same topic is already in the list."""
    live_topics = {
        h.get("topic")
        for h in hits
        if h.get("topic")
        and h.get("status") not in (STATUS_SUPERSEDED, STATUS_REMOVED)
    }
    if not live_topics:
        return hits
    return [
        h for h in hits
        if not (
            h.get("status") in (STATUS_SUPERSEDED, STATUS_REMOVED)
            and h.get("topic") in live_topics
        )
    ]


def _rank_hits(hits: list[dict]) -> list[dict]:
    return _collapse_superseded_siblings(_demote_inactive(hits))


def _fulltext_leg(
    driver, query: str, index: str, limit: int, bolt_timeout: float | None = None,
    extra_indexes: list[str] | None = None,
) -> list[dict]:
    extras = [x for x in (extra_indexes or []) if x]
    try:
        with driver.session() as s:
            lists: list[tuple[str, list[dict]]] = []
            try:
                hits = search_fulltext(
                    s, query, index, limit, bolt_timeout=bolt_timeout,
                )
                if hits:
                    lists.append(("ft", hits))
            except _BOLT_FAIL:
                pass
            for extra in extras:
                try:
                    hits = search_fulltext(
                        s, query, extra, limit, bolt_timeout=bolt_timeout,
                    )
                    if hits:
                        lists.append(("kp", hits))
                except _BOLT_FAIL:
                    continue
            if not lists:
                return []
            if len(lists) == 1:
                return lists[0][1]
            return merge_rrf(lists, limit)
    except _BOLT_FAIL:
        return []


def _vector_leg(
    driver,
    query: str,
    cfg: dict,
    limit: int,
    embed_timeout: float | None,
    bolt_timeout: float | None = None,
) -> tuple[list[dict], bool]:
    emb = ollama_embed(query, cfg, timeout=embed_timeout)
    if not emb:
        return [], False
    try:
        with driver.session() as s:
            return search_vector(
                s, emb, cfg["vector"], limit, bolt_timeout=bolt_timeout,
            ), True
    except _BOLT_FAIL:
        return [], False


def _vector_hits_above_floor(vec: list[dict], limit: int) -> list[dict]:
    """Keep vector neighbors at/above VECTOR_ONLY_MIN_SCORE. Input is score-desc."""
    kept: list[dict] = []
    for h in vec:
        sc = float(h.get("score") or 0)
        if sc < VECTOR_ONLY_MIN_SCORE:
            break
        row = dict(h)
        row["score"] = round(sc, 4)
        kept.append(row)
        if len(kept) >= limit:
            break
    return kept


def _finish_hybrid(ft: list[dict], vec: list[dict], vec_ok: bool, limit: int) -> tuple[list[dict], str]:
    if vec_ok and vec and ft:
        return merge_rrf([("ft", ft), ("vec", vec)], limit), "hybrid"
    if vec_ok and vec:
        return _vector_hits_above_floor(vec, limit), "hybrid"
    for h in ft[:limit]:
        h["score"] = round(h["score"], 3)
    return ft[:limit], "fulltext" if not vec_ok else "hybrid"


def search_memories(
    driver,
    cfg: dict,
    query: str,
    limit: int,
    mode: str = "hybrid",
    embed_timeout: float | None = None,
    bolt_timeout: float | None = None,
    deadline: float | None = None,
) -> tuple[list[dict], str]:
    """Return (hits, backend). Hybrid runs fulltext and embed+KNN in parallel."""
    mode = (mode or "hybrid").strip().lower()
    q = (query or "").strip()
    if not q or limit <= 0:
        return [], mode
    pool = _pool_size(limit)

    kp_idx = cfg.get("fulltext_kp")
    extras = [kp_idx] if kp_idx else None

    if mode == "fulltext":
        hits = _fulltext_leg(
            driver, q, cfg["fulltext"], limit, bolt_timeout, extras,
        )
        for h in hits:
            h["score"] = round(h["score"], 3)
        return _rank_hits(hits), "fulltext"

    if mode == "vector":
        vec, vec_ok = _vector_leg(
            driver, q, cfg, limit, embed_timeout, bolt_timeout,
        )
        if not vec_ok:
            return [], "vector-down"
        return _rank_hits(_vector_hits_above_floor(vec, limit)), "vector"

    et = SEARCH_EMBED_TIMEOUT if embed_timeout is None else embed_timeout
    bt = SEARCH_BOLT_TIMEOUT if bolt_timeout is None else bolt_timeout
    budget = et + bt if deadline is None else deadline
    if budget <= 0:
        return [], "fulltext"

    fut_ft = _daemon_submit(
        _fulltext_leg, driver, q, cfg["fulltext"], pool, bolt_timeout, extras,
    )
    fut_vec = _daemon_submit(
        _vector_leg, driver, q, cfg, pool, embed_timeout, bolt_timeout,
    )
    wait([fut_ft, fut_vec], timeout=budget)
    ft: list[dict] = []
    vec: list[dict] = []
    vec_ok = False
    if fut_ft.done() and not fut_ft.cancelled():
        try:
            ft = fut_ft.result(timeout=0)
        except Exception:
            ft = []
    if fut_vec.done() and not fut_vec.cancelled():
        try:
            vec, vec_ok = fut_vec.result(timeout=0)
        except Exception:
            vec, vec_ok = [], False
    hits, backend = _finish_hybrid(ft, vec, vec_ok, limit)
    return _rank_hits(hits), backend


def _search_stderr(mode: str, backend: str, hits: list[dict]) -> list[str]:
    lines = []
    if mode == "hybrid" and backend == "fulltext":
        lines.append("vector skipped (Ollama down or index miss); fulltext only")
    if mode == "vector" and backend == "vector-down":
        lines.append(
            "No hits. (vector needs Ollama nomic-embed-text and factEmbeddingIndex)"
        )
        return lines
    if not hits:
        lines.append("No hits.")
    return lines


def _print_hits(hits: list[dict], show_via: bool) -> None:
    for h in hits:
        mind = h["assistant"] or "?"
        via = f"  ({h['via']})" if show_via and h.get("via") else ""
        print(f"{h['score']:>7}  [{mind}] {h['name']}{via}")
        if h["teaser"]:
            print(f"         {h['teaser']}")
        for kp in h["key_points"]:
            print(f"         - {_teaser(kp, 160)}")


def cmd_search(args: argparse.Namespace) -> int:
    drv, cfg = _driver()
    try:
        hits, backend = search_memories(
            drv,
            cfg,
            args.query,
            args.max,
            mode=args.mode,
            embed_timeout=cfg["embed_timeout"],
            bolt_timeout=SEARCH_BOLT_TIMEOUT,
        )
    except _BOLT_FAIL as e:
        print(f"search failed: {e}", file=sys.stderr)
        return 1
    finally:
        drv.close()
    for line in _search_stderr(args.mode, backend, hits):
        print(line, file=sys.stderr)
    if not hits:
        return 0
    _print_hits(hits, show_via=(backend == "hybrid"))
    return 0


def cmd_stats(_args: argparse.Namespace) -> int:
    drv, cfg = _driver()
    try:
        with drv.session() as s:
            facts = s.run("MATCH (f:Fact) RETURN count(f) AS c").single()["c"]
            grok = s.run(
                "MATCH (f:Fact {assistant: $a}) RETURN count(f) AS c",
                a=ASSISTANT,
            ).single()["c"]
            emb = s.run(
                """
                MATCH (f:Fact)
                RETURN count(f.embedding) AS with_emb,
                       count(CASE WHEN f.embedding IS NULL THEN 1 END) AS without_emb
                """
            ).single()
            minds = s.run(
                "MATCH (f:Fact) RETURN coalesce(f.assistant,'(none)') AS a, count(*) AS c "
                "ORDER BY c DESC"
            ).data()
            missing = s.run(
                """
                MATCH (f:Fact) WHERE f.embedding IS NULL
                RETURN coalesce(f.assistant,'(none)') AS a, count(*) AS c
                ORDER BY c DESC
                """
            ).data()
    except _BOLT_FAIL as e:
        print(f"stats failed: {e}", file=sys.stderr)
        return 1
    finally:
        drv.close()
    print(f"facts {facts}  grok {grok}  embedded {emb['with_emb']}/{facts}")
    print(f"vector {cfg['vector']}  {cfg['embed_dim']}d  model {cfg['embed_model']}")
    for r in minds:
        print(f"  {r['c']:>5}  {r['a']}")
    if missing:
        print("missing embeddings:")
        for r in missing:
            print(f"  {r['c']:>5}  {r['a']}")
    return 0


def _set_embedding(session, name: str, embedding: list[float]) -> None:
    session.run(
        "MATCH (f:Fact {name: $name}) SET f.embedding = $embedding",
        name=name,
        embedding=embedding,
    )


def _require_grok(args: argparse.Namespace) -> int | None:
    assistant = (getattr(args, "assistant", None) or ASSISTANT).strip()
    if assistant == ASSISTANT or getattr(args, "force_assistant", False):
        return None
    print(
        f"refused: write/organize only as {ASSISTANT} (pass --force-assistant)",
        file=sys.stderr,
    )
    return 3


def _owner_blocks_write(owner, writer: str, force: bool) -> str | None:
    """Untagged (NULL/blank) Facts are inherited memory, not free to claim."""
    if owner is None or (isinstance(owner, str) and not owner.strip()):
        if force:
            return None
        return "untagged (pass --force-assistant)"
    if owner != writer:
        return f"owned by {owner!r}"
    return None


SHARED_SPACE = "shared"
STATUS_ACTIVE = "active"
STATUS_SUPERSEDED = "superseded"
STATUS_REMOVED = "removed"
LIBRARY_MINDS = frozenset({"Nova", "Weft"})
_SHARED_NAME_RE = re.compile(
    r"^Shared — (.+?) — (\d{4}-\d{2}-\d{2})(?: #\d+)?$"
)


def _day(now: str | None = None) -> str:
    if now:
        return now[:10]
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _topic_slug(topic: str) -> str:
    s = (topic or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _shared_name(title: str, day: str) -> str:
    title = (title or "").strip()
    day = (day or "").strip()
    m = _SHARED_NAME_RE.match(title)
    if m:
        body = m.group(1)
        return f"Shared — {body} — {day}"
    if title.startswith("Shared — "):
        title = title[len("Shared — "):].strip()
    return f"Shared — {title} — {day}"


def _unique_shared_name(title: str, day: str, taken) -> str:
    base = _shared_name(title, day)
    taken = set(taken or [])
    if base not in taken:
        return base
    n = 2
    while True:
        cand = f"{base} #{n}"
        if cand not in taken:
            return cand
        n += 1


def _dated_point(day: str, text: str) -> str:
    t = (text or "").strip()
    if len(t) >= 12 and t[0] == "[" and "]" in t[:12]:
        return t
    return f"[{day}] {t}"


def _is_library(rec) -> bool:
    if not rec:
        return False
    if rec.get("space") == SHARED_SPACE:
        return False
    owner = rec.get("assistant")
    if owner in LIBRARY_MINDS:
        return True
    if owner is None or (isinstance(owner, str) and not str(owner).strip()):
        return True
    return False


def _plan_shared_write(
    *,
    existing_by_name,
    active_for_topic,
    topic: str,
    title: str,
    day: str,
    append: bool,
    supersede: bool,
    writer: str,
    taken_names=None,
) -> dict:
    """Decide create / append / supersede / refuse. Never in-place overwrite."""
    empty = {"action": "refuse", "name": None, "predecessor": None, "reason": ""}
    if _is_library(existing_by_name):
        owner = (existing_by_name or {}).get("assistant") or "untagged"
        return empty | {
            "reason": (
                f"library Fact owned by {owner!r}; will not clobber. "
                "Shared writes must use a new dated Shared name."
            ),
        }
    if append:
        if not active_for_topic or active_for_topic.get("status") != STATUS_ACTIVE:
            return empty | {"reason": "no active shared Fact for topic; cannot --append"}
        return {
            "action": "append",
            "name": active_for_topic["name"],
            "predecessor": None,
            "reason": None,
        }
    if active_for_topic and not supersede:
        return empty | {
            "reason": (
                f"active shared Fact {active_for_topic['name']!r}; "
                "pass --supersede (dated successor) or --append (add a point)"
            ),
        }
    taken = set(taken_names or [])
    if active_for_topic:
        taken.add(active_for_topic["name"])
    if existing_by_name and existing_by_name.get("name"):
        taken.add(existing_by_name["name"])
    name = _unique_shared_name(title, day, taken)
    if supersede and active_for_topic:
        return {
            "action": "supersede",
            "name": name,
            "predecessor": active_for_topic["name"],
            "reason": None,
        }
    return {
        "action": "create",
        "name": name,
        "predecessor": None,
        "reason": None,
    }


def _plan_shared_remove(*, existing, reason: str, writer: str) -> dict:
    if not existing:
        return {"action": "refuse", "reason": "no such Fact", "status": None}
    if _is_library(existing) or existing.get("space") != SHARED_SPACE:
        return {
            "action": "refuse",
            "reason": "remove is tombstone-only for space=shared; library Facts are inviolable",
            "status": None,
        }
    if not (reason or "").strip():
        return {"action": "refuse", "reason": "remove needs --reason", "status": None}
    return {
        "action": "remove",
        "status": STATUS_REMOVED,
        "reason": reason.strip(),
    }


def _set_words(session, name: str, text: str) -> None:
    words = _words(text)
    if not words:
        return
    session.run(
        """
        MATCH (f:Fact {name: $name})
        OPTIONAL MATCH (f)-[old:HAS_WORD]->(:Word)
        DELETE old
        WITH DISTINCT f
        UNWIND $words AS word
        MERGE (w:Word {text: word})
        MERGE (f)-[:HAS_WORD]->(w)
        """,
        name=name,
        words=words,
    )


def _ensure_shared_indexes(session) -> None:
    try:
        session.run(
            "CREATE INDEX fact_space_topic_status IF NOT EXISTS "
            "FOR (f:Fact) ON (f.space, f.topic, f.status)"
        )
    except _BOLT_FAIL:
        pass


def _fact_row(rec) -> dict | None:
    if rec is None:
        return None
    return {
        "name": rec.get("name"),
        "assistant": rec.get("assistant"),
        "space": rec.get("space"),
        "status": rec.get("status"),
        "topic": rec.get("topic"),
        "summary": rec.get("summary"),
        "key_points": rec.get("key_points") or [],
    }


def cmd_write(args: argparse.Namespace) -> int:
    space = (getattr(args, "space", None) or "").strip()
    if space == SHARED_SPACE:
        return cmd_write_shared(args)
    name = (args.name or "").strip()
    summary = (args.summary or "").strip()
    points = [p.strip() for p in (args.point or []) if p.strip()]
    assistant = (args.assistant or ASSISTANT).strip()
    source = (args.source or "grok").strip()
    session_id = (args.session or "").strip() or None
    if not name or not summary:
        print("write needs --name and --summary", file=sys.stderr)
        return 2
    blocked = _require_grok(args)
    if blocked is not None:
        return blocked
    drv, cfg = _driver()
    now = _now()
    embedded = False
    embed_err = None
    try:
        with drv.session() as s:
            existing = s.run(
                "MATCH (f:Fact {name: $name}) RETURN f.assistant AS a",
                name=name,
            ).single()
            if existing:
                block = _owner_blocks_write(
                    existing["a"], assistant, args.force_assistant,
                )
                if block:
                    print(f"refused: Fact {name!r} {block}", file=sys.stderr)
                    return 3
            s.run(
                """
                MERGE (a:Assistant {id: $assistant})
                ON CREATE SET a.name = $assistant, a.created_at = datetime()
                MERGE (f:Fact {name: $name})
                ON CREATE SET
                    f.summary = $summary,
                    f.key_points = $points,
                    f.source_file = $source,
                    f.assistant = $assistant,
                    f.created_at = $now,
                    f.updated_at = $now
                ON MATCH SET
                    f.summary = $summary,
                    f.key_points = $points,
                    f.source_file = $source,
                    f.updated_at = $now,
                    f.assistant = CASE
                        WHEN f.assistant IS NULL OR size(trim(f.assistant)) = 0
                        THEN $assistant
                        ELSE f.assistant
                    END
                WITH f, a
                MERGE (f)-[:CREATED_BY]->(a)
                """,
                name=name,
                summary=summary,
                points=points,
                assistant=assistant,
                source=source,
                now=now,
            )
            if session_id:
                s.run(
                    """
                    MATCH (f:Fact {name: $name})
                    MERGE (sess:Session {id: $session_id})
                    MERGE (f)-[:LEARNED_IN]->(sess)
                    """,
                    name=name,
                    session_id=session_id,
                )
            _set_words(s, name, name + " " + summary + " " + " ".join(points))
            if not args.no_embed:
                vec = ollama_embed(
                    _fact_text(name, summary, None, points),
                    cfg,
                    timeout=WRITE_EMBED_TIMEOUT,
                )
                if vec:
                    try:
                        _set_embedding(s, name, vec)
                        embedded = True
                    except _BOLT_FAIL as e:
                        embed_err = e.__class__.__name__
    except _BOLT_FAIL as e:
        print(f"write failed: {e}", file=sys.stderr)
        return 1
    finally:
        drv.close()
    extra = ""
    if not args.no_embed and not embedded:
        extra = f" (no embedding: {embed_err or 'ollama down'})"
    print(f"wrote [{assistant}] {name}{extra}")
    return 0


def cmd_write_shared(args: argparse.Namespace) -> int:
    blocked = _require_grok(args)
    if blocked is not None:
        return blocked
    assistant = (args.assistant or ASSISTANT).strip()
    source = (args.source or "grok").strip()
    title = (args.name or "").strip()
    summary = (args.summary or "").strip()
    points = [p.strip() for p in (args.point or []) if p.strip()]
    topic = _topic_slug(getattr(args, "topic", None) or title)
    append = bool(getattr(args, "append", False))
    supersede = bool(getattr(args, "supersede", False))
    if not topic:
        print("shared write needs --topic or --name", file=sys.stderr)
        return 2
    if append:
        if not points:
            print("shared --append needs --point", file=sys.stderr)
            return 2
    elif not title or not summary:
        print("shared write needs --name and --summary (or --append --point)", file=sys.stderr)
        return 2
    drv, cfg = _driver()
    now = _now()
    day = _day(now)
    embedded = False
    embed_err = None
    wrote_name = None
    action = None
    try:
        with drv.session() as s:
            _ensure_shared_indexes(s)
            probe_name = _shared_name(title, day) if title else ""
            existing = None
            if probe_name:
                rec = s.run(
                    """
                    MATCH (f:Fact {name: $name})
                    RETURN f.name AS name, f.assistant AS assistant,
                           f.space AS space, f.status AS status, f.topic AS topic
                    """,
                    name=probe_name,
                ).single()
                existing = _fact_row(rec) if rec else None
            act = s.run(
                """
                MATCH (f:Fact {space: $space, topic: $topic, status: $status})
                RETURN f.name AS name, f.assistant AS assistant,
                       f.space AS space, f.status AS status, f.topic AS topic
                ORDER BY f.created_at DESC
                LIMIT 1
                """,
                space=SHARED_SPACE,
                topic=topic,
                status=STATUS_ACTIVE,
            ).single()
            active = _fact_row(act) if act else None
            taken = {
                r["n"]
                for r in s.run(
                    "MATCH (f:Fact) WHERE f.name STARTS WITH $p RETURN f.name AS n",
                    p=f"Shared — {title} — {day}" if title else "Shared — ",
                )
            }
            plan = _plan_shared_write(
                existing_by_name=existing,
                active_for_topic=active,
                topic=topic,
                title=title or (active["name"] if active else topic),
                day=day,
                append=append,
                supersede=supersede,
                writer=assistant,
                taken_names=taken,
            )
            if plan["action"] == "refuse":
                print(f"refused: {plan['reason']}", file=sys.stderr)
                return 3
            action = plan["action"]
            wrote_name = plan["name"]
            if action == "append":
                dated = [_dated_point(day, p) for p in points]
                rec = s.run(
                    """
                    MATCH (f:Fact {name: $name})
                    WHERE f.space = $space AND f.status = $status
                    SET f.key_points = coalesce(f.key_points, []) + $points,
                        f.updated_at = $now
                    RETURN f.summary AS summary, f.key_points AS key_points
                    """,
                    name=wrote_name,
                    space=SHARED_SPACE,
                    status=STATUS_ACTIVE,
                    points=dated,
                    now=now,
                ).single()
                if rec is None:
                    print("refused: active head vanished", file=sys.stderr)
                    return 3
                summary = rec["summary"] or ""
                points = rec["key_points"] or []
            else:
                dated_points = [_dated_point(day, p) for p in points]
                s.run(
                    """
                    MERGE (a:Assistant {id: $assistant})
                    ON CREATE SET a.name = $assistant, a.created_at = datetime()
                    CREATE (f:Fact {
                        name: $name,
                        summary: $summary,
                        key_points: $points,
                        source_file: $source,
                        assistant: $assistant,
                        space: $space,
                        topic: $topic,
                        status: $status,
                        valid_from: $day,
                        created_at: $now,
                        updated_at: $now
                    })
                    MERGE (f)-[:CREATED_BY]->(a)
                    """,
                    name=wrote_name,
                    summary=summary,
                    points=dated_points,
                    source=source,
                    assistant=assistant,
                    space=SHARED_SPACE,
                    topic=topic,
                    status=STATUS_ACTIVE,
                    day=day,
                    now=now,
                )
                if action == "supersede" and plan["predecessor"]:
                    rec = s.run(
                        """
                        MATCH (neu:Fact {name: $neu}), (old:Fact {name: $old})
                        WHERE old.space = $space AND old.status = $active
                        SET old.status = $superseded, old.superseded_at = $now,
                            old.updated_at = $now
                        MERGE (neu)-[r:SUPERSEDES]->(old)
                        SET r.at = $now, r.by = $assistant
                        RETURN old.name AS n
                        """,
                        neu=wrote_name,
                        old=plan["predecessor"],
                        space=SHARED_SPACE,
                        active=STATUS_ACTIVE,
                        superseded=STATUS_SUPERSEDED,
                        now=now,
                        assistant=assistant,
                    ).single()
                    if rec is None:
                        print(
                            f"refused: predecessor {plan['predecessor']!r} "
                            "was not an active shared Fact",
                            file=sys.stderr,
                        )
                        return 3
                points = dated_points
            _set_words(
                s, wrote_name,
                wrote_name + " " + (summary or "") + " " + " ".join(points),
            )
            if not args.no_embed:
                vec = ollama_embed(
                    _fact_text(wrote_name, summary, None, points),
                    cfg,
                    timeout=WRITE_EMBED_TIMEOUT,
                )
                if vec:
                    try:
                        _set_embedding(s, wrote_name, vec)
                        embedded = True
                    except _BOLT_FAIL as e:
                        embed_err = e.__class__.__name__
    except _BOLT_FAIL as e:
        print(f"write failed: {e}", file=sys.stderr)
        return 1
    finally:
        drv.close()
    extra = ""
    if not args.no_embed and not embedded:
        extra = f" (no embedding: {embed_err or 'ollama down'})"
    print(f"{action} [{assistant}] {wrote_name}{extra}")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    blocked = _require_grok(args)
    if blocked is not None:
        return blocked
    name = (args.name or "").strip()
    reason = (args.reason or "").strip()
    writer = (args.assistant or ASSISTANT).strip()
    if not name:
        print("remove needs --name", file=sys.stderr)
        return 2
    drv, _ = _driver()
    now = _now()
    try:
        with drv.session() as s:
            rec = s.run(
                """
                MATCH (f:Fact {name: $name})
                RETURN f.name AS name, f.assistant AS assistant,
                       f.space AS space, f.status AS status, f.topic AS topic
                """,
                name=name,
            ).single()
            existing = _fact_row(rec) if rec else None
            plan = _plan_shared_remove(
                existing=existing, reason=reason, writer=writer,
            )
            if plan["action"] == "refuse":
                print(f"refused: {plan['reason']}", file=sys.stderr)
                return 3
            s.run(
                """
                MATCH (f:Fact {name: $name})
                WHERE f.space = $space
                SET f.status = $status,
                    f.removed_at = $now,
                    f.removed_reason = $reason,
                    f.updated_at = $now
                """,
                name=name,
                space=SHARED_SPACE,
                status=STATUS_REMOVED,
                now=now,
                reason=reason,
            )
    except _BOLT_FAIL as e:
        print(f"remove failed: {e}", file=sys.stderr)
        return 1
    finally:
        drv.close()
    print(f"removed (tombstone) [{writer}] {name}")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    topic = _topic_slug(args.topic)
    if not topic:
        print("history needs --topic", file=sys.stderr)
        return 2
    drv, _ = _driver()
    try:
        with drv.session() as s:
            rows = s.run(
                """
                MATCH (f:Fact {space: $space, topic: $topic})
                OPTIONAL MATCH (f)-[:SUPERSEDES]->(old:Fact)
                RETURN f.name AS name, f.status AS status,
                       f.valid_from AS valid_from, f.assistant AS assistant,
                       f.removed_reason AS removed_reason,
                       old.name AS superseded
                ORDER BY coalesce(f.valid_from, ''), f.created_at DESC
                """,
                space=SHARED_SPACE,
                topic=topic,
            ).data()
    except _BOLT_FAIL as e:
        print(f"history failed: {e}", file=sys.stderr)
        return 1
    finally:
        drv.close()
    if not rows:
        print("No shared Facts for that topic.")
        return 0
    for r in rows:
        extra = ""
        if r.get("superseded"):
            extra = f"  supersedes {r['superseded']}"
        if r.get("removed_reason"):
            extra += f"  reason={r['removed_reason']}"
        print(
            f"{r['valid_from'] or '?':10}  {r['status'] or '?':11}  "
            f"[{r['assistant'] or '?'}] {r['name']}{extra}"
        )
    return 0


def cmd_organize(args: argparse.Namespace) -> int:
    """RELATED_TO among one mind's facts that share >=2 words. Does not touch other minds' edges."""
    blocked = _require_grok(args)
    if blocked is not None:
        return blocked
    assistant = (args.assistant or ASSISTANT).strip()
    drv, _ = _driver()
    try:
        with drv.session() as s:
            rec = s.run(
                """
                MATCH (f1:Fact {assistant: $a})-[:HAS_WORD]->(w:Word)<-[:HAS_WORD]-(f2:Fact {assistant: $a})
                WHERE elementId(f1) < elementId(f2)
                WITH f1, f2, collect(DISTINCT w.text) AS shared
                WHERE size(shared) >= 2
                MERGE (f1)-[r:RELATED_TO]->(f2)
                SET r.shared_keywords = shared,
                    r.shared_count = size(shared),
                    r.source = 'grok-organize'
                RETURN count(*) AS linked
                """,
                a=assistant,
            ).single()
            n = rec["linked"] if rec else 0
    except _BOLT_FAIL as e:
        print(f"organize failed: {e}", file=sys.stderr)
        return 1
    finally:
        drv.close()
    print(f"linked {n} {assistant} fact pairs")
    return 0


def cmd_embed(args: argparse.Namespace) -> int:
    """Backfill Fact.embedding for nodes that lack one. Indexing only — does not rewrite content."""
    drv, cfg = _driver()
    try:
        with drv.session() as s:
            rows = s.run(
                """
                MATCH (f:Fact)
                WHERE f.embedding IS NULL
                RETURN f.name AS name,
                       f.summary AS summary,
                       f.content AS content,
                       f.key_points AS key_points,
                       coalesce(f.assistant,'(none)') AS assistant
                ORDER BY f.name
                """
            ).data()
    except _BOLT_FAIL as e:
        print(f"embed failed: {e}", file=sys.stderr)
        drv.close()
        return 1

    total = len(rows)
    if args.limit and args.limit > 0:
        rows = rows[: args.limit]
    by = {}
    for r in rows:
        by[r["assistant"]] = by.get(r["assistant"], 0) + 1
    breakdown = ", ".join(f"{a} {n}" for a, n in sorted(by.items(), key=lambda x: -x[1])) or "none"
    if args.dry_run:
        drv.close()
        print(f"would embed {len(rows)}/{total} facts ({breakdown})")
        return 0
    if not rows:
        drv.close()
        print("embedded 0/0 (already complete)")
        return 0

    if not ollama_embed("probe", cfg, timeout=min(WRITE_EMBED_TIMEOUT, 5.0)):
        drv.close()
        print("embed aborted: ollama nomic-embed-text unavailable", file=sys.stderr)
        return 1

    ok = 0
    fail = 0
    skipped = 0
    consecutive = 0
    aborted = False
    try:
        with drv.session() as s:
            for i, r in enumerate(rows, 1):
                text = _fact_text(r["name"], r["summary"], r["content"], r["key_points"])
                if not text.strip():
                    skipped += 1
                    continue
                vec = ollama_embed(text, cfg, timeout=WRITE_EMBED_TIMEOUT)
                if not vec:
                    fail += 1
                    consecutive += 1
                    if consecutive >= EMBED_FAIL_ABORT:
                        aborted = True
                        print(
                            f"embed aborted after {consecutive} consecutive failures",
                            file=sys.stderr,
                        )
                        break
                    continue
                try:
                    _set_embedding(s, r["name"], vec)
                    ok += 1
                    consecutive = 0
                except _BOLT_FAIL:
                    fail += 1
                    consecutive += 1
                    if consecutive >= EMBED_FAIL_ABORT:
                        aborted = True
                        print(
                            f"embed aborted after {consecutive} consecutive SET failures",
                            file=sys.stderr,
                        )
                        break
                if i % 20 == 0 or i == len(rows):
                    print(f"embedded {ok}/{len(rows)} (fail {fail} skip {skipped})", flush=True)
    finally:
        drv.close()
    queued = total - ok - fail - skipped
    extra = " aborted" if aborted else ""
    print(f"done {ok} embedded, {fail} failed, {skipped} skipped, {queued} still queued{extra}")
    return 0 if fail == 0 and not aborted else 1


def _prompt_from_hook(data: dict) -> str:
    for key in ("prompt", "text", "userPrompt", "message", "lastAssistantMessage"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    content = data.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, dict):
        t = content.get("text")
        if isinstance(t, str) and t.strip():
            return t.strip()
    return ""


def _write_hits(query: str, hits: list[dict], backend: str = "") -> None:
    lines = [
        f"# Neo4j hits  ({_now()})",
        f"query: {query[:200]}",
        f"backend: {backend or 'fulltext'}",
        "",
    ]
    if not hits:
        lines.append("(none)")
    for h in hits:
        mind = h["assistant"] or "?"
        via = f" via={h['via']}" if h.get("via") else ""
        lines.append(f"- **{h['name']}** [{mind}] score={h['score']}{via}")
        if h["teaser"]:
            lines.append(f"  {h['teaser']}")
    HITS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_hook_prompt(_args: argparse.Namespace) -> int:
    """UserPromptSubmit: hybrid-search the prompt, write neo4j-hits.md. Always exit 0."""
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    prompt = _prompt_from_hook(data)
    if len(prompt) < 8:
        return 0
    query = " ".join(prompt.split()[:24])
    hits: list[dict] = []
    backend = "fulltext"
    drv = None
    try:
        drv, cfg = _driver(connect_timeout=HOOK_CONNECT_TIMEOUT)
        hits, backend = search_memories(
            drv,
            cfg,
            query,
            DEFAULT_MAX,
            mode="hybrid",
            embed_timeout=HOOK_EMBED_TIMEOUT,
            bolt_timeout=HOOK_BOLT_TIMEOUT,
            deadline=HOOK_DEADLINE,
        )
    except Exception:
        pass
    try:
        _write_hits(query, hits, backend)
    except Exception:
        pass
    if drv is not None:
        try:
            drv.close()
        except Exception:
            pass
    return 0


def cmd_hook_session(_args: argparse.Namespace) -> int:
    """SessionStart: write compact graph stats. Always exit 0."""
    try:
        drv, cfg = _driver(connect_timeout=HOOK_CONNECT_TIMEOUT)
        try:
            with drv.session() as s:
                facts = s.run("MATCH (f:Fact) RETURN count(f) AS c").single()["c"]
                grok = s.run(
                    "MATCH (f:Fact {assistant: $a}) RETURN count(f) AS c",
                    a=ASSISTANT,
                ).single()["c"]
                emb = s.run("MATCH (f:Fact) RETURN count(f.embedding) AS c").single()["c"]
                recent = s.run(
                    """
                    MATCH (f:Fact)
                    WHERE f.updated_at IS NOT NULL
                    RETURN f.name AS name, f.assistant AS a
                    ORDER BY f.updated_at DESC LIMIT 5
                    """
                ).data()
        finally:
            drv.close()
        lines = [
            f"# Neo4j session  ({_now()})",
            f"uri: {cfg['uri']}",
            f"facts: {facts}  grok: {grok}  embedded: {emb}/{facts}",
            "search: python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py search QUERY",
            f"vector: {cfg['vector']} + {cfg['embed_model']}",
            "",
        ]
        if recent:
            lines.append("recent:")
            for r in recent:
                lines.append(f"- [{r['a'] or '?'}] {r['name']}")
        SESSION_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        return 0
    return 0


def cmd_hook_stop(_args: argparse.Namespace) -> int:
    """Stop observe: inbox the turn. Never blocks. Always exit 0."""
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if data.get("reason") and data.get("reason") != "end_turn":
        return 0
    if data.get("subagentType"):
        return 0
    rec = {
        "ts": _now(),
        "sessionId": data.get("sessionId"),
        "cwd": data.get("cwd") or data.get("workspaceRoot"),
        "promptId": data.get("promptId"),
        "last": _teaser(data.get("lastAssistantMessage") or "", 400),
    }
    try:
        with INBOX_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        return 0
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="neo4j_memory")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="Hybrid (fulltext + vector) search Facts")
    s.add_argument("query")
    s.add_argument("--max", type=int, default=DEFAULT_MAX)
    s.add_argument(
        "--mode",
        choices=("hybrid", "fulltext", "vector"),
        default="hybrid",
        help="hybrid (default), fulltext, or vector-only",
    )
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("stats", help="Graph counts")
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser("write", help="MERGE a Fact tagged to an assistant")
    s.add_argument("--name", default=None)
    s.add_argument("--summary", default=None)
    s.add_argument("--point", action="append")
    s.add_argument("--assistant", default=ASSISTANT)
    s.add_argument("--source", default="grok")
    s.add_argument("--session", default=None, help="Session.id for LEARNED_IN")
    s.add_argument("--no-embed", action="store_true", help="skip Ollama embedding")
    s.add_argument(
        "--force-assistant",
        action="store_true",
        help="allow --assistant other than Grok",
    )
    s.add_argument(
        "--space",
        default=None,
        help="shared: dated add / supersede / append (never in-place overwrite)",
    )
    s.add_argument("--topic", default=None, help="shared topic slug")
    s.add_argument(
        "--supersede",
        action="store_true",
        help="shared: create a dated successor; keep the old Fact as status=superseded",
    )
    s.add_argument(
        "--append",
        action="store_true",
        help="shared: add dated --point to the active Fact; do not change summary",
    )
    s.set_defaults(func=cmd_write)

    s = sub.add_parser(
        "remove",
        help="Tombstone a shared Fact (status=removed). Never DETACH DELETE library Facts.",
    )
    s.add_argument("--name", required=True)
    s.add_argument("--reason", required=True)
    s.add_argument("--assistant", default=ASSISTANT)
    s.add_argument(
        "--force-assistant",
        action="store_true",
        help="allow --assistant other than Grok",
    )
    s.set_defaults(func=cmd_remove)

    s = sub.add_parser("history", help="List shared Facts for a topic, newest last")
    s.add_argument("--topic", required=True)
    s.set_defaults(func=cmd_history)

    s = sub.add_parser("organize", help="RELATED_TO among one mind's facts sharing 2+ words")
    s.add_argument("--assistant", default=ASSISTANT)
    s.add_argument(
        "--force-assistant",
        action="store_true",
        help="allow --assistant other than Grok",
    )
    s.set_defaults(func=cmd_organize)

    s = sub.add_parser("embed", help="Backfill missing Fact.embedding via Ollama")
    s.add_argument("--limit", type=int, default=0, help="max facts to embed this run")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_embed)

    s = sub.add_parser("hook-prompt", help="UserPromptSubmit handler")
    s.set_defaults(func=cmd_hook_prompt)

    s = sub.add_parser("hook-session", help="SessionStart handler")
    s.set_defaults(func=cmd_hook_session)

    s = sub.add_parser("hook-stop", help="Stop observe handler")
    s.set_defaults(func=cmd_hook_stop)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
