# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [SemVer](https://semver.org/).

---

## [1.0.0] - 2026-06-03

First stable release. Phases 0–3 and 6 are complete. The public redistribution
package now ships an importable library, a unified CLI, and full multi-mind support.

### Added
- `ai_memory` Python package — `from ai_memory import MemoryClient` after `pip install -e .`
- `MemoryClient` facade: unified API for search, traversal, parameter tracing, session state
- `ai_memory.state.MemoryStateManager` — per-session lazy loading state tracker with context manager support
- `CHANGELOG.md` — this file
- `MIGRATION.md` — upgrade guide from v0.1 / v0.2 to v1.0

### Changed
- `scripts/hybrid_memory_search.py` — thin CLI wrapper over `ai_memory.search` (identical CLI behaviour)
- `scripts/rlm/neo4j_traverse.py` — thin CLI wrapper over `ai_memory.graph` (identical CLI behaviour)
- `scripts/rlm/memory_state.py` — thin CLI wrapper over `ai_memory.state` (identical CLI behaviour); fixes pre-existing `NameError`
- `scripts/rlm/metadata.py` — single re-export from `ai_memory.metadata`
- `pyproject.toml` version bumped to `1.0.0`; `ai_memory*` package included in distribution

### Fixed
- `scripts/rlm/memory_state.py`: `MemoryStateManager.__init__` called `get_driver()` which was never defined in the file — would raise `NameError` at runtime. Now fixed via proper import from `ai_memory._config`.

---

## [0.3.0] - 2026-06-03

Phase 3: Core Library Extraction.

### Added
- `ai_memory/` package scaffold with six modules: `_config`, `metadata`, `search`, `graph`, `state`, `__init__`
- `tests/test_library.py` — 23 library smoke tests (35 total with Phase 2 smoke tests)
- `## Library (Phase 3)` quick-start section in `README.md`
- `UPGRADE_PLAN.md` Phase 3 status marked Complete

---

## [0.2.1] - 2026-06-03

QA-hardening pass on Phase 2 multi-tenancy features.

### Fixed
- `hybrid_memory_search.py`: `--metadata-only`/`--fields` transforms were applied *after* `format_output()` for semantic+graph results — transforms were silently discarded. Fixed: collect all results, apply transforms, then output.
- `neo4j_backfill_assistant.py`: `Word`/`Source` nodes have no `.id` property — null ids caused the batch loop to stall silently without making progress. Fixed: `WHERE n.id IS NOT NULL` guard in stage-1 query.
- `neo4j_backfill_assistant.py`: unscoped `MATCH (n {id: p[0]})` in `create_q` could match the wrong label on id collision. Fixed: f-string label scoping (`MATCH (n:{label} {id: p[0]})`).
- `neo4j_backfill_assistant.py`: `CREATED_BY` relationship direction was inverted `(a)→(n)`. Fixed to `(n)→(a)` in all three Cypher locations.
- `neo4j_backfill_assistant.py`: `ensure_assistant_node` used `coalesce(a.type, $type)` in `ON MATCH SET` — prevented backfill from correcting `type='submind'` already set by sync. Fixed: `a.type = $type` unconditional.
- `neo4j_sync.py` + `neo4j_learn_sync.py`: hardcoded `type='submind'` on Assistant `MERGE` mislabels the primary mind if sync runs before backfill. Fixed: removed `type` from sync tools; `backfill_assistant.py` now owns type assignment.

---

## [0.2.0] - 2026-06-03

Phase 2: Multi-tenancy / Submind Foundations.

### Added
- `Assistant` nodes + `assistant` property on Fact, Session, Event, Decision, ConversationTurn nodes
- `scripts/neo4j_backfill_assistant.py` — production-hardened migration tool (batched UNWIND, dry-run mode, ~12k node graphs tested)
- `--assistant`/`--mind` flag on all scripts: `hybrid_memory_search.py`, `neo4j_sync.py`, `neo4j_learn_sync.py`, `neo4j_traverse.py`, `scripts/cli.py`
- `neo4j_seed.py`: `Assistant` unique constraint + `fact_assistant_idx` + `session_assistant_idx` range indexes
- `templates/submind/` — identity.qmd + setup.qmd for new minds attaching to an existing graph
- `docs/SUBMINDS.md` — full guide for multi-mind setups (Option A read-heavy mode documented)
- `scripts/cli.py` — unified `ai-memory` CLI with `--assistant` on all subcommands
- `tests/test_cli_smoke.py` — 12 CLI smoke tests (no Neo4j required)

---

## [0.1.0] - 2026-05-27

Initial public redistribution package.

### Added
- Core sync scripts: `neo4j_seed.py`, `neo4j_sync.py`, `hybrid_memory_search.py`
- RLM experimental tools: `neo4j_traverse.py`, `memory_state.py`, `neo4j_learn_sync.py`
- Metadata helpers: `scripts/rlm/metadata.py`
- Templates: `AGENTS.md`, `SOUL.md`, `USER.md`, `TOOLS.md`, `HEARTBEAT.md`, `MEMORY.md`, `INDEX.qmd`
- Bootstrap documentation: `BOOTSTRAP.md`, `UPGRADE_PLAN.md`, `DECISIONS.md`
- Architecture docs: `docs/ARCHITECTURE.md`, `docs/RLM.md`, `docs/LEARNER.md`, `docs/CRON_JOBS.md`
- MIT license
