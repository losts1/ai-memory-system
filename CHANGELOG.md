# Changelog

All notable changes to this project will be documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versioning follows [SemVer](https://semver.org/).

---

## [1.4.0] - 2026-09-08

The retrieval redesign is a breaking change (`search()` defaults to hybrid, `graph=True`
no longer returns `related_facts`, hit keys `content`/`summary` became `teaser`), so
`pyproject.toml` is bumped to 1.4.0. Upgrading an existing install is not a `pip install`
away — read [UPGRADING.md](./UPGRADING.md) before you start.

### Fixed (pre-release review)
- `ai-memory nightly` no longer cuts the edge layer over when the re-embed reports
  `embed_failed > 0`: it exits 1 and the previous `rule_version` stays live, as the
  systemd README promised. `--json` omits the multi-megabyte `edge_list` unless
  `--dump-edges` is passed (#1).
- `ai-memory eval`: an empty candidate pool is unjudged (fails closed) instead of
  "judged with nDCG 0"; `exact5` shares the `judged_queries` denominator with the
  judged metrics; new `--gate-against before.json [--gate-ranker R]` applies the ship
  gate and exits 1 on FAIL; `eval-edges` is a real subparser listed by `--help` (#3).
- Library write paths no longer clobber another mind's Fact: `learn._sync_fact_tx` and
  `neo4j_sync.write_fact_with_embedding` read the existing node's `assistant` before the
  MERGE-by-name and refuse when it differs from the writer's (including an untagged
  writer) — nothing is set, no edges are maintained, and the caller is told
  (`owner_conflict` status, run-summary count, one stderr line). An untagged Fact stays
  claimable, the one deliberate relaxation vs the grok client. `MemoryClient.write()`
  gains `assistant=`. `choose_keeper`/`_break_tie` now treat a NULL `status` as live, like
  `is_handled`, the command emission and `retrieval._is_active` ("only live member") (#2).
- `neo4j_sync.py` / `neo4j_learn_sync.py`: `from __future__ import annotations` so
  `X | None` annotations import on Python 3.9 again; a guard test scans every module (#4).
- `vector_index.migrate()` returns `already_migrated` instead of dropping a live index
  that already carries the filter properties (#5).
- Retrieval contract gaps (#7): the grok client's `_escape_lucene` lowercases
  AND/OR/NOT like the library's; `same_topic` compares names case-insensitively in
  both implementations; `load_supersedes` / `load_supersedes_strict` return a multimap
  (`{new: {old, ...}}`) so a keeper with several losers no longer hides edges from
  `is_handled`, `plan_supersedes` (already-superseded and cycle checks) and
  `wordindex.is_duplicate`; `search_vector` latches the over-fetch fallback only after
  the `queryNodes` retry succeeds, never on index-not-found, only on the 22ND3 /
  SEARCH-unsupported errors, and under a lock; `MemoryClient.search(graph=True,
  mode=...)` raises on a conflicting mode; the grok `--mode` paths fuse over the pool
  width and lexical hits carry the neutral `lex` tag.
- `verify_schema.py --strict` exits 1 when the `RetrievalConfig` node is missing (#8).
- `full` extra now includes numpy, so `pip install 'ai-memory-system[full]'` can run
  `nightly` / `edges --rebuild` (#8).
- Judge endpoint defaults honour `AI_MEMORY_JUDGE_URL` / `AI_MEMORY_JUDGE_MODEL` (#8).
- Example systemd unit defaults `AI_MEMORY_DIR` to `~/.ai-memory`; its README no longer
  calls a real cutover a "dry run" and names no personal path (#1, #8).
- Grok skill redeploys copy the whole directory (`SKILL.md` changed in phases 4–5); the
  grok README verify step diffs the deployed copy. Root cause of #9: the README
  used to say copy only the two `.py` files (“no other files need to move”), so
  live `SKILL.md` lagged the script; a later script-only commit went undeployed
  for the same reason (`diff -rq` was not re-run) (#9).

### Added
- **`grok/`** — Grok Build TUI integration: copy-in skill, SessionStart /
  UserPromptSubmit / Stop hooks, home rule, and an LLM runbook
  (`grok/README.md`) to repeat the Neo4j wiring. Thin Bolt CLI: hybrid search
  (Lucene `fact_content` + `fact_key_points` fused with Ollama `nomic-embed-text`
  KNN via RRF; vector-only cosine floor 0.80; fulltext-only when Ollama is down),
  Grok-tagged write with `--force-assistant` override, shared space
  (`--space shared`: dated add / `--supersede` / `--append` / tombstone `remove`,
  `history`), `embed` backfill, per-mind organize. 47 offline unit tests.
  Does not require `pip install -e .`.

- **`ai_memory.eval.judge`** — LLM relevance judge for retrieval evaluation:
  fixed system prompt (three grades, JSON-only contract), prompt builder that
  hides scores and shuffles candidates deterministically, defensive parser,
  grade-2-vs-not calibration against a golden set (gate 0.8), nDCG@k /
  Recall@k / MRR, and a JSON judgment cache keyed on the prompt hash so a
  re-calibrated prompt never reuses old grades. The HTTP call is injected; the
  package has no network dependency. Calibrated live on the production graph
  with Qwen 3.8: 1.0 agreement over 25 items after the external design review
  (Grok 4.6) forced four fixes — nDCG ideal is taken over the whole judged pool
  (a ranker that misses a judged grade-2 can no longer score 1.0); a reply that
  omits any candidate is malformed and retried, never silently graded 0; the
  superseded rule is absolute so cached grades do not depend on list siblings;
  and `passes_ship_gate` requires Recall@5 as well as nDCG@5 not to drop.
  A second rubric, `EDGE_SYSTEM_PROMPT` (relatedness: same mechanism /
  shared entity / prerequisite / contrast), selected with `rubric="edge"` on
  `build_judge_messages`, `judge_query` and the cache key, with
  `calibration_agreement(..., boundary=1)` for the related-vs-unrelated
  boundary an edge encodes. 47 offline tests.

- **`ai_memory.retrieval`** (pure fusion/ranking/Cypher builders), `search_hybrid`,
  `ai_memory.eval.harness` + `ai-memory eval`.

- **`tests/test_retrieval_contract.py`** — cross-implementation contract between
  `ai_memory` and the grok client: asserts identical prepared text on shared
  fixtures, identical SEARCH clause modulo parameter names, identical
  fused-and-ranked order, the eight shared hit keys, identical CAS subquery.

- **`ai_memory.embed`** — canonical embedding text (`fact_embed_text`: name, summary,
  key points as dash lines, content; 2,000-char cap; corpus boilerplate 4-gram runs
  removed), versioned text sha, provenance-carrying compare-and-set embed writes
  (`embedding_model`, `embedding_dim`, `embedding_text_sha`, `boilerplate_version` set
  in the same statement as `embedding`), `embed_all` backfill with `embedding_prev`
  safety net, `vector_stats`. **`ai_memory.retrieval_config`** — the
  `(:RetrievalConfig {id: "current"})` singleton (boilerplate grams + version) that
  every writer reads before embedding. CLI: `ai-memory embed --all|--stale-only|
  --drop-prev|--rollback [--keep-prev] [--no-publish]`, `ai-memory stats`.

- **`ai_memory.vector_index`** — DDL/probe builders, `wait_online`, `membership_check`,
  `property_probes`, and a gated `preflight`/`migrate` for the vector index's filter
  properties (spec §4 "Index rebuild"): `preflight` creates a temporary `<index>_v2`,
  measures population, probes membership and per-property filtering, then always drops
  it — unless the live index already carries every requested filter property, in which
  case it reports `already_migrated: true` without creating anything; `migrate` probes
  `CYPHER 25` support and aborts before any change if rejected, then drops and recreates
  the live index, waits for `ONLINE`/100% population, then gates on the same checks —
  on gate failure it leaves the new index in place for the operator to inspect, and if
  the CREATE fails after a successful DROP it attempts to recreate a plain (unfiltered)
  index and raises with the exact statement to re-run. `build_create_index_ddl_plain`
  builds the unfiltered Cypher-5 DDL for servers that reject `CYPHER 25`.
  `scripts/neo4j_migrate_vector_filters.py` wraps both as a CLI (`--preflight`,
  `--migrate`, `--dry-run`, `--index`, `--props`, `--json`); `--dry-run` applies to
  `--migrate` only (rejected with `--preflight`, exit 2) and prints the statements and
  runs nothing; an empty `--props` is rejected (exit 2); any exception from
  `--preflight`/`--migrate` is reported as `{"ok": false, "mode", "index", "error"}`
  with exit 1 instead of a traceback. `ai_memory.eval.harness` gained `--subset
  all|scoped|unscoped` to grade only golden queries with/without filters, now also
  shown in the printed table. `validate_schema()`
  now reports `vector_filter_props` (`"ok"` / `"missing: [...]"` / `"index not found"`) for
  the configured vector index.

- **`ai_memory.wordindex`** (spec §7) — the RELATED_TO edge layer: `tokenize` (≤24
  tokens per Fact from the canonical embedding text, name tokens first), TF-IDF
  cosine over published `Word.idf` blended with embedding cosine and z-scored
  against random-pair baselines for the corpus, `maintain_edges_for` (on-write
  maintenance, no numpy required) and `rebuild_edges` (nightly full rebuild, needs
  `numpy`), and `edge_stats` (edge count, current/stale `rule_version` counts,
  isolated-Fact percentage, max/p95 degree). CLI: `ai-memory edges
  (--rebuild|--dry-run) [--seed] [--pairs] [--k] [--json]`, `ai-memory nightly
  [--seed] [--json]` (runs publish-boilerplate + re-embed, then rebuild edges and
  cut over, in that order), `ai-memory eval-edges` (judges a seeded RELATED_TO
  sample against the edge rubric; `--legacy`/`--rule-version` picks the sampled
  population, `--gate-against` compares against a previous run's `--json` output).
  `ai-memory stats` now also reports the edge-health fields from `edge_stats`.
  New `edges` extra (`numpy>=1.24`) — `pip install 'ai-memory-system[edges]'` —
  required only for the nightly rebuild; the on-write path has no numpy dependency.

- **`ai_memory.duplicates`** (phase 6) — a read-only owner report for duplicate
  Facts: same-topic re-learnings detected by a trailing time/date suffix on the
  name or by near-copy embedding cosine (≥0.95, found with one vector-index
  `SEARCH` query per embedded Fact, exact cosine recomputed server-side),
  merged into groups by union-find. A group is already `handled` when it has
  at most one live member or is already fully connected by `SUPERSEDES`
  edges; otherwise `choose_keeper` suggests one by newest dated name suffix
  (a clock-only suffix does not count), else newest `updated_at`, else
  newest `created_at`, else the only active
  member, else the lexicographically last name, and the report prints
  ready-to-paste `ai-memory supersede <keeper> <other> --apply` commands
  (each name passed through `shlex.quote`). A group spanning more than one
  `assistant` or `space` is marked `needs_owner_decision` with no suggested
  keeper. Nothing is ever merged or superseded by the report itself. CLI:
  `ai-memory duplicates [--cos 0.95] [--k 3] [--include-handled]
  [--index NAME] [--json PATH] [--markdown PATH]`.
- **`ai-memory supersede`** — guarded, plan-then-apply CLI for marking one
  Fact superseded by another: `ai-memory supersede NEW OLD [--apply]
  [--by NAME]`, or `--from-file decisions.json` (a JSON list of
  `{"new", "old", "apply": true|false}`, where `apply` must be a JSON
  boolean). Every pair is planned first and refused when `new`/`old` doesn't
  exist, `new == old`, `old` is already superseded by a different Fact, or
  the pair would create a `SUPERSEDES` cycle; without `--apply` it only
  prints the plan and exits 1 if any row is refused, so it doubles as a
  dry-run validator. With `--apply` it writes only the rows that are both ok
  and `apply: true`: `status='superseded'`, `superseded_at`, and
  `updated_at` on the old Fact; `status` on the new Fact only if it was
  unset; and a `SUPERSEDES` edge carrying `at`/`by` — the same write the
  grok client's shared-write supersede path uses, minus that path's
  `old.space = 'shared' AND old.status = 'active'` precondition (library
  Facts have null `status`/`space`; the guard is instead unknown-name,
  self-pair, already-superseded, and cycle checks).

### Changed
- **The grok client (`grok/skills/neo4j-memory/scripts/neo4j_memory.py`) is ported
  onto the retrieval contract.** It embeds `fact_embed_text` (canonical text, corpus
  boilerplate stripped per the live `RetrievalConfig` read over Bolt) instead of its
  own text; its vector leg is now the Cypher 25 `SEARCH ... WHERE ... LIMIT $k`
  clause with in-index equality filters (new `search --assistant/--space/--trust`,
  never `status`) instead of an over-fetch-and-post-filter query, so it requires the
  phase-3 migrated index and has no fallback if the index is un-migrated (the vector
  leg errors and search degrades to fulltext-only, same as Ollama down); its ranking
  pipeline is RRF over the whole fulltext+vector pool (scores now round to 6 decimals,
  matching the library's `fuse_rrf`), then the supersede/same-topic sink, then the
  active exact-name boost (same order as the library); and `write`,
  shared `write`, and `embed` write vector provenance (`embedding_model`,
  `embedding_dim`, `embedding_text_sha`, `boilerplate_version`) alongside `embedding`
  in one compare-and-set statement, so Grok-written Facts are indistinguishable from
  library-written ones. `embed` now also re-embeds foreign vectors (no
  `embedding_text_sha`), not just missing ones.
- **`MemoryClient.search(query)`'s default (`graph=False`) now runs the hybrid
  path** — the vector leg plus the two fulltext legs, fused by RRF — instead
  of a single vector query. Pass `mode="vector"` for the old vector-only
  behaviour; it now also applies the supersede sink and the 0.80 vector-only
  floor, which previously only ran under `mode="hybrid"`.
- **`MemoryClient.search(graph=True)` semantics changed.** `graph=True` is now
  a no-op alias for `mode="hybrid"`; it no longer appends `related_facts` /
  `relationships` to each hit. To get related Facts, call `client.traverse(name, depth=1)`.
- **`search_graph()` result shape changed.** No longer returns `related_facts`,
  `relationships`, or `related_count`; returns the same hit dict as `search_vector()`.
- **`search_vector()` hit dict keys changed.** Removed: `content`, `summary`.
  Added: `teaser`, `key_points`, `assistant`, `status`, `space`, `via` (and
  `source`; vector-origin hits also carry `vec_score`). `--fields summary` /
  `--fields content` no longer select anything — use `--fields teaser`.
  `--metadata-only` now reads `teaser` instead of `summary`/`content`.
- **New keyword arguments `space=` and `mode=`.** `space=` is available on
  `MemoryClient.search`, `search_vector`, and `search_graph` to filter to a
  shared space. `mode=` ("hybrid" | "fulltext" | "vector") is available on
  `MemoryClient.search` and the new `search_hybrid()`.
- **All in-repo writers embed the same text.** `MemoryClient.write()`/`learn()`,
  `neo4j_sync.py` and `neo4j_learn_sync.py` previously embedded several different texts
  (name+content, key points only, …) or none; they now embed `fact_embed_text(...)` and
  write text and vector in one statement, guarded by a compare-and-set on the text
  fields they do not own. A writer that cannot read `RetrievalConfig` or reach Ollama
  writes the text only; `ai-memory embed --all` fills the vector later.
- `scripts/neo4j_seed.py` creates `RetrievalConfig` version 1 (no boilerplate) on fresh
  installs; `validate_schema()` reports `retrieval_config`.
- `scripts/neo4j_seed.py` now creates the vector index with filter properties
  `assistant, space, status, provenance_trust` (`ai_memory.vector_index.DEFAULT_FILTER_PROPS`)
  on fresh installs against a Cypher-25-capable server, so the in-index `SEARCH … WHERE`
  path works without a later migration; on a Cypher-5-only server it falls back to the
  plain (unfiltered) `CREATE VECTOR INDEX` DDL — a fresh install there still gets a
  working vector index, just without in-index filtering until `--migrate` runs later.

- **RELATED_TO edges are now chosen per Fact instead of by shared-word count.**
  Each Fact picks its top 5 neighbours by a blend of TF-IDF cosine (binary TF-IDF
  over the tokenized canonical text) and embedding cosine, each z-scored against
  random-pair baselines for the corpus, kept only above a floor set at the blend's
  99th percentile over random pairs; duplicates (trailing-suffix twins, `SUPERSEDES`
  chains, cosine ≥ 0.95) never consume a pick. An edge exists while either endpoint
  still picks it, and now carries `weight`, `tfidf`, `cos`, `shared_keywords`,
  `picked_by`, `via`, `rule_version` in place of the old unweighted edge. Edges are
  maintained on every write (`maintain_edges_for`: the written Fact's own picks,
  plus any neighbour whose worst pick it beats) and rebuilt in full by `ai-memory
  nightly`, which publishes a new `rule_version` and deletes every edge with a
  different or missing one — the first nightly run after upgrading deletes every
  legacy shared-word edge, so export them first if you need them (`MATCH
  (a)-[r:RELATED_TO]->(b) RETURN a.name, b.name, properties(r)`). `traverse` and
  `trace_parameter` callers now see sparser, weighted edges; `related_count`'s
  *definition* is unchanged, but its values are not — sparser, name-ordered edges
  change which Facts report `related_count == 0`.
- **The Word index is now built from the canonical embedding text**
  (`fact_embed_text`, ≤24 tokens per Fact) instead of from name words — `learn`
  and the grok client's `write`/`write_shared` route through the new tokenizer
  (`wordindex.tokenize`, via `write_fact_tokens` / `_write_tokens`).
  `scripts/neo4j_sync.py` was not wired to this at first (writing `f.content`
  and the embedding only, with no tokenizer and no edge-maintenance call); a
  follow-on task wired it in — see below.
- **`learn.link_related_facts` and `learn._post_sync_tx` are removed.** The
  shared-word RELATED_TO edges they wrote are superseded by
  `wordindex.maintain_edges_for`, which `MemoryClient.write()`/`learn()`,
  the grok client, and `scripts/neo4j_sync.py` all call after each write.
- `scripts/neo4j_sync.py` now writes Word tokens from the canonical text and
  runs on-write edge maintenance like the other writers.
- **The grok client's `organize` no longer MERGEs edges between Facts sharing ≥2
  words.** It now runs the same on-write rule (`maintain_edges_for`, a verbatim
  port) over one mind's Facts, and requires a published edge rule first
  (`ai-memory nightly`) — it refuses if `RetrievalConfig.rule_version` is unset.
  Redeploy the grok client after upgrading (see MIGRATION.md).

### Fixed
- **The SEARCH statement inlined the vector index name.** Neo4j rejects a parameter
  inside the `VECTOR INDEX` clause (`Parameter cannot be used in a VECTOR INDEX clause`),
  so `build_search_cypher`'s `VECTOR INDEX $index` could never succeed — every call fell
  back to `db.index.vector.queryNodes` via the syntax-error latch, and the phase-1
  in-index path never ran. Found by the phase-3 pre-flight. Fixed: `build_search_cypher`
  now takes the index name as an argument, validates it (`validate_index_name`,
  `^[A-Za-z_][A-Za-z0-9_]*$`), and inlines it as a backtick-quoted identifier; `$vec`,
  `$pool`, and filter values stay parameters.
- **`build_create_index_ddl`'s `WITH [...]` filter-property list needs `CYPHER 25`.**
  Under the Cypher 5 default, Neo4j 2026.04 rejected it (`Invalid input 'WITH': expected
  'OPTIONS'`). The statement is now prefixed with a literal `CYPHER 25` line, matching
  `build_probe_cypher`; `build_drop_ddl` is unchanged (plain `DROP INDEX` is Cypher 5).
  On a server that does not support the `CYPHER 25` prefix, `--migrate` probes that
  support before the DROP and aborts before touching the live index if rejected;
  `scripts/neo4j_seed.py` falls back to the plain (unfiltered) DDL there, so
  `search_vector`'s existing over-fetch fallback has a live index to query.
- **`neo4j_migrate_vector_filters.py --index` resolved too early.** Its default read
  `os.getenv("NEO4J_VECTOR_INDEX", "fact_embeddings")` at argparse time, before
  `_open_driver()`'s `get_driver()` loads `<workspace>/.env.neo4j` — so an unset shell
  environment silently targeted `fact_embeddings` instead of the workspace's configured
  index. `--index` now defaults to `None` and is resolved (`a.index or
  os.getenv("NEO4J_VECTOR_INDEX", "fact_embeddings")`) after the driver opens; an
  explicit `--index` still takes precedence.
- **`_config.py` never muted server notifications.** `_driver_kwargs` gated
  `notifications_min_severity` on `inspect.signature(GraphDatabase.driver)`,
  whose signature is `(uri, *, auth, **config)`, so the key was never found
  and every "property does not exist" ping reached stderr. The kwarg is now
  always passed (default `OFF`; a `WARNING` floor would not have muted those
  pings, which are severity WARNING) and `get_driver` retries without it on
  drivers older than 5.6. Two tests cover both paths.
- **`search_vector` scoped searches no longer lose results to the top-k cliff;
  query timeouts are applied via `neo4j.Query`.**
- **`_sync_fact_tx` no longer swallows `TransientError`.**
- **The test suite was writing to whatever Neo4j `bolt://localhost:7687` reached.**
  `tests/conftest.py` now forces `NEO4J_URI` to `bolt://127.0.0.1:1` (unreachable) for
  every test, so a `Test Fact` node can no longer land in a live graph.

## [1.3.3] - 2026-06-05

QA audit round 8 — broad inconsistency sweep across all library and script
files. Fixes span two QA sessions: one for the library core (M-1–M-5, C-1)
and one for the CLI scripts (N-1–N-3, m-1–m-4). 113 tests, all passing.

### Fixed
- **[M-1] `parse_learned_topics` bullet extraction was fragile.**
  `_parse_key_points_and_summary` (used by `parse_learned_topics`) did not
  skip fenced code blocks, and its numbered-list regex (`^\d+\.`) matched
  version strings like `1.2.3 foo` as list items. Both bugs were absent in
  `_extract_bullets` (used by `parse_frontmatter_topic`). Fixed by porting
  fence tracking and tightening the regex to `^\d+\.\s` in the older parser,
  unifying behavior across both daily-note and frontmatter parse paths.
- **[M-2] `apply_metadata_only` always returned `related_count=0` for graph
  search results.** `search_graph` returned a `relationships` string field;
  `apply_metadata_only` looked for `relation_count` (a non-existent key) then
  fell back to `related_count`, also absent. Fixed: `search_graph` now
  includes `related_count: len(related)` in every result dict; the fallback
  chain in `apply_metadata_only` is removed.
- **[M-3] `MemoryClient.state()` opened a second driver on every call.**
  `MemoryStateManager.__init__` called `get_driver()` unconditionally,
  creating a new connection pool even when `MemoryClient` already had a
  cached driver. Fixed: `MemoryStateManager` accepts an optional `driver=`
  kwarg with an `_owns_driver` flag controlling whether `close()` tears it
  down. `MemoryClient.state()` now passes its cached driver.
- **[M-4] `sync_facts` and `rebuild_graph` ran graph maintenance in two
  separate transactions.** `link_related_facts` and `cleanup_orphaned_words`
  were sequential `execute_write` calls; a failure between them left the
  graph with broken RELATED_TO edges or orphaned Words with no rollback path.
  Fixed: combined into a single `_post_sync_tx` write transaction.
- **[M-5] `_build_traversal_cypher` always joined the Word index.**
  The `OPTIONAL MATCH (f)-[:HAS_WORD]->(w:Word) / WITH f, collect(w.text)`
  clause was hardcoded in the query template, paying the full Word-join cost
  even when only `name` was requested. Fixed: the join is now emitted
  conditionally on `need_words`.
- **[C-1] `Source` nodes and `FROM_SOURCE` edges were invisible to schema
  validation.** `learn._sync_fact_tx` has always written `Source` nodes and
  `FROM_SOURCE` edges, but `neo4j_seed.py` created no constraint on
  `Source.name`, and neither `validate_schema()` nor `verify_schema.py` knew
  the type existed. Concurrent `MERGE` runs could create duplicate Source
  nodes. Fixed: `source_name_unique` constraint added to `neo4j_seed.py`,
  `_config.py` `EXPECTED_CONSTRAINTS`, and `verify_schema.py`. A new
  `test_config_expected_constraints_matches_verify_schema` test guards against
  future drift between the two validators.
- **[N-1] `neo4j_traverse.py` accepted `--relationship PREREQUISITE_OF` at
  the CLI but rejected it in the library.** `PREREQUISITE_OF` was removed
  from `_ALLOWED_RELS` in v1.3.2 but left in the argparse `choices` list,
  producing a confusing runtime error after a clean parse. Removed from
  choices.
- **[N-2] `--fields related_count` silently suppressed graph search output.**
  `_MEANINGFUL_FIELDS` in `hybrid_memory_search.py` was not updated when
  `related_count` was added to `search_graph` results (M-2 fix), so
  `_has_meaningful_fields()` returned False and the section was suppressed.
  Added `related_count` to the set.
- **[N-3] `neo4j_backfill_assistant.py` silently skipped all Facts created
  via `ai_memory.learn`.** Both `backfill_label` and
  `create_created_by_relationships` keyed batch iteration on `n.id IS NOT
  NULL`. Facts written by `learn._sync_fact_tx` (which MERGEs on `name`
  without setting `id`) were therefore unreachable; on a pure-learn-path
  graph, backfill tagged 0 facts. Fixed: both functions now use
  `elementId(n)`, which is present on every node regardless of how it was
  created. `count_skipped_null_id()` removed — no longer applicable.
- **[N-3 follow-up] `create_created_by_relationships` could produce
  duplicate `CREATED_BY` edges under concurrent or re-run conditions.**
  The `CREATE (n)-[:CREATED_BY]->(a)` with a `WHERE NOT` pre-check is not
  atomic — two overlapping sessions can both pass the guard before either
  commits. Replaced with `MERGE`, which is idempotent.
- **[m-1] `HAS_WORD` removed from `_ALLOWED_RELS`.**
  At depth=1 it terminates at `Word` nodes (not `:Fact`), returning nothing.
  At depth=2 it finds Facts sharing a word — an expensive, undocumented
  equivalent of `RELATED_TO`. Removed from `_ALLOWED_RELS` and from
  `neo4j_traverse.py`'s argparse choices.
- **[m-3] `load_fact` empty-string session opt-out replaced with `track=`
  flag.** The old pattern (`pass session_id=""` to skip tracking) was
  undocumented except in an inline comment and inconsistent with
  `_resolve_sid`'s semantics elsewhere in `MemoryStateManager`. New
  signature: `load_fact(session_id, fact_name, *, track=True)`.
- **[m-4] `neo4j_learn_sync.py` used `requests` for embeddings; all other
  paths used the `ollama` SDK.** The script called the raw
  `/api/embeddings` REST endpoint via `requests.post`, while `search.py`
  and `neo4j_sync.py` used `ollama.embeddings()`. Migrated to the SDK;
  `OLLAMA_URL` env var dependency removed in favour of the SDK's
  `OLLAMA_HOST`. Two-attempt retry preserved.

### Added
- **[m-2] `MemoryClient.graph_stats()`** — wraps `ai_memory.graph.graph_stats`
  with the client's cached driver, consistent with `traverse()` and
  `trace_parameter()`. Previously `graph_stats` was exported in `__all__`
  but had no `MemoryClient` wrapper, requiring callers to import it directly
  and manage the driver themselves.

---

## [1.3.2] - 2026-06-04

Neo4j-usability round (QA round 7). Targeted findings from running the
library against a populated production graph (1,261 Facts, 7,431
ConversationTurns) and quantifying each issue live before fixing.

### Fixed
- **[N3] `search_graph.related_facts` walked the wrong relationship.**
  v1.3.1 traversed only `LEARNED_IN`, which is set by the legacy
  `neo4j_sync.py` path. Most Facts are written by `ai_memory.learn.sync_facts`
  via the Word index → `RELATED_TO`. On the test graph **77% of Facts had
  a `RELATED_TO` neighbor (avg 6.8); only 4% had a `LEARNED_IN` neighbor
  (avg 0.3)** — `search_graph` was silently dropping related-fact data for
  73% of results. The Cypher now uses `[:RELATED_TO|LEARNED_IN]`. Live
  verified: results with populated `relationships` jumped from ~30% to ~60%.
- **[N4] Connection / index errors no longer collapse to `[]`.** Library
  callers can now distinguish "Neo4j is down" from "the query returned zero
  hits." The new `ai_memory.exceptions` module defines
  `Neo4jConnectionError`, `Neo4jIndexNotFoundError`, and `Neo4jQueryError`
  (all derived from `AIMemoryError`). `search_vector` / `search_graph` /
  `get_driver` raise the typed exceptions; query-ran-zero-hits still
  returns `[]`. Ollama unavailability is left as `[]` (separate dependency).
- **[N2] Vector-index name discovery.** When `NEO4J_VECTOR_INDEX` doesn't
  match the configured index, `search_vector` now raises
  `Neo4jIndexNotFoundError` with the list of vector indexes Neo4j actually
  reports and a hint to set `NEO4J_VECTOR_INDEX` in `.env.neo4j`. v1.3.1
  silently returned `[]`.
- **[N9] Per-query timeout.** Read paths now pass `timeout=` (default 30s,
  override via `NEO4J_QUERY_TIMEOUT_S`). Prevents indefinite hangs on a
  slow/locked DB.

### Changed
- **[N1] Driver pooling.** `MemoryClient` now caches a single driver and
  closes it cleanly in `close()` / `__exit__`. `search_vector` and
  `search_graph` accept an optional `driver=` keyword; `MemoryClient`
  passes its cached driver through automatically. Reused drivers cut
  per-call overhead by ~30% in live measurement (more dramatic with
  TLS / remote DBs). Standalone callers retain the one-shot behavior.
- **`get_driver` now sets `max_connection_pool_size=50`,
  `max_connection_lifetime=3600`, `connection_acquisition_timeout=30`**,
  and `notifications_min_severity="WARNING"` when the installed driver
  supports it (≥5.6) — mutes the "property does not exist" notification
  chatter without losing real warnings.
- **`get_driver` raises `Neo4jConnectionError`** for auth, network, and
  TLS failures, replacing the bare `Exception` propagation.

### Added
- **`ai_memory.validate_schema(driver, *, vector_index=...)`** — a
  diagnostic helper that diffs expected constraints/indexes against the
  live schema, classifies them as `ok` / `drift` / `missing`, and lists
  available vector indexes. Useful for new-graph onboarding and for
  troubleshooting "search returns nothing" before reaching for the source.
- **`scripts/migrate_to_name_keying.py`** — v1.1 → v1.2 dedupe helper.
  Dry-run by default; `--apply` merges duplicate-name Facts via APOC's
  `mergeNodes` after explicit confirmation. Resolves the migration gap
  documented in `MIGRATION.md` but previously without tooling.

### Notes
- Connection/auth/index errors are now exceptions instead of silent
  `[]`. Callers that previously checked `if not results:` to gate
  fallback paths should add an `except Neo4jConnectionError` clause.
  The new behavior surfaces real infrastructure issues that v1.3.1
  hid.

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
