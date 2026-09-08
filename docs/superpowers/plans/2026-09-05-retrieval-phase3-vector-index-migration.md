# Retrieval Phase 3 — Vector Index Filter Properties Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recreate the Fact vector index with filter properties so the Cypher 25 `SEARCH … WHERE` path runs in-index on the live graph, with a pre-flight, a gated migration script, schema checks, and the harness proving `hybrid_search` on both scoped and unscoped golden queries.

**Architecture:** A new pure-ish module `ai_memory/vector_index.py` holds identifier validation, the DDL/probe builders, the wait-online loop, the pre-flight and the gated migrate routine (driver in, report dict out; every statement shape unit-tested against fake sessions). `scripts/neo4j_migrate_vector_filters.py` is the argparse wrapper (`--preflight`, `--migrate`, `--dry-run`). `ai_memory/retrieval.py::build_search_cypher` gains the index name as an inlined, validated identifier (Neo4j rejects a parameter there — see Measured). Seed, `_config`, `verify_schema` learn the filter-property list. The harness gains `--subset scoped|unscoped|all` so the spec's two-split gate is one flag.

**Tech Stack:** Python ≥ 3.9, neo4j-python driver, Neo4j 2026.04 (Cypher 25 `SEARCH`), pytest with fake sessions; live steps use local Ollama (`nomic-embed-text`) and the golden set at `~/.ai-memory/golden/retrieval-2026-09.json`.

**Spec:** `docs/superpowers/specs/2026-09-04-retrieval-index-design.md` — §3 (vector leg), §4 "Index rebuild" steps 1–4, §9 phase-3 row, §10 open verifications. Phases 0–2 landed on this branch (4f288d2..aa5c184); phase-2 gate passed with canonical vectors (hybrid_fallback exact@5 .80→.90, nDCG@5 .71→.77; `after-phase2.json` in the golden dir).

## Measured (pre-flight on the live graph, 2026-09-05, temporary index `factEmbeddingIndex_v2`, dropped afterwards)

| §10 item | answer |
|---|---|
| 10.1 predicates accepted inside `SEARCH … WHERE` | equality `=` and `IS NULL` accepted; `IN [...]` and `<>` rejected (`SyntaxError: The vector search filter predicate … not supported`). The §3 builder (equality, AND-joined, no `status`) is compatible. |
| 10.2 `SHOW INDEXES` exposes filter properties | yes — `properties` = `['embedding', 'assistant', 'space', 'status', 'provenance_trust']` |
| 10.3 nodes lacking a declared property are indexed | **yes** — unscoped `SEARCH … LIMIT 2000` returned all 1,502 embedded Facts, including all 165 without `assistant`, 1,453 without `space`/`status`, 1,501 without `provenance_trust`. No sentinel values needed; the full `WITH` list is safe. |
| 10.4 population time | ~1.0 s for 1,502 Facts |
| 10.5 equivalent index under another name | rejected: `Neo.ClientError.Schema.IndexAlreadyExists: There already exists an index (:Fact {embedding, assistant, space, status, provenance_trust})` — confirms drop-and-recreate |
| new 10.6 parameters inside the `VECTOR INDEX` clause | `VECTOR INDEX $index` → `SyntaxError: Parameter cannot be used in a VECTOR INDEX clause`; `LIMIT $pool` inside the clause is accepted. The phase-1 `build_search_cypher` uses `$index` and would therefore never succeed on a migrated index (it would latch the fallback via `_is_search_syntax_error`). **Task 1 fixes this.** |
| DDL shape | `CREATE VECTOR INDEX name FOR (f:Fact) ON (f.embedding) WITH [f.assistant, f.space, f.status, f.provenance_trust] OPTIONS {indexConfig: {…}}` — `WITH` before `OPTIONS`. |
| per-property probes | `f.assistant = 'Weft'` → 5, `f.space = 'shared'` → 5, `f.status = 'active'` → 5, `f.provenance_trust = 'trusted'` → 1, `f.assistant = 'Grok' AND f.space = 'shared'` → 5 |

Decided `WITH` list: `assistant, space, status, provenance_trust` (all four; §4 step 1 condition satisfied by 10.3).

## Global Constraints

- `requires-python = ">=3.9"`: new modules start with `from __future__ import annotations`; `X | None` / `list[str]` only in annotations; never in runtime-evaluated positions.
- No new runtime dependency. Embedding for probes goes through an injectable `embed_fn` (default `ai_memory.embed.embed_text`).
- Every Cypher 25 statement is prefixed with the literal line `CYPHER 25`.
- The vector index name is an **inlined identifier** in `SEARCH … (VECTOR INDEX \`name\` …)`, validated against `^[A-Za-z_][A-Za-z0-9_]*$` and backtick-quoted; everything else stays a parameter (`$vec`, `$pool`, filter values).
- In-index filters remain equality only, `AND`-joined, built only from inputs that are set; `status` is never filtered in-index (§3).
- Filter properties (the decided `WITH` list): `assistant, space, status, provenance_trust`, in that order, as `EXPECTED_VECTOR_FILTER_PROPS` in `ai_memory/_config.py`; seed, migrate, verify and `validate_schema` all read that one constant.
- Migration gate (§4 step 3): after `CREATE`, wait until `SHOW INDEXES` reports `state = 'ONLINE'` and `populationPercent = 100`; then (a) the number of distinct Facts returned by an unscoped `SEARCH … LIMIT $pool` with `$pool` = count of Facts with `embedding` must equal that count; (b) for every declared property that at least one Fact carries, `SEARCH … WHERE f.<prop> = $v` with the property's most common value must return ≥ 1 row. Either failure → the script exits non-zero and prints what failed; it never drops the new index on gate failure (the old one is already gone; the operator decides).
- `--dry-run` prints every statement the migration would run and runs none; the pre-flight always drops the temporary `<index>_v2` in a `finally`, even on error.
- Tests are offline (`tests/conftest.py` guard); run `venv/bin/python -m pytest tests -q -p no:cacheprovider`; ruff clean on every new file; no new findings in touched files.
- Commit after every task with the trailers `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01RTWNMeQjJ5Zg5FgAhDhsRZ`. Never push.

