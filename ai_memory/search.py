"""
Search functions for the AI Memory System.

Backends:
  search_vector — Neo4j vector index; SEARCH with in-index filters, or an
                  over-fetch + post-filter fallback when the index can't.
  search_graph  — Neo4j fulltext over fact_content + fact_key_points, fused.
  search_hybrid — the fused contract (spec §3): vector + lexical legs, the
                  vector-only floor, RRF, and rank_adjust (collapse/boost).
  search_files  — grep over markdown memory files
  search_faiss  — local FAISS index (offline fallback, Layer 5)

Error semantics (issue #N4):
  * Query ran, zero hits          → return ``[]`` (unchanged)
  * Neo4j unreachable / auth fail → raise ``Neo4jConnectionError``
  * Vector index name not found   → ``search_vector`` returns ``[]`` with one
    warning logged (no latch); a missing fulltext index in ``search_graph``
    likewise degrades (warning, continue with the other index) and only
    raises for other client errors.
  * Other Cypher errors           → raise ``Neo4jQueryError``
  * ``search_vector`` only: a vector index that rejects in-index filter
    predicates (Neo4j 22ND3), or a server that reports the SEARCH clause
    itself as unsupported, latches a process-wide over-fetch fallback
    (``queryNodes`` + post-filter) for ``FALLBACK_TTL_S`` seconds, logging the
    migration hint once per process (only ``reset_fallback()`` clears the
    "already warned" flag). The latch is set only after that retry succeeds,
    and any other Cypher syntax error — i.e. a bug in the SEARCH builder —
    propagates instead of latching.

Drivers can be supplied externally via the ``driver`` keyword to enable pooling
across calls (issue #N1). If absent, the function creates and closes its own.
"""
import logging
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

from neo4j import Query
from neo4j.exceptions import ClientError

from ai_memory._config import get_driver, get_query_timeout, get_workspace
from ai_memory.exceptions import (
    Neo4jConnectionError,
    Neo4jIndexNotFoundError,
    Neo4jQueryError,
)
from ai_memory.retrieval import (
    apply_vector_only_floor, build_fallback_cypher, build_filters, build_fulltext_cypher,
    build_search_cypher, fallback_pool, fuse_rrf, pool_size, rank_adjust,
)


def _list_vector_indexes(driver) -> list:
    """Return all VECTOR index names. Used to build actionable error messages."""
    try:
        with driver.session() as s:
            return [r["name"] for r in s.run(
                Query(
                    "SHOW INDEXES YIELD name, type WHERE type = 'VECTOR' RETURN name",
                    timeout=get_query_timeout(),
                )
            )]
    except Exception:
        return []


def _classify_client_error(driver, e: ClientError, vector_index: Optional[str] = None):
    """Map a neo4j ClientError to an ai_memory exception class with context.

    The vector-index-missing case (very common when users don't override
    NEO4J_VECTOR_INDEX) gets a discoverability hint.
    """
    msg = str(e)
    if "no such vector schema index" in msg.lower() or "no such index" in msg.lower():
        available = _list_vector_indexes(driver) if driver is not None else []
        return Neo4jIndexNotFoundError(vector_index or "<unknown>",
                                       kind="vector", available=available)
    return Neo4jQueryError(msg)


_LUCENE_OPS = re.compile(r"\b(AND|OR|NOT)\b")


def _escape_lucene(query: str) -> str:
    """Escape Lucene special characters and lower-case the boolean keywords so
    a user's 'AND'/'OR'/'NOT' cannot parse as operators (spec §3)."""
    special = r'[\+\-\&\|\!\(\)\{\}\[\]\^\"\~\*\?\:\/\\]'
    escaped = re.sub(special, lambda m: "\\" + m.group(), query)
    return _LUCENE_OPS.sub(lambda m: m.group(1).lower(), escaped)


