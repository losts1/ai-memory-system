"""Canonical embedding text, boilerplate removal and provenance-carrying embed writes (spec §4, §7.2).

Pure helpers (no I/O): normalize_ws, fact_embed_text, gram_tokens, detect_boilerplate,
strip_boilerplate, text_sha. I/O helpers (embed_text, embed_fact, embed_all, ...) are added
in later tasks of the phase-2 plan.
"""
from __future__ import annotations

import hashlib
import math
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Sequence

from ai_memory.retrieval_config import (
    RetrievalConfig,
    load_retrieval_config,
    publish_retrieval_config,
)

EMBED_MODEL = "nomic-embed-text"
EMBED_DIM = 768
EMBED_CHARS = 2000
EMBED_TIMEOUT_S = 30.0
_TOKEN = re.compile(r"[A-Za-z0-9]+")


def normalize_ws(s: str) -> str:
    return " ".join((s or "").split())


def gram_tokens(text: str) -> list[str]:
    return [m.group(0).lower() for m in _TOKEN.finditer(text or "")]


def _grams_of(tokens: list[str]) -> set:
    return {" ".join(tokens[i:i + 4]) for i in range(len(tokens) - 3)}


def detect_boilerplate(texts: Iterable[str], *, min_abs: int = 10, min_ratio: float = 0.01) -> frozenset[str]:
    """Token 4-grams present in >= max(min_abs, ceil(min_ratio * N)) texts."""
    texts = list(texts)
    n = len(texts)
    threshold = max(min_abs, math.ceil(min_ratio * n))
    df: Counter = Counter()
    for t in texts:
        df.update(_grams_of(gram_tokens(t)))
    return frozenset(g for g, c in df.items() if c >= threshold)


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


# --- I/O helpers -------------------------------------------------------------

EMBED_PARAM_NAMES = ("embedding", "embedding_model", "embedding_dim", "embedding_text_sha", "boilerplate_version")
_CAS_EMPTY = {"summary": "''", "key_points": "[]", "content": "''"}


def _cas_default(field: str):
    """A fresh empty value for `field` — a function, not a shared dict entry, so a mutable
    default (the `key_points` list) can never be aliased across calls."""
    return [] if field == "key_points" else ""


def embed_text(text: str, *, model: str = EMBED_MODEL) -> list | None:
    """Local Ollama embedding of the prepared text; None when unavailable or wrong length.
    Bounded by EMBED_TIMEOUT_S so a stalled Ollama server cannot block any caller forever
    (a caller that holds this behind a Neo4j write transaction is a bug, not a reason to
    remove the bound — see _prepare_embed in ai_memory/learn.py)."""
    try:
        import ollama
        vec = ollama.Client(timeout=EMBED_TIMEOUT_S).embeddings(model=model, prompt=text)["embedding"]
        if not isinstance(vec, list) or len(vec) != EMBED_DIM:
            print(f"Embedding rejected: expected {EMBED_DIM} dims, got {len(vec) if isinstance(vec, list) else type(vec).__name__}", file=sys.stderr)
            return None
        return [float(x) for x in vec]
    except Exception as e:  # noqa: BLE001 — Ollama is an optional dependency
        print(f"Embedding error (Ollama unreachable?): {e}", file=sys.stderr)
        return None


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


def read_fact_text(session, name: str) -> dict | None:
    rec = session.run(
        "MATCH (f:Fact {name: $name}) RETURN f.name AS name, f.summary AS summary, "
        "f.key_points AS key_points, f.content AS content", name=name).single()
    if rec is None:
        return None
    return {"name": rec["name"], "summary": rec["summary"], "key_points": rec["key_points"], "content": rec["content"]}


def embed_fact(session, name: str, cfg: RetrievalConfig, embed_fn: Callable[[str], list | None] = embed_text,
               *, keep_prev: bool = False) -> str:
    """Read → prepare → embed → CAS write (all three text fields). Returns embedded|cas_skipped|embed_failed|missing."""
    row = read_fact_text(session, name)
    if row is None:
        return "missing"
    text = fact_embed_text(row["name"], row["summary"], row["key_points"], row["content"], cfg.boilerplate)
    if not text.strip():
        return "embed_failed"
    vec = embed_fn(text)
    if not vec:
        return "embed_failed"
    cypher = "MATCH (f:Fact {name: $name})\n" + build_embed_subquery(["summary", "key_points", "content"], keep_prev=keep_prev) + "\nRETURN embedded"
    params = embed_params(vec, text_sha(text, cfg.version), cfg.version,
                          cas={"summary": row["summary"], "key_points": row["key_points"], "content": row["content"]})
    params["name"] = name
    rec = session.run(cypher, **params).single()
    return "embedded" if rec and rec["embedded"] else "cas_skipped"


_ALL_FACTS = ("MATCH (f:Fact) RETURN f.name AS name, f.summary AS summary, f.key_points AS key_points, "
              "f.content AS content, f.embedding_text_sha AS embedding_text_sha, f.boilerplate_version AS boilerplate_version "
              "ORDER BY f.name")