## File Structure

| file | responsibility |
|---|---|
| `ai_memory/retrieval.py` (modify) | `validate_index_name`, `build_search_cypher(where, index)` inlining the name |
| `ai_memory/search.py` (modify) | pass `index` to the builder |
| `ai_memory/vector_index.py` (create) | DDL/probe builders, `index_info`, `wait_online`, `membership_check`, `property_probes`, `preflight`, `migrate` |
| `scripts/neo4j_migrate_vector_filters.py` (create) | CLI wrapper: `--preflight`, `--migrate`, `--dry-run`, `--index`, `--props`, `--json` |
| `ai_memory/_config.py` (modify) | `EXPECTED_VECTOR_FILTER_PROPS`; `validate_schema` reports `vector_filter_props` |
| `scripts/neo4j_seed.py`, `scripts/verify_schema.py` (modify) | create with `WITH` list on fresh installs; print filter-property status |
| `ai_memory/eval/harness.py` (modify) | `--subset all|scoped|unscoped` |
| `tests/test_retrieval.py`, `tests/test_search_path.py` (modify); `tests/test_vector_index.py` (create); `tests/test_library.py` or `tests/test_verify_schema.py` (modify); `tests/test_harness.py` (modify) | |
| `CHANGELOG.md`, `MIGRATION.md`, `README.md` (modify) | |

---

### Task 1: Inline the index name in the SEARCH statement

**Files:**
- Modify: `ai_memory/retrieval.py:151-159` (`build_search_cypher`)
- Modify: `ai_memory/search.py:215` (call site)
- Modify: `tests/test_retrieval.py:125-140`, `tests/test_search_path.py` (any test asserting `$index` in the SEARCH text)

**Interfaces:**
- Produces: `validate_index_name(name: str) -> str` (returns the name; raises `ValueError` otherwise); `build_search_cypher(where: str, index: str) -> str`.
- Consumers: `search_vector` (Task 1), `vector_index.py` probes (Task 2), harness `hybrid_search` (unchanged — it goes through `search_hybrid`).

- [ ] **Step 1: Write the failing tests** (replace the two existing `build_search_cypher` tests in `tests/test_retrieval.py`)

```python
import pytest
from ai_memory.retrieval import build_search_cypher, validate_index_name


def test_validate_index_name_accepts_identifiers_and_rejects_the_rest():
    assert validate_index_name("factEmbeddingIndex") == "factEmbeddingIndex"
    assert validate_index_name("fact_embeddings_v2") == "fact_embeddings_v2"
    for bad in ("", "9abc", "fact-embeddings", "a b", "x`y", "$index", "idx;DROP INDEX x"):
        with pytest.raises(ValueError):
            validate_index_name(bad)


def test_build_search_cypher_inlines_index_and_keeps_params():
    c = build_search_cypher("f.assistant = $assistant", "factEmbeddingIndex")
    assert c.startswith("CYPHER 25\n")
    assert "SEARCH f IN (VECTOR INDEX `factEmbeddingIndex` FOR $vec WHERE f.assistant = $assistant LIMIT $pool) SCORE AS s" in c
    assert "$index" not in c
    c0 = build_search_cypher("", "factEmbeddingIndex")
    assert "FOR $vec LIMIT $pool) SCORE AS s" in c0 and "WHERE" not in c0


def test_build_search_cypher_rejects_bad_index_name():
    with pytest.raises(ValueError):
        build_search_cypher("", "bad name")
```

In `tests/test_search_path.py`, find every assertion that the SEARCH statement contains `$index` (grep `VECTOR INDEX $index`) and change it to expect `` VECTOR INDEX `<name>` `` where `<name>` is whatever `NEO4J_VECTOR_INDEX` the test sets (or the default `fact_embeddings`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_retrieval.py tests/test_search_path.py -q -p no:cacheprovider`
Expected: FAIL — `ImportError: cannot import name 'validate_index_name'`; `TypeError: build_search_cypher() takes 1 positional argument`.

- [ ] **Step 3: Implement**

```python
# ai_memory/retrieval.py
import re
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
```

