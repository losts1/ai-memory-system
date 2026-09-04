# Retrieval Redesign — Phases 0–1 (Harness + Library Retrieval Path) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `ai_memory` a retrieval path that filters inside the vector index when the server allows it and over-fetches when it does not, ranks by the agreed contract, and an offline harness that proves it against a golden set and the calibrated judge.

**Architecture:** Pure ranking and Cypher-building functions live in a new `ai_memory/retrieval.py` with no I/O. `ai_memory/search.py` keeps its public signatures and swaps the body of `search_vector` for the `CYPHER 25 SEARCH` path with a latched over-fetch fallback, adds the `fact_key_points` lexical leg, and gains `search_hybrid`. `ai_memory/eval/harness.py` runs named rankers over a golden file, pools their top-10 for the existing judge, and applies `passes_ship_gate`. The CLI and the standalone search script pass the new inputs through unchanged in shape.

**Tech Stack:** Python ≥ 3.9 (no `X | None` at runtime, no `list[str]` annotations without `from __future__ import annotations`), neo4j-python ≥ 5 (`neo4j.Query`), pytest, ruff, stdlib `urllib` for the judge HTTP call. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-04-retrieval-index-design.md` — this plan implements §3, §5 (library rows only), §8 harness, and phase 0/1 of §9. Phases 2–6 are separate plans.

## Global Constraints

- `requires-python = ">=3.9"`: every new module starts with `from __future__ import annotations`; never use `X | None` or PEP 585 generics in signatures evaluated at runtime otherwise (the CLI already broke on 3.9 once — see CHANGELOG).
- Every Cypher 25 statement is prefixed with the literal line `CYPHER 25` (§3).
- All Neo4j queries in `ai_memory/search.py` run as `neo4j.Query(text, timeout=get_query_timeout())`, never `session.run(text, timeout=...)` (§3 Timeout).
- In-index filters are equality only, `AND`-joined, built only from inputs that are set; `status` is never filtered in-index (§3).
- `pool = max(4k, 16)`; fallback `pool2 = min(50k, 2000)`; both paths return up to `pool` survivors to fusion (§3).
- Latch the fallback flag only on GQL `22ND3` (`Neo.ClientError.Statement.PropertyNotFound` mentioning "additional property") or a `SyntaxError` on a statement containing `SEARCH`; never on other errors. Re-probe after 600 s or when `NEO4J_VECTOR_INDEX` changes (§3).
- Index-not-found or populating on the vector leg makes that call return `[]` with one warning and no latch (§3).
- Ranking order: RRF k=60 → sink superseded/removed with same-topic collapse → exact-name boost among active hits only → ties by name (§3).
- Vector-only cosine floor `0.80` when the lexical leg is empty (§3).
- Hit dict keys, both implementations: `name, teaser, key_points, assistant, status, space, score, via` (§3).
- Golden file lives outside the repo; path from env `AI_MEMORY_GOLDEN` or `--golden` (§8). Never commit it.
- Ship rule: `passes_ship_gate(before, after)` on golden and judged splits; an unjudged golden query fails the gate (§8).
- Tests are offline and mocked; run with `venv/bin/python -m pytest tests -q -p no:cacheprovider` from the repo root; ruff with `ruff check ai_memory tests scripts`.
- Commit after every task with the repo's attribution trailers.

## File Structure

| file | responsibility |
|---|---|
| `ai_memory/retrieval.py` (create) | pure functions: pool sizes, RRF fusion, same-topic predicate, rank adjustment, vector-only floor, filter/Cypher builders |
| `ai_memory/search.py` (modify) | `search_vector` body → SEARCH + fallback latch; `search_graph` + `fact_key_points` leg + `space`; new `search_hybrid`; module logger |
| `ai_memory/__init__.py` (modify) | `MemoryClient.search` delegates to `search_hybrid`; `space`, `mode` params |
| `ai_memory/_config.py` (modify) | `EXPECTED_FULLTEXT_KP`; `validate_schema` checks it |
| `ai_memory/learn.py` (modify) | `_sync_fact_tx` re-raises `TransientError` |
| `ai_memory/eval/harness.py` (create) | golden loader, ranker registry, pooled judging, metrics, gate, labelling skeleton, CLI entry |
| `scripts/neo4j_seed.py` (modify) | create `fact_key_points` |
| `scripts/verify_schema.py` (modify) | expect `fact_key_points` |
| `scripts/cli.py` (modify) | `search --space --mode`; new `eval` subcommand |
| `scripts/hybrid_memory_search.py` (modify) | `--space --mode`; call `search_hybrid` |
| `MIGRATION.md`, `CHANGELOG.md` (modify) | `graph=True` meaning change; Unreleased entries |
| `tests/test_retrieval.py` (create), `tests/test_search_path.py` (create), `tests/test_harness.py` (create); `tests/test_library.py`, `tests/test_verify_schema.py`, `tests/test_cli_smoke.py` (modify) | |

---

### Task 1: retrieval.py — pool sizes, RRF fusion, same-topic predicate

**Files:**
- Create: `ai_memory/retrieval.py`
- Test: `tests/test_retrieval.py`

**Interfaces:**
- Produces:
  - `RRF_K: int = 60`, `VECTOR_ONLY_FLOOR: float = 0.80`
  - `pool_size(k: int) -> int` = `max(4 * k, 16)`
  - `fallback_pool(k: int) -> int` = `min(50 * k, 2000)`
  - `fuse_rrf(legs: List[Tuple[str, List[dict]]], k: int = RRF_K) -> List[dict]` — each hit dict has at least `name`; output sorted by `(-score, name)`, `score` = summed reciprocal ranks rounded to 6 dp, `via` = `"+".join(sorted(origins))`, longest `teaser` and longest `key_points` kept.
  - `strip_time_suffix(name: str) -> str`
  - `same_topic(a: dict, b: dict, supersedes: Optional[Dict[str, str]] = None) -> bool`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_retrieval.py
import pytest

from ai_memory.retrieval import (
    RRF_K, VECTOR_ONLY_FLOOR, fallback_pool, fuse_rrf, pool_size,
    same_topic, strip_time_suffix,
)


def test_constants():
    assert RRF_K == 60
    assert VECTOR_ONLY_FLOOR == 0.80


def test_pool_size_widens_small_k_and_scales():
    assert pool_size(1) == 16
    assert pool_size(5) == 20
    assert pool_size(10) == 40


def test_fallback_pool_caps_at_2000():
    assert fallback_pool(5) == 250
    assert fallback_pool(100) == 2000


def test_fuse_rrf_overlap_ranks_first_and_tags_via():
    ft = [{"name": "A", "teaser": "ft-a", "key_points": []}, {"name": "B", "teaser": "b", "key_points": []}]
    vec = [{"name": "C", "teaser": "c", "key_points": []}, {"name": "A", "teaser": "vec-a-longer", "key_points": ["k"]}]
    out = fuse_rrf([("ft", ft), ("vec", vec)])
    assert [h["name"] for h in out] == ["A", "B", "C"]   # A: 1/61+1/62 ; B,C: 1/61 tie -> name
    assert out[0]["via"] == "ft+vec"
    assert out[0]["teaser"] == "vec-a-longer"
    assert out[0]["key_points"] == ["k"]
    assert out[0]["score"] == pytest.approx(1 / 61 + 1 / 62, abs=1e-6)


def test_fuse_rrf_ties_break_by_name_only():
    ft = [{"name": "Zed"}, {"name": "Alpha"}]
    vec = [{"name": "Alpha"}, {"name": "Zed"}]
    out = fuse_rrf([("ft", ft), ("vec", vec)])
    assert [h["name"] for h in out] == ["Alpha", "Zed"]


def test_fuse_rrf_skips_nameless_and_handles_empty():
    assert fuse_rrf([]) == []
    assert fuse_rrf([("ft", [{"teaser": "no name"}])]) == []


@pytest.mark.parametrize("name,base", [
    ("Kelly Criterion & Position Sizing (15:30 EDT)", "Kelly Criterion & Position Sizing"),
    ("Hawkes Processes for Order Flow (2026-03-10)", "Hawkes Processes for Order Flow"),
    ("Shared — ntr: bitchat — 2026-08-30", "Shared — ntr: bitchat"),
    ("Shared — Foo — 2026-08-30 #2", "Shared — Foo"),
    ("Grok — memory is sacred 2026-08-29", "Grok — memory is sacred 2026-08-29"),  # no suffix shape -> unchanged
    ("Plain Name", "Plain Name"),
])
def test_strip_time_suffix_only_strips_trailing_suffixes(name, base):
    assert strip_time_suffix(name) == base


def test_same_topic_by_name_group_and_supersedes_chain():
    a = {"name": "Kelly Criterion & Position Sizing"}
    b = {"name": "Kelly Criterion & Position Sizing (15:30 EDT)"}
    c = {"name": "Unrelated"}
    assert same_topic(a, b)
    assert not same_topic(a, c)
    # SUPERSEDES: new -> old, chain of two
    sup = {"New v3": "New v2", "New v2": "Old v1"}
    assert same_topic({"name": "New v3"}, {"name": "Old v1"}, supersedes=sup)
    assert same_topic({"name": "Old v1"}, {"name": "New v3"}, supersedes=sup)
    assert not same_topic({"name": "New v3"}, {"name": "Unrelated"}, supersedes=sup)
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/python -m pytest tests/test_retrieval.py -q -p no:cacheprovider`
Expected: collection error `ModuleNotFoundError: No module named 'ai_memory.retrieval'`

- [ ] **Step 3: Implement**

