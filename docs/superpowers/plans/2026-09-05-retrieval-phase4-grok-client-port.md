# Retrieval Phase 4 — Grok Client Port and Contract Test — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The grok Build TUI's single-file Bolt client embeds the same canonical text as the library, searches the vector index in-index with the same `SEARCH` shape and ranking rules, writes vector provenance in a compare-and-set statement, and a contract test proves the two implementations agree byte-for-byte where the spec says they must.

**Architecture:** `grok/skills/neo4j-memory/scripts/neo4j_memory.py` stays a single file with no import of `ai_memory` (it is copied into `~/.grok`). It gains verbatim copies of the library's pure functions — `normalize_ws`, `gram_tokens`, `strip_boilerplate`, `fact_embed_text`, `text_sha`, `build_filters`, `validate_index_name`, `strip_time_suffix`, `_chain`, `same_topic`, `_is_active`, `rank_adjust`, `build_embed_subquery`, `embed_params` — plus small Bolt readers for the `RetrievalConfig` singleton and the `SUPERSEDES` map. Its vector leg becomes the Cypher 25 `SEARCH` statement with set-only equality filters; its ranking pipeline becomes floor → RRF over the pool → `rank_adjust` → `[:limit]`; its three embedding write sites go through one CAS helper. `tests/test_retrieval_contract.py` imports both modules and asserts equality on shared fixtures. Rollout is copy-to-`~/.grok` plus a live smoke.

**Tech Stack:** Python 3.12 for the grok client (it already uses `X | None` at runtime and is not part of the `ai_memory` package); pytest for `tests/`; the grok file's own `unittest` suite (`test_neo4j_memory.py`, 47 tests) keeps passing.

**Spec:** `docs/superpowers/specs/2026-09-04-retrieval-index-design.md` §6 (client port and contract test), §3 (contract), §4 (canonical text, provenance, CAS), §9 phase-4 row ("contract test green; live smoke"). Phases 0–3 landed on this branch (through c5f6d47); the live index carries filter properties, so the client needs no fallback path (§6).

## Global Constraints