`ai_memory/search.py:215`: `search_text = build_search_cypher(where, index)`. Keep `index` in `params` (the fallback's `db.index.vector.queryNodes($index, …)` still uses it; Neo4j ignores unused parameters). If `validate_index_name` raises inside `search_vector` (an operator set a bad `NEO4J_VECTOR_INDEX`), let the `ValueError` propagate — a configuration error, not a retrieval degradation.

- [ ] **Step 4: Run the tests**

Run: `venv/bin/python -m pytest tests/test_retrieval.py tests/test_search_path.py -q -p no:cacheprovider`
Expected: pass. Then the full suite.

- [ ] **Step 5: Commit**

```bash
git add ai_memory/retrieval.py ai_memory/search.py tests/test_retrieval.py tests/test_search_path.py
git commit -m "fix(retrieval): inline the validated vector index name in SEARCH (Neo4j rejects a parameter there)"
```

---

### Task 2: `ai_memory/vector_index.py` — builders, wait, probes, pre-flight, gated migrate

**Files:**
- Create: `ai_memory/vector_index.py`
- Create: `tests/test_vector_index.py`

**Interfaces:**
- Consumes: `validate_index_name` (Task 1), `embed_text` (`ai_memory.embed`), `EXPECTED_VECTOR_FILTER_PROPS` (Task 3 adds it to `_config`; until then this module defines `DEFAULT_FILTER_PROPS = ("assistant", "space", "status", "provenance_trust")` and Task 3 makes `_config` import it from here — one source of truth lives in `vector_index.py`).
- Produces:
  - `DEFAULT_FILTER_PROPS`, `EMBED_DIMS = 768`, `SIMILARITY = "cosine"`
  - `build_create_index_ddl(name, props, *, dims=EMBED_DIMS, similarity=SIMILARITY) -> str`
  - `build_drop_ddl(name) -> str` (`DROP INDEX \`name\` IF EXISTS`)
  - `build_probe_cypher(name, where) -> str` (Cypher 25, `LIMIT $pool`, `RETURN f.name AS name, s`)
  - `index_info(session, name) -> dict | None` (`{"state", "populationPercent", "properties"}`)
  - `wait_online(session, name, *, timeout_s=600.0, poll_s=0.5, clock=time.monotonic, sleep=time.sleep) -> float` (seconds waited; raises `TimeoutError`)
  - `embedded_count(session) -> int`
  - `membership_check(session, name, vec) -> dict` (`{"embedded": n, "returned": m, "ok": n == m}`)
  - `property_probes(session, name, vec, props) -> dict[str, dict]` (`{prop: {"present": k, "value": v, "rows": r, "ok": bool}}`; a property no Fact carries is `ok: True` with `rows: None`)
  - `preflight(driver, index, props, embed_fn=embed_text, *, query="kraken maker reserve balance guard", log=print) -> dict`
  - `migrate(driver, index, props, embed_fn=embed_text, *, dry_run=False, log=print) -> dict` with `report["ok"]`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_vector_index.py
from __future__ import annotations

import pytest

from ai_memory import vector_index as VI


class Res:
    def __init__(self, rows): self.rows = rows
    def single(self): return self.rows[0] if self.rows else None
    def __iter__(self): return iter(self.rows)
    def consume(self): return None


class Sess:
    """Scripted fake: `script` maps a substring of the Cypher to a list of row-lists (popped in order)."""
    def __init__(self, script=None):
        self.calls = []; self.script = {k: list(v) for k, v in (script or {}).items()}
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def run(self, q, params=None, **kw):
        p = dict(params or {}); p.update(kw); self.calls.append((q, p))
        for key, rows in self.script.items():
            if key in q:
                return Res(rows.pop(0) if rows else [])
        return Res([])


class Drv:
    def __init__(self, s): self.s = s
    def session(self): return self.s


PROPS = ("assistant", "space", "status", "provenance_trust")
VEC = [0.1] * 768


def test_create_ddl_shape():
    ddl = VI.build_create_index_ddl("factEmbeddingIndex", PROPS)
    assert ddl == ("CREATE VECTOR INDEX `factEmbeddingIndex` FOR (f:Fact) ON (f.embedding) "
                   "WITH [f.assistant, f.space, f.status, f.provenance_trust] "
                   "OPTIONS {indexConfig: {`vector.dimensions`: 768, `vector.similarity_function`: 'cosine'}}")
    with pytest.raises(ValueError):
        VI.build_create_index_ddl("bad name", PROPS)
    with pytest.raises(ValueError):
        VI.build_create_index_ddl("ok", ("assistant", "bad-prop"))


def test_drop_ddl_and_probe_shape():
    assert VI.build_drop_ddl("x_v2") == "DROP INDEX `x_v2` IF EXISTS"
    q = VI.build_probe_cypher("idx", "f.assistant = $v")
    assert q.startswith("CYPHER 25\nMATCH (f:Fact)\n")
    assert "SEARCH f IN (VECTOR INDEX `idx` FOR $vec WHERE f.assistant = $v LIMIT $pool) SCORE AS s" in q
    assert "WHERE" not in VI.build_probe_cypher("idx", "")


def test_index_info_and_wait_online():
    s = Sess({"SHOW INDEXES": [
        [{"state": "POPULATING", "populationPercent": 40.0, "properties": ["embedding"]}],
        [{"state": "ONLINE", "populationPercent": 100.0, "properties": ["embedding", "assistant"]}],
    ]})
    t = [0.0]
    def clock(): return t[0]
    def sleep(x): t[0] += x
    waited = VI.wait_online(s, "idx", timeout_s=10, poll_s=0.5, clock=clock, sleep=sleep)
    assert waited == pytest.approx(0.5) and len([c for c in s.calls if "SHOW INDEXES" in c[0]]) == 2


def test_wait_online_times_out():
    s = Sess({"SHOW INDEXES": [[{"state": "POPULATING", "populationPercent": 1.0, "properties": []}]] * 100})
    t = [0.0]
    with pytest.raises(TimeoutError):
        VI.wait_online(s, "idx", timeout_s=2, poll_s=1, clock=lambda: t[0], sleep=lambda x: t.__setitem__(0, t[0] + x))


def test_membership_check_compares_distinct_names_to_embedded_count():
    s = Sess({"count(f)": [[{"c": 3}]], "SEARCH f IN": [[{"name": "A", "s": 0.9}, {"name": "B", "s": 0.8}, {"name": "A", "s": 0.7}]]})
    r = VI.membership_check(s, "idx", VEC)
    assert r == {"embedded": 3, "returned": 2, "ok": False}
    q, p = [c for c in s.calls if "SEARCH f IN" in c[0]][0]
    assert p["pool"] == 3 and p["vec"] == VEC


def test_property_probes_use_most_common_value_and_skip_absent_props():
    s = Sess({
        "f.assistant IS NOT NULL": [[{"v": "Nova", "c": 1261}]],
        "f.space IS NOT NULL": [[]],
        "WHERE f.assistant = $v": [[{"name": "X", "s": 0.9}]],
    })
    r = VI.property_probes(s, "idx", VEC, ("assistant", "space"))
    assert r["assistant"] == {"present": 1261, "value": "Nova", "rows": 1, "ok": True}
    assert r["space"] == {"present": 0, "value": None, "rows": None, "ok": True}


def test_preflight_creates_waits_probes_and_always_drops_v2():
    s = Sess({
        "SHOW INDEXES": [[{"state": "ONLINE", "populationPercent": 100.0, "properties": ["embedding", "assistant"]}]] * 5,
        "count(f)": [[{"c": 2}]],
        "SEARCH f IN": [[{"name": "A", "s": 1.0}, {"name": "B", "s": 0.9}], [{"name": "A", "s": 1.0}]],
        "IS NOT NULL": [[{"v": "Nova", "c": 2}]],
    })
    rep = VI.preflight(Drv(s), "factEmbeddingIndex", ("assistant",), embed_fn=lambda t: VEC, log=lambda *a: None)
    stmts = [c[0] for c in s.calls]
    assert any(st.startswith("CREATE VECTOR INDEX `factEmbeddingIndex_v2`") for st in stmts)
    assert stmts[-1] == "DROP INDEX `factEmbeddingIndex_v2` IF EXISTS"
    assert rep["membership"]["ok"] and rep["probes"]["assistant"]["ok"] and rep["temp_index"] == "factEmbeddingIndex_v2"
    assert "population_s" in rep and rep["show_properties"] == ["embedding", "assistant"]


def test_preflight_drops_v2_even_when_a_probe_raises():
    class Boom(Sess):
        def run(self, q, params=None, **kw):
            if "SEARCH f IN" in q:
                self.calls.append((q, {})); raise RuntimeError("probe failed")
            return super().run(q, params, **kw)
    s = Boom({"SHOW INDEXES": [[{"state": "ONLINE", "populationPercent": 100.0, "properties": ["embedding"]}]] * 3, "count(f)": [[{"c": 1}]]})
    with pytest.raises(RuntimeError):
        VI.preflight(Drv(s), "idx", ("assistant",), embed_fn=lambda t: VEC, log=lambda *a: None)
    assert s.calls[-1][0] == "DROP INDEX `idx_v2` IF EXISTS"


def test_migrate_dry_run_runs_nothing():
    s = Sess()
    rep = VI.migrate(Drv(s), "idx", PROPS, embed_fn=lambda t: VEC, dry_run=True, log=lambda *a: None)
    assert s.calls == [] and rep["ok"] is True and rep["dry_run"] is True
    assert rep["statements"][0] == "DROP INDEX `idx` IF EXISTS" and rep["statements"][1].startswith("CREATE VECTOR INDEX `idx`")


def test_migrate_drops_creates_waits_and_gates():
    s = Sess({
        "SHOW INDEXES": [[{"state": "ONLINE", "populationPercent": 100.0, "properties": ["embedding", "assistant", "space", "status", "provenance_trust"]}]] * 5,
        "count(f)": [[{"c": 2}]],
        "SEARCH f IN": [[{"name": "A", "s": 1.0}, {"name": "B", "s": 0.9}]] + [[{"name": "A", "s": 1.0}]] * 4,
        "IS NOT NULL": [[{"v": "Nova", "c": 2}], [{"v": "shared", "c": 1}], [{"v": "active", "c": 1}], [{"v": "trusted", "c": 1}]],
    })
    rep = VI.migrate(Drv(s), "idx", PROPS, embed_fn=lambda t: VEC, log=lambda *a: None)
    stmts = [c[0] for c in s.calls]
    assert stmts[0] == "DROP INDEX `idx` IF EXISTS" and stmts[1].startswith("CREATE VECTOR INDEX `idx` ")
    assert rep["ok"] is True and rep["membership"]["ok"] and all(p["ok"] for p in rep["probes"].values())


def test_migrate_gate_fails_on_membership_mismatch_and_keeps_index():
    s = Sess({
        "SHOW INDEXES": [[{"state": "ONLINE", "populationPercent": 100.0, "properties": ["embedding"]}]] * 3,
        "count(f)": [[{"c": 3}]],
        "SEARCH f IN": [[{"name": "A", "s": 1.0}]] * 5,
        "IS NOT NULL": [[]] * 4,
    })
    rep = VI.migrate(Drv(s), "idx", PROPS, embed_fn=lambda t: VEC, log=lambda *a: None)
    assert rep["ok"] is False and rep["membership"] == {"embedded": 3, "returned": 1, "ok": False}
    assert not any(st.startswith("DROP INDEX `idx`") for st in [c[0] for c in s.calls][2:])   # no drop after the create


def test_migrate_fails_closed_without_embedding():
    s = Sess()
    with pytest.raises(RuntimeError):
        VI.migrate(Drv(s), "idx", PROPS, embed_fn=lambda t: None, log=lambda *a: None)
    assert s.calls == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `venv/bin/python -m pytest tests/test_vector_index.py -q -p no:cacheprovider`
Expected: FAIL — `ModuleNotFoundError: No module named 'ai_memory.vector_index'`.

- [ ] **Step 3: Implement**

```python
# ai_memory/vector_index.py
"""Vector index with filter properties (spec §4 "Index rebuild"): DDL/probe builders, wait-online,
pre-flight on a temporary `<index>_v2`, and the gated drop-and-recreate migration.

Measured on Neo4j 2026.04 (pre-flight 2026-09-05): the index name cannot be a parameter inside the
SEARCH clause (inlined, validated identifier); LIMIT may be a parameter; nodes lacking a declared
filter property are still indexed; population of 1,502 Facts takes ~1 s.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from ai_memory.embed import embed_text
from ai_memory.retrieval import validate_index_name

DEFAULT_FILTER_PROPS = ("assistant", "space", "status", "provenance_trust")
EMBED_DIMS = 768
SIMILARITY = "cosine"
PREFLIGHT_QUERY = "kraken maker reserve balance guard"


def _prop(p: str) -> str:
    return validate_index_name(p)          # same identifier rule


def build_create_index_ddl(name: str, props: Sequence[str], *, dims: int = EMBED_DIMS, similarity: str = SIMILARITY) -> str:
    n = validate_index_name(name)
    with_list = ", ".join(f"f.{_prop(p)}" for p in props)
    return (f"CREATE VECTOR INDEX `{n}` FOR (f:Fact) ON (f.embedding) WITH [{with_list}] "
            f"OPTIONS {{indexConfig: {{`vector.dimensions`: {int(dims)}, `vector.similarity_function`: '{similarity}'}}}}")


def build_drop_ddl(name: str) -> str:
    return f"DROP INDEX `{validate_index_name(name)}` IF EXISTS"


def build_probe_cypher(name: str, where: str) -> str:
    n = validate_index_name(name)
    inner = f"WHERE {where} " if where else ""
    return ("CYPHER 25\nMATCH (f:Fact)\n"
            f"SEARCH f IN (VECTOR INDEX `{n}` FOR $vec {inner}LIMIT $pool) SCORE AS s\n"
            "RETURN f.name AS name, s")


def index_info(session, name: str) -> dict | None:
    rows = list(session.run(
        "SHOW INDEXES YIELD name, state, populationPercent, properties WHERE name = $n "
        "RETURN state, populationPercent, properties", n=name))
    if not rows:
        return None
    r = rows[0]
    return {"state": r["state"], "populationPercent": float(r["populationPercent"] or 0.0), "properties": list(r["properties"] or [])}


def wait_online(session, name: str, *, timeout_s: float = 600.0, poll_s: float = 0.5,
                clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> float:
    t0 = clock()
    while True:
        info = index_info(session, name)
        if info and info["state"] == "ONLINE" and info["populationPercent"] >= 100.0:
            return clock() - t0
        if clock() - t0 >= timeout_s:
            raise TimeoutError(f"index {name!r} not ONLINE/100% after {timeout_s}s: {info}")
        sleep(poll_s)


def embedded_count(session) -> int:
    return int(session.run("MATCH (f:Fact) WHERE f.embedding IS NOT NULL RETURN count(f) AS c").single()["c"])


def membership_check(session, name: str, vec: list) -> dict:
    n = embedded_count(session)
    rows = list(session.run(build_probe_cypher(name, ""), vec=vec, pool=max(n, 1)))
    returned = len({r["name"] for r in rows})
    return {"embedded": n, "returned": returned, "ok": returned == n}


def property_probes(session, name: str, vec: list, props: Sequence[str]) -> dict:
    out = {}
    for p in props:
        _prop(p)
        top = session.run(f"MATCH (f:Fact) WHERE f.{p} IS NOT NULL RETURN f.{p} AS v, count(*) AS c ORDER BY c DESC LIMIT 1").single()
        if top is None:
            out[p] = {"present": 0, "value": None, "rows": None, "ok": True}
            continue
        rows = list(session.run(build_probe_cypher(name, f"f.{p} = $v"), vec=vec, pool=5, v=top["v"]))
        out[p] = {"present": int(top["c"]), "value": top["v"], "rows": len(rows), "ok": len(rows) >= 1}
    return out


def _embed_or_fail(embed_fn, query):
    vec = embed_fn(query)
    if not vec:
        raise RuntimeError("embedding unavailable (Ollama down?); refusing to run index probes without a query vector")
    return vec


def preflight(driver, index: str, props: Sequence[str], embed_fn=embed_text, *, query: str = PREFLIGHT_QUERY, log=print) -> dict:
    """Create `<index>_v2` with the filter properties, wait, measure, probe, and ALWAYS drop it."""
    base = validate_index_name(index)
    temp = f"{base}_v2"
    vec = _embed_or_fail(embed_fn, query)
    rep = {"index": base, "temp_index": temp, "props": list(props)}
    with driver.session() as s:
        try:
            s.run(build_create_index_ddl(temp, props)).consume()
            rep["population_s"] = wait_online(s, temp)
            info = index_info(s, temp) or {}
            rep["show_properties"] = info.get("properties", [])
            rep["membership"] = membership_check(s, temp, vec)
            rep["probes"] = property_probes(s, temp, vec, props)
            log(f"preflight {temp}: population {rep['population_s']:.1f}s, membership {rep['membership']}, "
                f"probes ok={all(p['ok'] for p in rep['probes'].values())}")
        finally:
            s.run(build_drop_ddl(temp)).consume()
    rep["ok"] = bool(rep.get("membership", {}).get("ok")) and all(p["ok"] for p in rep.get("probes", {}).values())
    return rep


def migrate(driver, index: str, props: Sequence[str], embed_fn=embed_text, *, dry_run: bool = False,
            query: str = PREFLIGHT_QUERY, log=print) -> dict:
    """Drop and recreate `index` with the filter properties, wait until ONLINE/100%, then gate (§4 step 3).
    On gate failure the new index is left in place and rep['ok'] is False."""
    base = validate_index_name(index)
    statements = [build_drop_ddl(base), build_create_index_ddl(base, props)]
    rep = {"index": base, "props": list(props), "dry_run": dry_run, "statements": statements}
    if dry_run:
        for st in statements:
            log(st)
        rep["ok"] = True
        return rep
    vec = _embed_or_fail(embed_fn, query)
    with driver.session() as s:
        for st in statements:
            log(st)
            s.run(st).consume()
        rep["population_s"] = wait_online(s, base)
        info = index_info(s, base) or {}
        rep["show_properties"] = info.get("properties", [])
        rep["membership"] = membership_check(s, base, vec)
        rep["probes"] = property_probes(s, base, vec, props)
    rep["ok"] = rep["membership"]["ok"] and all(p["ok"] for p in rep["probes"].values())
    log(f"migrate {base}: population {rep['population_s']:.1f}s, membership {rep['membership']}, gate ok={rep['ok']}")
    return rep
```

Note for the tests' `Sess` fake: `property_probes` runs the `IS NOT NULL` query then a `SEARCH` probe per present property, in that order; the scripted rows in the tests are consumed in that order. Adjust nothing in the tests; if the implementation's query order differs, match the tests.

- [ ] **Step 4: Run tests**

Run: `venv/bin/python -m pytest tests/test_vector_index.py -q -p no:cacheprovider`
Expected: 12 passed. Ruff: `venv/bin/ruff check ai_memory/vector_index.py tests/test_vector_index.py` → All checks passed.

- [ ] **Step 5: Commit**

```bash
git add ai_memory/vector_index.py tests/test_vector_index.py
git commit -m "feat(vector-index): DDL/probe builders, wait-online, pre-flight on a temp index, gated drop-and-recreate migrate"
```

---

### Task 3: Filter properties as one constant — config, seed, verify

**Files:**
- Modify: `ai_memory/_config.py` (`EXPECTED_VECTOR_FILTER_PROPS`, `validate_schema` adds `vector_filter_props`)
- Modify: `scripts/neo4j_seed.py:82-108` (create with the `WITH` list; hint when an existing index lacks it)
- Modify: `scripts/verify_schema.py` (`main()` prints the filter-property status; `diff_schema` untouched)
- Modify: `tests/test_verify_schema.py` (or `tests/test_library.py` where `validate_schema` tests live)

**Interfaces:**
- Produces: `ai_memory._config.EXPECTED_VECTOR_FILTER_PROPS = DEFAULT_FILTER_PROPS` (imported from `ai_memory.vector_index`); `validate_schema(...)["vector_filter_props"]` = `"ok"` | `"missing: [..]"` | `"index not found"` for the configured `vector_index` (when `vector_index` is None, checks the env `NEO4J_VECTOR_INDEX` default `fact_embeddings`).

- [ ] **Step 1: Failing tests**

```python
def test_validate_schema_reports_vector_filter_props():
    from ai_memory import _config
    class Sess:
        def __init__(self, props): self.props = props
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def run(self, q, **kw):
            if "type = 'VECTOR' AND name = $name" in q:
                return iter([{"properties": self.props}]) if self.props is not None else iter([])
            if "RetrievalConfig" in q:
                return iter([])
            return iter([])
    class Drv:
        def __init__(self, props): self.props = props
        def session(self): return Sess(self.props)
    assert _config.validate_schema(Drv(["embedding", "assistant", "space", "status", "provenance_trust"]), vector_index="idx")["vector_filter_props"] == "ok"
    assert _config.validate_schema(Drv(["embedding"]), vector_index="idx")["vector_filter_props"] == "missing: ['assistant', 'provenance_trust', 'space', 'status']"
    assert _config.validate_schema(Drv(None), vector_index="idx")["vector_filter_props"] == "index not found"
```

Plus a seed test if `scripts/neo4j_seed.py` has any (check; if `tests/test_cli_smoke.py` or another file imports it, add a test that the DDL string the seed builds contains `WITH [f.assistant, f.space, f.status, f.provenance_trust]` — factor the DDL through `ai_memory.vector_index.build_create_index_ddl(VECTOR_INDEX, EXPECTED_VECTOR_FILTER_PROPS)` with `IF NOT EXISTS` inserted after the name; expose it as `seed_vector_index_ddl()` in the seed script so the test can call it).

- [ ] **Step 2: Run to see them fail** (`KeyError: 'vector_filter_props'`).

- [ ] **Step 3: Implement**

`_config.py`:
```python
from ai_memory.vector_index import DEFAULT_FILTER_PROPS as EXPECTED_VECTOR_FILTER_PROPS
```
(guard against import cycles: `vector_index` imports `ai_memory.embed` and `ai_memory.retrieval`, neither imports `_config` at module import time — verify with `venv/bin/python -c "import ai_memory._config"`; if a cycle appears, define the tuple in `_config.py` and have `vector_index.py` import it from there instead — one source of truth either way, and update the plan's statement of where it lives in your report.)

In `validate_schema`, inside the session block:
```python
        vi_name = vector_index or os.getenv("NEO4J_VECTOR_INDEX", "fact_embeddings")
        vi_rows = list(s.run("SHOW INDEXES YIELD name, type, properties WHERE type = 'VECTOR' AND name = $name RETURN properties", name=vi_name))
        if not vi_rows:
            out["vector_filter_props"] = "index not found"
        else:
            missing = sorted(set(EXPECTED_VECTOR_FILTER_PROPS) - set(vi_rows[0]["properties"] or []))
            out["vector_filter_props"] = "ok" if not missing else f"missing: {missing}"
```

Seed: replace the `CREATE VECTOR INDEX … OPTIONS …` string with `seed_vector_index_ddl()` = `build_create_index_ddl(VECTOR_INDEX, EXPECTED_VECTOR_FILTER_PROPS).replace(f"CREATE VECTOR INDEX `{VECTOR_INDEX}` ", f"CREATE VECTOR INDEX `{VECTOR_INDEX}` IF NOT EXISTS ", 1)`. After the existing "ready" print, run `SHOW INDEXES … properties` for `VECTOR_INDEX` and, if any expected filter property is missing, print `  Note: '{VECTOR_INDEX}' exists without filter properties — run: python scripts/neo4j_migrate_vector_filters.py --migrate`.

`verify_schema.py::main()`: print one informational line from `validate_schema(driver)["vector_filter_props"]` next to the existing `retrieval_config` line; do not gate the exit code on it.

- [ ] **Step 4: Run tests + full suite; commit**

```bash
git add ai_memory/_config.py scripts/neo4j_seed.py scripts/verify_schema.py tests/test_verify_schema.py
git commit -m "feat(schema): vector index filter properties as one constant; seed creates them, validate_schema/verify report them"
```

---

### Task 4: Migration CLI script

**Files:**
- Create: `scripts/neo4j_migrate_vector_filters.py`
- Create: `tests/test_migrate_vector_filters.py`

**Interfaces:**
- Consumes: `preflight`, `migrate`, `DEFAULT_FILTER_PROPS` (`ai_memory.vector_index`), `get_driver` (`ai_memory._config`).
- Produces: `main(argv) -> int`; flags: exactly one of `--preflight | --migrate`; `--dry-run` (with `--migrate`); `--index NAME` (default env `NEO4J_VECTOR_INDEX` or `fact_embeddings`); `--props a,b,c` (default the constant); `--json PATH`. Exit 0 when the report's `ok` is True, 1 otherwise, 2 on argument errors.

- [ ] **Step 1: Failing tests**

```python
# tests/test_migrate_vector_filters.py
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def test_migrate_cli_dispatch_and_exit_codes(monkeypatch, tmp_path, capsys):
    import neo4j_migrate_vector_filters as M
    seen = {}
    monkeypatch.setattr(M, "_open_driver", lambda: object())
    monkeypatch.setattr(M.VI, "preflight", lambda drv, index, props, **kw: seen.update(kind="preflight", index=index, props=tuple(props)) or {"ok": True, "index": index})
    monkeypatch.setattr(M.VI, "migrate", lambda drv, index, props, **kw: seen.update(kind="migrate", dry=kw.get("dry_run")) or {"ok": False, "index": index})
    assert M.main(["--preflight", "--index", "idx", "--props", "assistant,space"]) == 0
    assert seen == {"kind": "preflight", "index": "idx", "props": ("assistant", "space")}
    out = tmp_path / "r.json"
    assert M.main(["--migrate", "--dry-run", "--index", "idx", "--json", str(out)]) == 1
    assert seen["kind"] == "migrate" and seen["dry"] is True
    assert json.loads(out.read_text())["ok"] is False


def test_migrate_cli_requires_exactly_one_mode():
    import neo4j_migrate_vector_filters as M
    with pytest.raises(SystemExit):
        M.main([])
    with pytest.raises(SystemExit):
        M.main(["--preflight", "--migrate"])
```

- [ ] **Step 2: Run to see them fail** (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

```python
#!/usr/bin/env python3
"""Vector index filter-property migration (spec §4 "Index rebuild").

  --preflight   create <index>_v2 WITH the filter properties, measure population, probe, drop it
  --migrate     DROP <index>; CREATE it WITH the filter properties; wait ONLINE/100%; gate
  --dry-run     with --migrate: print the statements, run nothing

During --migrate, callers using db.index.vector.queryNodes(<index>) fail until the index is
ONLINE (measured ~1 s for 1,502 Facts); the library's search_vector degrades to lexical-only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_memory import vector_index as VI  # noqa: E402


def _open_driver():
    from ai_memory._config import get_driver
    return get_driver()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--migrate", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--index", default=os.getenv("NEO4J_VECTOR_INDEX", "fact_embeddings"))
    ap.add_argument("--props", default=",".join(VI.DEFAULT_FILTER_PROPS))
    ap.add_argument("--json", dest="json_out", default=None)
    a = ap.parse_args(argv)
    props = tuple(p.strip() for p in a.props.split(",") if p.strip())
    driver = _open_driver()
    try:
        if a.preflight:
            rep = VI.preflight(driver, a.index, props)
        else:
            rep = VI.migrate(driver, a.index, props, dry_run=a.dry_run)
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001, S110
            pass
    print(json.dumps(rep, indent=2, default=str))
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests + ruff; commit**

```bash
git add scripts/neo4j_migrate_vector_filters.py tests/test_migrate_vector_filters.py
git commit -m "feat(scripts): neo4j_migrate_vector_filters --preflight/--migrate/--dry-run with gate exit codes"
```

---

### Task 5: Harness `--subset`

**Files:**
- Modify: `ai_memory/eval/harness.py` (`main`: new flag; `filter_golden(golden, subset)` helper)
- Modify: `tests/test_harness.py`

**Interfaces:**
- Produces: `filter_golden(golden: list[dict], subset: str) -> list[dict]` with `subset ∈ {"all", "scoped", "unscoped"}` (`scoped` = non-empty `filters`); `--subset` flag default `all`; the results JSON gains `"subset": <value>` and `"queries": <count>`.

- [ ] **Step 1: Failing tests**

```python
def test_filter_golden_subsets():
    from ai_memory.eval.harness import filter_golden
    g = [{"query": "a", "filters": {}, "expect": []}, {"query": "b", "filters": {"assistant": "Weft"}, "expect": []}]
    assert filter_golden(g, "all") == g
    assert filter_golden(g, "scoped") == [g[1]]
    assert filter_golden(g, "unscoped") == [g[0]]
    import pytest
    with pytest.raises(ValueError):
        filter_golden(g, "nope")


def test_main_subset_flag_reaches_evaluate(monkeypatch, tmp_path):
    import json
    from ai_memory.eval import harness as H
    golden = tmp_path / "g.json"
    golden.write_text(json.dumps([{"query": "a", "filters": {}, "expect": []}, {"query": "b", "filters": {"space": "shared"}, "expect": []}]))
    seen = {}
    monkeypatch.setattr(H, "_open_driver", lambda ws: object())
    monkeypatch.setattr(H, "make_rankers", lambda drv, ws: {"r": lambda q, f, k: []})
    monkeypatch.setattr(H, "evaluate", lambda g, *a, **k: seen.update(n=len(g)) or {"per_ranker": {"r": {"exact5": 0, "ndcg5": 0, "recall5": 0, "mrr": 0, "unjudged": [], "judged_queries": 0, "candidates": 0}}, "pool_size": 0})
    out = tmp_path / "o.json"
    assert H.main(["--golden", str(golden), "--rankers", "r", "--subset", "scoped", "--json", str(out)]) == 0
    assert seen["n"] == 1
    assert json.loads(out.read_text())["subset"] == "scoped" and json.loads(out.read_text())["queries"] == 1
```

(Adapt the `_open_driver`/`make_rankers` monkeypatch targets to the actual names in `harness.main` — read it first; the existing label-mode tests show the pattern.)

- [ ] **Step 2: Run to see them fail**; **Step 3: implement** `filter_golden` and the flag; the results dict gets `res["subset"] = a.subset; res["queries"] = len(golden)` before printing/writing; **Step 4: run; commit**

```bash
git add ai_memory/eval/harness.py tests/test_harness.py
git commit -m "feat(eval): --subset all|scoped|unscoped for the two-split ship gate"
```

---

### Task 6: Docs

**Files:** `CHANGELOG.md`, `MIGRATION.md`, `README.md`

- CHANGELOG `### Added`: `ai_memory.vector_index` + `scripts/neo4j_migrate_vector_filters.py` (`--preflight`, `--migrate`, `--dry-run`, gate); harness `--subset`; `validate_schema()["vector_filter_props"]`. `### Fixed`: the SEARCH statement inlined the index name — Neo4j rejects a parameter inside the `VECTOR INDEX` clause, so the phase-1 in-index path could never run and always fell back (found by the phase-3 pre-flight). `### Changed`: `neo4j_seed.py` creates the vector index with filter properties `assistant, space, status, provenance_trust`.
- MIGRATION: new subsection "Vector index filter properties (phase 3)": why (in-index `SEARCH … WHERE` instead of over-fetch), the procedure (`--preflight` → read the report → schedule → `--migrate` → the gate; `--dry-run`), the window (callers of `db.index.vector.queryNodes` on that index fail until ONLINE; measured ~1 s for 1,502 Facts; the library degrades to lexical-only and its fallback latch re-probes within 600 s), what the gate checks, and that a failed gate leaves the new index in place for the operator to inspect. Note the predicate limits measured (equality and `IS NULL` only). Note that after migration the harness's `hybrid_search` ranker runs and `hybrid_fallback` remains callable for comparison.
- README: one line for the migration script where scripts are listed.
- Commit: `docs: phase 3 vector index migration, SEARCH index-name fix, harness --subset`.

---

### Task 7 (operational, controller-run): migrate the live index and gate

Run from the worktree with `AI_MEMORY_DIR=~/.grok` and `AI_MEMORY_GOLDEN=~/.ai-memory/golden/retrieval-2026-09.json`.

- [ ] **Step 1:** `venv/bin/python scripts/neo4j_migrate_vector_filters.py --preflight --json ~/.ai-memory/golden/phase3-preflight.json` — expected: `ok: true`, population ≈ 1 s, membership 1502/1502, all four probes ok (matches the hand-run pre-flight in Measured).
- [ ] **Step 2:** `--migrate --dry-run` — read the two statements. Then `--migrate --json ~/.ai-memory/golden/phase3-migrate.json` — expected `ok: true`; the window is the DROP→ONLINE interval (~1–2 s). `ai-memory stats` and `python scripts/verify_schema.py` afterwards: `vector_filter_props: ok`.
- [ ] **Step 3:** Harness on the new index, all three rankers, three subsets:
  ```bash
  for sub in all scoped unscoped; do venv/bin/python -m ai_memory.eval.harness --rankers legacy,hybrid_fallback,hybrid_search --subset $sub --json ~/.ai-memory/golden/phase3-$sub.json; done
  ```
  (the pre-migration 22ND3 warning must no longer appear; `hybrid_search` must not raise).
- [ ] **Step 4: gate** — `passes_ship_gate` with before = `hybrid_fallback` and after = `hybrid_search` on the `scoped` and `unscoped` JSONs (same vectors, so this isolates the SEARCH path):
  ```bash
  venv/bin/python - <<'EOF'
  import json
  from ai_memory.eval.harness import gate
  for sub in ("scoped", "unscoped", "all"):
      r = json.load(open(f"~/.ai-memory/golden/phase3-{sub}.json"))
      print(sub, "hybrid_search vs hybrid_fallback:", gate({"per_ranker": {"x": r["per_ranker"]["hybrid_fallback"]}}, {"per_ranker": {"x": r["per_ranker"]["hybrid_search"]}}, "x"))
  EOF
  ```
  Expected `True` on both splits (on this graph the fallback already returns k, so equal metrics are the likely result; a drop means the in-index path lost members — check `membership` in the migrate report first).
- [ ] **Step 5:** Live smoke of a scoped library call through the SEARCH path: `MemoryClient.search("who is Weft", assistant="Weft", k=5)` from the worktree venv — expect ≥ 1 hit and no fallback warning. Record the tables in the phase-4 plan preamble.

---

## Follow-on plans (not in this document)

- **Phase 4** — grok client port (`_fact_text` = verbatim `fact_embed_text` reading the config over Bolt; SEARCH with the inlined index name; provenance in the CAS statement) + `tests/test_retrieval_contract.py` over `tests/fixtures/embed_text_cases.json`.
- **Phase 5** — `ai_memory/wordindex.py` edge layer; z-score baselines and edge floor on `RetrievalConfig`; nightly `rule_version` cutover; retire `link_related_facts`/`_post_sync_tx`/grok `organize`.
- **Phase 6** — duplicate/supersede report.

## Self-review notes

- Spec coverage: §4 step 1 pre-flight (Measured + T2/T4/T7), step 2 window (T2 `migrate`, T6 docs, T7), step 3 gate (T2 gate + T5 subset + T7), step 4 seed/verify/`_config` (T3); §10.1–10.5 answered in Measured, 10.6 added; §9 phase-3 row ("indexed-node count, per-property probe, ship gate on unscoped and scoped queries with `hybrid_search`; Nova observed working" — the last item is the owner's observation after T7).
- Type consistency: `validate_index_name` (T1) used by T2 builders; `build_probe_cypher(name, where)` shape mirrors `build_search_cypher(where, index)`; `DEFAULT_FILTER_PROPS` (T2) = `EXPECTED_VECTOR_FILTER_PROPS` (T3) consumed by seed, verify, CLI default (T4); `preflight`/`migrate` report keys (`ok, membership, probes, population_s, show_properties, statements, dry_run`) consumed by T4's exit code and T7.
- Known deviation to flag to reviewers: the pre-flight already ran by hand (controller script); T7 step 1 re-runs it through the shipped script so the recorded report comes from reviewed code.
