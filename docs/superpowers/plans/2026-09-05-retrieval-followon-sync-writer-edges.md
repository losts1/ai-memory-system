# Retrieval Follow-on — Sync Writer Joins the Word Index and On-Write Edge Maintenance — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the gap left in phase 5: Facts written through `scripts/neo4j_sync.py` get their Word-index tokens at write time and, when an edge rule is published, the same on-write edge maintenance every other writer performs, so they are not edge-less until the next nightly.

**Architecture:** `write_fact_with_embedding` computes the canonical prepared text unconditionally (boilerplate from the RetrievalConfig when available, empty otherwise), tokenises it with `ai_memory.wordindex.tokenize`, and writes `HAS_WORD` edges in the same statement that MERGEs the Fact (the exact block `ai_memory.learn._sync_fact_tx` uses). `ai_memory.learn._maintain_edges` becomes the public `maintain_edges_after_write(session, name)` (old name kept as an alias) and `sync_file` calls it after each successful Fact write. Docs stop saying the sync script is unwired.

**Tech Stack:** Python ≥ 3.9 for the package (`from __future__ import annotations`); the script already imports from `ai_memory`.

**Spec:** `docs/superpowers/specs/2026-09-04-retrieval-index-design.md` §7.1 (Word index from the canonical text), §7.4 (on-write maintenance), §7.5 (writers). Phase-5 ledger ruling (2026-09-05): "scripts/neo4j_sync.py stays un-wired in this phase … wiring it is a follow-on owner item." This plan is that follow-on.

## Global Constraints

- The Fact write must still be ONE statement (text + optional CAS embedding + `HAS_WORD` rewrite); the words block is exactly the one in `ai_memory/learn.py::_sync_fact_tx` (delete existing `HAS_WORD` for the Fact, `UNWIND $words AS w MERGE (word:Word {text: w}) MERGE (f)-[:HAS_WORD]->(word)`), fed by `tokenize(prepared_text, name)` where `prepared_text = fact_embed_text(name, summary_seen, kp_seen, content, boilerplate)` with `boilerplate = cfg.boilerplate if cfg else ()`.
- The `summary`/`key_points` read used for the CAS embed becomes unconditional (it is needed for the prepared text even when embedding is off); it is a single `OPTIONAL MATCH … RETURN` and must stay one query.
- Edge maintenance runs AFTER the write succeeded, once per Fact, via the public `ai_memory.learn.maintain_edges_after_write(session, name)`; it never raises into the sync loop; it is skipped when no edge config is published.
- `write_fact_with_embedding` keeps its return contract (`"embedded" | "cas_skipped" | "text_only"`) and signature (add nothing; the caller passes `cfg` already).
- Tests offline; `venv/bin/python -m pytest tests -q -p no:cacheprovider` (545 green); ruff: no new findings in touched files. Never `git stash`. Commit trailers `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>` / `Claude-Session: https://claude.ai/code/session_01RTWNMeQjJ5Zg5FgAhDhsRZ`. Never push.

## File Structure

| file | responsibility |
|---|---|
| `scripts/neo4j_sync.py` (modify) | prepared text + tokens + words block in the write; call maintenance after each write |
| `ai_memory/learn.py` (modify) | `maintain_edges_after_write` public name; `_maintain_edges` alias |
| `tests/test_neo4j_sync_embed.py` (modify), `tests/test_learn.py` (modify) | |
| `CHANGELOG.md`, `MIGRATION.md` (modify) | correct the "sync script is unwired" statements |

---

### Task 1: Wire the sync writer

**Files:** as above.

- [ ] **Step 1: Failing tests** in `tests/test_neo4j_sync_embed.py` (reuse its fake session/embed_fn style):
  - the write statement contains the words block and `params["words"] == tokenize(fact_embed_text(name, summary_seen, kp_seen, content, boilerplate), name)` — with a `content` that contributes tokens the name/summary do not, so dropping content fails the test;
  - with `embed_fn=None` (or `cfg=None`) the words block is still present and tokens come from the prepared text with empty boilerplate;
  - `sync_file` calls `maintain_edges_after_write(session, name)` once per written Fact (monkeypatched, assert names), and a raising maintenance never aborts the sync (result dict unchanged);
  - `tests/test_learn.py`: `learn.maintain_edges_after_write` exists and `learn._maintain_edges is learn.maintain_edges_after_write`.
- [ ] **Step 2: run → fail. Step 3: implement.** In `learn.py` rename `_maintain_edges` → `maintain_edges_after_write`, keep `_maintain_edges = maintain_edges_after_write`, update its two call sites. In `neo4j_sync.py`: import `tokenize` from `ai_memory.wordindex` and `maintain_edges_after_write` from `ai_memory.learn`; hoist the `seen` query out of the `if`; compute `text`/`tokens` always; append the words block after the `LEARNED_IN` MERGE and before the embed block (`WITH f` chaining exactly as `_sync_fact_tx` does); add `params["words"]`; in `sync_file`, after a write that returned any status, call `maintain_edges_after_write(session, fact["name"])`.
- [ ] **Step 4: suites + ruff. Step 5: docs** — `CHANGELOG.md` (Unreleased: "Changed: `scripts/neo4j_sync.py` now writes Word tokens from the canonical text and runs on-write edge maintenance like the other writers") and fix the two sentences that say it does not (`CHANGELOG.md` ~204/210, `MIGRATION.md` phase-5 subsection). **Step 6: Commit** `feat(sync): neo4j_sync writes Word tokens and runs on-write edge maintenance`.

---

### Task 2 (operational, controller-run)

- Do NOT run the sync script against the live workspace (it ingests files and writes Facts). Verify with the suites only, plus `venv/bin/python -c "import scripts.neo4j_sync"`-style import check if the repo's smoke test does not already compile it.
- Push to `private`; update memory; report.