log = logging.getLogger("ai_memory.search")
FALLBACK_TTL_S = 600.0
_fallback_state = {"active": False, "until": 0.0, "index": None, "warned": False}
# The latch is process-wide and search_vector is called from worker threads; every
# read-modify-write of _fallback_state goes through this lock (it is NOT reentrant,
# so no locked helper may call another).
_fallback_lock = threading.Lock()
MIGRATION_HINT = "run scripts/neo4j_migrate_vector_filters.py to add filter properties to the vector index"


def reset_fallback() -> None:
    """Clear the process-wide vector-search fallback latch. Test/ops helper."""
    with _fallback_lock:
        _fallback_state.update(active=False, until=0.0, index=None, warned=False)


def _use_fallback(index: str) -> bool:
    with _fallback_lock:
        st = _fallback_state
        if not st["active"]:
            return False
        if st["index"] != index or time.monotonic() >= st["until"]:
            st.update(active=False)      # re-probe SEARCH on next call
            return False
        return True


def _activate_fallback(index: str) -> None:
    with _fallback_lock:
        _fallback_state.update(active=True, index=index, until=time.monotonic() + FALLBACK_TTL_S)


def _latch_fallback(index: str) -> None:
    """22ND3 path: activate the over-fetch fallback and log the migration
    hint. The hint is logged once per process — only reset_fallback() clears
    ``warned`` (module docstring / MIGRATION.md)."""
    with _fallback_lock:
        _fallback_state.update(active=True, index=index, until=time.monotonic() + FALLBACK_TTL_S)
        warn = not _fallback_state["warned"]
        if warn:
            _fallback_state["warned"] = True
    if warn:
        log.warning("vector index %r lacks filter properties; using over-fetch fallback — %s", index, MIGRATION_HINT)


def _is_22nd3(e: ClientError) -> bool:
    msg = str(e)
    return ("22ND3" in msg) or ("not an additional property" in msg) or (
        getattr(e, "code", "") == "Neo.ClientError.Statement.PropertyNotFound" and "additional property" in msg)


# A server that cannot run the SEARCH clause says so: either it rejects the
# CYPHER 25 language version outright, or it reports SEARCH itself as unsupported
# / unknown. Anything else with a SyntaxError code is a bug in the *builder* and
# must surface (it was how the earlier $index parameterization bug hid itself).
_SEARCH_UNSUPPORTED = re.compile(
    r"unsupported language version"
    r"|search\b[^.]{0,60}?(?:not supported|unsupported|unknown|unrecognized)"
    r"|(?:not supported|unsupported|unknown|unrecognized)[^.]{0,60}?\bsearch\b",
    re.IGNORECASE,
)


def _is_search_syntax_error(e: ClientError, statement: str) -> bool:
    """True only for a SyntaxError that says the server cannot run the SEARCH
    clause at all — never for an arbitrary SyntaxError on a statement that
    happens to contain the word SEARCH."""
    if getattr(e, "code", "") != "Neo.ClientError.Statement.SyntaxError":
        return False
    if "SEARCH" not in statement:
        return False
    return bool(_SEARCH_UNSUPPORTED.search(str(e)))


def _is_index_unavailable(e: ClientError) -> bool:
    m = str(e).lower()
    return ("no such index" in m or "no such vector schema index" in m
            or "no such fulltext schema index" in m or "populating" in m)


def _embed(query: str):
    """Ollama embedding; None when unavailable (kept separate so tests can stub it)."""
    try:
        import ollama
        return ollama.embeddings(model="nomic-embed-text", prompt=query)["embedding"]
    except Exception as e:  # noqa: BLE001 — Ollama is a separate dependency
        print(f"Embedding error (Ollama unreachable?): {e}", file=sys.stderr)
        return None