- The grok client imports nothing from `ai_memory`; copied functions are byte-identical in body to the library's (`ai_memory/embed.py`, `ai_memory/retrieval.py`), differing only in the module they live in. The contract test is the enforcement; a divergence is a test failure, not a judgment call.
- Prepared text (§4): `fact_embed_text(name, summary, key_points, content, boilerplate)`; the client reads `boilerplate` and `version` from `(:RetrievalConfig {id: "current"})` over Bolt before embedding; a client that cannot read it writes the text and skips the vector (never invents a version).
- Vector leg statement: `CYPHER 25\nMATCH (f:Fact)\nSEARCH f IN (VECTOR INDEX \`<name>\` FOR $embedding <WHERE …> LIMIT $k) SCORE AS s\n…`; the index name is an inlined identifier validated by `^[A-Za-z_][A-Za-z0-9_]*$`; filters are equality only, AND-joined, built only from inputs that are set; never `status`. The contract compares the statement through the `SCORE AS s` line after mapping the client's `$embedding`/`$k` to the library's `$vec`/`$pool`; RETURN lists may differ (the client returns `topic` as an extra).
- Ranking: RRF k = 60 over the pool (`max(4·limit, 16)`), then `rank_adjust` (drop inactive with an active same-topic hit; active before inactive; active exact-name boost; ties by name), then `[:limit]`; vector-only cosine floor 0.80 when the lexical leg is empty. Same-topic = SUPERSEDES chain or equal name after stripping the trailing time/date suffix (library `_TRAILING_SUFFIX`); the client's old `topic`-property collapse is removed.
- Hit dict keys: both implementations carry `name, teaser, key_points, assistant, status, space, score, via`; extras (`topic` on the client; `source`, `vec_score` on the library) are allowed and documented.
- Provenance written in the same statement as `embedding` via the copied `build_embed_subquery` / `embed_params`; CAS on all three text fields (`summary`, `key_points`, `content`) as read back before embedding; `embedding_text_sha = sha256(f"{version}\n{text}")[:16]`; model `nomic-embed-text`, dim 768 (the client's `EMBED_DIM`/`EMBED_MODEL` constants, which may be overridden by `.env.neo4j` — the provenance records the value actually used).
- No new dependency; the client keeps `neo4j`, `python-dotenv`, stdlib. Tests offline; `venv/bin/python -m pytest tests -q -p no:cacheprovider` from the worktree root, and the grok suite `venv/bin/python -m pytest grok/skills/neo4j-memory/scripts/test_neo4j_memory.py -q -p no:cacheprovider` (it is unittest-style; pytest collects it). ruff: `venv/bin/ruff check grok/skills/neo4j-memory/scripts tests/test_retrieval_contract.py` — record before/after on the grok files; add none.
- Commit after every task with the trailers `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` and `Claude-Session: https://claude.ai/code/session_01RTWNMeQjJ5Zg5FgAhDhsRZ`. Never push. Never `git stash`.

## File Structure

| file | responsibility |
|---|---|
| `grok/skills/neo4j-memory/scripts/neo4j_memory.py` (modify) | copies of the pure functions; `_load_retrieval_config`, `_load_supersedes`; SEARCH vector leg with filters; `rank_pipeline`; `_embed_fact_cas` used by `cmd_write`, shared write, `cmd_embed`; `search --assistant/--space/--trust` |
| `grok/skills/neo4j-memory/scripts/test_neo4j_memory.py` (modify) | updated/added unit tests |
| `tests/test_retrieval_contract.py` (create) | cross-implementation contract |
| `tests/fixtures/embed_text_cases.json` (existing, phase 2) | shared fixtures |
| `grok/README.md`, `grok/skills/neo4j-memory/SKILL.md`, `CHANGELOG.md`, `MIGRATION.md` (modify) | docs |

---

### Task 1: Canonical text, sha and the config reader in the grok client

**Files:**
- Modify: `grok/skills/neo4j-memory/scripts/neo4j_memory.py` (replace `_fact_text` at ~line 179; add copies near it)
- Modify: `grok/skills/neo4j-memory/scripts/test_neo4j_memory.py` (`FactText` class)

**Interfaces:**
- Produces (all module-level in `neo4j_memory.py`): `normalize_ws`, `gram_tokens`, `strip_boilerplate`, `fact_embed_text`, `text_sha` — copied verbatim from `ai_memory/embed.py` (function bodies identical; keep the same names and signatures; the client already has `re` imported and needs `hashlib` — check); `EMBED_CHARS = 2000` replaces `FACT_EMBED_CHARS` (keep `FACT_EMBED_CHARS = EMBED_CHARS` as an alias so old references and tests still resolve); `_fact_text(name, summary=None, content=None, key_points=None, boilerplate=()) -> str` becomes a thin wrapper: `return fact_embed_text(name, summary, key_points, content, boilerplate)`; `_load_retrieval_config(session) -> dict | None` returning `{"version": int, "boilerplate": frozenset[str]}` from `MATCH (c:RetrievalConfig {id: 'current'}) RETURN c.version AS version, c.boilerplate AS boilerplate` (None when no row or version null; any `_BOLT_FAIL` → None).

- [ ] **Step 1: Write the failing tests** (replace `FactText` in `test_neo4j_memory.py`)

```python
class FactText(unittest.TestCase):
    def test_canonical_order_and_dash_points(self):
        self.assertEqual(nm._fact_text("Name", "Sum", "Body", ["p1", "p2"]), "Name Sum - p1 - p2 Body")

    def test_cap_2000(self):
        self.assertEqual(len(nm._fact_text("n", "x" * 5000)), nm.EMBED_CHARS)
        self.assertEqual(nm.FACT_EMBED_CHARS, nm.EMBED_CHARS)

    def test_boilerplate_runs_removed(self):
        grams = {"topic selection gap filling", "selection gap filling memory"}
        self.assertEqual(nm._fact_text("Fact", "Topic Selection Gap Filling memory. VPIN matters.", None, None, grams),
                         "Fact VPIN matters.")

    def test_text_sha_versioned(self):
        self.assertEqual(nm.text_sha("t", 1), hashlib.sha256(b"1\nt").hexdigest()[:16])
        self.assertNotEqual(nm.text_sha("t", 1), nm.text_sha("t", 2))


class RetrievalConfigReader(unittest.TestCase):
    class _S:
        def __init__(self, rows, raise_=None): self.rows, self.raise_ = rows, raise_
        def run(self, q, **kw):
            if self.raise_: raise self.raise_
            rows = self.rows
            class R:
                def single(self_inner): return rows[0] if rows else None
            return R()

    def test_reads_version_and_grams(self):
        cfg = nm._load_retrieval_config(self._S([{"version": 3, "boilerplate": ["a b c d"]}]))
        self.assertEqual(cfg, {"version": 3, "boilerplate": frozenset({"a b c d"})})

    def test_missing_or_failed_is_none(self):
        self.assertIsNone(nm._load_retrieval_config(self._S([])))
        self.assertIsNone(nm._load_retrieval_config(self._S([{"version": None, "boilerplate": []}])))
        self.assertIsNone(nm._load_retrieval_config(self._S([], raise_=DriverError("down"))))
```

Add `import hashlib` to the test module.

- [ ] **Step 2: Run to verify they fail**

Run: `venv/bin/python -m pytest grok/skills/neo4j-memory/scripts/test_neo4j_memory.py -q -p no:cacheprovider -k "FactText or RetrievalConfigReader"`
Expected: FAIL — old `_fact_text` output `"Name Sum Body p1 p2"`; `AttributeError: text_sha`.

- [ ] **Step 3: Implement** — copy the function bodies of `normalize_ws`, `gram_tokens`, `_grams_of` (needed by nothing here; skip), `strip_boilerplate`, `fact_embed_text`, `text_sha` from `ai_memory/embed.py` into the client (same names; the client already has `from __future__ import annotations`; the `_TOKEN` regex must be copied too — name it `_TOKEN` as in the library). Replace `_fact_text` with the wrapper. Add:

```python
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
```

Keep every call site of `_fact_text` compiling (they pass 4 positional args today; Task 4 changes them).

- [ ] **Step 4: Run the grok suite** — `venv/bin/python -m pytest grok/skills/neo4j-memory/scripts/test_neo4j_memory.py -q -p no:cacheprovider` → all pass.

- [ ] **Step 5: Commit**

```bash
git add grok/skills/neo4j-memory/scripts/neo4j_memory.py grok/skills/neo4j-memory/scripts/test_neo4j_memory.py
git commit -m "feat(grok): canonical fact text, boilerplate stripping, versioned sha, RetrievalConfig reader (verbatim library copies)"
```

---

### Task 2: In-index SEARCH vector leg with set-only filters

**Files:**
- Modify: `neo4j_memory.py` (`search_vector`, `search_fulltext`, `_hit_from_record`, `_vector_leg`, `_fulltext_leg`, `search_memories`, `cmd_search`, argparse `search`)
- Modify: `test_neo4j_memory.py`

**Interfaces:**
- Produces: `validate_index_name(name) -> str` and `build_filters(assistant, space, trust) -> tuple[str, dict]` (verbatim library copies from `ai_memory/retrieval.py`); `build_vector_search_cypher(index: str, where: str) -> str` returning
  ```
  CYPHER 25
  MATCH (f:Fact)
  SEARCH f IN (VECTOR INDEX `<index>` FOR $embedding <WHERE where >LIMIT $k) SCORE AS s
  RETURN f.name AS name, coalesce(f.summary, f.content) AS text, f.assistant AS assistant,
         f.key_points AS key_points, f.status AS status, f.space AS space, f.topic AS topic, s AS score
  ORDER BY s DESC
  ```
  `search_vector(session, embedding, index, limit, bolt_timeout=None, *, assistant=None, space=None, trust=None)`; `search_fulltext(...)` gains the same three keyword filters applied as a `WHERE` after `YIELD node, score` (`node.` prefix — rewrite the copied clauses' `f.` to `node.` or alias `WITH node AS f, score`; choose the alias so the copied `build_filters` text is used unchanged); `_hit_from_record` adds `"space": r["space"]`; `search_memories(..., assistant=None, space=None, trust=None)` threads them to both legs; `cmd_search` reads `--assistant/--space/--trust`.

- [ ] **Step 1: Failing tests**

```python
class VectorSearchCypher(unittest.TestCase):
    def test_shape_and_filters(self):
        where, params = nm.build_filters("Weft", None, None)
        self.assertEqual((where, params), ("f.assistant = $assistant", {"assistant": "Weft"}))
        c = nm.build_vector_search_cypher("factEmbeddingIndex", where)
        self.assertTrue(c.startswith("CYPHER 25\nMATCH (f:Fact)\n"))
        self.assertIn("SEARCH f IN (VECTOR INDEX `factEmbeddingIndex` FOR $embedding WHERE f.assistant = $assistant LIMIT $k) SCORE AS s", c)
        self.assertIn("f.space AS space", c)
        self.assertNotIn("$index", c)
        c0 = nm.build_vector_search_cypher("idx", "")
        self.assertIn("FOR $embedding LIMIT $k) SCORE AS s", c0)
        with self.assertRaises(ValueError):
            nm.build_vector_search_cypher("bad name", "")

    def test_filters_never_status_and_and_joined(self):
        where, params = nm.build_filters("Grok", "shared", "trusted")
        self.assertEqual(where, "f.assistant = $assistant AND f.space = $space AND f.provenance_trust = $trust")
        self.assertEqual(params, {"assistant": "Grok", "space": "shared", "trust": "trusted"})
        self.assertNotIn("status", where)


class SearchVectorRunsSearchStatement(unittest.TestCase):
    def test_statement_and_params(self):
        seen = {}
        class S:
            def run(self, q, **kw):
                seen["q"], seen["kw"] = (q.text if hasattr(q, "text") else q), kw
                return []
        hits = nm.search_vector(S(), [0.1] * 3, "idx", 7, assistant="Weft")
        self.assertEqual(hits, [])
        self.assertIn("SEARCH f IN (VECTOR INDEX `idx` FOR $embedding WHERE f.assistant = $assistant LIMIT $k)", seen["q"])
        self.assertEqual(seen["kw"]["k"], 7)
        self.assertEqual(seen["kw"]["assistant"], "Weft")
        self.assertNotIn("index", seen["kw"])
```

Also update `_hit_from_record` tests/fakes in the file (grep `"text":`) to supply a `space` key, and add a `search` argparse test: `nm.build_parser().parse_args(["search", "q", "--assistant", "Weft", "--space", "shared"])` yields those attributes (find how the existing `Parser` tests build the parser).

- [ ] **Step 2: Run to verify failure.** **Step 3: Implement** per the interfaces (copy `validate_index_name` and `build_filters` verbatim; `_query(...)` keeps the timeout wrapping; the fulltext statement becomes `CALL db.index.fulltext.queryNodes($index, $q) YIELD node, score WITH node AS f, score <WHERE where> RETURN f.name AS name, coalesce(f.summary, f.content) AS text, f.assistant AS assistant, f.key_points AS key_points, f.status AS status, f.space AS space, f.topic AS topic, score ORDER BY score DESC LIMIT $limit`). No fallback: a `_BOLT_FAIL` from the vector leg still returns `([], False)` (fulltext-only), as today. **Step 4: grok suite green.** **Step 5: Commit** `feat(grok): in-index SEARCH vector leg with set-only filters; space in hits; search --assistant/--space/--trust`.

---

### Task 3: Ranking pipeline = library rules

**Files:**
- Modify: `neo4j_memory.py` (`_demote_inactive`, `_collapse_superseded_siblings`, `_rank_hits`, `_finish_hybrid`, `merge_rrf`, `search_memories`)
- Modify: `test_neo4j_memory.py` (`FinishHybridFloor`, `HybridParallel`, `FulltextKpFuse` adaptations; new `RankPipeline`)

**Interfaces:**
- Produces: verbatim copies `_TRAILING_SUFFIX`, `strip_time_suffix`, `_chain`, `same_topic`, `_is_active`, `rank_adjust(hits, query, supersedes=None)` from `ai_memory/retrieval.py`; `INACTIVE = (STATUS_SUPERSEDED, STATUS_REMOVED)`; `_load_supersedes(session) -> dict` (`MATCH (n:Fact)-[:SUPERSEDES]->(o:Fact) RETURN n.name AS n, o.name AS o`; `{}` on failure); `apply_vector_only_floor(vec_hits, lexical_hits)` — verbatim copy, but the client's vector hits carry the cosine in `score` (not `vec_score`): set `h["vec_score"] = h["score"]` in `search_vector`'s hit construction so the copied function applies unchanged; `merge_rrf(ranked_lists, limit=None, k=RRF_K)` — `limit=None` means no slice (fuse the whole pool); `rank_pipeline(ft, vec, vec_ok, query, supersedes, limit) -> tuple[list[dict], str]` = `vec = apply_vector_only_floor(vec, ft) if vec_ok else []`; legs = those non-empty of `("ft", ft), ("vec", vec)`; `fused = merge_rrf(legs)`; `ranked = rank_adjust(fused, query, supersedes)[:limit]`; backend `"hybrid"` when vec_ok else `"fulltext"`; rounding of `score` to 4 places kept.
- `_rank_hits` and `_collapse_superseded_siblings` are deleted; `_demote_inactive` is deleted (subsumed). `search_memories` loads `supersedes` once per call (`_load_supersedes` inside a session; `{}` on failure) and routes `fulltext`/`vector`/`hybrid` modes through `rank_pipeline` (single-leg modes pass the other leg empty).

- [ ] **Step 1: Failing tests**

```python
class RankPipeline(unittest.TestCase):
    def _h(self, name, score, status=None, via="ft"):
        return {"name": name, "teaser": "", "key_points": [], "assistant": None, "status": status,
                "space": None, "score": score, "via": via, "topic": None, "vec_score": score if via == "vec" else None}

    def test_superseded_sibling_dropped_when_active_present(self):
        ft = [self._h("Shared — x — 2026-08-30", 5.0, "superseded"), self._h("Shared — x — 2026-08-30 #2", 4.0)]
        hits, backend = nm.rank_pipeline(ft, [], False, "", {}, 5)
        self.assertEqual([h["name"] for h in hits], ["Shared — x — 2026-08-30 #2"])
        self.assertEqual(backend, "fulltext")

    def test_supersedes_chain_collapses(self):
        ft = [self._h("Old name", 5.0, "superseded"), self._h("New name", 4.0)]
        hits, _ = nm.rank_pipeline(ft, [], False, "", {"New name": "Old name"}, 5)
        self.assertEqual([h["name"] for h in hits], ["New name"])

    def test_exact_name_boost_active_only(self):
        ft = [self._h("Other", 5.0), self._h("Weft — identity", 1.0)]
        hits, _ = nm.rank_pipeline(ft, [], False, "weft — identity", {}, 5)
        self.assertEqual(hits[0]["name"], "Weft — identity")
        ft = [self._h("Other", 5.0), self._h("Gone", 1.0, "removed")]
        hits, _ = nm.rank_pipeline(ft, [], False, "gone", {}, 5)
        self.assertEqual(hits[0]["name"], "Other")

    def test_vector_only_floor_then_rank(self):
        vec = [self._h("Strong", 0.91, via="vec"), self._h("Weak", 0.5, via="vec")]
        hits, backend = nm.rank_pipeline([], vec, True, "", {}, 5)
        self.assertEqual([h["name"] for h in hits], ["Strong"])
        self.assertEqual(backend, "hybrid")

    def test_pool_fused_before_slice(self):
        ft = [self._h("A", 5.0, "superseded"), self._h("B", 4.0), self._h("C", 3.0)]
        hits, _ = nm.rank_pipeline(ft, [], False, "", {}, 2)
        self.assertEqual([h["name"] for h in hits], ["B", "C"])   # inactive A sinks; slice happens after ranking
```

Adapt `FinishHybridFloor`/`HybridParallel`/`FulltextKpFuse` to call `rank_pipeline` (or `search_memories`) with the new shapes; keep their assertions' intent (floor 0.80, hybrid when both legs, fulltext when Ollama down).

- [ ] **Step 2: Run to fail.** **Step 3: Implement.** **Step 4: Both suites green.** **Step 5: Commit** `feat(grok): ranking = library rules (RRF over pool, rank_adjust, vector-only floor) via rank_pipeline`.

---

### Task 4: Provenance-carrying CAS embed write for all three write sites

**Files:**
- Modify: `neo4j_memory.py` (`_set_embedding` → `_embed_fact_cas`; `cmd_write`, `cmd_write_shared`, `cmd_embed`)
- Modify: `test_neo4j_memory.py`

**Interfaces:**
- Produces: verbatim copies `EMBED_PARAM_NAMES`, `_cas_default`, `build_embed_subquery`, `embed_params` from `ai_memory/embed.py`; `_read_fact_text(session, name) -> dict | None` (same query as the library's `read_fact_text`); `_embed_fact_cas(session, name, cfg_dict, retrieval_cfg, *, timeout) -> str` returning `"embedded" | "cas_skipped" | "embed_failed" | "missing" | "no_config"`: reads the text fields, builds `fact_embed_text(..., retrieval_cfg["boilerplate"])`, returns `"embed_failed"` for empty text, embeds via `ollama_embed(text, cfg_dict, timeout=timeout)`, writes `MATCH (f:Fact {name:$name})\n<build_embed_subquery(["summary","key_points","content"])>\nRETURN embedded` with `embed_params(vec, text_sha(text, version), version, cas={...})` — with `embedding_model`/`embedding_dim` taken from `cfg_dict["embed_model"]`/`cfg_dict["embed_dim"]` (the values actually used) instead of the copied constants (edit the copied `embed_params` call site, not the function: pass through a post-hoc override `params["embedding_model"] = cfg["embed_model"]; params["embedding_dim"] = cfg["embed_dim"]`). When `retrieval_cfg is None` → `"no_config"` and no Ollama call.
- `cmd_write` and `cmd_write_shared`: after the text MERGE and `_set_words`, `rc = _load_retrieval_config(s)`; `status = _embed_fact_cas(s, name, cfg, rc, timeout=WRITE_EMBED_TIMEOUT)`; `embedded = status == "embedded"`; the trailing message says `(no embedding: no RetrievalConfig)` / `(no embedding: ollama down)` / `(no embedding: cas_skipped — concurrent edit)` accordingly.
- `cmd_embed`: selects Facts `WHERE f.embedding IS NULL OR f.embedding_text_sha IS NULL` (missing or foreign vectors), reports `embedded/cas_skipped/failed/skipped` counts; aborts if `_load_retrieval_config` is None with the message `embed aborted: no RetrievalConfig — run ai-memory embed --all from the library first`; the consecutive-failure abort logic stays.

- [ ] **Step 1: Failing tests** — a fake session recording `run` calls: (a) `_embed_fact_cas` on a fact row → two calls (read, CAS write) with `$cas_summary/$cas_key_points/$cas_content` params, `embedding_text_sha == text_sha(fact_embed_text(...), version)`, `embedding_model == cfg["embed_model"]`, `boilerplate_version == version`, returns `"embedded"` when the write returns `{"embedded": 1}` and `"cas_skipped"` on 0; (b) `retrieval_cfg=None` → `"no_config"` and zero calls; (c) `ollama_embed` patched to return None → `"embed_failed"` with one call (the read). Plus `cmd_embed` selection query contains `embedding_text_sha IS NULL`.

- [ ] **Step 2: fail; Step 3: implement (patch `nm.ollama_embed` in tests); Step 4: both suites green; Step 5: Commit** `feat(grok): CAS embed write with provenance for write/shared-write/embed; backfill covers foreign vectors`.

---

### Task 5: Contract test

**Files:**
- Create: `tests/test_retrieval_contract.py`

- [ ] **Step 1: Write the test** (this task's deliverable is the test; it should pass against Tasks 1–4 — if it fails, the divergence is a defect in the client port and is fixed in the client, not in the test)

```python
"""Spec §6 contract: the library (ai_memory) and the grok client (grok/skills/neo4j-memory) agree."""
from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

from ai_memory import embed as LE
from ai_memory import retrieval as LR

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "grok" / "skills" / "neo4j-memory" / "scripts" / "neo4j_memory.py"
FIX = ROOT / "tests" / "fixtures" / "embed_text_cases.json"


@pytest.fixture(scope="module")
def nm():
    spec = importlib.util.spec_from_file_location("grok_neo4j_memory", CLIENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_prepared_text_byte_equal_on_fixtures(nm):
    for c in json.loads(FIX.read_text(encoding="utf-8")):
        bp = frozenset(c["boilerplate"])
        lib = LE.fact_embed_text(c["name"], c["summary"], c["key_points"], c["content"], bp)
        cli = nm.fact_embed_text(c["name"], c["summary"], c["key_points"], c["content"], bp)
        assert lib == cli == c["expected"], c["id"]


@pytest.mark.parametrize("text,grams", [
    ("keep0 a, b c d e keep1", {"a b c d", "b c d e"}),
    ("start w x y z zz!", {"w x y z", "x y z zz"}),
    ("a b c d e keep", {"a b c d", "b c d e"}),
    ("Ünïcode — a b c d e — tail", {"a b c d", "b c d e"}),
    ("the probability of informed trading matters", {"probability of informed trading"}),
])
def test_strip_boilerplate_parity(nm, text, grams):
    assert LE.strip_boilerplate(text, grams) == nm.strip_boilerplate(text, grams)


def test_text_sha_parity(nm):
    assert LE.text_sha("N S - k", 7) == nm.text_sha("N S - k", 7)


def _search_clause(cypher: str) -> str:
    return "\n".join(line for line in cypher.splitlines() if not line.startswith(("RETURN", "ORDER BY")) and "AS name" not in line)


def test_vector_leg_cypher_identical_modulo_param_names(nm):
    for where in ("", "f.assistant = $assistant", "f.assistant = $assistant AND f.space = $space"):
        lib = _search_clause(LR.build_search_cypher(where, "factEmbeddingIndex"))
        cli = _search_clause(nm.build_vector_search_cypher("factEmbeddingIndex", where))
        cli = cli.replace("$embedding", "$vec").replace("$k)", "$pool)")
        assert lib == cli, where
    assert LR.build_filters("Grok", "shared", "trusted") == nm.build_filters("Grok", "shared", "trusted")


def _h(name, score, status=None, via="ft"):
    return {"name": name, "teaser": "", "key_points": [], "assistant": None, "status": status,
            "space": None, "score": score, "via": via, "vec_score": score if via == "vec" else None}


CASES = {
    "plain": (["A", "B", "C"], ["C", "A", "D"], "", {}),
    "superseded-suffix": (["Shared — x — 2026-08-30", "Shared — x — 2026-08-30 #2"], [], "", {}),
    "supersedes-chain": (["Old", "New"], ["Old"], "", {"New": "Old"}),
    "exact-name": (["Other", "Weft — identity"], ["Other"], "weft — identity", {}),
    "exact-tie": (["B", "A"], ["A", "B"], "", {}),
}
INACTIVE = {"Shared — x — 2026-08-30", "Old"}


@pytest.mark.parametrize("case", list(CASES))
def test_fused_and_ranked_order_identical(nm, case):
    ft_names, vec_names, query, supersedes = CASES[case]
    ft = [_h(n, 10.0 - i, "superseded" if n in INACTIVE else None) for i, n in enumerate(ft_names)]
    vec = [_h(n, 0.95 - i * 0.01, "superseded" if n in INACTIVE else None, "vec") for i, n in enumerate(vec_names)]
    legs = [(o, l) for o, l in (("ft", ft), ("vec", vec)) if l]
    lib = [h["name"] for h in LR.rank_adjust(LR.fuse_rrf(legs), query, supersedes)[:5]]
    cli = [h["name"] for h in nm.rank_pipeline(ft, vec, bool(vec), query, supersedes, 5)[0]]
    assert lib == cli, case


def test_vector_only_floor_identical(nm):
    vec = [_h("Strong", 0.91, via="vec"), _h("Weak", 0.5, via="vec")]
    lib = [h["name"] for h in LR.rank_adjust(LR.fuse_rrf([("vec", LR.apply_vector_only_floor(vec, []))]), "", {})]
    cli = [h["name"] for h in nm.rank_pipeline([], vec, True, "", {}, 5)[0]]
    assert lib == cli == ["Strong"]


SPEC_KEYS = {"name", "teaser", "key_points", "assistant", "status", "space", "score", "via"}


def test_hit_dict_keys(nm):
    class R(dict):
        def keys(self): return dict.keys(self)
    rec = R(name="N", text="t", assistant="Grok", key_points=["k"], status=None, space="shared", topic=None, score=0.5)
    cli_hit = nm._hit_from_record(rec)
    assert SPEC_KEYS <= set(cli_hit)
    lib_row = {"name": "N", "text": "t", "key_points": ["k"], "assistant": "Grok", "status": None, "space": "shared", "s": 0.5}
    from ai_memory.search import _rows_to_hits
    assert SPEC_KEYS <= set(_rows_to_hits([lib_row], "vec")[0])


def test_cas_subquery_identical(nm):
    for fields in (["content"], ["summary", "key_points"], ["summary", "key_points", "content"]):
        assert LE.build_embed_subquery(fields) == nm.build_embed_subquery(fields)
        assert LE.build_embed_subquery(fields, keep_prev=True) == nm.build_embed_subquery(fields, keep_prev=True)
    assert LE.embed_params([0.1], "sha", 3, cas={"summary": None, "key_points": "abc", "content": ""}) == \
        nm.embed_params([0.1], "sha", 3, cas={"summary": None, "key_points": "abc", "content": ""})
```

(Drop the `monkeypatch_module=None` parameter from the fixture — it is a leftover; the fixture takes no arguments. If `_rows_to_hits` needs a record with `__getitem__` rather than a dict, wrap `lib_row` in the same `R` class; if the client's `_hit_from_record` uses `r.keys()`, the `R` dict subclass already provides it.)

- [ ] **Step 2: Run** — `venv/bin/python -m pytest tests/test_retrieval_contract.py -q -p no:cacheprovider` → all pass; any failure is fixed in the client (Tasks 1–4 code), never by loosening the test. Then the full suite and the grok suite.

- [ ] **Step 3: Commit** `test: retrieval contract between ai_memory and the grok client (text, cypher, ranking, keys, CAS)`.

---

### Task 6: Docs

- `grok/skills/neo4j-memory/SKILL.md` "Search (always hybrid)": the vector leg is now the Cypher 25 `SEARCH` clause with in-index filters (`--assistant`, `--space`, `--trust`) on the migrated `factEmbeddingIndex`; ranking = RRF over the pool → superseded/removed sink with same-topic collapse (trailing date suffix or SUPERSEDES) → active exact-name boost; vector-only floor 0.80 unchanged; Ollama down or index error → fulltext only (no over-fetch fallback in the client). "Write": stores `embedding` with `embedding_model`, `embedding_dim`, `embedding_text_sha`, `boilerplate_version` in one compare-and-set statement from the canonical text (name, summary, key points, content; boilerplate removed per `RetrievalConfig`); if the config node is missing the write stores text only. "embed": fills missing **or foreign** vectors (no sha) and requires `RetrievalConfig`.
- `grok/README.md`: same three facts in the search/write/embed paragraphs; rollout line "copy `neo4j_memory.py` and `test_neo4j_memory.py` to `~/.grok/skills/neo4j-memory/scripts/`".
- `CHANGELOG.md` `### Changed` (grok): text, search, ranking, provenance; `### Added`: `tests/test_retrieval_contract.py`.
- `MIGRATION.md`: grok section — requires the phase-3 migrated index (the client has no fallback; on an un-migrated index the vector leg errors and search degrades to fulltext-only); redeploy by copying the two files; run `embed` once to give Grok-written Facts provenance.
- Commit `docs: grok client on the retrieval contract (SEARCH filters, canonical text, provenance), contract test`.

---

### Task 7 (operational, controller-run): deploy and live smoke

- [ ] Run both suites once more from the worktree. Copy `grok/skills/neo4j-memory/scripts/neo4j_memory.py` and `test_neo4j_memory.py` to `~/.grok/skills/neo4j-memory/scripts/` (the live copy was identical to the repo at phase start — re-diff first; if it drifted, stop and diff before overwriting).
- [ ] Live smoke (read-only): `python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py search "who is Weft" --max 5 --assistant Weft` → hits tagged `ft+vec`/`vec`, stderr backend `hybrid`; `… search "kraken maker reserve balance guard" --max 5` unscoped; `… embed --dry-run` → reports how many Facts lack a vector or sha (expect the Grok-written ones: ~65 minus those the phase-2 backfill already canonicalised — the backfill gave every Fact a sha, so expect 0–1).
- [ ] Optional write smoke only if the owner wants a test Fact in the live graph — default: skip; the CAS path is covered by unit tests and by the phase-2 library run.
- [ ] Record results in the ledger and the final message; update memory (`reference_ai_memory_repo.md`).

## Follow-on plans (not in this document)

- **Phase 5** — `ai_memory/wordindex.py` edge layer; z-score baselines and edge floor on `RetrievalConfig`; nightly `rule_version` cutover; retire `link_related_facts`/`_post_sync_tx`/grok `organize`.
- **Phase 6** — duplicate/supersede report.

## Self-review notes

- Spec coverage §6: SEARCH shape with set-only filters and no status predicate (T2); `merge_rrf` exact-tie-then-name (kept; contract exact-tie case, T5); `_rank_hits` = sink, sibling collapse, active-only exact-name boost (T3 via verbatim `rank_adjust`); `_fact_text` = verbatim `fact_embed_text` incl. boilerplate removal (T1); provenance in the same CAS statement (T4); `fact_key_points` swallow-and-degrade kept (T2 leaves `_fulltext_leg`'s try/except); no fallback (T2); contract test with the four assertion groups (T5); rollout (T7).
- Type consistency: `fact_embed_text(name, summary, key_points, content, boilerplate)` order is the library's; `_fact_text(name, summary, content, key_points, boilerplate)` keeps the client's historical order and maps; `rank_pipeline(ft, vec, vec_ok, query, supersedes, limit) -> (hits, backend)` used by T3 tests, `search_memories`, and T5; `build_vector_search_cypher(index, where)` param names `$embedding`/`$k` mapped to `$vec`/`$pool` in T5; hit `vec_score` added on client vector hits so the copied floor function works unchanged.
- Known deviation to flag: the client keeps its text MERGE and the CAS embed as two statements in one session (the library's `write()` folds them into one statement); spec §6 requires only that provenance shares the CAS statement with `embedding`, which holds.