```python
# ai_memory/retrieval.py
"""Pure retrieval helpers: fusion, ranking rules, and Cypher builders.

No I/O and no driver here — everything is a unit test. Implemented twice
(this module and the grok client's neo4j_memory.py); tests/test_retrieval_contract.py
(phase 4) asserts the two agree on fixtures.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

RRF_K = 60
VECTOR_ONLY_FLOOR = 0.80
INACTIVE = ("superseded", "removed")

# Trailing time/date suffixes only: "(15:30 EDT)", "(2026-03-10)", "— 2026-08-30", "— 2026-08-30 #2".
_TRAILING_SUFFIX = re.compile(
    r"(\s*\((?=[^()]*(?:\d{1,2}:\d{2}|\d{4}-\d{2}-\d{2}|UTC|EDT|EST|\bET\b))[^()]*\)"
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


def _chain(name: str, supersedes: Dict[str, str]) -> set:
    """All names reachable from ``name`` along SUPERSEDES in either direction."""
    inv: Dict[str, List[str]] = {}
    for new, old in supersedes.items():
        inv.setdefault(old, []).append(new)
    seen, todo = set(), [name]
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen.add(n)
        if n in supersedes:
            todo.append(supersedes[n])
        todo.extend(inv.get(n, []))
    return seen


def same_topic(a: dict, b: dict, supersedes: Optional[Dict[str, str]] = None) -> bool:
    """Spec §3: connected by a SUPERSEDES chain, or same name once the trailing
    time/date suffix is stripped."""
    an, bn = a.get("name") or "", b.get("name") or ""
    if an == bn:
        return True
    if strip_time_suffix(an).lower() == strip_time_suffix(bn).lower():
        return True
    if supersedes and bn in _chain(an, supersedes):
        return True
    return False
```

- [ ] **Step 4: Run to verify pass**

Run: `venv/bin/python -m pytest tests/test_retrieval.py -q -p no:cacheprovider && /home/lost/.local/bin/ruff check ai_memory/retrieval.py tests/test_retrieval.py`
Expected: all pass; ruff clean.

- [ ] **Step 5: Commit**

```bash
git add ai_memory/retrieval.py tests/test_retrieval.py
git commit -m "feat(retrieval): pool sizes, RRF fusion, same-topic predicate (spec §3)"
```

---

### Task 2: retrieval.py — rank adjustment and vector-only floor

**Files:**
- Modify: `ai_memory/retrieval.py`
- Test: `tests/test_retrieval.py`

**Interfaces:**
- Produces:
  - `rank_adjust(hits: List[dict], query: str, supersedes: Optional[Dict[str, str]] = None) -> List[dict]` — input is fused output (has `score`); applies sink+collapse, then active-only exact-name boost; stable otherwise.
  - `apply_vector_only_floor(vec_hits: List[dict], lexical_hits: List[dict]) -> List[dict]` — when `lexical_hits` is empty, drops vector hits whose `vec_score < VECTOR_ONLY_FLOOR`; otherwise returns `vec_hits` unchanged. Vector hits carry `vec_score` (raw cosine).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_retrieval.py
from ai_memory.retrieval import apply_vector_only_floor, rank_adjust


def _h(name, score, status=None):
    return {"name": name, "score": score, "status": status, "teaser": "", "key_points": [], "via": "ft"}


def test_rank_adjust_sinks_inactive_below_active():
    hits = [_h("old", 0.9, "superseded"), _h("new", 0.5), _h("gone", 0.4, "removed"), _h("other", 0.3)]
    out = rank_adjust(hits, query="nothing matches")
    assert [h["name"] for h in out] == ["new", "other", "old", "gone"]


def test_rank_adjust_collapses_inactive_sibling_of_active_hit():
    hits = [_h("Kelly Criterion (15:30 EDT)", 0.9, "superseded"), _h("Kelly Criterion", 0.5), _h("Other", 0.3)]
    out = rank_adjust(hits, query="x")
    assert [h["name"] for h in out] == ["Kelly Criterion", "Other"]


def test_rank_adjust_keeps_inactive_without_active_sibling():
    hits = [_h("Lonely (15:30 EDT)", 0.9, "superseded"), _h("Other", 0.3)]
    out = rank_adjust(hits, query="x")
    assert [h["name"] for h in out] == ["Other", "Lonely (15:30 EDT)"]


def test_rank_adjust_exact_name_boost_is_active_only():
    hits = [_h("B", 0.9), _h("reserve guard", 0.2), _h("Reserve Guard", 0.1, "superseded")]
    out = rank_adjust(hits, query="Reserve Guard")
    # active exact match to the top; the superseded exact match stays sunk
    assert [h["name"] for h in out] == ["reserve guard", "B", "Reserve Guard"]


def test_rank_adjust_uses_supersedes_chain_for_collapse():
    hits = [_h("Old v1", 0.9, "superseded"), _h("New v3", 0.5)]
    out = rank_adjust(hits, query="x", supersedes={"New v3": "New v2", "New v2": "Old v1"})
    assert [h["name"] for h in out] == ["New v3"]


def test_vector_only_floor_applies_only_when_lexical_empty():
    vec = [{"name": "a", "vec_score": 0.88}, {"name": "b", "vec_score": 0.75}]
    assert [h["name"] for h in apply_vector_only_floor(vec, [])] == ["a"]
    assert apply_vector_only_floor(vec, [{"name": "x"}]) == vec
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/python -m pytest tests/test_retrieval.py -q -p no:cacheprovider`
Expected: `ImportError: cannot import name 'apply_vector_only_floor'`

- [ ] **Step 3: Implement** (append to `ai_memory/retrieval.py`)

```python
def _is_active(h: dict) -> bool:
    return (h.get("status") or "active") not in INACTIVE


def rank_adjust(hits: List[dict], query: str, supersedes: Optional[Dict[str, str]] = None) -> List[dict]:
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
```

- [ ] **Step 4: Run to verify pass**

Run: `venv/bin/python -m pytest tests/test_retrieval.py -q -p no:cacheprovider && /home/lost/.local/bin/ruff check ai_memory/retrieval.py tests/test_retrieval.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add ai_memory/retrieval.py tests/test_retrieval.py
git commit -m "feat(retrieval): rank adjustment (sink, collapse, active-only boost) and vector-only floor"
```

---

### Task 3: retrieval.py — filter and Cypher builders

**Files:**
- Modify: `ai_memory/retrieval.py`
- Test: `tests/test_retrieval.py`

**Interfaces:**
- Produces:
  - `build_filters(assistant: Optional[str], space: Optional[str], trust: Optional[str]) -> Tuple[str, Dict[str, str]]` — returns `("f.assistant = $assistant AND f.space = $space", {"assistant": ..., "space": ...})`; only set inputs; `("", {})` when none. Alias is always `f`.
  - `build_search_cypher(where: str) -> str` — `CYPHER 25` first line; `SEARCH f IN (VECTOR INDEX $index FOR $vec [WHERE ...] LIMIT $pool) SCORE AS s`.
  - `build_fallback_cypher(where: str) -> str` — `CALL db.index.vector.queryNodes($index, $pool2, $vec) YIELD node AS f, score AS s [WHERE ...] ... LIMIT $pool`.
  - `build_fulltext_cypher(where: str) -> str` — `CALL db.index.fulltext.queryNodes($index, $q) YIELD node AS f, score AS s [WHERE ...] ... LIMIT $pool`.
  - `RETURN_FIELDS: str` shared by all three: `f.name AS name, coalesce(f.summary, f.content) AS text, f.key_points AS key_points, f.assistant AS assistant, f.status AS status, f.space AS space, s`.

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_retrieval.py
from ai_memory.retrieval import (
    RETURN_FIELDS, build_fallback_cypher, build_filters, build_fulltext_cypher, build_search_cypher,
)


def test_build_filters_only_set_inputs_and_joined_by_and():
    assert build_filters(None, None, None) == ("", {})
    where, params = build_filters("Grok", None, "trusted")
    assert where == "f.assistant = $assistant AND f.provenance_trust = $trust"
    assert params == {"assistant": "Grok", "trust": "trusted"}
    assert "status" not in build_filters("Grok", "shared", "trusted")[0]


def test_search_cypher_is_cypher25_and_filters_inside_search():
    c = build_search_cypher("f.assistant = $assistant")
    assert c.lstrip().startswith("CYPHER 25\n")
    assert "SEARCH f IN (VECTOR INDEX $index FOR $vec WHERE f.assistant = $assistant LIMIT $pool) SCORE AS s" in c
    assert RETURN_FIELDS in c
    c0 = build_search_cypher("")
    assert "FOR $vec LIMIT $pool) SCORE AS s" in c0 and "WHERE" not in c0


def test_fallback_cypher_filters_after_yield_and_returns_pool():
    c = build_fallback_cypher("f.space = $space")
    assert "CYPHER 25" not in c
    assert "CALL db.index.vector.queryNodes($index, $pool2, $vec)" in c
    assert c.index("YIELD") < c.index("WHERE f.space = $space") < c.index("RETURN")
    assert c.rstrip().endswith("LIMIT $pool")


def test_fulltext_cypher_filters_after_yield():
    c = build_fulltext_cypher("f.assistant = $assistant")
    assert "CALL db.index.fulltext.queryNodes($index, $q)" in c
    assert c.index("YIELD") < c.index("WHERE") < c.index("RETURN")
    assert build_fulltext_cypher("").count("WHERE") == 0
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/python -m pytest tests/test_retrieval.py -q -p no:cacheprovider`
Expected: `ImportError: cannot import name 'RETURN_FIELDS'`

- [ ] **Step 3: Implement** (append to `ai_memory/retrieval.py`)

```python
RETURN_FIELDS = (
    "f.name AS name, coalesce(f.summary, f.content) AS text, f.key_points AS key_points, "
    "f.assistant AS assistant, f.status AS status, f.space AS space, s"
)


def build_filters(assistant: Optional[str], space: Optional[str], trust: Optional[str]) -> Tuple[str, Dict[str, str]]:
    """Equality predicates for the inputs that are set, AND-joined. Never status (spec §3)."""
    clauses, params = [], {}
    if assistant:
        clauses.append("f.assistant = $assistant"); params["assistant"] = assistant
    if space:
        clauses.append("f.space = $space"); params["space"] = space
    if trust:
        clauses.append("f.provenance_trust = $trust"); params["trust"] = trust
    return " AND ".join(clauses), params


def build_search_cypher(where: str) -> str:
    inner = f"WHERE {where} " if where else ""
    return (
        "CYPHER 25\n"
        "MATCH (f:Fact)\n"
        f"SEARCH f IN (VECTOR INDEX $index FOR $vec {inner}LIMIT $pool) SCORE AS s\n"
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
```

- [ ] **Step 4: Run to verify pass**

Run: `venv/bin/python -m pytest tests/test_retrieval.py -q -p no:cacheprovider && /home/lost/.local/bin/ruff check ai_memory/retrieval.py tests/test_retrieval.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add ai_memory/retrieval.py tests/test_retrieval.py
git commit -m "feat(retrieval): filter builder and SEARCH/fallback/fulltext Cypher builders"
```