def _rows_to_hits(rows, via: str) -> List[dict]:
    hits = []
    for r in rows:
        text = r["text"] or ""
        hits.append({
            "name": r["name"],
            "teaser": text[:500],
            "key_points": list(r["key_points"] or []),
            "assistant": r["assistant"],
            "status": r["status"],
            "space": r["space"],
            "score": round(float(r["s"]), 4),
            "via": via,
            "source": f"neo4j://Fact/{r['name']}",
        })
        if via == "vec":
            hits[-1]["vec_score"] = round(float(r["s"]), 4)
    return hits


def search_vector(
    query: str,
    *,
    workspace=None,
    max_results: int = 5,
    assistant: Optional[str] = None,
    space: Optional[str] = None,
    trust_filter: Optional[str] = None,
    driver=None,
    pool: Optional[int] = None,
) -> List[dict]:
    """Semantic search via the vector index (spec §3 vector leg).

    Filters run inside the index (Cypher 25 SEARCH). On a server or index that
    cannot, a process-wide latch switches to over-fetch + post-filter (approach B).
    Returns up to ``pool`` hits when ``pool`` is given (callers that fuse), else
    up to ``max_results``. Empty / whitespace query -> []; Ollama down -> [];
    vector index missing or populating -> [] with one warning, no latch.
    """
    if not query or not query.strip():
        return []
    embedding = _embed(query)
    if embedding is None:
        return []
    owns_driver = driver is None
    if owns_driver:
        driver = get_driver(workspace)
    index = os.getenv("NEO4J_VECTOR_INDEX", "fact_embeddings")
    width = pool if pool is not None else pool_size(max_results)
    where, fparams = build_filters(assistant, space, trust_filter)
    params = dict(fparams, index=index, vec=embedding, pool=width, pool2=fallback_pool(max_results))
    timeout = get_query_timeout()
    try:
        with driver.session() as session:
            def run(text):
                return list(session.run(Query(text, timeout=timeout), params))
            try:
                if _use_fallback(index):
                    rows = run(build_fallback_cypher(where))
                else:
                    search_text = build_search_cypher(where, index)
                    try:
                        rows = run(search_text)
                    except ClientError as e:
                        # Latch only once the queryNodes retry has actually
                        # succeeded: if it fails (e.g. the index is missing or
                        # still populating) the outer handler returns [] and the
                        # latch must not be left set for FALLBACK_TTL_S.
                        if _is_22nd3(e):
                            rows = run(build_fallback_cypher(where))
                            _latch_fallback(index)
                        elif _is_search_syntax_error(e, search_text):
                            # The server can't run the SEARCH clause at all — a
                            # different failure from the 22ND3 missing-filter-
                            # properties case, so don't misreport it as a
                            # missing migration.
                            rows = run(build_fallback_cypher(where))
                            _activate_fallback(index)
                            log.warning("vector index %r: SEARCH statement rejected: %s", index, e)
                        else:
                            raise
            except ClientError as e:
                if _is_index_unavailable(e):
                    log.warning("vector index %r not found or still populating; vector leg skipped for this call", index)
                    return []
                raise _classify_client_error(driver, e, vector_index=index) from e
        hits = _rows_to_hits(rows, "vec")
        return hits[:pool] if pool is not None else hits[:max_results]
    finally:
        if owns_driver:
            driver.close()


