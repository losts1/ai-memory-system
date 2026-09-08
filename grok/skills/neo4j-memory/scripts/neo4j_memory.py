#!/usr/bin/env python3
"""Grok ↔ Neo4j memory CLI.

Reads credentials from ~/.grok/.env.neo4j (or $GROK_HOME/.env.neo4j).
Writes Facts tagged assistant=Grok. Refuses to overwrite another mind's Fact.

Search is hybrid by default: Lucene fulltext + Ollama nomic-embed-text against
the Neo4j vector index. Ollama down or a missing index → fulltext only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import threading
import urllib.error
import urllib.request
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import Future, wait
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values
from neo4j import GraphDatabase, Query
from neo4j.exceptions import ConfigurationError, DriverError, Neo4jError

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
VECTOR_ONLY_FLOOR = 0.80
VECTOR_ONLY_MIN_SCORE = VECTOR_ONLY_FLOOR  # alias kept for old references
HOOK_EMBED_TIMEOUT = 3.0
HOOK_BOLT_TIMEOUT = 4.0
HOOK_CONNECT_TIMEOUT = 1.5
HOOK_DEADLINE = 5.0
SEARCH_EMBED_TIMEOUT = 15.0
SEARCH_BOLT_TIMEOUT = 15.0
WRITE_EMBED_TIMEOUT = 30.0
EMBED_FAIL_ABORT = 3
EMBED_CHARS = 2000
FACT_EMBED_CHARS = EMBED_CHARS

_LUCENE_SPECIAL = re.compile(r'[\+\-\&\|\!\(\)\{\}\[\]\^\"\~\*\?\:\/\\]')
_LUCENE_OPS = re.compile(r"\b(AND|OR|NOT)\b")


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
    # Server notifications ("property key does not exist", index hints) are
    # logged to stderr by the driver and read as errors by a TUI model.
    # notifications_min_severity exists since neo4j-python 5.6. The driver
    # signature is (uri, *, auth, **config), so the key cannot be detected by
    # inspection; try it and fall back for older drivers that reject it.
    try:
        drv = GraphDatabase.driver(
            c["uri"], auth=(c["user"], c["password"]),
            notifications_min_severity="OFF", **kw,
        )
    except (ConfigurationError, TypeError):
        drv = GraphDatabase.driver(
            c["uri"], auth=(c["user"], c["password"]), **kw,
        )
    return drv, c


def _daemon_submit(fn, *args, **kwargs) -> Future:
    fut: Future = Future()

    def run() -> None:
        if not fut.set_running_or_notify_cancel():
            return
        try:
            fut.set_result(fn(*args, **kwargs))
        except Exception as e:
            if not fut.cancelled():
                fut.set_exception(e)

    threading.Thread(target=run, name="neo4j-mem", daemon=True).start()
    return fut


def _escape_lucene(q: str) -> str:
    """Escape Lucene special characters and lower-case the boolean keywords so a
    user's 'AND'/'OR'/'NOT' cannot parse as operators (spec §3). Copied verbatim
    from ai_memory/search.py::_escape_lucene; parity is asserted by
    tests/test_retrieval_contract.py."""
    escaped = _LUCENE_SPECIAL.sub(lambda m: "\\" + m.group(), q)
    return _LUCENE_OPS.sub(lambda m: m.group(1).lower(), escaped)


def _teaser(text, n=TEASER) -> str:
    if not text:
        return ""
    s = " ".join(str(text).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


_TOKEN = re.compile(r"[A-Za-z0-9]+")


def normalize_ws(s: str) -> str:
    return " ".join((s or "").split())


def gram_tokens(text: str) -> list[str]:
    return [m.group(0).lower() for m in _TOKEN.finditer(text or "")]


def strip_boilerplate(text: str, grams, *, min_run: int = 2) -> str:
    """Remove runs of >= min_run consecutive boilerplate grams; keep everything else verbatim
    (whitespace-normalised). A lone boilerplate gram is kept."""
    text = text or ""
    grams = set(grams or ())
    spans = [(m.start(), m.end(), m.group(0).lower()) for m in _TOKEN.finditer(text)]
    toks = [s[2] for s in spans]
    if not grams or len(toks) < 4:
        return normalize_ws(text)
    hit = [" ".join(toks[i:i + 4]) in grams for i in range(len(toks) - 3)]

    # Find maximal runs of boilerplate grams and collect removal ranges
    remove_ranges = []
    i = 0
    while i < len(hit):
        if hit[i]:
            j = i
            while j < len(hit) and hit[j]:
                j += 1
            # Run from hit position i to j-1 covers tokens i to j+2
            if j - i >= min_run:
                run_start_token = i
                run_end_token = j + 2
                start_pos = spans[run_start_token][0]
                if run_end_token + 1 < len(spans):
                    end_pos = spans[run_end_token + 1][0]
                else:
                    end_pos = len(text)
                remove_ranges.append((start_pos, end_pos))
            i = j
        else:
            i += 1

    # Build output by removing the identified ranges
    if not remove_ranges:
        return normalize_ws(text)
    out = []
    cursor = 0
    for start, end in remove_ranges:
        out.append(text[cursor:start])
        cursor = end
    out.append(text[cursor:])
    return normalize_ws("".join(out))


def fact_embed_text(name: str | None, summary: str | None, key_points, content: str | None, boilerplate) -> str:
    parts = [name or ""]
    if summary and summary.strip():
        parts.append(summary.strip())
    if isinstance(key_points, str):
        key_points = [key_points]
    for p in (key_points or []):
        if p and str(p).strip():
            parts.append("- " + str(p).strip())
    if content and content.strip():
        parts.append(content.strip())
    prepared = normalize_ws("\n".join(parts))[:EMBED_CHARS]
    return strip_boilerplate(prepared, boilerplate)


def text_sha(text: str, version: int) -> str:
    return hashlib.sha256(f"{version}\n{text}".encode()).hexdigest()[:16]


def _fact_text(name, summary=None, content=None, key_points=None, boilerplate=()) -> str:
    return fact_embed_text(name, summary, key_points, content, boilerplate)


# --- Provenance-carrying CAS embed write (verbatim from ai_memory/embed.py; a contract
# test asserts build_embed_subquery/embed_params produce identical output to the library's).

EMBED_PARAM_NAMES = ("embedding", "embedding_model", "embedding_dim", "embedding_text_sha", "boilerplate_version")
_CAS_EMPTY = {"summary": "''", "key_points": "[]", "content": "''"}


def _cas_default(field: str):
    """A fresh empty value for `field` — a function, not a shared dict entry, so a mutable
    default (the `key_points` list) can never be aliased across calls."""
    return [] if field == "key_points" else ""


def build_embed_subquery(cas_fields: Sequence[str], *, keep_prev: bool = False) -> str:
    """CALL subquery that sets embedding + provenance on `f` only if the CAS fields still match."""
    for f in cas_fields:
        if f not in _CAS_EMPTY:
            raise ValueError(f"unknown CAS field {f!r}")
    where = " AND ".join(f"coalesce(f.{f}, {_CAS_EMPTY[f]}) = $cas_{f}" for f in cas_fields) or "true"
    sets = ["f.embedding_prev = f.embedding"] if keep_prev else []
    sets += [f"f.{p} = ${p}" for p in EMBED_PARAM_NAMES]
    return (
        "CALL {\n"
        "  WITH f\n"
        f"  WITH f WHERE {where}\n"
        f"  SET {', '.join(sets)}\n"
        "  RETURN count(f) AS embedded\n"
        "}"
    )


def embed_params(vector: list, sha: str, version: int, *, cas: dict) -> dict:
    p = {"embedding": vector, "embedding_model": EMBED_MODEL, "embedding_dim": EMBED_DIM,
         "embedding_text_sha": sha, "boilerplate_version": version}
    for f, v in cas.items():
        if f == "key_points":
            # Stored as-is (a STRING key_points is tolerated, not coerced) so the CAS
            # compares against whatever shape is actually on the node.
            p[f"cas_{f}"] = [] if v is None else v if isinstance(v, str) else list(v)
        else:
            p[f"cas_{f}"] = v if v not in (None, "") else _cas_default(f)
    return p


def _read_fact_text(session, name: str) -> dict | None:
    rec = session.run(
        "MATCH (f:Fact {name: $name}) RETURN f.name AS name, f.summary AS summary, "
        "f.key_points AS key_points, f.content AS content", name=name).single()
    if rec is None:
        return None
    return {"name": rec["name"], "summary": rec["summary"], "key_points": rec["key_points"], "content": rec["content"]}


def _embed_fact_cas(
    session, name: str, cfg: dict, retrieval_cfg: dict | None, *, timeout: float | None, row: dict | None = None,
) -> str:
    """Read → prepare → embed → CAS write (all three text fields), stamping provenance from
    `cfg` (the model/dim actually used) rather than the module defaults. Returns one of
    embedded|cas_skipped|embed_failed|empty_text|missing|no_config.

    `row` lets a caller that already read the Fact (e.g. to build the same text for
    `_write_tokens`) pass it in instead of paying a second round trip; the prepared text is
    still derived the same way from it, so both writers see identical canonical text."""
    if retrieval_cfg is None:
        return "no_config"
    if row is None:
        row = _read_fact_text(session, name)
    if row is None:
        return "missing"
    text = fact_embed_text(row["name"], row["summary"], row["key_points"], row["content"], retrieval_cfg["boilerplate"])
    if not text.strip():
        return "empty_text"
    vec = ollama_embed(text, cfg, timeout=timeout)
    if not vec:
        return "embed_failed"
    version = retrieval_cfg["version"]
    params = embed_params(
        vec, text_sha(text, version), version,
        cas={"summary": row["summary"], "key_points": row["key_points"], "content": row["content"]},
    )
    params["embedding_model"] = cfg["embed_model"]
    params["embedding_dim"] = cfg["embed_dim"]
    params["name"] = name
    cypher = "MATCH (f:Fact {name: $name})\n" + build_embed_subquery(["summary", "key_points", "content"]) + "\nRETURN embedded"
    rec = session.run(cypher, **params).single()
    return "embedded" if rec and rec["embedded"] else "cas_skipped"


_EMBED_STATUS_MSG = {
    "no_config": "no RetrievalConfig",
    "cas_skipped": "cas_skipped — concurrent edit",
    "empty_text": "empty text",
}


def _load_retrieval_config(session) -> dict | None:
    """(:RetrievalConfig {id: 'current'}) → {"version", "boilerplate"}; None when absent or unreadable (spec §4)."""
    try:
        rec = session.run(
            "MATCH (c:RetrievalConfig {id: 'current'}) RETURN c.version AS version, c.boilerplate AS boilerplate"
        ).single()
    except _BOLT_FAIL:
        return None
    if rec is None or rec["version"] is None:
        return None
    return {"version": int(rec["version"]), "boilerplate": frozenset(rec["boilerplate"] or [])}


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
        "space": r["space"],
    }


def _query(cypher: str, timeout: float | None):
    return cypher if timeout is None else Query(cypher, timeout=timeout)


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_index_name(name: str) -> str:
    """Vector index names are inlined into the SEARCH clause (Neo4j rejects a parameter there),
    so only plain identifiers are accepted."""
    if not isinstance(name, str) or not _IDENT.match(name):
        raise ValueError(f"invalid vector index name {name!r}: expected [A-Za-z_][A-Za-z0-9_]*")
    return name


def build_filters(assistant: str | None, space: str | None, trust: str | None) -> tuple[str, dict]:
    """Equality predicates for the inputs that are set, AND-joined. Never status (spec §3)."""
    clauses, params = [], {}
    if assistant:
        clauses.append("f.assistant = $assistant")
        params["assistant"] = assistant
    if space:
        clauses.append("f.space = $space")
        params["space"] = space
    if trust:
        clauses.append("f.provenance_trust = $trust")
        params["trust"] = trust
    return " AND ".join(clauses), params


def build_vector_search_cypher(index: str, where: str) -> str:
    name = validate_index_name(index)
    inner = f"WHERE {where} " if where else ""
    return (
        "CYPHER 25\n"
        "MATCH (f:Fact)\n"
        f"SEARCH f IN (VECTOR INDEX `{name}` FOR $embedding {inner}LIMIT $k) SCORE AS s\n"
        "RETURN f.name AS name, coalesce(f.summary, f.content) AS text, f.assistant AS assistant, "
        "f.key_points AS key_points, f.status AS status, f.space AS space, f.topic AS topic, s AS score\n"
        "ORDER BY s DESC"
    )


def _leg_filters(assistant: str | None, space: str | None, trust: str | None) -> dict:
    """Only forward set filters, so callers (and mocks) with the old signature are unaffected."""
    kw = {}
    if assistant is not None:
        kw["assistant"] = assistant
    if space is not None:
        kw["space"] = space
    if trust is not None:
        kw["trust"] = trust
    return kw


def search_fulltext(
    session, query: str, index: str, limit: int, bolt_timeout: float | None = None,
    *, assistant: str | None = None, space: str | None = None, trust: str | None = None,
    via: str = "ft",
) -> list[dict]:
    """`via` names the index this call queried ("ft" = content, "kp" = key points),
    like ai_memory/search.py::search_graph tagging each index's rows separately —
    a key-points-only hit must not claim it was found in the content index."""
    if not query or not query.strip() or limit <= 0:
        return []
    lucene = _escape_lucene(query)
    where, params = build_filters(assistant, space, trust)
    post = f"WHERE {where}\n" if where else ""
    rows = session.run(
        _query(
            "CALL db.index.fulltext.queryNodes($index, $q)\n"
            "YIELD node, score\n"
            "WITH node AS f, score\n"
            f"{post}"
            "RETURN f.name AS name,\n"
            "       coalesce(f.summary, f.content) AS text,\n"
            "       f.assistant AS assistant,\n"
            "       f.key_points AS key_points,\n"
            "       f.status AS status,\n"
            "       f.space AS space,\n"
            "       f.topic AS topic,\n"
            "       score\n"
            "ORDER BY score DESC\n"
            "LIMIT $limit",
            bolt_timeout,
        ),
        index=index,
        q=lucene,
        limit=limit,
        **params,
    )
    return [_hit_from_record(r) | {"via": via} for r in rows]


def search_vector(
    session, embedding: list[float], index: str, limit: int,
    bolt_timeout: float | None = None,
    *, assistant: str | None = None, space: str | None = None, trust: str | None = None,
) -> list[dict]:
    if not embedding or limit <= 0:
        return []
    where, params = build_filters(assistant, space, trust)
    cypher = build_vector_search_cypher(index, where)
    rows = session.run(
        _query(cypher, bolt_timeout),
        k=limit,
        embedding=embedding,
        **params,
    )
    hits = []
    for r in rows:
        h = _hit_from_record(r)
        h["via"] = "vec"
        h["vec_score"] = h["score"]
        hits.append(h)
    return hits


def merge_rrf(
    ranked_lists: list[tuple[str, list[dict]]],
    limit: int | None = None,
    k: int = RRF_K,
) -> list[dict]:
    """Reciprocal-rank fusion. `ranked_lists` is [(origin, hits), ...].
    `limit=None` fuses (and returns) the whole pool, unsliced."""
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
    ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
    if limit is not None:
        ranked = ranked[: max(limit, 0)]
    out = []
    for name, sc in ranked:
        h = dict(meta[name])
        h["score"] = round(sc, 6)
        h["via"] = "+".join(sorted(origins[name]))
        out.append(h)
    return out


def _pool_size(limit: int) -> int:
    return max(int(limit) * 4, 16)


# --- Ranking rules, copied verbatim from ai_memory/retrieval.py so the two
# implementations agree on fixtures (tests/test_retrieval_contract.py). ---

# Trailing time/date suffixes only: "(15:30 EDT)", "(2026-03-10)", "— 2026-08-30", "— 2026-08-30 #2".
_TRAILING_SUFFIX = re.compile(
    r"(\s*\([^()]*?(?:\d{1,2}:\d{2}|\d{4}-\d{2}-\d{2}|UTC|EDT|EST|\bET\b)\s*\)"
    r"|\s*[—-]\s*\d{4}-\d{2}-\d{2}(?:\s*#\d+)?)\s*$"
)


def strip_time_suffix(name: str) -> str:
    return _TRAILING_SUFFIX.sub("", name or "").strip()


def as_supersedes_multimap(supersedes) -> dict:
    """Normalise a SUPERSEDES map to the multimap shape ``{new: {old, ...}}``.

    ``_load_supersedes`` returns that shape so a keeper superseding several olds
    keeps every edge. A legacy ``{new: old}`` mapping is accepted and widened."""
    out: dict = {}
    for new, olds in (supersedes or {}).items():
        bucket = out.setdefault(new, set())
        if isinstance(olds, str):
            bucket.add(olds)
        else:
            bucket.update(olds)
    return out


def _chain(name: str, supersedes) -> set:
    """All names reachable from ``name`` along SUPERSEDES in either direction."""
    fwd = as_supersedes_multimap(supersedes)
    inv: dict = {}
    for new, olds in fwd.items():
        for old in olds:
            inv.setdefault(old, []).append(new)
    seen, todo = set(), [name]
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen.add(n)
        todo.extend(fwd.get(n, ()))
        todo.extend(inv.get(n, []))
    return seen


def same_topic(a: dict, b: dict, supersedes: dict | None = None) -> bool:
    """Spec §3: connected by a SUPERSEDES chain, or same name once the trailing
    time/date suffix is stripped (compared case-insensitively, like the
    exact-name boost in ``rank_adjust``)."""
    an, bn = a.get("name") or "", b.get("name") or ""
    if an == bn:
        return True
    if strip_time_suffix(an).casefold() == strip_time_suffix(bn).casefold():
        return True
    if supersedes and bn in _chain(an, supersedes):  # noqa: SIM103 (byte-identical w/ ai_memory/retrieval.py)
        return True
    return False


# --- Edge-layer rules, copied verbatim from ai_memory/wordindex.py (is_duplicate uses
# this file's own strip_time_suffix/_chain above rather than an ai_memory import).
# Parity with the library is asserted by tests/test_retrieval_contract.py. ---

STOP = frozenset([
    "the", "and", "for", "with", "from", "via", "per", "how", "but", "key", "what", "when", "why", "this", "that",
    "are", "was", "were", "not", "you", "your", "has", "have",
    "utc", "edt", "est", "pst", "pdt", "cst", "cdt", "gmt",
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
    "nova", "weft", "grok", "claude", "thread", "shared", "ntr"
])
SHORT = frozenset({"ai", "ml", "sql", "gpu", "nlp", "rl", "api", "cli", "qa", "etl"})
EDGE_K = 5
DUP_COS = 0.95
TOKEN_CAP = 24
_TOK = re.compile(r"[a-z0-9]+")


def _keep(w: str) -> bool:
    return w not in STOP and not w.isdigit() and (len(w) >= 3 or w in SHORT)


def tokenize(text: str, name: str = "", cap: int = TOKEN_CAP) -> list[str]:
    cnt = Counter(w for w in _TOK.findall((text or "").lower()) if _keep(w))
    name_toks = [w for w in dict.fromkeys(_TOK.findall((name or "").lower())) if w in cnt]
    rest = [w for w, _ in cnt.most_common() if w not in name_toks]
    return (name_toks + rest)[:cap]


def _w(tok: str, idf, default_idf: float) -> float:
    return idf.get(tok, default_idf)


def tfidf_norm(tokens, idf, default_idf: float) -> float:
    return math.sqrt(sum(_w(t, idf, default_idf) ** 2 for t in set(tokens)))


def tfidf_cosine(tokens_a, tokens_b, idf, norm_a: float, norm_b: float, default_idf: float) -> float:
    if not norm_a or not norm_b:
        return 0.0
    shared = set(tokens_a) & set(tokens_b)
    return sum(_w(t, idf, default_idf) ** 2 for t in shared) / (norm_a * norm_b)


def shared_keywords(tokens_a, tokens_b, idf, cap: int = 10) -> list[str]:
    shared = set(tokens_a) & set(tokens_b)
    return sorted(shared, key=lambda t: (-idf.get(t, 0.0), t))[:cap]


def zscore(v: float, mean: float, std: float) -> float:
    return (v - mean) / std if std else 0.0


def blend(t: float, c: float, base) -> float:
    return 0.5 * zscore(t, base["t_mean"], base["t_std"]) + 0.5 * zscore(c, base["c_mean"], base["c_std"])


def is_duplicate(name_a: str, name_b: str, cos: float, supersedes: dict | None = None) -> bool:
    """``supersedes`` is the ``{new: {old, ...}}`` multimap from _load_supersedes,
    so every twin of a keeper is recognised, not just the last edge loaded."""
    if strip_time_suffix(name_a) == strip_time_suffix(name_b):
        return True
    if cos >= DUP_COS:
        return True
    return bool(supersedes) and name_b in _chain(name_a, supersedes)


def pick(cands, floor: float, k: int = EDGE_K) -> list:
    ok = [c for c in cands if c[1] >= floor]
    ok.sort(key=lambda c: (-c[1], c[0]))
    return ok[:k]


def canonical_pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def edges_from_picks(picks) -> dict:
    out: dict = {}
    for src, lst in picks.items():
        for other, b, t, c in lst:
            key = canonical_pair(src, other)
            e = out.setdefault(key, {"weight": b, "tfidf": t, "cos": c, "picked_by": []})
            if src not in e["picked_by"]:
                e["picked_by"].append(src)
    for e in out.values():
        e["picked_by"].sort()
        e["via"] = "both" if len(e["picked_by"]) == 2 else e["picked_by"][0]
    return out


def _is_active(h: dict) -> bool:
    return (h.get("status") or "active") not in INACTIVE


def rank_adjust(hits: list[dict], query: str, supersedes: dict | None = None) -> list[dict]:
    """Spec §3 steps 2–4, applied to fused hits (already sorted by (-score, name)).

    1. Drop an inactive hit when an active hit on the same topic is present.
    2. Active hits before inactive ones, order within each group preserved.
    3. An active hit whose name equals the query (case-insensitive) moves to the top.
    """
    active = [h for h in hits if _is_active(h)]
    inactive = [
        h for h in hits
        if not _is_active(h) and not any(same_topic(h, a, supersedes) for a in active)
    ]
    ordered = active + inactive
    q = (query or "").strip().lower()
    if q:
        exact = [h for h in active if (h.get("name") or "").strip().lower() == q]
        if exact:
            rest = [h for h in ordered if h not in exact]
            ordered = exact + rest
    return ordered


def apply_vector_only_floor(vec_hits: list[dict], lexical_hits: list[dict]) -> list[dict]:
    """Spec §3 step 5: with no lexical evidence, drop weak vector neighbours."""
    if lexical_hits:
        return vec_hits
    return [h for h in vec_hits if float(h.get("vec_score") or 0.0) >= VECTOR_ONLY_FLOOR]


def _fulltext_leg(
    driver, query: str, index: str, limit: int, bolt_timeout: float | None = None,
    extra_indexes: list[str] | None = None,
    *, assistant: str | None = None, space: str | None = None, trust: str | None = None,
) -> list[dict]:
    extras = [x for x in (extra_indexes or []) if x]
    filt = _leg_filters(assistant, space, trust)
    try:
        with driver.session() as s:
            lists: list[tuple[str, list[dict]]] = []
            try:
                hits = search_fulltext(
                    s, query, index, limit, bolt_timeout=bolt_timeout, **filt,
                )
                if hits:
                    lists.append(("ft", hits))
            except _BOLT_FAIL:
                pass
            for extra in extras:
                try:
                    hits = search_fulltext(
                        s, query, extra, limit, bolt_timeout=bolt_timeout, via="kp", **filt,
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
    *, assistant: str | None = None, space: str | None = None, trust: str | None = None,
) -> tuple[list[dict], bool]:
    emb = ollama_embed(query, cfg, timeout=embed_timeout)
    if not emb:
        return [], False
    filt = _leg_filters(assistant, space, trust)
    try:
        with driver.session() as s:
            return search_vector(
                s, emb, cfg["vector"], limit, bolt_timeout=bolt_timeout, **filt,
            ), True
    except (*_BOLT_FAIL, ValueError):
        return [], False


def _load_supersedes(session, bolt_timeout: float | None = None) -> dict:
    """{new_name: {old_name, ...}} for every SUPERSEDES edge — a multimap, so a keeper
    that supersedes several olds keeps every edge (a plain {new: old} dict would keep
    only the last row). `{}` on any failure — best-effort, like
    ai_memory/search.py::load_supersedes (the name-suffix rule still applies)."""
    out: dict = {}
    try:
        rows = session.run(
            _query(
                "MATCH (n:Fact)-[:SUPERSEDES]->(o:Fact) RETURN n.name AS n, o.name AS o",
                bolt_timeout,
            )
        )
        for r in rows:
            out.setdefault(r["n"], set()).add(r["o"])
    except Exception:  # noqa: BLE001 — collapse is best-effort; name-suffix rule still applies
        return {}
    return out


def _supersedes_leg(driver, bolt_timeout: float | None = None) -> dict:
    """Open its own session and load the SUPERSEDES map. `{}` on any exception, so a
    slow or failing lookup degrades to no collapsing instead of blocking the caller
    or propagating (used both as a daemon future in hybrid mode and inline, bounded
    by bolt_timeout, in the single-leg modes)."""
    try:
        with driver.session() as s:
            return _load_supersedes(s, bolt_timeout)
    except Exception:  # noqa: BLE001 — best-effort, like _load_supersedes
        return {}


def rank_pipeline(
    ft: list[dict],
    vec: list[dict],
    vec_ok: bool,
    query: str,
    supersedes: dict,
    limit: int,
) -> tuple[list[dict], str]:
    """Fuse the whole ft+vec pool (RRF, unsliced), apply the library's ranking
    rules, then slice to `limit`. Backend is "hybrid" when vec_ok, else "fulltext"."""
    vec = apply_vector_only_floor(vec, ft) if vec_ok else []
    # "lex", not "ft": `ft` is the already-fused content+key-points pool, and
    # merge_rrf recomputes `via` from the origin label. Labelling it "ft" would
    # tell a key-points-only hit it came from the content index. Same neutral
    # label as ai_memory/search.py::search_hybrid.
    legs = [(origin, hits) for origin, hits in (("lex", ft), ("vec", vec)) if hits]
    fused = merge_rrf(legs)
    ranked = rank_adjust(fused, query, supersedes)[:limit]
    backend = "hybrid" if vec_ok else "fulltext"
    return ranked, backend


def search_memories(
    driver,
    cfg: dict,
    query: str,
    limit: int,
    mode: str = "hybrid",
    embed_timeout: float | None = None,
    bolt_timeout: float | None = None,
    deadline: float | None = None,
    *, assistant: str | None = None, space: str | None = None, trust: str | None = None,
) -> tuple[list[dict], str]:
    """Return (hits, backend). Hybrid runs fulltext and embed+KNN in parallel."""
    mode = (mode or "hybrid").strip().lower()
    q = (query or "").strip()
    if not q or limit <= 0:
        return [], mode
    pool = _pool_size(limit)
    filt = _leg_filters(assistant, space, trust)

    kp_idx = cfg.get("fulltext_kp")
    extras = [kp_idx] if kp_idx else None

    # Single-leg modes retrieve at pool width and fuse/rank exactly like hybrid,
    # just with the other leg empty (ai_memory/search.py::search_hybrid passes the
    # same `pool` to both legs for every mode); `limit` only slices at the end.
    if mode == "fulltext":
        hits = _fulltext_leg(
            driver, q, cfg["fulltext"], pool, bolt_timeout, extras, **filt,
        )
        supersedes = _supersedes_leg(driver, bolt_timeout)
        ranked, _ = rank_pipeline(hits, [], False, q, supersedes, limit)
        return ranked, "fulltext"

    if mode == "vector":
        vec, vec_ok = _vector_leg(
            driver, q, cfg, pool, embed_timeout, bolt_timeout, **filt,
        )
        if not vec_ok:
            return [], "vector-down"
        supersedes = _supersedes_leg(driver, bolt_timeout)
        ranked, _ = rank_pipeline([], vec, True, q, supersedes, limit)
        return ranked, "vector"

    et = SEARCH_EMBED_TIMEOUT if embed_timeout is None else embed_timeout
    bt = SEARCH_BOLT_TIMEOUT if bolt_timeout is None else bolt_timeout
    budget = et + bt if deadline is None else deadline
    if budget <= 0:
        return [], "fulltext"

    fut_ft = _daemon_submit(
        _fulltext_leg, driver, q, cfg["fulltext"], pool, bolt_timeout, extras, **filt,
    )
    fut_vec = _daemon_submit(
        _vector_leg, driver, q, cfg, pool, embed_timeout, bolt_timeout, **filt,
    )
    fut_sup = _daemon_submit(_supersedes_leg, driver, bolt_timeout)
    wait([fut_ft, fut_vec, fut_sup], timeout=budget)
    ft: list[dict] = []
    vec: list[dict] = []
    vec_ok = False
    supersedes: dict = {}
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
    if fut_sup.done() and not fut_sup.cancelled():
        try:
            supersedes = fut_sup.result(timeout=0)
        except Exception:  # noqa: BLE001 — collapse is best-effort; {} degrades safely
            supersedes = {}
    return rank_pipeline(ft, vec, vec_ok, q, supersedes, limit)


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
            assistant=args.assistant,
            space=args.space,
            trust=args.trust,
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
INACTIVE = (STATUS_SUPERSEDED, STATUS_REMOVED)
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


def _load_edge_config(session) -> dict | None:
    """Verbatim port of ai_memory.wordindex.load_edge_config: the z-score baselines +
    edge floor published alongside RetrievalConfig, or None when the edge layer hasn't
    been built yet (rule_version missing/null)."""
    rec = session.run(
        "MATCH (c:RetrievalConfig {id: $id}) "
        "RETURN c.rule_version AS rule_version, c.edge_floor AS edge_floor, "
        "c.t_mean AS t_mean, c.t_std AS t_std, c.c_mean AS c_mean, c.c_std AS c_std, "
        "c.n_facts AS n_facts",
        id="current",
    ).single()
    if rec is None or rec["rule_version"] is None:
        return None
    return {
        "rule_version": int(rec["rule_version"]),
        "edge_floor": float(rec["edge_floor"]),
        "t_mean": float(rec["t_mean"]),
        "t_std": float(rec["t_std"]),
        "c_mean": float(rec["c_mean"]),
        "c_std": float(rec["c_std"]),
        "n_facts": int(rec["n_facts"]),
    }


def _write_fact_tokens(session, name: str, tokens: list[str], norm: float) -> None:
    """Verbatim port of ai_memory.wordindex.write_fact_tokens: replace one Fact's
    HAS_WORD edges and tfidf_norm."""
    session.run(
        "MATCH (f:Fact {name: $name}) "
        "OPTIONAL MATCH (f)-[old:HAS_WORD]->() "
        "DELETE old "
        "WITH f "
        "SET f.tfidf_norm = $norm "
        "WITH f "
        "UNWIND $tokens AS t "
        "MERGE (w:Word {text: t}) "
        "MERGE (f)-[:HAS_WORD]->(w)",
        name=name, tokens=list(tokens), norm=norm,
    )


def _write_edges(session, edges: dict, rule_version: int, *, batch: int = 500) -> int:
    """Verbatim port of ai_memory.wordindex.write_edges."""
    stmt = (
        "UNWIND $rows AS r "
        "MATCH (a:Fact {name: r.a}), (b:Fact {name: r.b}) "
        "MERGE (a)-[e:RELATED_TO]->(b) "
        "SET e.weight = r.weight, e.tfidf = r.tfidf, e.cos = r.cos, "
        "e.shared_keywords = r.shared, e.picked_by = r.picked_by, e.via = r.via, "
        "e.rule_version = $rv "
        "REMOVE e.shared_count, e.source "
        "RETURN count(e) AS n"
    )
    items = list(edges.items())
    written = 0
    for i in range(0, len(items), batch):
        rows = [
            {"a": pair[0], "b": pair[1], "weight": e["weight"], "tfidf": e["tfidf"], "cos": e["cos"],
             "shared": e.get("shared_keywords", []), "picked_by": e["picked_by"], "via": e["via"]}
            for pair, e in items[i:i + batch]
        ]
        rec = session.run(stmt, rows=rows, rv=rule_version).single()
        written += int(rec["n"]) if rec else 0
    return written


def _write_tokens(session, name: str, prepared_text: str) -> None:
    """Replacement for the old _set_words: tokenize `prepared_text` with the same rule
    the library's nightly rebuild uses (`tokenize`), look up each token's published
    Word.idf, compute tfidf_norm, then replace the Fact's HAS_WORD edges via
    _write_fact_tokens. `n_facts` for the IDF default comes from the published edge
    config when there is one, else a fresh Fact count (this client has no batch vocab
    of its own outside the graph)."""
    tokens = tokenize(prepared_text, name)
    edge_cfg = _load_edge_config(session)
    if edge_cfg is not None:
        n_facts = edge_cfg["n_facts"]
    else:
        rec = session.run("MATCH (f:Fact) RETURN count(f) AS n").single()
        n_facts = int(rec["n"]) if rec else 0
    default_idf = math.log(max(n_facts, 2))
    idf = {
        r["text"]: r["idf"]
        for r in session.run(
            "MATCH (w:Word) WHERE w.text IN $toks RETURN w.text AS text, w.idf AS idf", toks=tokens,
        )
        if r["idf"] is not None
    }
    norm = tfidf_norm(tokens, idf, default_idf)
    _write_fact_tokens(session, name, tokens, norm)


def _maintain_edges_for(session, name: str, edge_cfg: dict, *, k: int = EDGE_K, log=None) -> dict:
    """Verbatim port of ai_memory.wordindex.maintain_edges_for (same statements, same
    parameter names) -- on-write edge maintenance for one Fact `name` ("X"). Uses this
    module's own tokenize/tfidf/blend/is_duplicate/pick copies above and the existing
    _load_supersedes helper instead of an ai_memory.search import. Returns
    {"picked", "repicked", "deleted", "revoked", "skipped"}."""
    empty = {"picked": 0, "repicked": 0, "deleted": 0, "revoked": 0}
    if not edge_cfg:
        return {**empty, "skipped": "no_config"}

    rec = session.run(
        "MATCH (f:Fact {name: $n}) "
        "RETURN f.embedding IS NOT NULL AS has_emb, [(f)-[:HAS_WORD]->(w) | w.text] AS toks, "
        "f.tfidf_norm AS norm",
        n=name,
    ).single()
    if rec is None:
        return {**empty, "skipped": "missing"}
    if not rec["has_emb"]:
        return {**empty, "skipped": "no_embedding"}
    toks_x = list(rec["toks"] or [])

    default_idf = math.log(max(edge_cfg["n_facts"], 2))

    idf1 = {
        r["text"]: r["idf"]
        for r in session.run(
            "MATCH (w:Word) WHERE w.text IN $toks RETURN w.text AS text, w.idf AS idf", toks=toks_x,
        )
        if r["idf"] is not None
    }
    norm_x = tfidf_norm(toks_x, idf1, default_idf)
    _write_fact_tokens(session, name, toks_x, norm_x)

    # vector.similarity.cosine returns the server's *normalised* similarity (1+cos)/2,
    # not raw cosine -- de-normalise it here so `cos` lands on the same axis as the
    # nightly rebuild's raw-cosine baselines (c_mean/c_std) and DUP_COS.
    rows = [dict(r) for r in session.run(
        "MATCH (f:Fact {name: $n}) MATCH (g:Fact) WHERE g <> f AND g.embedding IS NOT NULL "
        "RETURN g.name AS name, 2 * vector.similarity.cosine(f.embedding, g.embedding) - 1 AS cos, "
        "[(g)-[:HAS_WORD]->(w) | w.text] AS toks, g.tfidf_norm AS norm",
        n=name,
    )]
    toks_by_name = {r["name"]: list(r["toks"] or []) for r in rows}

    union_toks = set(toks_x)
    for toks in toks_by_name.values():
        union_toks.update(toks)
    idf2 = {
        r["text"]: r["idf"]
        for r in session.run(
            "MATCH (w:Word) WHERE w.text IN $toks RETURN w.text AS text, w.idf AS idf",
            toks=sorted(union_toks),
        )
        if r["idf"] is not None
    }

    supersedes = _load_supersedes(session)

    cands: list[tuple[str, float, float, float]] = []
    for r in rows:
        g_name = r["name"]
        cos = float(r["cos"]) if r["cos"] is not None else 0.0
        if is_duplicate(name, g_name, cos, supersedes):
            continue
        toks_g = toks_by_name[g_name]
        norm_g = r["norm"]
        norm_g = float(norm_g) if norm_g is not None else tfidf_norm(toks_g, idf2, default_idf)
        t = tfidf_cosine(toks_x, toks_g, idf2, norm_x, norm_g, default_idf)
        b = blend(t, cos, edge_cfg)
        cands.append((g_name, b, t, cos))

    x_picks = pick(cands, edge_cfg["edge_floor"], k)
    ok = [c for c in cands if c[1] >= edge_cfg["edge_floor"]]

    # Which of `ok` already have X among their own picks? Those get a weight-only
    # update below (no eviction, not counted as a re-pick).
    already_picks_x: set[str] = set()
    if ok:
        for r in session.run(
            "UNWIND $names AS g "
            "MATCH (x:Fact {name: $n})-[e:RELATED_TO]-(o:Fact {name: g}) "
            "WHERE g IN coalesce(e.picked_by, []) "
            "RETURN o.name AS g",
            n=name, names=[c[0] for c in ok],
        ):
            already_picks_x.add(r["g"])

    repicked: list[tuple[str, float, float, float]] = []
    weight_updates: list[tuple[str, float, float, float]] = []
    unpicks: list[tuple[str, str]] = []
    for g_name, b, t, c in ok:
        if g_name in already_picks_x:
            weight_updates.append((g_name, b, t, c))
            continue
        current = [
            (r["other"], r["weight"])
            for r in session.run(
                "MATCH (g:Fact {name: $g})-[e:RELATED_TO]-(o:Fact) "
                "WHERE $g IN coalesce(e.picked_by, []) AND o.name <> $n "
                "RETURN o.name AS other, e.weight AS weight ORDER BY e.weight ASC",
                g=g_name, n=name,
            )
        ]
        if len(current) < k:
            repicked.append((g_name, b, t, c))
        elif b > current[0][1]:
            repicked.append((g_name, b, t, c))
            unpicks.append((g_name, current[0][0]))

    edges: dict[tuple[str, str], dict] = {}
    for g_name, b, t, c in x_picks:
        pair = canonical_pair(name, g_name)
        e = edges.setdefault(pair, {
            "weight": b, "tfidf": t, "cos": c, "picked_by": set(),
            "shared_keywords": shared_keywords(toks_x, toks_by_name[g_name], idf2),
        })
        e["picked_by"].add(name)
    for g_name, b, t, c in repicked:
        pair = canonical_pair(name, g_name)
        e = edges.setdefault(pair, {
            "weight": b, "tfidf": t, "cos": c, "picked_by": set(),
            "shared_keywords": shared_keywords(toks_x, toks_by_name[g_name], idf2),
        })
        e["picked_by"].add(g_name)
    for g_name, b, t, c in weight_updates:
        pair = canonical_pair(name, g_name)
        e = edges.setdefault(pair, {
            "weight": b, "tfidf": t, "cos": c, "picked_by": set(),
            "shared_keywords": shared_keywords(toks_x, toks_by_name[g_name], idf2),
        })
        e["weight"], e["tfidf"], e["cos"] = b, t, c
        e["picked_by"].add(g_name)

    if edges:
        pairs_param = [{"a": a, "b": b} for a, b in edges]
        for r in session.run(
            "UNWIND $pairs AS p "
            "MATCH (a:Fact {name: p.a})-[e:RELATED_TO]-(b:Fact {name: p.b}) "
            "RETURN p.a AS a, p.b AS b, coalesce(e.picked_by, []) AS picked_by",
            pairs=pairs_param,
        ):
            pair = (r["a"], r["b"])
            if pair in edges and r["picked_by"]:
                edges[pair]["picked_by"].update(r["picked_by"])
        for e in edges.values():
            e["picked_by"] = sorted(e["picked_by"])
            e["via"] = "both" if len(e["picked_by"]) == 2 else e["picked_by"][0]
        _write_edges(session, edges, edge_cfg["rule_version"])

    deleted = 0
    for g_name, weakest in unpicks:
        r = session.run(
            "MATCH (g:Fact {name: $g})-[e:RELATED_TO]-(o:Fact {name: $weakest}) "
            "SET e.picked_by = [p IN coalesce(e.picked_by, []) WHERE p <> $g] "
            "WITH e, size(e.picked_by) AS remaining "
            "SET e.via = CASE remaining WHEN 2 THEN 'both' WHEN 1 THEN e.picked_by[0] ELSE null END "
            "WITH e, remaining WHERE remaining = 0 "
            "DELETE e "
            "RETURN remaining",
            g=g_name, weakest=weakest,
        ).single()
        if r is not None:
            deleted += 1

    # Revoke X from any edge it previously picked but no longer does.
    keep = [g_name for g_name, *_ in x_picks]
    rrec = session.run(
        "MATCH (x:Fact {name: $n})-[e:RELATED_TO]-(o:Fact) "
        "WHERE $n IN coalesce(e.picked_by, []) AND NOT o.name IN $keep "
        "SET e.picked_by = [p IN coalesce(e.picked_by, []) WHERE p <> $n] "
        "WITH e, size(e.picked_by) AS remaining "
        "SET e.via = CASE remaining WHEN 2 THEN 'both' WHEN 1 THEN e.picked_by[0] ELSE null END "
        "WITH collect(e) AS revoked_edges, collect(CASE WHEN remaining = 0 THEN e END) AS empties "
        "FOREACH (d IN empties | DELETE d) "
        "RETURN size(revoked_edges) AS revoked, size(empties) AS deleted",
        n=name, keep=keep,
    ).single()
    revoked = int(rrec["revoked"]) if rrec else 0
    deleted += int(rrec["deleted"]) if rrec else 0

    result = {"picked": len(x_picks), "repicked": len(repicked), "deleted": deleted,
              "revoked": revoked, "skipped": None}
    if log is not None:
        log(f"maintain_edges_for({name}): {result}")
    return result


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
    status = "no_config"
    try:
        with drv.session() as s:
            existing = s.run(
                "MATCH (f:Fact {name: $name}) RETURN f.assistant AS a, f.space AS space",
                name=name,
            ).single()
            if existing:
                if existing["space"] == SHARED_SPACE:
                    # Shared Facts are never overwritten in place; the plain
                    # write path must not bypass supersede/append/tombstone.
                    print(
                        f"refused: Fact {name!r} is in space=shared; "
                        "use --space shared with --supersede or --append",
                        file=sys.stderr,
                    )
                    return 3
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
            rc = _load_retrieval_config(s)
            boilerplate = rc["boilerplate"] if rc else frozenset()
            row = _read_fact_text(s, name)
            prepared = (
                fact_embed_text(row["name"], row["summary"], row["key_points"], row["content"], boilerplate)
                if row is not None
                else fact_embed_text(name, summary, points, None, boilerplate)
            )
            _write_tokens(s, name, prepared)
            if not args.no_embed:
                try:
                    status = _embed_fact_cas(s, name, cfg, rc, timeout=WRITE_EMBED_TIMEOUT, row=row)
                except _BOLT_FAIL:
                    status = "embed_failed"
                embedded = status == "embedded"
            try:
                edge_cfg = _load_edge_config(s)
                if edge_cfg is not None:
                    _maintain_edges_for(s, name, edge_cfg)
            except Exception as e:  # noqa: BLE001 — edge maintenance must never fail a write
                print(f"edge maintenance failed for {name!r}: {e}", file=sys.stderr)
    except _BOLT_FAIL as e:
        print(f"write failed: {e}", file=sys.stderr)
        return 1
    finally:
        drv.close()
    extra = ""
    if not args.no_embed and not embedded:
        extra = f" (no embedding: {_EMBED_STATUS_MSG.get(status, 'ollama down')})"
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
    status = "no_config"
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
            rc = _load_retrieval_config(s)
            boilerplate = rc["boilerplate"] if rc else frozenset()
            row = _read_fact_text(s, wrote_name)
            prepared = (
                fact_embed_text(row["name"], row["summary"], row["key_points"], row["content"], boilerplate)
                if row is not None
                else fact_embed_text(wrote_name, summary, points, None, boilerplate)
            )
            _write_tokens(s, wrote_name, prepared)
            if not args.no_embed:
                try:
                    status = _embed_fact_cas(s, wrote_name, cfg, rc, timeout=WRITE_EMBED_TIMEOUT, row=row)
                except _BOLT_FAIL:
                    status = "embed_failed"
                embedded = status == "embedded"
            try:
                edge_cfg = _load_edge_config(s)
                if edge_cfg is not None:
                    _maintain_edges_for(s, wrote_name, edge_cfg)
            except Exception as e:  # noqa: BLE001 — edge maintenance must never fail a write
                print(f"edge maintenance failed for {wrote_name!r}: {e}", file=sys.stderr)
    except _BOLT_FAIL as e:
        print(f"write failed: {e}", file=sys.stderr)
        return 1
    finally:
        drv.close()
    extra = ""
    if not args.no_embed and not embedded:
        extra = f" (no embedding: {_EMBED_STATUS_MSG.get(status, 'ollama down')})"
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
    """Run the on-write edge-maintenance rule (_maintain_edges_for, a verbatim port of
    ai_memory.wordindex.maintain_edges_for) over every one of this mind's Facts. Needs
    a published edge rule (a RetrievalConfig with rule_version set) -- run the
    library's `ai-memory nightly` first. Does not touch other minds' edges directly,
    though a shared RELATED_TO edge may gain/lose this mind from its picked_by."""
    blocked = _require_grok(args)
    if blocked is not None:
        return blocked
    assistant = (args.assistant or ASSISTANT).strip()
    drv, _ = _driver()
    names: list[str] = []
    picked = repicked = revoked = deleted = 0
    try:
        with drv.session() as s:
            edge_cfg = _load_edge_config(s)
            if edge_cfg is None:
                print(
                    "organize needs a published edge rule (run the library's ai-memory nightly first)",
                    file=sys.stderr,
                )
                return 1
            names = [
                r["name"]
                for r in s.run(
                    "MATCH (f:Fact {assistant: $a}) RETURN f.name AS name ORDER BY name",
                    a=assistant,
                )
            ]
            for name in names:
                result = _maintain_edges_for(s, name, edge_cfg)
                picked += result["picked"]
                repicked += result["repicked"]
                revoked += result["revoked"]
                deleted += result["deleted"]
    except _BOLT_FAIL as e:
        print(f"organize failed: {e}", file=sys.stderr)
        return 1
    finally:
        drv.close()
    print(f"maintained {len(names)} facts: picked {picked}, repicked {repicked}, revoked {revoked}, deleted {deleted}")
    return 0


def cmd_embed(args: argparse.Namespace) -> int:
    """Backfill Fact.embedding for nodes missing a vector or carrying a foreign one (no
    embedding_text_sha). Indexing only — does not rewrite content."""
    drv, cfg = _driver()
    try:
        with drv.session() as s:
            rows = s.run(
                """
                MATCH (f:Fact)
                WHERE f.embedding IS NULL OR f.embedding_text_sha IS NULL
                RETURN f.name AS name,
                       coalesce(f.assistant,'(none)') AS assistant
                ORDER BY f.name
                """
            ).data()
            rc = _load_retrieval_config(s)
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
    if rc is None:
        drv.close()
        print(
            "embed aborted: no RetrievalConfig — run ai-memory embed --all from the library first",
            file=sys.stderr,
        )
        return 1

    if not ollama_embed("probe", cfg, timeout=min(WRITE_EMBED_TIMEOUT, 5.0)):
        drv.close()
        print("embed aborted: ollama nomic-embed-text unavailable", file=sys.stderr)
        return 1

    ok = 0
    cas_skipped = 0
    fail = 0
    skipped = 0
    consecutive = 0
    aborted = False
    try:
        with drv.session() as s:
            for i, r in enumerate(rows, 1):
                try:
                    status = _embed_fact_cas(s, r["name"], cfg, rc, timeout=WRITE_EMBED_TIMEOUT)
                except _BOLT_FAIL:
                    status = "embed_failed"
                if status == "embedded":
                    ok += 1
                    consecutive = 0
                elif status == "cas_skipped":
                    cas_skipped += 1
                    consecutive = 0
                elif status in ("missing", "empty_text"):
                    skipped += 1
                    consecutive = 0
                else:
                    fail += 1
                    consecutive += 1
                    if consecutive >= EMBED_FAIL_ABORT:
                        aborted = True
                        print(
                            f"embed aborted after {consecutive} consecutive failures",
                            file=sys.stderr,
                        )
                        break
                if i % 20 == 0 or i == len(rows):
                    print(
                        f"embedded {ok}/{len(rows)} (cas_skipped {cas_skipped} fail {fail} skip {skipped})",
                        flush=True,
                    )
    finally:
        drv.close()
    queued = total - ok - cas_skipped - fail - skipped
    extra = " aborted" if aborted else ""
    print(
        f"done {ok} embedded, {cas_skipped} cas_skipped, {fail} failed, {skipped} skipped, "
        f"{queued} still queued{extra}"
    )
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
    s.add_argument("--assistant", default=None, help="filter: exact f.assistant match")
    s.add_argument("--space", default=None, help="filter: exact f.space match")
    s.add_argument("--trust", default=None, help="filter: exact f.provenance_trust match")
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

    s = sub.add_parser(
        "organize",
        help="Run the published edge rule's on-write maintenance over one mind's facts",
    )
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