---

### Task 4: search.py — search_vector on SEARCH with latched fallback and Query timeouts

**Files:**
- Modify: `ai_memory/search.py:67-155` (replace `search_vector` body; keep signature, add `space=None`, `pool=None`)
- Modify: `ai_memory/search.py:19-30` (imports: `logging`, `time`, `Query`, `TransientError` not needed here)
- Test: `tests/test_search_path.py` (create)
- Modify: `tests/test_library.py:467-499` (`test_search_vector_cypher_omits_node_id`), `:887-920` (`test_search_vector_passes_trust_filter_to_session_run`) — these read `session.run` call args; adapt to `neo4j.Query`.

**Interfaces:**
- Consumes: Task 3 builders, Task 1 `pool_size`, `fallback_pool`; `ai_memory._config.get_query_timeout`, `get_driver`; `ai_memory.exceptions.Neo4jIndexNotFoundError`, `Neo4jQueryError`.
- Produces:
  - `search_vector(query, *, workspace=None, max_results=5, assistant=None, space=None, trust_filter=None, driver=None, pool=None) -> List[dict]` — returns hit dicts with keys `name, teaser, key_points, assistant, status, space, score, vec_score, via="vec", source`. Returns up to `pool` hits when `pool` is given, else up to `max_results`.
  - `reset_fallback()` — test/ops helper clearing the latch.
  - module logger `log = logging.getLogger("ai_memory.search")`.
  - `_fallback_state: dict` with keys `active: bool, until: float, index: Optional[str], warned: bool`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_search_path.py
"""search_vector: SEARCH path, latched fallback, timeouts, degrade rules (spec §3)."""
import logging

import pytest
from neo4j import Query
from neo4j.exceptions import ClientError

import ai_memory.search as S


class FakeResult(list):
    pass


class FakeSession:
    def __init__(self, script):
        # script: list of callables(query_text, params) -> rows or raising
        self.script = list(script); self.calls = []

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def run(self, query, params=None, **kw):
        text = query.text if isinstance(query, Query) else query
        self.calls.append((query, dict(params or {}, **kw)))
        step = self.script.pop(0)
        return FakeResult(step(text, params or kw))


class FakeDriver:
    def __init__(self, script): self.session_obj = FakeSession(script); self.closed = False
    def session(self): return self.session_obj
    def close(self): self.closed = True


def _rec(name, s=0.9):
    return {"name": name, "text": f"about {name}", "key_points": ["p"], "assistant": "Grok",
            "status": None, "space": None, "s": s}


def _22nd3():
    return ClientError({"code": "Neo.ClientError.Statement.PropertyNotFound",
                        "message": "22ND3: The property `assistant` is not an additional property for vector search with filters on the vector index `x`."})


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    S.reset_fallback()
    monkeypatch.setattr(S, "_embed", lambda q: [0.0] * 768)   # no Ollama
    yield
    S.reset_fallback()


def test_search_path_uses_cypher25_query_object_with_timeout():
    drv = FakeDriver([lambda t, p: [_rec("A"), _rec("B", 0.8)]])
    hits = S.search_vector("q", driver=drv, max_results=5, assistant="Grok")
    q, params = drv.session_obj.calls[0]
    assert isinstance(q, Query) and q.text.lstrip().startswith("CYPHER 25")
    assert q.timeout == S.get_query_timeout()
    assert "WHERE f.assistant = $assistant" in q.text
    assert params["pool"] == 20 and params["assistant"] == "Grok"
    assert [h["name"] for h in hits] == ["A", "B"]
    assert hits[0]["via"] == "vec" and hits[0]["vec_score"] == 0.9 and hits[0]["score"] == 0.9


def test_pool_param_widens_returned_list_and_max_results_caps_otherwise():
    rows = [_rec(f"n{i}", 1 - i / 100) for i in range(30)]
    drv = FakeDriver([lambda t, p: rows])
    assert len(S.search_vector("q", driver=drv, max_results=5)) == 5
    drv = FakeDriver([lambda t, p: rows])
    assert len(S.search_vector("q", driver=drv, max_results=5, pool=20)) == 20


def test_22nd3_latches_fallback_and_reruns_with_queryNodes(caplog):
    def boom(t, p):
        raise _22nd3()
    drv = FakeDriver([boom, lambda t, p: [_rec("A")]])
    with caplog.at_level(logging.WARNING, logger="ai_memory.search"):
        hits = S.search_vector("q", driver=drv, max_results=5, assistant="Grok")
    assert [h["name"] for h in hits] == ["A"]
    second, params = drv.session_obj.calls[1]
    assert "db.index.vector.queryNodes($index, $pool2, $vec)" in second.text
    assert params["pool2"] == 250 and params["pool"] == 20
    assert S._fallback_state["active"] is True
    assert sum("neo4j_migrate_vector_filters" in r.message for r in caplog.records) == 1
    # next call goes straight to fallback, no second warning
    drv2 = FakeDriver([lambda t, p: [_rec("B")]])
    with caplog.at_level(logging.WARNING, logger="ai_memory.search"):
        S.search_vector("q", driver=drv2, max_results=5)
    assert "queryNodes" in drv2.session_obj.calls[0][0].text
    assert sum("neo4j_migrate_vector_filters" in r.message for r in caplog.records) == 1


def test_other_client_errors_do_not_latch():
    def boom(t, p):
        raise ClientError({"code": "Neo.ClientError.Statement.ArgumentError", "message": "bad arg"})
    drv = FakeDriver([boom])
    with pytest.raises(S.Neo4jQueryError):
        S.search_vector("q", driver=drv)
    assert S._fallback_state["active"] is False


def test_index_not_found_returns_empty_with_warning_and_no_latch(caplog):
    def boom(t, p):
        raise ClientError({"code": "Neo.ClientError.Procedure.ProcedureCallFailed",
                           "message": "There is no such vector schema index: factEmbeddingIndex"})
    drv = FakeDriver([boom])
    with caplog.at_level(logging.WARNING, logger="ai_memory.search"):
        assert S.search_vector("q", driver=drv) == []
    assert S._fallback_state["active"] is False
    assert any("not found" in r.message.lower() or "no such" in r.message.lower() for r in caplog.records)


def test_latch_expires_after_ttl_and_on_index_name_change(monkeypatch):
    S._fallback_state.update(active=True, until=1000.0, index="factEmbeddingIndex", warned=True)
    monkeypatch.setattr(S.time, "monotonic", lambda: 999.0)
    assert S._use_fallback("factEmbeddingIndex") is True
    monkeypatch.setattr(S.time, "monotonic", lambda: 1001.0)
    assert S._use_fallback("factEmbeddingIndex") is False          # TTL -> re-probe
    S._fallback_state.update(active=True, until=5000.0, index="factEmbeddingIndex")
    assert S._use_fallback("other_index") is False                 # name change -> re-probe


def test_status_is_never_filtered_in_index():
    drv = FakeDriver([lambda t, p: []])
    S.search_vector("q", driver=drv, assistant="Grok", space="shared", trust_filter="trusted")
    q, _ = drv.session_obj.calls[0]
    assert "status" not in q.text.split("SEARCH")[1].split("SCORE")[0]
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/python -m pytest tests/test_search_path.py -q -p no:cacheprovider`
Expected: `AttributeError: module 'ai_memory.search' has no attribute 'reset_fallback'`

- [ ] **Step 3: Implement** — replace `search_vector` in `ai_memory/search.py` and add the module state. Keep `_list_vector_indexes`, `_classify_client_error`, `_escape_lucene` as they are.

```python
# imports (top of ai_memory/search.py) — add:
import logging
import time
from neo4j import Query
from ai_memory.retrieval import (
    build_fallback_cypher, build_filters, build_search_cypher, fallback_pool, pool_size,
)

log = logging.getLogger("ai_memory.search")
FALLBACK_TTL_S = 600.0
_fallback_state = {"active": False, "until": 0.0, "index": None, "warned": False}
MIGRATION_HINT = "run scripts/neo4j_migrate_vector_filters.py to add filter properties to the vector index"


def reset_fallback() -> None:
    _fallback_state.update(active=False, until=0.0, index=None, warned=False)


def _use_fallback(index: str) -> bool:
    st = _fallback_state
    if not st["active"]:
        return False
    if st["index"] != index or time.monotonic() >= st["until"]:
        st.update(active=False)          # re-probe SEARCH on next call
        return False
    return True


def _latch_fallback(index: str) -> None:
    st = _fallback_state
    st.update(active=True, index=index, until=time.monotonic() + FALLBACK_TTL_S)
    if not st["warned"]:
        log.warning("vector index %r lacks filter properties; using over-fetch fallback — %s", index, MIGRATION_HINT)
        st["warned"] = True


def _is_22nd3(e: ClientError) -> bool:
    msg = str(e)
    return ("22ND3" in msg) or ("not an additional property" in msg) or (
        getattr(e, "code", "") == "Neo.ClientError.Statement.PropertyNotFound" and "additional property" in msg)


def _is_search_syntax_error(e: ClientError) -> bool:
    return getattr(e, "code", "") == "Neo.ClientError.Statement.SyntaxError" and "SEARCH" in str(e)


def _is_index_unavailable(e: ClientError) -> bool:
    m = str(e).lower()
    return "no such index" in m or "no such vector schema index" in m or "populating" in m


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
                    try:
                        rows = run(build_search_cypher(where))
                    except ClientError as e:
                        if _is_22nd3(e) or _is_search_syntax_error(e):
                            _latch_fallback(index)
                            rows = run(build_fallback_cypher(where))
                        else:
                            raise
            except ClientError as e:
                if _is_index_unavailable(e):
                    log.warning("vector index %r not found or still populating; vector leg skipped for this call", index)
                    return []
                raise _classify_client_error(driver, e, vector_index=index) from e
        hits = _rows_to_hits(rows, "vec")
        return hits if pool is not None else hits[:max_results]
    finally:
        if owns_driver:
            driver.close()