def embed_all(driver, *, keep_prev: bool = False, publish: bool = True, stale_only: bool = False,
              embed_fn: Callable[[str], list | None] = embed_text, log=print) -> dict:
    """Backfill every Fact: (optionally) recompute boilerplate and publish a new RetrievalConfig
    version, then prepare/embed/CAS-write each Fact (spec §4 Backfill, §7.5 order)."""
    if stale_only and publish:
        raise ValueError(
            "stale_only requires publish=False: publishing a new RetrievalConfig version makes every Fact stale"
        )
    out = {"facts": 0, "embedded": 0, "cas_skipped": 0, "embed_failed": 0, "missing": 0, "skipped_fresh": 0,
           "config_version": None, "grams": 0, "cas_skipped_names": []}
    with driver.session() as s:
        rows = [dict(r) for r in s.run(_ALL_FACTS)]
        out["facts"] = len(rows)
        if publish:
            raw = [fact_embed_text(r["name"], r["summary"], r["key_points"], r["content"], ()) for r in rows]
            grams = detect_boilerplate(raw)
            cfg = publish_retrieval_config(s, grams)
            log(f"published RetrievalConfig version {cfg.version} with {len(grams)} boilerplate grams")
        else:
            cfg = load_retrieval_config(s)
            if cfg is None:
                raise RuntimeError("no RetrievalConfig node; run scripts/neo4j_seed.py or ai-memory embed --all (publish)")
        out["config_version"] = cfg.version
        out["grams"] = len(cfg.boilerplate)
        for i, r in enumerate(rows, 1):
            if stale_only and r.get("boilerplate_version") == cfg.version:
                text = fact_embed_text(r["name"], r["summary"], r["key_points"], r["content"], cfg.boilerplate)
                if r.get("embedding_text_sha") == text_sha(text, cfg.version):
                    out["skipped_fresh"] += 1
                    continue
            status = embed_fact(s, r["name"], cfg, embed_fn, keep_prev=keep_prev)
            out[status] += 1
            if status == "cas_skipped" and len(out["cas_skipped_names"]) < 20:
                out["cas_skipped_names"].append(r["name"])
            if i % 100 == 0:
                log(f"  {i}/{len(rows)} … embedded={out['embedded']} cas_skipped={out['cas_skipped']} failed={out['embed_failed']}")
    return out


def drop_prev(driver) -> int:
    with driver.session() as s:
        rec = s.run("MATCH (f:Fact) WHERE f.embedding_prev IS NOT NULL REMOVE f.embedding_prev RETURN count(f) AS n").single()
        return int(rec["n"]) if rec else 0


def rollback_prev(driver) -> int:
    """Restore embedding_prev and clear provenance — but only on Facts that had a previous
    vector. A Fact first embedded in this run has no embedding_prev, so it keeps its new
    vector and provenance after a rollback (there is nothing to restore it to)."""
    with driver.session() as s:
        rec = s.run(
            "MATCH (f:Fact) WHERE f.embedding_prev IS NOT NULL "
            "SET f.embedding = f.embedding_prev "
            "REMOVE f.embedding_prev, f.embedding_model, f.embedding_dim, f.embedding_text_sha, f.boilerplate_version "
            "RETURN count(f) AS n").single()
        return int(rec["n"]) if rec else 0


_STATS_ROWS = ("MATCH (f:Fact) RETURN f.name AS name, f.summary AS summary, f.key_points AS key_points, f.content AS content, "
               "f.embedding IS NOT NULL AS has_emb, f.embedding_model AS model, f.embedding_text_sha AS sha, "
               "f.embedding_prev IS NOT NULL AS has_prev")


def vector_stats(driver) -> dict:
    from ai_memory.wordindex import edge_stats

    st = {"facts": 0, "with_embedding": 0, "without_embedding": 0, "foreign": 0, "wrong_model": 0, "stale": 0,
          "with_prev": 0, "config_version": None}
    with driver.session() as s:
        cfg = load_retrieval_config(s)
        st["config_version"] = cfg.version if cfg else None
        if cfg is None:
            st["stale"] = None
        for r in s.run(_STATS_ROWS):
            st["facts"] += 1
            st["with_prev"] += bool(r["has_prev"])
            if not r["has_emb"]:
                st["without_embedding"] += 1
                continue
            st["with_embedding"] += 1
            if r["sha"] is None:
                st["foreign"] += 1
                continue
            if r["model"] != EMBED_MODEL:
                st["wrong_model"] += 1
            if cfg is not None:
                text = fact_embed_text(r["name"], r["summary"], r["key_points"], r["content"], cfg.boilerplate)
                if r["sha"] != text_sha(text, cfg.version):
                    st["stale"] += 1
        # Edge-layer health (spec §7.5 / phase 5): isolated Facts, degree drift, rule
        # currency. `edge_stats` is the single source of truth for "isolated" (it's
        # degree-based); the old per-row RELATED_TO existence check above was a
        # separate query computing the same fact, so it was dropped in favor of this.
        edge_st = edge_stats(s)
        st["edges"] = edge_st["edges"]
        st["edges_current_rule"] = edge_st["edges_current_rule"]
        st["edges_stale_rule"] = edge_st["edges_stale_rule"]
        st["rule_version"] = edge_st["rule_version"]
        st["isolated"] = edge_st["isolated"]
        st["isolated_pct"] = edge_st["isolated_pct"]
        st["max_degree"] = edge_st["max_degree"]
        st["p95_degree"] = edge_st["p95_degree"]
    return st