def search_graph(
    query: str,
    *,
    workspace=None,
    max_results: int = 5,
    assistant: Optional[str] = None,
    space: Optional[str] = None,
    trust_filter: Optional[str] = None,
    driver=None,
    pool: Optional[int] = None,
) -> List[dict]:
    """Lexical leg (spec §3): fact_content and fact_key_points fulltext indexes,
    filters as a post-WHERE, fused within the leg. A missing key-points index
    degrades to content-only with one warning."""
    if not query or not query.strip():
        return []
    lucene = _escape_lucene(query)
    owns_driver = driver is None
    if owns_driver:
        driver = get_driver(workspace)
    width = pool if pool is not None else pool_size(max_results)
    where, fparams = build_filters(assistant, space, trust_filter)
    cypher = build_fulltext_cypher(where)
    timeout = get_query_timeout()
    indexes = [("ft", os.getenv("NEO4J_FULLTEXT_INDEX", "fact_content")),
               ("kp", os.getenv("NEO4J_FULLTEXT_KP_INDEX", "fact_key_points"))]
    legs = []
    try:
        with driver.session() as session:
            for via, index in indexes:
                params = dict(fparams, index=index, q=lucene, pool=width)
                try:
                    rows = list(session.run(Query(cypher, timeout=timeout), params))
                except ClientError as e:
                    if _is_index_unavailable(e):
                        log.warning("fulltext index %r not found; lexical leg continues without it", index)
                        continue
                    raise _classify_client_error(driver, e) from e
                if rows:
                    legs.append((via, _rows_to_hits(rows, via)))
    finally:
        if owns_driver:
            driver.close()
    if not legs:
        return []
    hits = legs[0][1] if len(legs) == 1 else fuse_rrf(legs)
    return hits[:pool] if pool is not None else hits[:max_results]


def search_files(
    query: str,
    *,
    workspace=None,
    max_results: int = 5,
    max_files: Optional[int] = None,
) -> List[dict]:
    """
    Search memory files via grep (fixed-string, case-insensitive).

    Empty / whitespace-only queries short-circuit to [] — ``grep -F ""``
    matches every line of every file, so unguarded empty queries can flood
    output (issue #40, sibling to #39's guard on the Neo4j/FAISS backends).

    Searches MEMORY.md first (score 5.0), then *.md files in ``memory/``
    (score 3.0), sorted by mtime descending so the most recently touched
    files are searched first.

    ``max_files`` caps how many *.md files are scanned (None = no cap).
    Useful when the memory dir is small and you want full coverage; the
    previous behaviour was a hard 30-file cap reverse-alphabetic, which
    silently missed half of any semantically-named corpus.

    ``MEMORY.md`` is looked up first at ``workspace/MEMORY.md`` (the
    project-redistribution convention) and then at
    ``workspace/memory/MEMORY.md`` (the Claude-style index inside the
    memory dir). First match wins.

    Returns [] if no matches or memory directory doesn't exist.
    """
    if not query or not query.strip():
        return []
    ws = get_workspace(workspace)
    memory_dir = ws / "memory"
    results = []

    try:
        for memory_file in (ws / "MEMORY.md", memory_dir / "MEMORY.md"):
            if memory_file.exists():
                proc = subprocess.run(
                    ["grep", "-F", "-i", "-C", "2", "--", query, str(memory_file)],
                    capture_output=True,
                    text=True,
                )
                if proc.returncode == 0:
                    results.append({
                        "source": str(memory_file),
                        "score": 5.0,
                        "content": proc.stdout[:500],
                    })
                break

        if memory_dir.exists():
            # Sort by mtime descending — works for both YYYY-MM-DD daily notes
            # (newest first) and semantically-named files (recently touched first).
            # Skip MEMORY.md — it was already searched above at score 5.0.
            def _safe_mtime(f: Path) -> float:
                # stat() can raise on broken symlinks or permission issues;
                # treat unreachable files as oldest rather than crash the sort.
                try:
                    return f.stat().st_mtime
                except OSError:
                    return 0.0
            daily_files = sorted(
                (f for f in memory_dir.glob("*.md") if f.name != "MEMORY.md"),
                key=_safe_mtime,
                reverse=True,
            )
            if max_files is not None:
                daily_files = daily_files[:max_files]
            for f in daily_files:
                proc = subprocess.run(
                    ["grep", "-F", "-i", "-C", "2", "--", query, str(f)],
                    capture_output=True,
                    text=True,
                )
                if proc.returncode == 0:
                    results.append({
                        "source": str(f),
                        "score": 3.0,
                        "content": proc.stdout[:300],
                    })
                    if len(results) >= max_results:
                        break

    except Exception as e:
        print(f"File search error: {e}", file=sys.stderr)

    return results