```

Then update the two existing tests in `tests/test_library.py` that inspect `session.run` arguments (`test_search_vector_cypher_omits_node_id` at ~467 and `test_search_vector_passes_trust_filter_to_session_run` at ~887): where they read the Cypher string from `run.call_args[0][0]`, read `.text` from the `Query` object instead, and where they assert `k=` in kwargs, assert `params["pool"]` and the presence of `$trust` in the text. Also add `from ai_memory import search as _s; _s.reset_fallback()` at the top of each so a latched state from another test cannot leak.

- [ ] **Step 4: Run to verify pass**

Run: `venv/bin/python -m pytest tests/test_search_path.py tests/test_library.py -q -p no:cacheprovider && /home/lost/.local/bin/ruff check ai_memory/search.py tests/test_search_path.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add ai_memory/search.py tests/test_search_path.py tests/test_library.py
git commit -m "feat(search): search_vector on Cypher 25 SEARCH with latched over-fetch fallback and Query timeouts (spec §3)"
```

---

### Task 5: search.py — search_graph gains space and the fact_key_points leg

**Files:**
- Modify: `ai_memory/search.py:157-243` (`search_graph`)
- Test: `tests/test_search_path.py`; adapt `tests/test_library.py:443-466` (`test_search_graph_cypher_uses_related_to_or_learned_in`, `test_search_graph_uses_configurable_fulltext_index`) to `Query.text`.

**Interfaces:**
- Consumes: Task 3 `build_fulltext_cypher`, `build_filters`; Task 4 `_rows_to_hits`, `_is_index_unavailable`, `log`.
- Produces: `search_graph(query, *, workspace=None, max_results=5, assistant=None, space=None, trust_filter=None, driver=None, pool=None) -> List[dict]` — hits `via="ft"` (content index) or `via="kp"` (key-points index); fused within the leg by `fuse_rrf` when both indexes return rows; `related_facts`/`relationships` columns are **dropped** (they moved to `graph=True` semantics change, see Task 7 and MIGRATION.md). Env: `NEO4J_FULLTEXT_INDEX` (default `fact_content`), `NEO4J_FULLTEXT_KP_INDEX` (default `fact_key_points`).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_search_path.py
def test_search_graph_queries_both_fulltext_indexes_and_fuses(caplog):
    script = [lambda t, p: [_rec("A", 3.0)] if p["index"] == "fact_content" else [_rec("B", 9.0), _rec("A", 1.0)]]
    drv = FakeDriver(script * 2)
    hits = S.search_graph("report.md", driver=drv, max_results=5, assistant="Grok")
    texts = [c[0].text for c in drv.session_obj.calls]
    assert all("db.index.fulltext.queryNodes($index, $q)" in t for t in texts)
    assert {c[1]["index"] for c in drv.session_obj.calls} == {"fact_content", "fact_key_points"}
    assert all("WHERE f.assistant = $assistant" in t for t in texts)
    assert [h["name"] for h in hits] == ["A", "B"]      # A appears in both legs
    assert hits[0]["via"] == "ft+kp"


def test_search_graph_degrades_when_key_points_index_missing(caplog):
    def kp_missing(t, p):
        if p["index"] == "fact_key_points":
            raise ClientError({"code": "Neo.ClientError.Procedure.ProcedureCallFailed",
                               "message": "There is no such fulltext schema index: fact_key_points"})
        return [_rec("A", 3.0)]
    drv = FakeDriver([kp_missing, kp_missing])
    with caplog.at_level(logging.WARNING, logger="ai_memory.search"):
        hits = S.search_graph("q", driver=drv)
    assert [h["name"] for h in hits] == ["A"] and hits[0]["via"] == "ft"
    assert any("fact_key_points" in r.message for r in caplog.records)


def test_search_graph_escapes_lucene_and_lowercases_operators():
    drv = FakeDriver([lambda t, p: [], lambda t, p: []])
    S.search_graph("reserve AND (guard) OR NOT", driver=drv)
    q = drv.session_obj.calls[0][1]["q"]
    assert "\\(" in q and " and " in q and " or " in q and " not" in q
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/python -m pytest tests/test_search_path.py -q -p no:cacheprovider -k search_graph`
Expected: FAIL — the old body sends one query with `$fulltext_index`/`$lucene_query` parameters (KeyError `'index'` in the fake script).

- [ ] **Step 3: Implement** — replace `search_graph` and `_escape_lucene` in `ai_memory/search.py`:

```python
from ai_memory.retrieval import build_fulltext_cypher, fuse_rrf   # add to the retrieval import

_LUCENE_OPS = re.compile(r"\b(AND|OR|NOT)\b")


def _escape_lucene(query: str) -> str:
    """Escape Lucene special characters and lower-case the boolean keywords so
    a user's 'AND'/'OR'/'NOT' cannot parse as operators (spec §3)."""
    special = r'[\+\-\&\|\!\(\)\{\}\[\]\^\"\~\*\?\:\/\\]'
    escaped = re.sub(special, lambda m: "\\" + m.group(), query)
    return _LUCENE_OPS.sub(lambda m: m.group(1).lower(), escaped)


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
    return hits if pool is not None else hits[:max_results]
```

Update `tests/test_library.py` `test_search_graph_cypher_uses_related_to_or_learned_in` → rename to `test_search_graph_cypher_has_no_related_facts_join` asserting `"RELATED_TO" not in q.text` (the join is gone; see MIGRATION.md), and `test_search_graph_uses_configurable_fulltext_index` → assert `params["index"] == monkeypatched value` for the first call.

- [ ] **Step 4: Run to verify pass**

Run: `venv/bin/python -m pytest tests/test_search_path.py tests/test_library.py -q -p no:cacheprovider && /home/lost/.local/bin/ruff check ai_memory/search.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add ai_memory/search.py tests/test_search_path.py tests/test_library.py
git commit -m "feat(search): lexical leg over fact_content + fact_key_points with post-filters and degrade"
```

---

### Task 6: search.py — search_hybrid; MemoryClient.search delegates

**Files:**
- Modify: `ai_memory/search.py` (append `search_hybrid`, `load_supersedes`)
- Modify: `ai_memory/__init__.py:110-182` (`MemoryClient.search`)
- Test: `tests/test_search_path.py`, `tests/test_library.py` (`test_memory_client_search_*` still pass)

**Interfaces:**
- Consumes: Tasks 1–5.
- Produces:
  - `search_hybrid(query, *, workspace=None, k=5, assistant=None, space=None, trust=None, mode="hybrid", driver=None) -> List[dict]` — `mode` ∈ {"hybrid","fulltext","vector"}; hybrid = both legs at `pool_size(k)`, `apply_vector_only_floor`, `fuse_rrf`, `rank_adjust(..., supersedes=load_supersedes(session))`, cut to `k`.
  - `load_supersedes(session) -> Dict[str, str]` — `MATCH (n:Fact)-[:SUPERSEDES]->(o:Fact) RETURN n.name AS n, o.name AS o` (cheap: 4 edges today; cached per call).
  - `MemoryClient.search(query, *, assistant=None, max_results=5, graph=False, use_embeddings=False, metadata_only=False, fields=None, trust_filter=None, space=None, mode="hybrid")` — `use_embeddings` keeps the FAISS path; otherwise delegates to `search_hybrid` with `mode = "hybrid" if (graph or mode == "hybrid") else mode`; `graph=True` therefore means "lexical leg on" (breaking change recorded in Task 7).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_search_path.py
def test_search_hybrid_fuses_both_legs_and_applies_rank_rules():
    def script(t, p):
        if "SEARCH" in t:            # vector leg: an active hit and its superseded sibling
            return [_rec("Kelly Criterion", 0.9),
                    dict(_rec("Kelly Criterion (15:30 EDT)", 0.85), status="superseded")]
        if "fulltext" in t:          # two lexical calls (ft, kp)
            return [_rec("Other", 5.0)] if p["index"] == "fact_content" else []
        if "SUPERSEDES" in t:
            return []
        raise AssertionError(t)
    drv = FakeDriver([script] * 4)
    hits = S.search_hybrid("Kelly Criterion", driver=drv, k=5)
    names = [h["name"] for h in hits]
    assert names[0] == "Kelly Criterion"                    # exact-name boost among active
    assert "Kelly Criterion (15:30 EDT)" not in names       # collapsed: superseded sibling of an active hit
    assert "Other" in names
    assert set(hits[0]) >= {"name", "teaser", "key_points", "assistant", "status", "space", "score", "via"}


def test_search_hybrid_vector_only_floor_when_lexical_empty():
    def script(t, p):
        if "SEARCH" in t:
            return [dict(_rec("strong", 0.88)), dict(_rec("weak", 0.75))]
        return []
    drv = FakeDriver([script] * 4)
    assert [h["name"] for h in S.search_hybrid("q", driver=drv, k=5)] == ["strong"]


def test_search_hybrid_modes():
    drv = FakeDriver([lambda t, p: [_rec("V", 0.9)]])
    assert [h["name"] for h in S.search_hybrid("q", driver=drv, mode="vector")] == ["V"]
    drv = FakeDriver([lambda t, p: [_rec("F", 2.0)], lambda t, p: []])
    assert [h["name"] for h in S.search_hybrid("q", driver=drv, mode="fulltext")] == ["F"]
    with pytest.raises(ValueError):
        S.search_hybrid("q", driver=FakeDriver([]), mode="nope")


def test_memory_client_search_delegates_to_search_hybrid(monkeypatch, tmp_path):
    from ai_memory import MemoryClient
    seen = {}
    monkeypatch.setattr(S, "search_hybrid", lambda q, **kw: seen.update(kw) or [{"name": "X", "score": 1, "via": "vec"}])
    import ai_memory
    monkeypatch.setattr(ai_memory, "search_hybrid", S.search_hybrid, raising=False)
    c = MemoryClient(workspace=tmp_path)
    monkeypatch.setattr(c, "driver", lambda: "drv")
    out = c.search("q", assistant="Grok", space="shared", graph=True, max_results=3)
    assert out[0]["name"] == "X"
    assert seen["assistant"] == "Grok" and seen["space"] == "shared" and seen["k"] == 3 and seen["mode"] == "hybrid"
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/python -m pytest tests/test_search_path.py -q -p no:cacheprovider -k "hybrid or delegates"`
Expected: `AttributeError: module 'ai_memory.search' has no attribute 'search_hybrid'`

- [ ] **Step 3: Implement**

Append to `ai_memory/search.py`:

```python
from ai_memory.retrieval import apply_vector_only_floor, rank_adjust   # add to the retrieval import

