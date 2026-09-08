# Retrieval Phase 6 — Duplicate / Supersede Report — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the owner a reviewable report of same-topic re-learnings (trailing time/date suffix twins and cosine ≥ 0.95 near-copies) with a suggested keeper per group, plus an explicit, one-pair-at-a-time `supersede` command that applies the shared-space status mechanism. No automatic merges, no automatic supersedes.

**Architecture:** `ai_memory/duplicates.py` holds the pure grouping/suggestion logic (suffix groups keyed by `strip_time_suffix`, canonical pairs, keeper choice by the newest timestamp, `handled` detection via existing `status`/`SUPERSEDES`), the I/O that finds near-copies with one in-index `SEARCH` per Fact (exact cosine recomputed server-side with the `2·x−1` de-normalisation), the report builder, markdown/JSON renderers, and `supersede_fact` (the library twin of the grok shared-write's supersede statement, with cycle and double-supersede guards). `scripts/cli.py` gains `duplicates` (report) and `supersede` (apply one reviewed pair, or reviewed rows from a decisions file, only with `--apply`). The grok client is unchanged.

**Tech Stack:** Python ≥ 3.9 (no numpy needed); Neo4j 2026.04 (Cypher 25 `SEARCH`, `vector.similarity.cosine`).

**Spec:** `docs/superpowers/specs/2026-09-04-retrieval-index-design.md` — §7.6 (duplicates: detection rule, "listed by a report for a supersede pass using the shared-space status mechanism. No automatic merges."), §3 rule 2 (same topic = SUPERSEDES chain or same name after the trailing suffix), §9 phase-6 row ("owner review; no automatic merges"). Phases 0–5 landed on this branch (through 2695e8a; live cutover done).

## Measured (live, 2026-09-05)

- 1,502 Facts; `status`: 1,453 null, 45 `active`, 4 `superseded`; 4 `SUPERSEDES` edges; `created_at` on 1,434 Facts (DateTime), `updated_at` on some; the shared-space mechanism (grok client `cmd_write_shared`, action `supersede`) does: `SET old.status='superseded', old.superseded_at=$now, old.updated_at=$now MERGE (neu)-[r:SUPERSEDES]->(old) SET r.at=$now, r.by=$assistant`.
- Spec survey (2026-09-04): 58 suffix name groups (118 Facts, 62 pairs) and 43 further pairs at cosine ≥ 0.95.
- `vector.similarity.cosine` and the vector index `SCORE` are both `(1+cos)/2` on this server; the code recomputes exact cosine as `2 * vector.similarity.cosine($vec, g.embedding) - 1` and never trusts the index score scale.
- Existing helpers to reuse: `ai_memory.retrieval.strip_time_suffix`, `_chain`, `validate_index_name`, `_TRAILING_SUFFIX`; `ai_memory.search.load_supersedes` (`{new: old}`); the index name resolution used by `ai_memory/search.py` (env/`.env.neo4j`, default `factEmbeddingIndex`); `ai_memory.wordindex.DUP_COS = 0.95`.

## Global Constraints

- `requires-python = ">=3.9"`; `from __future__ import annotations`; `X | None` only in annotations.
- Detection rule (§7.6): two Facts are duplicates when `strip_time_suffix(a).lower() == strip_time_suffix(b).lower()`, or exact cosine ≥ `DUP_COS` (0.95). A group's `signals` lists which applied (`"suffix"`, `"cosine"`, or both).
- `handled` = the group already has at most one member that is neither `superseded` nor `removed`, or every non-keeper member is the target of a `SUPERSEDES` edge from another member. Handled groups are omitted unless `--include-handled`.
- Keeper suggestion: the member with the newest time key, where the time key is (1) the trailing date/time parsed from the name when present, else (2) `updated_at`, else (3) `created_at`; ties → the member with `status='active'`; remaining ties → lexicographically last name. Groups whose members span different `assistant` values or different `space` values are marked `needs_owner_decision` and get NO suggested command (the owner decides whose Fact wins).
- The report never writes to the graph. `supersede` writes only with `--apply`; without it, it prints the plan and exits 0. It refuses: unknown names, `new == old`, `old` already superseded by a different Fact, and any pair where `old` is reachable from `new` upward through existing `SUPERSEDES` chains (would create a cycle). Statement mirrors the grok shared-write but without the `space`/`status='active'` precondition on `old` (library Facts have null status): `MATCH (neu:Fact {name:$neu}), (old:Fact {name:$old}) WHERE neu <> old SET old.status = 'superseded', old.superseded_at = $now, old.updated_at = $now, neu.status = coalesce(neu.status, 'active') MERGE (neu)-[r:SUPERSEDES]->(old) SET r.at = $now, r.by = $by RETURN old.name AS old`.
- Near-copy search: one `SEARCH` per embedded Fact, `LIMIT $pool` with `pool = k + 1` (k default 3), exact cosine returned by the query; pairs canonicalised `(a < b)` and de-duplicated; self excluded.
- Tests offline (`tests/conftest.py`); `venv/bin/python -m pytest tests -q -p no:cacheprovider` (491 green now); ruff clean on new files, none new in touched files. Never `git stash`. Commit trailers `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` / `Claude-Session: https://claude.ai/code/session_01RTWNMeQjJ5Zg5FgAhDhsRZ`. Never push.

## File Structure

| file | responsibility |
|---|---|
| `ai_memory/duplicates.py` (create) | pure: `name_time_key`, `suffix_groups`, `canonical_pair`, `merge_groups`, `choose_keeper`, `is_handled`, `build_report`, `render_markdown`; I/O: `load_fact_meta`, `near_copy_pairs`, `duplicate_report`, `supersede_fact`, `plan_supersedes` |
| `scripts/cli.py` (modify) | `duplicates`, `supersede` subcommands |
| `ai_memory/__init__.py` (modify) | export `duplicate_report`, `supersede_fact` |
| `tests/test_duplicates.py` (create), `tests/test_cli_smoke.py` (modify) | |
| `CHANGELOG.md`, `MIGRATION.md`, `README.md` (modify) | |

---

### Task 1: Pure grouping, keeper choice, report shape, markdown

**Files:** Create `ai_memory/duplicates.py`, `tests/test_duplicates.py`.

**Interfaces (produces):**
```python
DUP_COS = 0.95   # import from ai_memory.wordindex, do not redefine
def name_time_key(name: str) -> str | None
    # "(21:01 ET)" → "T21:01"; "— 2026-08-30" / "— 2026-08-30 #2" → "2026-08-30" (+ "#2" → "2026-08-30#02"); "(19:01)" → "T19:01"; None when no suffix
def suffix_groups(names: Iterable[str]) -> dict[str, list[str]]   # base(lower) → sorted names, only len ≥ 2
def canonical_pair(a: str, b: str) -> tuple[str, str]
def merge_groups(suffix: Mapping[str, list[str]], pairs: Iterable[tuple[str, str, float]]) -> list[dict]
    # union-find over suffix groups and cosine pairs → [{"members": sorted names, "signals": sorted set, "cos": {"a|b": cos, ...}}], sorted by members[0]
def choose_keeper(members: list[dict]) -> tuple[str | None, str | None]
    # members: [{"name","status","assistant","space","created_at","updated_at"}] → (keeper_name, reason) or (None, "needs_owner_decision")
def is_handled(members: list[dict], supersedes: Mapping[str, str]) -> bool
def build_report(groups: list[dict], meta: Mapping[str, dict], supersedes: Mapping[str, str], *, include_handled: bool = False) -> dict
    # {"generated_at", "n_facts", "groups": [{"members":[meta+...], "signals", "cos", "handled", "keeper", "reason", "commands": ["ai-memory supersede \"<keeper>\" \"<other>\" --apply", ...]}], "summary": {"groups", "facts", "handled", "needs_owner_decision", "suggested_commands"}}
def render_markdown(report: dict) -> str
```

- [ ] **Step 1: Failing tests**

```python
# tests/test_duplicates.py
from __future__ import annotations

import pytest

from ai_memory import duplicates as D


def test_name_time_key():
    assert D.name_time_key("Kill Switch State Machine (21:01 ET)") == "T21:01"
    assert D.name_time_key("Shared — ntr: bitchat — 2026-08-30") == "2026-08-30"
    assert D.name_time_key("Shared — ntr: bitchat — 2026-08-30 #2") == "2026-08-30#02"
    assert D.name_time_key("Bloom Filters") is None


def test_suffix_groups_and_pairs():
    g = D.suffix_groups(["Topic (19:01)", "topic — 2026-08-30", "Other", "Other (1)"])
    assert g == {"topic": ["Topic (19:01)", "topic — 2026-08-30"]}
    assert D.canonical_pair("b", "a") == ("a", "b")


def test_merge_groups_union_find():
    suffix = {"topic": ["Topic (19:01)", "Topic — 2026-08-30"]}
    pairs = [("Topic — 2026-08-30", "Topic v2", 0.97), ("X", "Y", 0.96)]
    groups = D.merge_groups(suffix, pairs)
    assert [g["members"] for g in groups] == [["Topic (19:01)", "Topic — 2026-08-30", "Topic v2"], ["X", "Y"]]
    assert groups[0]["signals"] == ["cosine", "suffix"] and groups[1]["signals"] == ["cosine"]
    assert groups[0]["cos"]["Topic — 2026-08-30|Topic v2"] == 0.97


def _m(name, status=None, assistant="Nova", space=None, created=None, updated=None):
    return {"name": name, "status": status, "assistant": assistant, "space": space, "created_at": created, "updated_at": updated}


def test_choose_keeper_prefers_name_time_then_updated_then_created():
    members = [_m("T (19:01)", created="2026-08-01T00:00:00"), _m("T (21:01)", created="2026-07-01T00:00:00")]
    assert D.choose_keeper(members) == ("T (21:01)", "newest name time")
    members = [_m("A", updated="2026-08-02T00:00:00"), _m("B", updated="2026-08-03T00:00:00")]
    assert D.choose_keeper(members) == ("B", "newest updated_at")
    members = [_m("A", created="2026-08-02T00:00:00"), _m("B", created="2026-08-01T00:00:00")]
    assert D.choose_keeper(members) == ("A", "newest created_at")
    members = [_m("A", status="active"), _m("B")]
    assert D.choose_keeper(members) == ("A", "only active member")
    members = [_m("A"), _m("B")]
    assert D.choose_keeper(members) == ("B", "no timestamps; lexicographically last")


def test_choose_keeper_needs_owner_decision_across_minds_or_spaces():
    assert D.choose_keeper([_m("A", assistant="Nova"), _m("B", assistant="Grok")]) == (None, "needs_owner_decision")
    assert D.choose_keeper([_m("A", space="shared"), _m("B", space=None)]) == (None, "needs_owner_decision")


def test_is_handled():
    assert D.is_handled([_m("A", status="active"), _m("B", status="superseded")], {})
    assert D.is_handled([_m("A"), _m("B")], {"A": "B"})
    assert not D.is_handled([_m("A"), _m("B")], {})
    assert not D.is_handled([_m("A"), _m("B"), _m("C", status="superseded")], {})


def test_build_report_and_markdown():
    groups = [{"members": ["A (19:01)", "A (21:01)"], "signals": ["suffix"], "cos": {}},
              {"members": ["X", "Y"], "signals": ["cosine"], "cos": {"X|Y": 0.96}}]
    meta = {"A (19:01)": _m("A (19:01)"), "A (21:01)": _m("A (21:01)"), "X": _m("X", status="active"), "Y": _m("Y", status="superseded")}
    rep = D.build_report(groups, meta, {"X": "Y"}, include_handled=False)
    assert rep["summary"] == {"groups": 1, "facts": 2, "handled": 1, "needs_owner_decision": 0, "suggested_commands": 1}
    g = rep["groups"][0]
    assert g["keeper"] == "A (21:01)" and g["commands"] == ['ai-memory supersede "A (21:01)" "A (19:01)" --apply']
    rep2 = D.build_report(groups, meta, {"X": "Y"}, include_handled=True)
    assert rep2["summary"]["groups"] == 2 and rep2["groups"][1]["handled"] is True
    md = D.render_markdown(rep)
    assert "A (21:01)" in md and "ai-memory supersede" in md and md.startswith("# Duplicate Facts report")
```

- [ ] **Step 2: run → ModuleNotFoundError. Step 3: implement** (`name_time_key` uses `ai_memory.retrieval._TRAILING_SUFFIX` to locate the suffix, then two small regexes for `HH:MM` and `YYYY-MM-DD(#n)`; timestamps compared as ISO strings after `str()`; `choose_keeper` order exactly as the tests; `build_report` sorts members by name, attaches meta, computes `handled`, `keeper`, `reason`, `commands` (empty when `needs_owner_decision` or handled), `summary`; `render_markdown` writes a heading, the summary table, then one section per group with a member table (name, assistant, space, status, created_at, updated_at, name time) and the suggested commands in a fenced block). **Step 4: run + ruff. Step 5: Commit** `feat(duplicates): pure grouping, keeper suggestion, report shape and markdown`.

---

### Task 2: I/O — fact meta, near-copies via the vector index, report, supersede

**Files:** Modify `ai_memory/duplicates.py` (append), `ai_memory/__init__.py`; modify `tests/test_duplicates.py`.

**Interfaces (produces):**
```python
def load_fact_meta(session) -> dict[str, dict]   # MATCH (f:Fact) RETURN f.name AS name, f.status AS status, f.assistant AS assistant, f.space AS space, toString(f.created_at) AS created_at, toString(f.updated_at) AS updated_at, f.embedding IS NOT NULL AS has_embedding
def near_copy_pairs(session, *, index: str, threshold: float = DUP_COS, k: int = 3) -> list[tuple[str, str, float]]
    # for each name with has_embedding: vec = MATCH (f:Fact {name:$name}) RETURN f.embedding; then
    # CYPHER 25\nMATCH (g:Fact)\nSEARCH g IN (VECTOR INDEX `<index>` FOR $vec LIMIT $pool) SCORE AS s\nWITH g WHERE g.name <> $name\nRETURN g.name AS name, 2 * vector.similarity.cosine($vec, g.embedding) - 1 AS cos
    # (index validated with validate_index_name and inlined; pool = k + 1); keep cos >= threshold; canonical, de-duplicated, sorted
def duplicate_report(driver, *, threshold: float = DUP_COS, k: int = 3, include_handled: bool = False, index: str | None = None) -> dict
    # index None → the same resolution search.py uses; loads meta + supersedes (search.load_supersedes) + suffix groups + pairs → build_report; adds report["params"] = {"threshold","k","index"}
def plan_supersedes(session, pairs: list[tuple[str, str]], supersedes: Mapping[str, str]) -> list[dict]
    # per (new, old): {"new","old","ok": bool, "reason": None|"unknown new"|"unknown old"|"same fact"|"old already superseded by <x>"|"would create a cycle"}
def supersede_fact(session, new_name: str, old_name: str, *, by: str = "ai-memory", now: str | None = None) -> dict
    # runs plan_supersedes for the single pair (loading supersedes itself) → if not ok raise ValueError(reason); else run the Global-Constraints statement; returns {"new","old","at"}
```

- [ ] **Step 1: Failing tests** (fake session scripted by statement substring, as in `tests/test_wordindex.py`): `load_fact_meta` maps rows; `near_copy_pairs` issues one embedding fetch + one SEARCH per embedded Fact with `pool == k+1`, the SEARCH statement starts with `CYPHER 25`, contains the backticked index and `2 * vector.similarity.cosine($vec, g.embedding) - 1`, rejects an invalid index name (`ValueError`), keeps only `cos >= threshold`, canonicalises and de-duplicates the (a,b)/(b,a) pair; `plan_supersedes` truth table for all five refusal reasons (cycle: supersedes `{"B": "A"}` and pair `("A", "B")` → cycle; also a longer chain); `supersede_fact` refuses via `ValueError` without running the write, and on ok runs exactly the statement with params `neu, old, now, by` and returns the dict; `duplicate_report` (fake driver) merges and reports, includes `params`.
- [ ] **Step 2–5:** implement, run, ruff, commit `feat(duplicates): near-copy discovery via the vector index, report assembly, guarded supersede`.

---

### Task 3: CLI

**Files:** Modify `scripts/cli.py`, `tests/test_cli_smoke.py`.

- `ai-memory duplicates [--cos 0.95] [--k 3] [--include-handled] [--index NAME] [--json PATH] [--markdown PATH]` → `duplicate_report(driver, ...)`; prints the summary line and the markdown to stdout when no `--markdown` given; writes files when asked. Exit 0 always on success; RuntimeError/ValueError → message on stderr, exit 1.
- `ai-memory supersede NEW OLD [--by NAME] [--apply]` and `ai-memory supersede --from-file decisions.json [--by NAME] [--apply]` (mutually exclusive with positional pair). The decisions file is a JSON list of `{"new": ..., "old": ..., "apply": true|false}` (the report's JSON `groups[*].commands` is NOT the input; the owner writes decisions, or a small documented jq recipe converts). Without `--apply`: print the plan (`plan_supersedes`) one line per pair with ok/reason and exit 0 (exit 1 if any row is not ok, so a dry run doubles as validation). With `--apply`: apply only rows with `apply: true` and `ok`, in order; print each result; exit 1 if any refused.
- Tests: arg parsing, dry-run prints plan and exits 1 on a refused row, `--apply` calls `supersede_fact` only for ok+apply rows (monkeypatched), JSON/markdown written, `duplicates` passes threshold/k/index.
- Commit `feat(cli): ai-memory duplicates report and guarded ai-memory supersede`.

---

### Task 4: Docs

CHANGELOG (Added: `ai_memory.duplicates`, `ai-memory duplicates`, `ai-memory supersede`); MIGRATION (phase 6 subsection: how to run the report, how to read `needs_owner_decision`, the decisions-file format, that nothing is merged or superseded automatically, that superseded Facts sink in ranking and stop consuming edge picks at the next write/nightly); README (commands). Commit `docs: phase 6 duplicate report`.

---

### Task 5 (operational, controller-run)

- `AI_MEMORY_DIR=~/.grok venv/bin/python scripts/cli.py duplicates --json ~/.ai-memory/golden/duplicates-2026-09-05.json --markdown ~/.ai-memory/golden/duplicates-2026-09-05.md` (read-only). Record the summary (groups, facts, handled, needs_owner_decision, suggested commands) in the ledger. Do NOT apply any supersede — that is the owner's pass.
- `ai-memory supersede --from-file` dry-run against a decisions file built from the report's suggestions (validation only, no `--apply`) to prove the plan path works live.
- Push to `private`; update memory; final message with the report location and counts.

## Self-review

- Spec coverage: §7.6 detection (T1/T2), report for the supersede pass (T1–T3), shared-space status mechanism (T2 `supersede_fact` statement mirrors the grok shared-write), no automatic merges (report never writes; `supersede` requires `--apply` and one reviewed pair or a decisions file with explicit `apply: true`).
- Type consistency: `merge_groups` output `{"members","signals","cos"}` is what `build_report` consumes; `load_fact_meta` keys `name,status,assistant,space,created_at,updated_at,has_embedding` are what `choose_keeper`/`is_handled` read; `plan_supersedes` rows are what the CLI prints and `supersede_fact` checks.
- Deliberately excluded: automatic application, grok client changes, edge maintenance after a supersede (the nightly and the next on-write handle it).