def search_faiss(
    query: str,
    *,
    workspace=None,
    max_results: int = 5,
) -> List[dict]:
    """
    Search local FAISS index for semantic similarity (Layer 5 — offline fallback).

    Returns [] if the FAISS index hasn't been built yet or the query is empty.
    """
    if not query or not query.strip():
        return []
    ws = get_workspace(workspace)
    index_path = ws / "memory" / "embeddings" / "faiss.index"
    meta_path = ws / "memory" / "embeddings" / "faiss_meta.pkl"

    if not index_path.exists() or not meta_path.exists():
        return []

    try:
        import faiss
        import numpy as np
        import pickle
        import ollama

        embedding = ollama.embeddings(model="nomic-embed-text", prompt=query)["embedding"]
        index = faiss.read_index(str(index_path))
        with open(meta_path, "rb") as f:
            meta = pickle.load(f)

        q = np.array([embedding], dtype=np.float32)
        distances, indices = index.search(q, max_results)

        return [
            {
                "source": meta[i]["source"],
                "score": float(distances[0][j]),
                "name": meta[i]["name"],
            }
            for j, i in enumerate(indices[0])
            if i != -1
        ]

    except Exception as e:
        print(f"FAISS search error: {e}", file=sys.stderr)
        return []


MODES = ("hybrid", "fulltext", "vector")


def load_supersedes(session) -> Dict[str, Set[str]]:
    """Every SUPERSEDES edge as the multimap ``{new_name: {old_name, ...}}``.

    A multimap, not ``{new: old}``: a keeper that supersedes several olds has one
    row per old, and a plain dict comprehension would keep only the last of them.
    ``{}`` on any failure — best-effort, the name-suffix rule still applies.
    """
    out: Dict[str, Set[str]] = {}
    try:
        for r in session.run(
            Query("MATCH (n:Fact)-[:SUPERSEDES]->(o:Fact) RETURN n.name AS n, o.name AS o",
                  timeout=get_query_timeout())):
            out.setdefault(r["n"], set()).add(r["o"])
    except Exception:  # noqa: BLE001 — collapse is best-effort; name-suffix rule still applies
        return {}
    return out


def search_hybrid(
    query: str,
    *,
    workspace=None,
    k: int = 5,
    assistant: Optional[str] = None,
    space: Optional[str] = None,
    trust: Optional[str] = None,
    mode: str = "hybrid",
    driver=None,
) -> List[dict]:
    """The retrieval contract (spec §3): vector + lexical legs at pool width,
    vector-only floor, RRF, sink/collapse, active-only exact-name boost, cut to k."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
    if not query or not query.strip():
        return []
    owns_driver = driver is None
    if owns_driver:
        driver = get_driver(workspace)
    width = pool_size(k)
    try:
        common = dict(workspace=workspace, max_results=k, assistant=assistant, space=space,
                      trust_filter=trust, driver=driver, pool=width)
        vec = search_vector(query, **common) if mode in ("hybrid", "vector") else []
        lex = search_graph(query, **common) if mode in ("hybrid", "fulltext") else []
        # Single-leg modes go through the same floor -> fuse -> rank_adjust
        # pipeline as hybrid, just with the other leg empty (I2/M4): this
        # applies the vector-only floor to mode="vector" and gives
        # mode="fulltext" hits the same `via` tag hybrid mode would.
        vec = apply_vector_only_floor(vec, lex)
        legs = [(leg, hits) for leg, hits in (("vec", vec), ("lex", lex)) if hits]
        fused = fuse_rrf(legs)
        with driver.session() as session:
            supersedes = load_supersedes(session)
        return rank_adjust(fused, query, supersedes)[:k]
    finally:
        if owns_driver:
            driver.close()