MODES = ("hybrid", "fulltext", "vector")


def load_supersedes(session) -> dict:
    try:
        return {r["n"]: r["o"] for r in session.run(
            Query("MATCH (n:Fact)-[:SUPERSEDES]->(o:Fact) RETURN n.name AS n, o.name AS o", timeout=get_query_timeout()))}
    except Exception:  # noqa: BLE001 — collapse is best-effort; name-suffix rule still applies
        return {}


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
        if mode == "vector":
            return vec[:k]
        if mode == "fulltext":
            return lex[:k]
        vec = apply_vector_only_floor(vec, lex)
        legs = [(leg, hits) for leg, hits in (("vec", vec), ("lex", lex)) if hits]
        fused = fuse_rrf(legs)
        with driver.session() as session:
            supersedes = load_supersedes(session)
        return rank_adjust(fused, query, supersedes)[:k]
    finally:
        if owns_driver:
            driver.close()
```

Replace the body of `MemoryClient.search` in `ai_memory/__init__.py` (keep the docstring, update it):

```python
    def search(
        self,
        query: str,
        *,
        assistant: Optional[str] = None,
        max_results: int = 5,
        graph: bool = False,
        use_embeddings: bool = False,
        metadata_only: bool = False,
        fields: Optional[List[str]] = None,
        trust_filter: Optional[str] = None,
        space: Optional[str] = None,
        mode: str = "hybrid",
    ) -> List[dict]:
        """Hybrid search (spec §3). ``graph=True`` now means "lexical leg on",
        which the default hybrid mode already is; it no longer appends
        ``related_facts`` (see MIGRATION.md). ``use_embeddings`` keeps the FAISS
        path and is incompatible with ``trust_filter``/``space``."""
        if use_embeddings and (trust_filter is not None or space is not None):
            raise ValueError("trust_filter/space are incompatible with use_embeddings=True: FAISS has no Fact metadata.")
        if trust_filter is not None and trust_filter == "":
            raise ValueError("trust_filter cannot be an empty string; pass None to disable filtering.")
        if use_embeddings:
            results = search_faiss(query, workspace=self._workspace, max_results=max_results)
        else:
            from ai_memory import search as _search   # late import keeps monkeypatching simple
            effective_mode = "hybrid" if graph else mode
            results = _search.search_hybrid(
                query, workspace=self._workspace, k=max_results, assistant=assistant,
                space=space, trust=trust_filter, mode=effective_mode, driver=self.driver(),
            )
        if metadata_only:
            results = [apply_metadata_only(r) for r in results]
        if fields:
            results = [apply_fields_filter(r, fields) for r in results]
        return results
```

- [ ] **Step 4: Run to verify pass**

Run: `venv/bin/python -m pytest tests -q -p no:cacheprovider && /home/lost/.local/bin/ruff check ai_memory tests`
Expected: all pass (fix any `test_library.py` test that asserted the old `graph_results` merge; it now asserts delegation as above).

- [ ] **Step 5: Commit**

```bash
git add ai_memory/search.py ai_memory/__init__.py tests/test_search_path.py tests/test_library.py
git commit -m "feat(search): search_hybrid implements the retrieval contract; MemoryClient.search delegates (space, mode)"
```

---

### Task 7: MIGRATION.md and CHANGELOG entries for the breaking change

**Files:**
- Modify: `MIGRATION.md` (append a section), `CHANGELOG.md` (Unreleased → Changed / Fixed)

- [ ] **Step 1: Append to MIGRATION.md**

```markdown
## Upgrading to the hybrid retrieval path (unreleased)

- `MemoryClient.search(graph=True)` no longer appends `related_facts` /
  `relationships` to each hit. Hybrid mode (the default) already runs the
  lexical leg, so `graph=True` is now a no-op alias for `mode="hybrid"`.
  To get related Facts, call `client.traverse(name, depth=1)`.
- `search_graph()` no longer returns `related_facts`, `relationships` or
  `related_count`; it returns the same hit dict as `search_vector()`.
- New keyword arguments: `space=` (shared-space filter) and `mode=`
  ("hybrid" | "fulltext" | "vector") on `MemoryClient.search`, `search_vector`
  and `search_graph`.
- The vector leg filters inside the index when the index declares filter
  properties (see `scripts/neo4j_migrate_vector_filters.py`, phase 3). Until
  then it logs one warning per process and over-fetches; results are correct
  but a small mind may return fewer than `k` on very large graphs.
- `NEO4J_FULLTEXT_KP_INDEX` (default `fact_key_points`) names the key-points
  fulltext index; `scripts/neo4j_seed.py` now creates it.
```

- [ ] **Step 2: Add to CHANGELOG.md under `## [Unreleased]`** — a `### Changed` block with the three bullets above (condensed) and under `### Fixed`: "`search_vector` scoped searches no longer lose results to the top-k cliff; query timeouts are applied via `neo4j.Query`."

- [ ] **Step 3: Commit**

```bash
git add MIGRATION.md CHANGELOG.md
git commit -m "docs: MIGRATION and CHANGELOG for the hybrid retrieval path (graph=True semantics)"
```

---

### Task 8: CLI and hybrid_memory_search.py pass-through

**Files:**
- Modify: `scripts/hybrid_memory_search.py:77-130` (`main`)
- Modify: `scripts/cli.py:59-76` (`cmd_search`), `:217-231` (search parser)
- Test: `tests/test_cli_smoke.py`

**Interfaces:**
- Consumes: `ai_memory.search.search_hybrid`, `search_files`, `search_faiss`.
- Produces: `hybrid_memory_search.py "q" [--space S] [--mode hybrid|fulltext|vector] [--assistant A] [--max-results N] [--files-only] [--use-embeddings] [--metadata-only] [--fields ...]`; `ai-memory search` forwards `--space` and `--mode`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_cli_smoke.py`, using its existing `run_cli` helper)

```python
def test_cli_search_advertises_space_and_mode():
    r = run_cli(["search", "--help"])
    assert r.returncode == 0
    assert "--space" in r.stdout and "--mode" in r.stdout


def test_hybrid_script_advertises_space_and_mode():
    import subprocess, sys
    r = subprocess.run([sys.executable, "scripts/hybrid_memory_search.py", "--help"], capture_output=True, text=True)
    assert r.returncode == 0
    assert "--space" in r.stdout and "--mode" in r.stdout
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/python -m pytest tests/test_cli_smoke.py -q -p no:cacheprovider -k "space_and_mode"`
Expected: FAIL, `--space` absent from help.

- [ ] **Step 3: Implement**

In `scripts/hybrid_memory_search.py` `main()`: add

```python
    parser.add_argument("--space", default=None, help="Filter to a space (e.g. shared)")
    parser.add_argument("--mode", choices=("hybrid", "fulltext", "vector"), default="hybrid",
                        help="hybrid (default), fulltext-only, or vector-only")
```

and replace the block that calls `search_vector(...)` / `search_graph(...)` (lines ~117–128) with:

```python
    from ai_memory.search import search_hybrid
    results = search_hybrid(args.query, workspace=workspace, k=args.max_results,
                            assistant=args.assistant, space=args.space,
                            mode="hybrid" if args.graph else args.mode)
    format_output(results, "hybrid")
```

(keep the existing `--files-only` and `--use-embeddings` branches above it unchanged; keep the `--assistant`+`--use-embeddings` refusal).

In `scripts/cli.py`: in the search parser add

```python
    p.add_argument("--space", default=None, help="Filter to a space (e.g. shared)")
    p.add_argument("--mode", choices=("hybrid", "fulltext", "vector"), default=None,
                   help="hybrid (default), fulltext-only, or vector-only")
```

and in `cmd_search` forward them:

```python
    if args.space:
        extra += ["--space", args.space]
    if args.mode:
        extra += ["--mode", args.mode]
```

- [ ] **Step 4: Run to verify pass**

Run: `venv/bin/python -m pytest tests/test_cli_smoke.py -q -p no:cacheprovider && /home/lost/.local/bin/ruff check scripts/cli.py scripts/hybrid_memory_search.py`
Expected: all pass (including the existing `format_output` tests — `format_output` must accept hits without `related_facts`; it already tolerates missing `source`).

- [ ] **Step 5: Commit**

```bash
git add scripts/cli.py scripts/hybrid_memory_search.py tests/test_cli_smoke.py
git commit -m "feat(cli): --space and --mode on search; standalone script calls search_hybrid"
```

---

### Task 9: fact_key_points in seed, verify_schema, _config

**Files:**
- Modify: `scripts/neo4j_seed.py:110-125` (after the `fact_content` block)
- Modify: `scripts/verify_schema.py:57-58` and the diff function
- Modify: `ai_memory/_config.py:128` (`EXPECTED_FULLTEXT_KP`), `validate_schema`
- Test: `tests/test_verify_schema.py`, `tests/test_library.py` (`test_neo4j_seed_contains_required_indexes`, `test_config_expected_fulltext_props_matches_verify_schema`)

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_verify_schema.py
def test_expected_key_points_fulltext_index_declared():
    import scripts.verify_schema as vs
    assert vs.EXPECTED_FULLTEXT_KP == "fact_key_points"
    assert vs.EXPECTED_FULLTEXT_KP_PROPS == {"key_points"}


# append to tests/test_library.py
def test_neo4j_seed_creates_key_points_fulltext_index():
    src = open("scripts/neo4j_seed.py", encoding="utf-8").read()
    assert "CREATE FULLTEXT INDEX fact_key_points IF NOT EXISTS" in src
    assert "ON EACH [n.key_points]" in src


def test_config_expects_key_points_fulltext_index():
    from ai_memory import _config
    assert _config.EXPECTED_FULLTEXT_KP == "fact_key_points"
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/python -m pytest tests/test_verify_schema.py tests/test_library.py -q -p no:cacheprovider -k "key_points"`
Expected: `AttributeError: ... has no attribute 'EXPECTED_FULLTEXT_KP'`

- [ ] **Step 3: Implement**

`scripts/neo4j_seed.py`, after the `fact_content` try-block:

```python
        print("Creating key-points full-text index...")
        session.run("""
            CREATE FULLTEXT INDEX fact_key_points IF NOT EXISTS
            FOR (n:Fact) ON EACH [n.key_points]
        """)
```

`scripts/verify_schema.py` near line 58:

```python
EXPECTED_FULLTEXT_KP       = os.getenv("NEO4J_FULLTEXT_KP_INDEX", "fact_key_points")
EXPECTED_FULLTEXT_KP_PROPS = {"key_points"}
```

and in the diff function, mirror the existing `fact_content` check: missing → `missing.append(f"fulltext index {EXPECTED_FULLTEXT_KP}")`; present with wrong properties → `drift`.

`ai_memory/_config.py` after line 128:

```python
EXPECTED_FULLTEXT_KP = os.getenv("NEO4J_FULLTEXT_KP_INDEX", "fact_key_points")
EXPECTED_FULLTEXT_KP_PROPS = {"key_points"}
```

and in `validate_schema`, after the `fact_content` fulltext loop, repeat the same loop for `EXPECTED_FULLTEXT_KP` / `EXPECTED_FULLTEXT_KP_PROPS`.

- [ ] **Step 4: Run to verify pass**

Run: `venv/bin/python -m pytest tests -q -p no:cacheprovider && /home/lost/.local/bin/ruff check ai_memory scripts/verify_schema.py scripts/neo4j_seed.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add scripts/neo4j_seed.py scripts/verify_schema.py ai_memory/_config.py tests/test_verify_schema.py tests/test_library.py
git commit -m "feat(schema): create and verify the fact_key_points fulltext index"
```

---

### Task 10: learn.py — _sync_fact_tx re-raises TransientError

**Files:**
- Modify: `ai_memory/learn.py` (`_sync_fact_tx`, the `except Exception` at the end of the function)
- Test: `tests/test_learn.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_learn.py
def test_sync_fact_tx_lets_transient_error_reach_driver_retry():
    from neo4j.exceptions import TransientError
    import pytest
    from ai_memory.learn import _sync_fact_tx

    class Tx:
        def run(self, *a, **k):
            raise TransientError("deadlock")

    topic = {"name": "n", "summary": "s", "key_points": [], "source_file": "f", "created_at": "2026-01-01T00:00:00Z"}
    with pytest.raises(TransientError):
        _sync_fact_tx(Tx(), topic, None)
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/python -m pytest tests/test_learn.py -q -p no:cacheprovider -k transient`
Expected: FAIL — `_sync_fact_tx` returns `False` instead of raising.

- [ ] **Step 3: Implement** — in `_sync_fact_tx`, add before the generic handler:

```python
    except TransientError:
        raise                      # let execute_write's managed retry handle deadlocks / leader switches
    except Exception as e:
        print(f"Error syncing topic '{topic.get('name', 'unknown')}': {e}", file=sys.stderr)
        return False
```

with `from neo4j.exceptions import TransientError` added to the imports.

- [ ] **Step 4: Run to verify pass**

Run: `venv/bin/python -m pytest tests/test_learn.py -q -p no:cacheprovider && /home/lost/.local/bin/ruff check ai_memory/learn.py`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add ai_memory/learn.py tests/test_learn.py
git commit -m "fix(learn): do not swallow TransientError inside execute_write callbacks"
```

---

### Task 11: harness.py — golden loader, rankers, metrics, gate

**Files:**
- Create: `ai_memory/eval/harness.py`
- Test: `tests/test_harness.py`

**Interfaces:**
- Consumes: `ai_memory.eval.judge` (`judge_query`, `JudgeCache`, `ndcg_at_k`, `recall_at_k`, `mrr`, `passes_ship_gate`, `calibration_agreement`); `ai_memory.search.search_hybrid`, `search_vector`; `ai_memory.retrieval`.
- Produces:
  - `load_golden(path) -> List[dict]` — each `{"query": str, "filters": {"assistant"?, "space"?, "trust"?}, "expect": List[str]}`; raises `ValueError` listing the offending index on a bad entry.
  - `Ranker = Callable[[str, dict, int], List[dict]]` (query, filters, k) → hits.
  - `make_rankers(driver, workspace) -> Dict[str, Ranker]` with keys `legacy`, `hybrid_fallback`, `hybrid_search`.
  - `exact_hit_rate(hits, expect, k) -> float`.
  - `evaluate(golden, rankers, judge_call, model, cache, k=5, pool_n=10) -> dict` — `{"per_ranker": {name: {"exact5", "ndcg5", "recall5", "mrr", "judged_queries", "unjudged": [queries]}}, "pool_size": int}`; a query whose pooled judging returns `None` is listed under `unjudged` and **excluded from metric averages but marks `gate_ok=False`**.
  - `gate(results_before, results_after, ranker) -> bool` = `passes_ship_gate` on `{"golden": {...}, "judged": {...}}` built from the two `evaluate` outputs; `False` if either has unjudged queries.
  - `openai_chat_call(url, model, timeout=300) -> Callable[[list], str]`.
  - `format_table(results) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_harness.py
import json

import pytest

from ai_memory.eval import harness as H

GOLDEN = [
    {"query": "reserve balance guard", "filters": {}, "expect": ["Reserve Guard"]},
    {"query": "grok hybrid floor", "filters": {"assistant": "Grok"}, "expect": ["Floor Fact"]},
    {"query": "nothing here", "filters": {}, "expect": []},
]


def _ranker(table):
    def r(query, filters, k):
        return [{"name": n, "score": 1.0 - i / 10, "via": "vec", "summary": f"about {n}", "key_points": [],
                 "assistant": filters.get("assistant"), "status": None} for i, n in enumerate(table.get(query, []))][:k]
    return r


def test_load_golden_validates_shape(tmp_path):
    p = tmp_path / "g.json"; p.write_text(json.dumps(GOLDEN))
    assert H.load_golden(p) == GOLDEN
    p.write_text(json.dumps([{"query": "x"}]))
    with pytest.raises(ValueError) as e:
        H.load_golden(p)
    assert "index 0" in str(e.value)


def test_exact_hit_rate():
    hits = [{"name": "A"}, {"name": "B"}]
    assert H.exact_hit_rate(hits, ["A", "C"], 5) == 0.5
    assert H.exact_hit_rate(hits, [], 5) == 1.0          # nothing expected, nothing wrong


def test_evaluate_pools_top_n_and_scores_each_ranker():
    good = _ranker({"reserve balance guard": ["Reserve Guard", "X"], "grok hybrid floor": ["Floor Fact"], "nothing here": ["Y"]})
    bad = _ranker({"reserve balance guard": ["X", "Reserve Guard"], "grok hybrid floor": ["Z"], "nothing here": []})
    judged = []

    def judge(messages):
        user = messages[1]["content"]
        judged.append(user)
        names = [line.split("name: ", 1)[1] for line in user.splitlines() if line.startswith("name: ")]
        grade = lambda n: 2 if n in ("Reserve Guard", "Floor Fact") else 0
        return json.dumps([{"name": n, "grade": grade(n), "why": ""} for n in names])

    res = H.evaluate(GOLDEN, {"good": good, "bad": bad}, judge, model="m", cache=None, k=5, pool_n=10)
    g, b = res["per_ranker"]["good"], res["per_ranker"]["bad"]
    assert g["exact5"] == 1.0 and b["exact5"] == pytest.approx(2 / 3)
    assert g["ndcg5"] > b["ndcg5"] and g["recall5"] >= b["recall5"] and g["mrr"] > b["mrr"]
    assert g["unjudged"] == [] and b["unjudged"] == []
    assert len(judged) == 3                                # one pooled judging per query, shared by both rankers


def test_evaluate_marks_unjudged_and_gate_fails_closed():
    r = _ranker({"reserve balance guard": ["Reserve Guard"], "grok hybrid floor": ["Floor Fact"], "nothing here": ["Y"]})
    res = H.evaluate(GOLDEN, {"r": r}, lambda m: "garbage", model="m", cache=None)
    assert set(res["per_ranker"]["r"]["unjudged"]) == {g["query"] for g in GOLDEN}
    assert H.gate(res, res, "r") is False


def test_gate_uses_passes_ship_gate_on_golden_and_judged():
    mk = lambda e, n, rc: {"per_ranker": {"r": {"exact5": e, "ndcg5": n, "recall5": rc, "mrr": 0.5, "unjudged": [], "judged_queries": 3}}}
    assert H.gate(mk(0.5, 0.6, 0.5), mk(0.6, 0.6, 0.5), "r") is True
    assert H.gate(mk(0.5, 0.6, 0.5), mk(0.6, 0.5, 0.5), "r") is False


def test_make_rankers_has_the_three_names():
    names = set(H.make_rankers(driver=object(), workspace=None))
    assert names == {"legacy", "hybrid_fallback", "hybrid_search"}


