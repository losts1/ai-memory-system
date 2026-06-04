# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [SemVer](https://semver.org/).

---

## [1.3.1] - 2026-06-04

Fix-only patch addressing six bug reports against v1.3.0 (issues
[#34](https://github.com/losts1/ai-memory-system/issues/34)–[#39](https://github.com/losts1/ai-memory-system/issues/39)),
all reproducible on a populated Neo4j 2026.04 graph (1,261 Facts).
Live-verified end-to-end. Tests: 69 → 78.

### Fixed
- **#37 / #38** — `search_vector` and `search_graph` now return
  `content`, `summary`, and `key_points` from Fact nodes. v1.3.0 returned
  only `node.content`, but Facts written by `ai_memory.learn.sync_facts`
  store data in `summary` + `key_points` and leave `content` NULL. In a
  representative production graph **1,181 / 1,261 Facts (93.7%)** had
  `content=NULL` — vector and graph search returned empty teasers for
  almost everything. The Cypher now uses `coalesce(node.content, node.summary)`
  for `content` and additionally returns `summary` and `key_points`; the
  result builders emit them when present.
- **#39** — Empty / whitespace-only queries to `search_vector`,
  `search_graph`, and `search_faiss` short-circuit to `[]` before
  contacting Ollama or Neo4j. `ollama.embeddings(prompt="")` returns a
  0-dim vector, which `db.index.vector.queryNodes` rejects with a
  dimension-mismatch error against the 768-dim index.
- **#35** — `scripts/hybrid_memory_search.py:format_output` used `r['source']`
  unconditionally and crashed with `KeyError: 'source'` when `--fields`
  stripped the field. All field accesses now use `.get()` with truthiness
  guards. The formatter additionally surfaces the new `summary` and
  `key_points` fields.
- **#34** — `ai-memory search` exposes `--files-only` and
  `--use-embeddings` flags, forwarded to `hybrid_memory_search.py`.
  The round-2 guard rejecting `--use-embeddings --assistant <X>` still
  fires for the incompatible combination.
- **#36** — `ai-memory state` previously emitted `--session X --init`,
  which `memory_state.py` rejected because the script uses positional
  subcommands. `cmd_state` now maps the boolean action flags to
  subcommand names (`init`, `pending`, `summary`, `record-query`,
  `mark-loaded`, `load-fact`, `cleanup`) and emits them positionally
  before `--session`. Zero or two-plus action flags produce explicit
  usage errors (exit 2).

### Tests
- 9 new tests: empty-query guards for all three search backends,
  `cmd_search` --help advertises the two new flags, `cmd_state`
  action-rewriting + no-action + double-action paths, `cmd_backfill`
  multi-mind flattening (round-2 regression cover), and
  `format_output` tolerates a missing `source` field.

---

## [1.3.0] - 2026-06-04

Frontmatter-aware parsing + search shape fixes. Driven by QA rounds 3–5
applying the library against a real organic memory corpus (60 YAML-headed
markdown files) and a populated Neo4j 2026.04 (1183 Facts).

### Added
- `ai_memory.learn.parse_frontmatter_topic(content, filepath)` — parser
  for YAML-frontmatter single-topic memory files. Sibling to
  `parse_learned_topics` (which is daily-note-shaped). The frontmatter
  `description` field becomes the topic `summary` (curated one-liner);
  body bullets become `key_points`. Skips fenced code blocks; tolerates a
  UTF-8 BOM; the numbered-list pattern requires whitespace after the
  period so `1.2.3 foo` is not mistaken for a list item.

### Changed
- `ai_memory.search.search_files`:
  - Replaced the hard-coded 30-file reverse-alphabetic cap with a
    configurable `max_files: Optional[int] = None` parameter (no cap by
    default) and an mtime-descending sort. The old behaviour was correct
    for `YYYY-MM-DD.md` daily notes but silently dropped half of any
    semantically-named corpus.
  - `MEMORY.md` is now looked up at both `workspace/MEMORY.md` (project
    layout) and `workspace/memory/MEMORY.md` (Claude-style layout); first
    match wins. The daily `*.md` glob skips `MEMORY.md` so it is never
    reported twice.
  - The mtime sort key catches `OSError` so a broken symlink or
    permission-denied file no longer crashes the entire sort.
- `ai_memory.metadata.make_teaser(summary, *, description=None)` — when
  a frontmatter `description` is supplied, it wins (descriptions are
  curated one-liners, no truncation needed). Backward compatible.

### Fixed
- `ai_memory.search.search_vector`: `NEO4J_VECTOR_INDEX` was read before
  `get_driver()` triggered `load_dotenv`, so the very first call always
  used the default `fact_embeddings` index name. The read order is now
  swapped — vector search works against custom-named indexes (e.g.
  `factEmbeddingIndex`) on the very first invocation.
- `ai_memory.metadata.apply_metadata_only`: `search_vector` returns
  `content=None` when `node.content` is null; `summary or content` then
  yielded `None` and `len(teaser_src)` raised `TypeError`. Switched to
  `result.get('summary') or ''` (and same for `content`).
- `ai_memory.metadata.make_teaser`: a whitespace-only `description`
  used to override a real summary and return `''`. Now treated as absent.

### Tests
- 52 → 69. New tests cover frontmatter parsing (BOM, code-fences,
  version strings, missing name, description fallback, key-point cap),
  dual `MEMORY.md` lookup, no-cap and explicit-cap search behaviour,
  `MEMORY.md` dedup, `None` content/summary handling, and
  whitespace-only descriptions.

---

## [1.2.1] - 2026-06-04

QA round 2 (ultrathink). CLI surface + multi-tenancy backfill tool.
Fix-only patch — no schema changes since v1.2.0.

### Fixed
- `scripts/cli.py`: `ai-memory backfill --additional A --additional B`
  silently dropped all but the last value (the wrapped script declares
  `--additional` with `nargs="*"`, so repeated flags overwrote). The CLI
  now flattens to a single `--additional A B`, preserving every mind.
- `scripts/neo4j_backfill_assistant.py`: `backfill_label` stage-2 update
  used an unlabeled `MATCH (n)`, so a non-Fact node sharing an `id` with
  a Fact and lacking the `assistant` property could be tagged with the
  wrong assistant. Now scoped to `MATCH (n:{label})`, closing the same
  bug pattern that the Phase 2 QA round fixed in `create_q`.
- `scripts/hybrid_memory_search.py`: `--use-embeddings --assistant <X>`
  silently dropped the tenant filter (FAISS has no `assistant` parameter).
  Now exits with code 2 rather than returning cross-tenant results.
- `scripts/cli.py`: `--max-results` (default 10) and `--batch-size`
  (default 100) silently overrode the wrapped scripts' own defaults
  (5 and 500 respectively) because of `if args.X:` truthy pass-through.
  Defaults are now `None` with `is not None` checks; omitting the flag
  falls through to the wrapped script's default.
- `scripts/neo4j_backfill_assistant.py`: dry-run reported `total_to_do`
  using a count that didn't apply the wet-run's `n.id IS NOT NULL`
  filter, so dry-runs inflated the projected count. Both counts now
  match. New `count_skipped_null_id` helper logs the cohort of untagged
  nodes with `null` ids (e.g. v1.2 Facts created via `ai_memory.learn`)
  so users see what backfill cannot reach.
- `scripts/neo4j_backfill_assistant.py:count_nodes_needing_backfill`
  caught bare `Exception` and returned 0, silently masking
  `ServiceUnavailable` and similar real connection failures as "label
  not present". Narrowed to `(ClientError, DatabaseError)`.

### Changed
- `scripts/cli.py`: `ai-memory init` is now clearly labeled in both its
  output ("INSTRUCTIONS ONLY — Nothing is created on disk") and its
  subparser help text. The command still only prints; behavior unchanged.

---

## [1.2.0] - 2026-06-04

QA round (ultrathink). Unifies the two Fact-sync paths under a single
schema identity and fixes several correctness bugs in `ai_memory/state.py`.

### Changed (schema — requires re-seed; see [MIGRATION.md](./MIGRATION.md))
- **Fact identity is now `f.name`-primary.** `scripts/neo4j_sync.py` MERGEs
  on `name` instead of `id=sha256(file:name)`. Same fact across multiple
  session files now resolves to one node instead of duplicates.
- `scripts/neo4j_seed.py`: new `CREATE CONSTRAINT fact_name_unique FOR (f:Fact) REQUIRE f.name IS UNIQUE`. Existing `fact_id_unique` retained.
- `scripts/neo4j_sync.py`: `f.id` preserved via `coalesce(f.id, $id)` so
  `neo4j_backfill_assistant.py` and embedding lookups keep working.

### Fixed
- `ai_memory.state.MemoryStateManager.cleanup()` returned 0 or 1 due to a
  per-`ms` grouping in the `WITH` clause. Now collects sessions/queries/facts
  into single rows then `FOREACH`-deletes, returning `size(sessions)`.
- `ai_memory.state.MemoryStateManager.load_fact()` silently dropped state
  tracking when called without a prior `record_query` (because `mark_loaded`
  only updated pre-existing `MemoryFact` nodes). Now MERGEs the `MemoryFact`
  in the session.
- Read methods (`get_pending`, `get_summary`, helpers, `list_sessions`)
  no longer call `_ensure_session` — reads no longer create empty sessions
  or bump `updated_at`, preserving the `cleanup(max_age_hours)` TTL semantics.
- Write methods (`init_session`, `record_query`, `mark_loaded`) ensure the
  `MemoryState` node within the same driver session (1 round-trip instead of 2).
- `ai_memory.MemoryClient.search(graph=True)` deduplicates graph results
  by `name` against vector/FAISS results (vector wins).

### Added
- `ai_memory.MemoryClient.state(session_id=…)` and
  `ai_memory.state.MemoryStateManager(session_id=…)` now bind a default
  session_id. Session-id-taking methods accept `Optional[str]` and fall
  back to the bound value; explicit per-call `session_id` still overrides.
  Existing callers passing `session_id` to every call continue to work.
- `MIGRATION.md`: v1.1 → v1.2 section with dedupe queries and re-seed steps.

### Not changed (deliberate)
- Cross-tenant `RELATED_TO` edges from the shared `Word` index were left
  as-is — `Word`/`RELATED_TO` are tenant-shared by design.

---

## [1.1.0] - 2026-06-04

Phase 4: Learn pipeline as library + examples.

### Added
- `ai_memory/learn.py` — `parse_learned_topics`, `extract_words`,
  `normalize_name`, `is_topic_saturated`, `sync_facts`, `rebuild_graph`,
  `link_related_facts`, `cleanup_orphaned_words`.
- `ai_memory.MemoryClient.learn(days, *, assistant)` — scan
  `{workspace}/memory/*.md` daily notes and sync as Fact nodes.
- `examples/01_lazy_loading_session.py` — RLM lazy loading demo.
- `examples/02_learn_and_traverse.py` — learn → traverse pipeline demo.
- `docs/RLM.md` — Library API section covering all RLM modules.
- `tests/test_learn.py` — 17 smoke tests (no Neo4j required).

### Changed
- `scripts/rlm/neo4j_learn_sync.py` — thin CLI wrapper over
  `ai_memory.learn` (identical CLI behaviour).

### Fixed
- `ai_memory/learn.py`: UTC time-annotation regex was missing the
  minute group (`\(\d+:\s*UTC\)` → `\(\d+:\d+\s*UTC\)`).
- `ai_memory/learn.py`: dead `if not line.startswith('|'): pass` block
  now actually skips markdown table rows.
- `ai_memory/learn.py`: progress messages in `_sync_fact_tx` now print
  to stderr instead of polluting stdout.
- `scripts/rlm/neo4j_learn_sync.py`: success-log message no longer
  prints stale names when middle topics fail to sync.
- `examples/02_learn_and_traverse.py`: graceful Neo4j error handling
  and tempfile cleanup via `try/finally`.

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
