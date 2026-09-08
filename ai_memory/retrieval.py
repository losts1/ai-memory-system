"""Pure retrieval helpers: fusion, ranking rules, and Cypher builders.

No I/O and no driver here — everything is a unit test. Implemented twice
(this module and the grok client's neo4j_memory.py); tests/test_retrieval_contract.py
(phase 4) asserts the two agree on fixtures.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Set, Tuple

RRF_K = 60
VECTOR_ONLY_FLOOR = 0.80
INACTIVE = ("superseded", "removed")

# Trailing time/date suffixes only: "(15:30 EDT)", "(2026-03-10)", "— 2026-08-30", "— 2026-08-30 #2".
_TRAILING_SUFFIX = re.compile(
    r"(\s*\([^()]*?(?:\d{1,2}:\d{2}|\d{4}-\d{2}-\d{2}|UTC|EDT|EST|\bET\b)\s*\)"
    r"|\s*[—-]\s*\d{4}-\d{2}-\d{2}(?:\s*#\d+)?)\s*$"
)


def pool_size(k: int) -> int:
    return max(4 * k, 16)


def fallback_pool(k: int) -> int:
    return min(50 * k, 2000)


def fuse_rrf(legs: List[Tuple[str, List[dict]]], k: int = RRF_K) -> List[dict]:
    """Reciprocal-rank fusion. ``legs`` is [(origin, ranked_hits), ...]."""
    scores: Dict[str, float] = {}
    meta: Dict[str, dict] = {}
    origins: Dict[str, set] = {}
    for origin, hits in legs:
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
            if len(h.get("teaser") or "") > len(merged.get("teaser") or ""):
                merged["teaser"] = h["teaser"]
            hk, pk = h.get("key_points") or [], merged.get("key_points") or []
            if hk and (not pk or len(hk) > len(pk)):
                merged["key_points"] = hk
            meta[name] = merged
    out = []
    for name, sc in sorted(scores.items(), key=lambda x: (-x[1], x[0])):
        h = dict(meta[name])
        h["score"] = round(sc, 6)
        h["via"] = "+".join(sorted(origins[name]))
        out.append(h)
    return out


def strip_time_suffix(name: str) -> str:
    return _TRAILING_SUFFIX.sub("", name or "").strip()


def as_supersedes_multimap(supersedes) -> Dict[str, Set[str]]:
    """Normalise a SUPERSEDES map to the multimap shape ``{new: {old, ...}}``.

    ``load_supersedes``/``load_supersedes_strict`` return that shape so a keeper
    superseding several olds keeps every edge. A legacy ``{new: old}`` mapping
    (a single string value) is still accepted and widened to a one-element set.
    """
    out: Dict[str, Set[str]] = {}
    for new, olds in (supersedes or {}).items():
        bucket = out.setdefault(new, set())
        if isinstance(olds, str):
            bucket.add(olds)
        else:
            bucket.update(olds)
    return out


def _chain(name: str, supersedes) -> set:
    """All names reachable from ``name`` along SUPERSEDES in either direction.

    ``supersedes`` is the ``{new: {old, ...}}`` multimap (a legacy ``{new: old}``
    mapping is accepted too — see ``as_supersedes_multimap``)."""
    fwd = as_supersedes_multimap(supersedes)
    inv: Dict[str, List[str]] = {}
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


def same_topic(a: dict, b: dict, supersedes: Optional[Dict[str, Set[str]]] = None) -> bool:
    """Spec §3: connected by a SUPERSEDES chain, or same name once the trailing
    time/date suffix is stripped (compared case-insensitively, like the
    exact-name boost in ``rank_adjust``)."""
    an, bn = a.get("name") or "", b.get("name") or ""
    if an == bn:
        return True
    if strip_time_suffix(an).casefold() == strip_time_suffix(bn).casefold():
        return True
    if supersedes and bn in _chain(an, supersedes):
        return True
    return False


def _is_active(h: dict) -> bool:
    return (h.get("status") or "active") not in INACTIVE


def rank_adjust(hits: List[dict], query: str, supersedes: Optional[Dict[str, Set[str]]] = None) -> List[dict]:
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


def apply_vector_only_floor(vec_hits: List[dict], lexical_hits: List[dict]) -> List[dict]:
    """Spec §3 step 5: with no lexical evidence, drop weak vector neighbours."""
    if lexical_hits:
        return vec_hits
    return [h for h in vec_hits if float(h.get("vec_score") or 0.0) >= VECTOR_ONLY_FLOOR]


RETURN_FIELDS = (
    "f.name AS name, coalesce(f.summary, f.content) AS text, f.key_points AS key_points, "
    "f.assistant AS assistant, f.status AS status, f.space AS space, s"
)


def build_filters(assistant: Optional[str], space: Optional[str], trust: Optional[str]) -> Tuple[str, Dict[str, str]]:
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


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_index_name(name: str) -> str:
    """Vector index names are inlined into the SEARCH clause (Neo4j rejects a parameter there),
    so only plain identifiers are accepted."""
    if not isinstance(name, str) or not _IDENT.match(name):
        raise ValueError(f"invalid vector index name {name!r}: expected [A-Za-z_][A-Za-z0-9_]*")
    return name


def build_search_cypher(where: str, index: str) -> str:
    name = validate_index_name(index)
    inner = f"WHERE {where} " if where else ""
    return (
        "CYPHER 25\n"
        "MATCH (f:Fact)\n"
        f"SEARCH f IN (VECTOR INDEX `{name}` FOR $vec {inner}LIMIT $pool) SCORE AS s\n"
        f"RETURN {RETURN_FIELDS}\n"
        "ORDER BY s DESC"
    )


def build_fallback_cypher(where: str) -> str:
    post = f"WHERE {where}\n" if where else ""
    return (
        "CALL db.index.vector.queryNodes($index, $pool2, $vec) YIELD node AS f, score AS s\n"
        f"{post}"
        f"RETURN {RETURN_FIELDS}\n"
        "ORDER BY s DESC\n"
        "LIMIT $pool"
    )


def build_fulltext_cypher(where: str) -> str:
    post = f"WHERE {where}\n" if where else ""
    return (
        "CALL db.index.fulltext.queryNodes($index, $q) YIELD node AS f, score AS s\n"
        f"{post}"
        f"RETURN {RETURN_FIELDS}\n"
        "ORDER BY s DESC\n"
        "LIMIT $pool"
    )