def test_format_table_lists_each_ranker():
    res = {"per_ranker": {"a": {"exact5": 0.5, "ndcg5": 0.4, "recall5": 0.3, "mrr": 0.2, "unjudged": [], "judged_queries": 3}}, "pool_size": 3}
    out = H.format_table(res)
    assert "a" in out and "0.50" in out and "ndcg5" in out
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/python -m pytest tests/test_harness.py -q -p no:cacheprovider`
Expected: `ImportError: cannot import name 'harness'`

- [ ] **Step 3: Implement**

```python
# ai_memory/eval/harness.py
"""Retrieval evaluation harness (spec §8).

Runs named rankers over a golden file, pools their top-N per query for one
judging pass, and reports exact-hit rate, nDCG@k, Recall@k and MRR per ranker.
The golden file lives OUTSIDE the repo (private content); pass --golden or set
AI_MEMORY_GOLDEN. A golden query the judge cannot grade fails the gate.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ai_memory.eval.judge import JudgeCache, judge_query, mrr, ndcg_at_k, passes_ship_gate, recall_at_k

Ranker = Callable[[str, dict, int], List[dict]]
GOLDEN_ENV = "AI_MEMORY_GOLDEN"


def load_golden(path) -> List[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("golden file must be a JSON list")
    out = []
    for i, g in enumerate(data):
        if not isinstance(g, dict) or not isinstance(g.get("query"), str) or not isinstance(g.get("expect"), list):
            raise ValueError(f"golden entry at index {i} must have 'query' (str) and 'expect' (list)")
        out.append({"query": g["query"], "filters": dict(g.get("filters") or {}), "expect": list(g["expect"])})
    return out


def exact_hit_rate(hits: List[dict], expect: List[str], k: int) -> float:
    if not expect:
        return 1.0
    top = {h.get("name") for h in hits[:k]}
    return len(top & set(expect)) / len(expect)


def openai_chat_call(url: str, model: str, timeout: float = 300.0) -> Callable[[list], str]:
    def call(messages):
        body = json.dumps({"model": model, "messages": messages, "stream": False, "think": False, "temperature": 0}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)["choices"][0]["message"]["content"]
    return call


def make_rankers(driver, workspace) -> Dict[str, Ranker]:
    """legacy = today's post-filter path; hybrid_fallback = contract over the
    over-fetch path; hybrid_search = contract over SEARCH (fails loudly before
    phase 3 on an un-migrated index — that is the point of having it)."""
    from ai_memory import search as S

    def legacy(query, filters, k):
        # Reproduce the pre-redesign behaviour: top-k from the index, then filter, then LIMIT k.
        from neo4j import Query
        emb = S._embed(query)
        if emb is None:
            return []
        where, params = S.build_filters(filters.get("assistant"), filters.get("space"), filters.get("trust"))
        cypher = ("CALL db.index.vector.queryNodes($index, $k, $vec) YIELD node AS f, score AS s\n"
                  + (f"WHERE {where}\n" if where else "") + f"RETURN {S.RETURN_FIELDS}\nORDER BY s DESC\nLIMIT $k")
        with driver.session() as session:
            rows = list(session.run(Query(cypher, timeout=S.get_query_timeout()),
                                    dict(params, index=os.getenv("NEO4J_VECTOR_INDEX", "fact_embeddings"), k=k, vec=emb)))
        return S._rows_to_hits(rows, "vec")

    def hybrid_fallback(query, filters, k):
        S.reset_fallback()
        S._fallback_state.update(active=True, index=os.getenv("NEO4J_VECTOR_INDEX", "fact_embeddings"), until=float("inf"), warned=True)
        try:
            return S.search_hybrid(query, workspace=workspace, k=k, driver=driver, **_kw(filters))
        finally:
            S.reset_fallback()

    def hybrid_search(query, filters, k):
        S.reset_fallback()
        return S.search_hybrid(query, workspace=workspace, k=k, driver=driver, **_kw(filters))

    return {"legacy": legacy, "hybrid_fallback": hybrid_fallback, "hybrid_search": hybrid_search}


def _kw(filters: dict) -> dict:
    return {"assistant": filters.get("assistant"), "space": filters.get("space"), "trust": filters.get("trust")}


def evaluate(golden: List[dict], rankers: Dict[str, Ranker], judge_call, model: str,
             cache: Optional[JudgeCache], k: int = 5, pool_n: int = 10) -> dict:
    per: Dict[str, dict] = {n: {"exact5": [], "ndcg5": [], "recall5": [], "mrr": [], "unjudged": [], "judged_queries": 0} for n in rankers}
    pool_total = 0
    for g in golden:
        runs = {n: r(g["query"], g["filters"], pool_n) for n, r in rankers.items()}
        pool: Dict[str, dict] = {}
        for hits in runs.values():
            for h in hits[:pool_n]:
                pool.setdefault(h["name"], h)
        pool_total += len(pool)
        grades = judge_query(g["query"], list(pool.values()), judge_call, model=model, cache=cache) if pool else {}
        for n, hits in runs.items():
            per[n]["exact5"].append(exact_hit_rate(hits, g["expect"], k))
            if grades is None:
                per[n]["unjudged"].append(g["query"])
                continue
            gmap = {name: j["grade"] for name, j in grades.items()}
            ranked = [h["name"] for h in hits]
            per[n]["ndcg5"].append(ndcg_at_k(ranked, gmap, k))
            per[n]["recall5"].append(recall_at_k(ranked, gmap, k))
            per[n]["mrr"].append(mrr(ranked, gmap))
            per[n]["judged_queries"] += 1
    out = {"per_ranker": {}, "pool_size": pool_total}
    for n, m in per.items():
        avg = lambda xs: (sum(xs) / len(xs)) if xs else 0.0
        out["per_ranker"][n] = {"exact5": avg(m["exact5"]), "ndcg5": avg(m["ndcg5"]), "recall5": avg(m["recall5"]),
                                "mrr": avg(m["mrr"]), "unjudged": m["unjudged"], "judged_queries": m["judged_queries"]}
    return out


def gate(before: dict, after: dict, ranker: str) -> bool:
    b, a = before["per_ranker"][ranker], after["per_ranker"][ranker]
    if a["unjudged"] or b["unjudged"]:
        return False
    return passes_ship_gate(
        before={"golden": {"ndcg5": b["exact5"], "recall5": b["exact5"]}, "judged": {"ndcg5": b["ndcg5"], "recall5": b["recall5"]}},
        after={"golden": {"ndcg5": a["exact5"], "recall5": a["exact5"]}, "judged": {"ndcg5": a["ndcg5"], "recall5": a["recall5"]}},
    )


def format_table(results: dict) -> str:
    lines = [f"{'ranker':<18} {'exact5':>7} {'ndcg5':>7} {'recall5':>8} {'mrr':>6} {'judged':>7} {'unjudged':>9}"]
    for n, m in results["per_ranker"].items():
        lines.append(f"{n:<18} {m['exact5']:>7.2f} {m['ndcg5']:>7.2f} {m['recall5']:>8.2f} {m['mrr']:>6.2f} "
                     f"{m['judged_queries']:>7} {len(m['unjudged']):>9}")
    lines.append(f"pooled candidates judged: {results.get('pool_size', 0)}")
    return "\n".join(lines)
```

(`golden` split in `gate` uses the exact-hit rate for both metrics because the golden labels are binary; the judged split carries graded nDCG/recall — this is the spec's "golden and judged splits".)

- [ ] **Step 4: Run to verify pass**

Run: `venv/bin/python -m pytest tests/test_harness.py -q -p no:cacheprovider && /home/lost/.local/bin/ruff check ai_memory/eval/harness.py tests/test_harness.py`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add ai_memory/eval/harness.py tests/test_harness.py
git commit -m "feat(eval): retrieval harness — golden loader, three rankers, pooled judging, ship gate"
```

---

### Task 12: labelling skeleton and the `ai-memory eval` command

**Files:**
- Modify: `ai_memory/eval/harness.py` (append `pool_candidates`, `main`)
- Modify: `scripts/cli.py` (new `eval` subparser)
- Test: `tests/test_harness.py`, `tests/test_cli_smoke.py`

**Interfaces:**
- Produces:
  - `pool_candidates(queries: List[dict], rankers: Dict[str, Ranker], n=10) -> List[dict]` — for each `{query, filters}` returns `{"query", "filters", "expect": [], "candidates": [{"name","teaser","assistant","status","seen_in":[ranker names]}]}`; the owner deletes non-answers and moves names into `expect`.
  - `main(argv=None) -> int` — args: `--golden PATH` (default env), `--rankers a,b` (default `legacy,hybrid_fallback,hybrid_search`), `--judge-url` (default `http://192.168.99.235:8080/v1/chat/completions`), `--judge-model` (default `qwen3.8-27b-q6k`), `--cache PATH` (default `~/.ai-memory/judge_cache.json`), `--label` (write skeleton to stdout instead of evaluating), `--json PATH` (write results), `--k 5`, `--pool 10`. Exit 0; exit 2 on golden errors.
  - `ai-memory eval …` forwards all of the above to `python -m ai_memory.eval.harness` via the existing `_run_script`-style subprocess? No — `scripts/cli.py` dispatches scripts by path; add `cmd_eval` that calls `harness.main(argv)` directly (it is importable after `pip install -e .`).

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_harness.py
def test_pool_candidates_skeleton_has_empty_expect_and_provenance():
    r1 = _ranker({"q1": ["A", "B"]}); r2 = _ranker({"q1": ["B", "C"]})
    sk = H.pool_candidates([{"query": "q1", "filters": {"assistant": "Grok"}}], {"r1": r1, "r2": r2}, n=10)
    assert sk[0]["expect"] == [] and sk[0]["filters"] == {"assistant": "Grok"}
    names = {c["name"]: c["seen_in"] for c in sk[0]["candidates"]}
    assert names == {"A": ["r1"], "B": ["r1", "r2"], "C": ["r2"]}


def test_main_label_mode_prints_skeleton(tmp_path, capsys, monkeypatch):
    p = tmp_path / "queries.json"; p.write_text(json.dumps([{"query": "q1", "filters": {}, "expect": []}]))
    monkeypatch.setattr(H, "make_rankers", lambda driver, workspace: {"r": _ranker({"q1": ["A"]})})
    monkeypatch.setattr(H, "_open_driver", lambda workspace: object())
    assert H.main(["--golden", str(p), "--label"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["candidates"][0]["name"] == "A"


def test_main_reports_missing_golden(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(H.GOLDEN_ENV, raising=False)
    assert H.main([]) == 2
    assert "golden" in capsys.readouterr().err.lower()


# append to tests/test_cli_smoke.py
def test_cli_eval_help():
    r = run_cli(["eval", "--help"])
    assert r.returncode == 0
    assert "--golden" in r.stdout and "--rankers" in r.stdout and "--label" in r.stdout
```

- [ ] **Step 2: Run to verify failure**

Run: `venv/bin/python -m pytest tests/test_harness.py tests/test_cli_smoke.py -q -p no:cacheprovider -k "pool_candidates or main_ or eval_help"`
Expected: `AttributeError: module ... has no attribute 'pool_candidates'`

- [ ] **Step 3: Implement**

Append to `ai_memory/eval/harness.py`:

```python
def pool_candidates(queries: List[dict], rankers: Dict[str, Ranker], n: int = 10) -> List[dict]:
    out = []
    for g in queries:
        seen: Dict[str, dict] = {}
        for rname, r in rankers.items():
            for h in r(g["query"], dict(g.get("filters") or {}), n)[:n]:
                c = seen.setdefault(h["name"], {"name": h["name"], "teaser": (h.get("teaser") or h.get("summary") or "")[:200],
                                                 "assistant": h.get("assistant"), "status": h.get("status"), "seen_in": []})
                c["seen_in"].append(rname)
        out.append({"query": g["query"], "filters": dict(g.get("filters") or {}), "expect": [], "candidates": list(seen.values())})
    return out


def _open_driver(workspace):
    from ai_memory._config import get_driver
    return get_driver(workspace)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="ai-memory eval", description="Retrieval evaluation against a golden set (spec §8)")
    ap.add_argument("--golden", default=os.getenv(GOLDEN_ENV), help=f"golden JSON path (default ${GOLDEN_ENV})")
    ap.add_argument("--rankers", default="legacy,hybrid_fallback,hybrid_search")
    ap.add_argument("--judge-url", default="http://192.168.99.235:8080/v1/chat/completions")
    ap.add_argument("--judge-model", default="qwen3.8-27b-q6k")
    ap.add_argument("--cache", default=str(Path.home() / ".ai-memory" / "judge_cache.json"))
    ap.add_argument("--label", action="store_true", help="print a labelling skeleton (pooled candidates) instead of evaluating")
    ap.add_argument("--json", dest="json_out", default=None, help="write results JSON here")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--pool", type=int, default=10)
    ap.add_argument("--workspace", default=None)
    a = ap.parse_args(argv)
    if not a.golden:
        print(f"error: no golden file; pass --golden or set {GOLDEN_ENV}", file=sys.stderr)
        return 2
    try:
        golden = load_golden(a.golden)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"error: golden file: {e}", file=sys.stderr)
        return 2
    driver = _open_driver(a.workspace)
    try:
        rankers = make_rankers(driver, a.workspace)
        chosen = {n: rankers[n] for n in a.rankers.split(",") if n}
        if a.label:
            print(json.dumps(pool_candidates(golden, chosen, a.pool), indent=2, ensure_ascii=False))
            return 0
        cache = JudgeCache(a.cache)
        res = evaluate(golden, chosen, openai_chat_call(a.judge_url, a.judge_model), model=a.judge_model, cache=cache, k=a.k, pool_n=a.pool)
        print(format_table(res))
        if a.json_out:
            Path(a.json_out).write_text(json.dumps(res, indent=2), encoding="utf-8")
        return 0
    finally:
        close = getattr(driver, "close", None)
        if close:
            close()


if __name__ == "__main__":
    sys.exit(main())
```

In `scripts/cli.py` add:

```python
def cmd_eval(args: argparse.Namespace) -> int:
    from ai_memory.eval.harness import main as harness_main
    return harness_main(args.args)
```

and the subparser (before `return parser`):

```python
    p = subparsers.add_parser("eval", help="Retrieval evaluation against a golden set (--golden PATH, --rankers, --label)")
    p.add_argument("args", nargs=argparse.REMAINDER, help="passed to ai_memory.eval.harness (see --golden --rankers --label --judge-url)")
    p.set_defaults(func=cmd_eval)
```

Because `--help` on the `eval` subparser must show `--golden`, `--rankers`, `--label`, set the subparser's `description` to the harness parser's option list: build it as `description=" ".join(["--golden PATH", "--rankers a,b", "--label", "--judge-url URL", "--judge-model M", "--cache PATH", "--json PATH", "--k N", "--pool N"])`.

- [ ] **Step 4: Run to verify pass**

Run: `venv/bin/python -m pytest tests -q -p no:cacheprovider && /home/lost/.local/bin/ruff check ai_memory scripts/cli.py tests`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add ai_memory/eval/harness.py scripts/cli.py tests/test_harness.py tests/test_cli_smoke.py
git commit -m "feat(eval): labelling skeleton and ai-memory eval command"
```

---

### Task 13: Python 3.9 and packaging check, CHANGELOG, push

**Files:**
- Modify: `CHANGELOG.md` (Unreleased → Added: harness, retrieval module; Fixed: TransientError, query timeout)

- [ ] **Step 1: Run the 3.9 import matrix** (uv is installed; the venv from the audit may still exist)

```bash
/home/lost/.local/bin/uv venv -q --python 3.9 /tmp/venv39 && /home/lost/.local/bin/uv pip install -q --python /tmp/venv39/bin/python 'neo4j>=5.0' 'python-dotenv>=1.0' pytest
for m in ai_memory ai_memory.retrieval ai_memory.search ai_memory.eval.harness; do /tmp/venv39/bin/python -c "import $m; print('$m OK')"; done
/tmp/venv39/bin/python -m pytest tests -q -p no:cacheprovider 2>&1 | tail -1
```

Expected: four `OK` lines; the pytest line shows only the pre-existing `scripts/cli.py` 3.9 failures (20, from `list[str] | None` in cli.py — out of scope for this plan and tracked in CHANGELOG) or fewer. Any **new** 3.9 failure in `test_retrieval.py`, `test_search_path.py` or `test_harness.py` is a defect in this plan's code: fix it (usually a missing `from __future__ import annotations`).

- [ ] **Step 2: CHANGELOG** — under `## [Unreleased]` add to `### Added`: "`ai_memory.retrieval` (pure fusion/ranking/Cypher builders), `search_hybrid`, `ai_memory.eval.harness` + `ai-memory eval`"; to `### Fixed`: "`_sync_fact_tx` no longer swallows `TransientError`."

- [ ] **Step 3: Full suite, ruff, commit, push**

```bash
venv/bin/python -m pytest tests -q -p no:cacheprovider && /home/lost/.local/bin/ruff check ai_memory tests scripts/cli.py scripts/hybrid_memory_search.py
git add CHANGELOG.md
git commit -m "docs(changelog): phase 0-1 retrieval path and harness"
git push origin master
```

---

### Task 14 (operational, not code): phase 0 labelling session and phase 1 gate

- [ ] **Step 1: Produce the labelling skeleton against the live graph** (read-only)

```bash
cd /home/lost/ai-memory-system
cat > /tmp/queries.json <<'EOF'
[ {"query": "<owner writes ~30 queries here>", "filters": {}, "expect": []} ]
EOF
AI_MEMORY_DIR=/home/lost/.grok venv/bin/python -m ai_memory.eval.harness --golden /tmp/queries.json --label --rankers legacy,hybrid_fallback > /tmp/golden_skeleton.json
```

The `.env.neo4j` under `/home/lost/.grok` supplies credentials via `AI_MEMORY_DIR`. `hybrid_search` is omitted here on purpose: before phase 3 it raises 22ND3 on the un-migrated index; include it only after the migration (or use the offline simulation from the spec's scratchpad if SEARCH-only hits must be labelled earlier).

- [ ] **Step 2: Owner labels** — for each query, move the correct `candidates[].name` values into `expect`, delete `candidates`, save as the golden file **outside the repo**, e.g. `/home/lost/.ai-memory/golden/retrieval-2026-09.json`, and `export AI_MEMORY_GOLDEN=/home/lost/.ai-memory/golden/retrieval-2026-09.json`. Mix: ~10 unscoped, ~10 with `filters.assistant` or `filters.space`, 5 with one clear answer, 5 with `expect: []`.

- [ ] **Step 3: Baseline** (phase 0 gate)

```bash
AI_MEMORY_DIR=/home/lost/.grok venv/bin/python -m ai_memory.eval.harness --rankers legacy,hybrid_fallback --json /home/lost/.ai-memory/golden/baseline-phase0.json
```

Record the table in the phase-2 plan's preamble. Expected shape: the `legacy` row shows lower `exact5` on scoped queries than `hybrid_fallback`.

- [ ] **Step 4: Phase 1 gate** — after Tasks 1–13 are deployed (they are, once merged; nothing to restart — library code), the gate compares `hybrid_fallback` against `legacy` on today's vectors:

```bash
venv/bin/python - <<'EOF'
import json
from ai_memory.eval.harness import gate
b = json.load(open("/home/lost/.ai-memory/golden/baseline-phase0.json"))
print("phase-1 gate (hybrid_fallback vs legacy):", gate({"per_ranker": {"x": b["per_ranker"]["legacy"]}}, {"per_ranker": {"x": b["per_ranker"]["hybrid_fallback"]}}, "x"))
EOF
```

Expected: `True`. If `False`, the ranking contract lost recall somewhere on your data; the per-query rows in the JSON say where, and the fix goes back through Tasks 2 or 6 test-first.

---

## Follow-on plans (not in this document)

- **Phase 2** — `ai_memory/embed.py`, boilerplate detector, `RetrievalConfig` singleton, provenance properties, CAS embed write, `ai-memory embed --all`, `embedding_prev`; gate with the harness before/after re-embed.
- **Phase 3** — `scripts/neo4j_migrate_vector_filters.py` with pre-flight (answers §10.1–10.5, decides the `WITH` list), scheduled window, gate on indexed-node count and per-property probe; `hybrid_search` joins the harness.
- **Phase 4** — grok client port + `tests/test_retrieval_contract.py`.
- **Phase 5** — `ai_memory/wordindex.py`, retire `link_related_facts`/`_post_sync_tx`/grok `organize`, on-write worst-pick rule, nightly `rule_version` cutover.
- **Phase 6** — duplicate/supersede report.

## Self-review notes

- Spec coverage for phases 0–1: §3 contract (Tasks 1–6), §5 library rows for `search.py`/`__init__`/`_config`/`learn` (Tasks 4–6, 9, 10), `hybrid_memory_search.py`/`cli.py` (Task 8), MIGRATION (Task 7), §8 harness incl. three rankers and fail-closed gate (Tasks 11–12), phase 0/1 of §9 (Task 14). `fact_key_points` in seed/verify (Task 9). `neo4j.Query` timeouts (Tasks 4–5). Not in scope: §4, §6, §7, phases 2–6.
- Type consistency: hit dict keys `name, teaser, key_points, assistant, status, space, score, via` (+ `vec_score` on vector hits, `source` for backwards compatibility) are produced by `_rows_to_hits` (Task 4) and consumed by `fuse_rrf`/`rank_adjust` (Tasks 1–2) and `pool_candidates` (Task 12). `Ranker(query, filters, k)` in Tasks 11–12. `search_hybrid(query, *, workspace, k, assistant, space, trust, mode, driver)` in Tasks 6, 8, 11.
- Known deviation to flag to the reviewer: `search_graph` drops `related_facts`; recorded as breaking in Task 7.
