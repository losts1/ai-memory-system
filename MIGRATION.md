# Migration Guide

This file lists *what changed* per version. For the step-by-step procedure of taking a
new public version into an existing install (fetch, reinstall, seed, migrate, redeploy,
verify, roll back) see [docs/UPDATING.md](./docs/UPDATING.md).

---

## Upgrading to v1.3.x (from v1.2.0)

**No breaking changes.** Notable additions and behavior changes:

- **Provenance** (v1.3.x): `Provenance` dataclass, `MemoryClient.write(name, summary=, key_points=, provenance=)`, flat `provenance_*` properties on Fact nodes, and a nested `provenance:` frontmatter block parsed by the learn pipeline. See `docs/PROVENANCE.md`.
- **Trust filtering**: `search()` / `search_vector()` / `search_graph()` gained `trust_filter`. Note it **raises `ValueError`** when combined with `use_embeddings=True` (FAISS results carry no provenance metadata).
- **`search_files()`**: gained a `max_files` parameter; file-scan cap and ordering changed (see CHANGELOG v1.3.1).
- **Python 3.9 compatibility** (v1.3.3): `X | Y` union syntax reverted to `Optional[...]` — the package imports cleanly on 3.9 again.

---

## Upgrading to v1.2.0 (from v1.0.x or v1.1.x)

**One breaking schema change.** Existing CLIs and library imports continue to work,
but a one-time graph dedupe may be required before the new constraint will apply.

### What changed

`Fact.name` is now the **primary identity** for a Fact node:

| Before (v1.0/1.1) | After (v1.2) |
|-------------------|--------------|
| `neo4j_sync.py` MERGEd on `f.id` (sha256 of `file:name`) — same fact in two files → two nodes | `neo4j_sync.py` MERGEs on `f.name` — one node per fact name |
| `ai_memory.learn` MERGEd on `f.name` — no `f.id` set | `ai_memory.learn` unchanged — still MERGEs on `f.name` |
| The two paths could create **duplicate Fact nodes** with disjoint property sets | Both paths converge on the same node, properties merge |
| Schema had unique constraint on `f.id` only | Schema adds unique constraint on `f.name` (existing `f.id` constraint kept) |

`f.id` is still set by `neo4j_sync.py` (preserved via `coalesce` so legacy lookups
in `neo4j_backfill_assistant.py` keep working). Facts created only by `ai_memory.learn`
have no `f.id` — Neo4j 5 `IS UNIQUE` constraints ignore nulls, so this is allowed.

### Migration steps

**1. Dedupe existing facts by name (only if upgrading an existing graph).**

The new `fact_name_unique` constraint will fail to install if any duplicate names exist
(common after running `neo4j_sync.py` on the same facts across multiple session files).
`neo4j_seed.py` catches this and prints a warning rather than crashing, but the
constraint will not be active until duplicates are merged.

Check for duplicates:

```cypher
MATCH (f:Fact)
WITH f.name AS name, count(*) AS n
WHERE n > 1
RETURN name, n
ORDER BY n DESC LIMIT 20;
```

Merge them (requires APOC):

```cypher
MATCH (f:Fact)
WITH f.name AS name, collect(f) AS dups
WHERE size(dups) > 1
CALL apoc.refactor.mergeNodes(dups, {properties: 'discard', mergeRels: true}) YIELD node
RETURN count(node);
```

`properties: 'discard'` keeps the first node's properties on collisions (use `'combine'`
to keep all values as arrays). `mergeRels: true` consolidates duplicate relationships.

**2. Re-run the seed to install the new constraint.**

```bash
python3 scripts/neo4j_seed.py
```

**3. Verify.**

```cypher
SHOW CONSTRAINTS YIELD name WHERE name = 'fact_name_unique' RETURN name;
```

### Why this matters

Before v1.2, the same conceptual fact could exist as two separate nodes — one created
by `neo4j_sync.py` (with `id`, `content`, `source`) and one by `ai_memory.learn` (with
`summary`, `key_points`, `source_file`). Downstream queries returned divergent shapes
depending on which sync produced the node, and traversal results undercounted
relationships. The v1.2 schema collapses these into a single node per fact name.

### Library / read changes

`ai_memory/state.py` had several correctness fixes in v1.2:

- `MemoryStateManager.cleanup()` now returns the actual count of deleted sessions
  (previously returned 0 or 1 due to a Cypher grouping bug).
- `MemoryStateManager.load_fact()` now MERGEs a `MemoryFact` when called directly
  without a prior `record_query` — previously it silently dropped the tracking.
- Read methods (`get_pending`, `get_summary`, `list_sessions`, helpers) no longer
  call `_ensure_session`, so reading a non-existent session returns empty rather
  than creating it, and `updated_at` is not bumped on reads (preserving `cleanup`
  TTL semantics).
- `MemoryClient.state(session_id=…)` and `MemoryStateManager(session_id=…)` now
  bind a default session_id. All session-id-taking methods accept `Optional[str]`
  and fall back to the bound value:

  ```python
  with client.state("weft:main") as mgr:
      mgr.init_session()           # uses bound "weft:main"
      mgr.record_query("gamma", results)
      pending = mgr.get_pending()
  ```

  Explicit per-call session_id still overrides the bound default. Existing code
  passing session_id explicitly to every call continues to work.

- `MemoryClient.search(graph=True)` now dedupes graph results by `name` against
  the vector/FAISS results (vector wins).

---

## Upgrading to v1.0.0 (from v0.2.x or v0.3.x)

**No breaking changes to the CLI.** All existing scripts work identically.

### What changed

Three scripts are now thin wrappers around the `ai_memory` library. Their CLI
interfaces are **identical** — if you run them from the command line, nothing changes.

| Script | Old behaviour | New behaviour |
|--------|--------------|---------------|
| `scripts/hybrid_memory_search.py` | All logic inline | Imports from `ai_memory.search` |
| `scripts/rlm/neo4j_traverse.py` | All logic inline | Imports from `ai_memory.graph` |
| `scripts/rlm/memory_state.py` | All logic inline (broken) | Imports from `ai_memory.state` (fixed) |
| `scripts/rlm/metadata.py` | Functions inline | Re-exports from `ai_memory.metadata` |

### Bug fix: memory_state.py NameError

The old `memory_state.py` had a pre-existing bug: `MemoryStateManager.__init__` called
`get_driver()` which was never defined in that file, causing a `NameError` at runtime.

If you were **subclassing `MemoryStateManager`**, the `__init__` signature changed:

```python
# Old (broken — NameError at runtime)
def __init__(self):
    self.driver = get_driver()   # NameError!

# New (fixed)
def __init__(self, workspace=None):
    self.driver = None
    self.driver = get_driver(workspace)
```

Update any subclass `__init__` to accept `workspace=None` and call `super().__init__(workspace)`.

### New capability: import as a library

After `pip install -e .` (or with the repo root on PYTHONPATH):

```python
from ai_memory import MemoryClient

with MemoryClient() as client:
    # Semantic search (requires Neo4j + Ollama)
    results = client.search("transformer attention")

    # Filter by assistant/mind (Phase 2 multi-tenancy)
    results = client.search("inventory management", assistant="Weft")

    # Graph traversal (requires Neo4j)
    facts = client.traverse("Attention Is All You Need", depth=2)

    # RLM parameter tracing
    matches = client.trace_parameter("Avellaneda-Stoikov", "gamma")

    # Per-session memory state (requires Neo4j)
    with client.state("weft:main") as mgr:
        mgr.init_session("weft:main")
        pending = mgr.get_pending("weft:main")
```

Low-level functions are also importable directly:

```python
from ai_memory._config import get_workspace, get_driver
from ai_memory.search import search_vector, search_graph, search_files, search_faiss
from ai_memory.graph import traverse, trace_parameter, graph_stats
from ai_memory.metadata import apply_metadata_only, apply_fields_filter, make_teaser
from ai_memory.state import MemoryStateManager
```

### Workspace resolution

All library functions accept an optional `workspace` parameter:

```python
from ai_memory.search import search_files

# Uses AI_MEMORY_DIR env var or ~/.ai-memory by default
results = search_files("gamma")

# Explicit path
results = search_files("gamma", workspace="/path/to/my-memory")
```

---

## Upgrading to v0.2.0 (from v0.1.x)

**No breaking changes.** The `--assistant`/`--mind` flag is opt-in everywhere.

### What changed

All scripts gained an `--assistant`/`--mind` flag for tagging data to a specific mind.
When the flag is not passed, behaviour is identical to v0.1.

### Backfill an existing graph

If you have an existing v0.1 graph and want to register the primary mind:

```bash
# Dry run first (strongly recommended)
python3 scripts/neo4j_backfill_assistant.py --primary "Nova" --dry-run

# Real backfill (tags all existing Fact/Session/Event/etc nodes)
python3 scripts/neo4j_backfill_assistant.py --primary "Nova"

# Optionally wire CREATED_BY relationships (slower on large graphs)
python3 scripts/neo4j_backfill_assistant.py --primary "Nova" --create-relationships
```

### Schema changes (fresh install via neo4j_seed.py)

The following are **additive** — they do not affect existing queries:
- New `Assistant` node label with unique constraint on `id`
- New range indexes: `fact_assistant_idx`, `session_assistant_idx`

### New template directory

`templates/submind/` — starter files for a new mind attaching to an existing graph
(rather than bootstrapping from scratch). See [docs/SUBMINDS.md](./docs/SUBMINDS.md).

---

## Upgrading to the hybrid retrieval path (unreleased)

- **The default changed.** `MemoryClient.search(query)` with `graph=False`
  (the default) used to run a single vector query; it now always runs the
  hybrid path — the vector leg plus the two fulltext legs, fused by RRF —
  regardless of `graph=`. Pass `mode="vector"` for the old vector-only
  behaviour; note it now also applies the supersede sink and the 0.80
  vector-only floor, which previously only ran under `mode="hybrid"`.
- `MemoryClient.search(graph=True)` no longer appends `related_facts` /
  `relationships` to each hit. Hybrid mode (the default) already runs the
  lexical leg, so `graph=True` is now a no-op alias for `mode="hybrid"` — and
  passing `graph=True` together with an explicit `mode="vector"` or `"fulltext"`
  raises `ValueError` instead of silently running hybrid.
  To get related Facts, call `client.traverse(name, depth=1)`.
- `search_graph()` no longer returns `related_facts`, `relationships` or
  `related_count`; it returns the same hit dict as `search_vector()`.
- **`search_vector()` hit dict keys changed.** Removed: `content`, `summary`.
  Added: `teaser`, `key_points`, `assistant`, `status`, `space`, `via` (and
  `source`; vector-origin hits also carry `vec_score`). `--fields summary`
  and `--fields content` no longer select anything — use `--fields teaser`.
  `--metadata-only` now reads `teaser` (it used to read `summary`/`content`).
- New keyword arguments: `space=` (shared-space filter) is new on
  `MemoryClient.search`, `search_vector`, and `search_graph`. `mode=`
  ("hybrid" | "fulltext" | "vector") is new on `MemoryClient.search` and
  on the new `ai_memory.search.search_hybrid()`.
- The vector leg filters inside the index when the index declares filter
  properties (see `scripts/neo4j_migrate_vector_filters.py`, phase 3). Until
  then it logs one warning per process and over-fetches; results are correct
  but a small mind may return fewer than `k` on very large graphs.
- `NEO4J_FULLTEXT_KP_INDEX` (default `fact_key_points`) names the key-points
  fulltext index; `scripts/neo4j_seed.py` creates it on fresh installs. On an
  existing graph create it with `CREATE FULLTEXT INDEX fact_key_points IF NOT EXISTS FOR (n:Fact) ON EACH [n.key_points]`.
- **A missing vector index no longer raises.** v1.3.2 raised `Neo4jIndexNotFoundError`
  from `search_vector()`; the vector leg now logs one warning and returns `[]`, so
  `search()` degrades to the two fulltext legs. Check `ai-memory stats` /
  `validate_schema()` rather than relying on an exception. The library's default index
  name is `fact_embeddings`; the grok client's is `factEmbeddingIndex` — set
  `NEO4J_VECTOR_INDEX` in `.env.neo4j` so both agree with the live graph.

### Embedding provenance and the canonical text (phase 2)

New Fact properties: `embedding_model`, `embedding_dim`, `embedding_text_sha`,
`boilerplate_version`, and (during a backfill run with `--keep-prev`) `embedding_prev`.
New node: `(:RetrievalConfig {id: "current", version, boilerplate, updated_at})`.

Existing graphs: run `python scripts/neo4j_seed.py` (idempotent; creates the config node),
then `ai-memory embed --all --keep-prev`. This re-embeds every Fact from the canonical
text (about 10 s per 1,500 Facts on local Ollama) and publishes a new `RetrievalConfig`
version. Vectors written by tooling outside this repo have no sha and show as `foreign`
in `ai-memory stats`; the backfill makes them canonical. Compare retrieval quality with
`ai-memory eval` before and after; if the gate passes run `ai-memory embed --drop-prev`,
otherwise `ai-memory embed --rollback` restores the previous vectors (and clears the
provenance properties, since their text is unknown). Rollback restores only Facts that had
a previous vector; a Fact first embedded in this run keeps its vector and provenance
(there is nothing to restore it to).

Writers: `MemoryClient.write()` and `learn()` now embed when Ollama and the config node
are reachable (they previously never embedded). `neo4j_sync.py` no longer embeds
`name + content[:500]`; `neo4j_learn_sync.py` no longer embeds key points only and no
longer keeps a pickle cache under `memory/embeddings/`. The grok client still embeds its
own text until phase 4.

The `EMBEDDING_MODEL` environment override that `neo4j_learn_sync.py` used to honour is
gone — every writer embeds with `ai_memory.embed.EMBED_MODEL` (`nomic-embed-text`,
768-d); changing the model is a code change plus a full `ai-memory embed --all`, because
the vector index is 768-d and provenance records the model.

`ai-memory embed --stale-only` re-embeds only Facts whose prepared text changed under the
current config; `--all` publishes a new config version and re-embeds everything, and is
idempotent — re-run it after an interrupted run and it converges.

### Vector index filter properties (phase 3)

Why: the vector leg previously filtered `assistant`/`space`/`provenance_trust` by
over-fetching a pool and post-filtering in Cypher. Neo4j 2026.04's `SEARCH … WHERE`
can filter inside the index itself when the index declares those properties, which is
more accurate on a small pool and avoids the over-fetch warning (see the phase-1 note
above). This requires a one-time index rebuild, because a vector index's declared
properties cannot be altered in place — the filter-property list is set at `CREATE`
time.

**Predicate limits (measured on Neo4j 2026.04):** inside `SEARCH … WHERE`, only equality
(`=`) and `IS NULL` are accepted; `IN [...]` and `<>` are rejected
(`SyntaxError: The vector search filter predicate … not supported`).
`ai_memory.retrieval.build_filters` only ever builds equality clauses, AND-joined, so
it stays compatible; `status` is never filtered in-index (spec §3).

**Membership (measured on Neo4j 2026.04, 1,502 embedded Facts):** an unscoped
`SEARCH … LIMIT 2000` returned all 1,502 Facts, including the 165 with no `assistant`,
the 1,453 with no `space`/`status`, and the 1,501 with no `provenance_trust` — a node
missing a declared filter property is still indexed and returned. No sentinel values
are needed for facts that lack a property.

**Procedure:**

1. `python scripts/neo4j_migrate_vector_filters.py --preflight` — creates a temporary
   `<index>_v2` index with the filter properties, waits for it to come online, measures
   population time, checks that it still returns every embedded Fact (including those
   missing a declared property) and that each property's most common value is
   filterable, then always drops `<index>_v2` (even on error). Read the printed report
   before scheduling `--migrate`. `--preflight` is for an **un-migrated** index: if the
   live index already carries every requested filter property (a previous `--migrate`
   already ran), it returns `{"ok": true, "already_migrated": true, "index", "props",
   "show_properties"}` without creating `<index>_v2` or requiring Ollama — re-verify a
   completed migration with `validate_schema()["vector_filter_props"]` instead.
2. Schedule the migration window (see below), then run
   `python scripts/neo4j_migrate_vector_filters.py --migrate`. This drops the live
   index and recreates it with the filter properties, waits for `ONLINE`/100%
   population, then runs the same two checks as the gate.
3. `--dry-run` applies to `--migrate` only — it prints the `DROP INDEX` /
   `CREATE VECTOR INDEX` statements it would run and runs neither, for reviewing the
   exact DDL first. `--preflight --dry-run` is rejected at argparse time (exit 2):
   `--preflight` never mutates the live index, so "dry run" has nothing to add.
4. `--index` defaults to `NEO4J_VECTOR_INDEX` as loaded from the workspace's
   `<workspace>/.env.neo4j` (falling back to `fact_embeddings`), resolved after the
   driver opens; pass `--index <name>` explicitly to override. `--props` defaults to
   the same four properties (`assistant,space,status,provenance_trust`) and accepts a
   comma-separated override; an empty `--props` is rejected at argparse time (exit 2).
   Any exception from `--preflight`/`--migrate` (including a rejected `CYPHER 25`
   probe or a failed CREATE) is caught at the CLI boundary and reported as
   `{"ok": false, "mode", "index", "error"}` — printed and written to `--json` if
   given — with exit 1 and no traceback.

**The gate** (run automatically by `--migrate`, and by `--preflight` against the
temporary index): after `CREATE`, wait until `SHOW INDEXES` reports `state = 'ONLINE'`
and `populationPercent = 100`; then (a) an unscoped `SEARCH … LIMIT $pool` (`$pool` =
the count of Facts with `embedding`) must return every one of those Facts by distinct
name; (b) for every declared property that at least one Fact carries, `SEARCH … WHERE
f.<prop> = $v` with that property's most common value must return at least one row. A
failed gate exits non-zero and prints what failed; **the script never drops the new
index on gate failure** — the old index is already gone, so it leaves the (possibly
broken) new one in place for the operator to inspect rather than deleting evidence. A
Fact deleted between the `embedded_count` and the membership `SEARCH` (both run inside
the same gate) makes `returned == embedded - 1` and fails the gate spuriously — this is
non-destructive (the new index is kept; re-running the gate passes), so treat a
membership failure with a `missing_sample` of one very recently deleted-looking name as
"re-run" before "investigate". The exact-equality membership check (`returned ==
embedded`) is fine at this graph's size (1,502 Facts) and cost (~1 s), but `SEARCH` is
an approximate (HNSW) search — at an order of magnitude larger a healthy index may
legitimately not return every node for `k = embedded`, so exact equality should be
revisited (a tolerance, or chunked probes) before this graph grows that much.

**The window:** measured population time is ~1.0 s for 1,502 Facts, but the count on
the live graph may be larger. While the index is `DROPPED`/being recreated, callers of
`db.index.vector.queryNodes(<index>)` (the fallback path) and `SEARCH` (the in-index
path) both fail with an index-not-found error; `search_vector` catches this and returns
`[]` with one warning, no latch — the vector leg is skipped for that call and
`search_hybrid` degrades to lexical-only (the two fulltext legs still run) until the
index is back online. Separately, if the process had already latched the over-fetch
fallback before the migration (e.g. from the old index lacking filter properties), that
latch independently expires after `FALLBACK_TTL_S` (600 s) and re-probes the in-index
path on its own — no restart needed either way.

**`CYPHER 25` requirement:** the `CREATE VECTOR INDEX … WITH […]` filter-property list
is Cypher 25 syntax; under the Cypher 5 default Neo4j 2026.04 rejects it (`Invalid
input 'WITH': expected 'OPTIONS'`), so `build_create_index_ddl` prefixes the statement
with a literal `CYPHER 25` line (`build_drop_ddl`'s plain `DROP INDEX` is unaffected —
it's Cypher 5). **On a Neo4j 5.x server that only supports Cypher 5:** `scripts/neo4j_seed.py`
creates the vector index *without* the filter properties (see below) — a fresh install
is never left without a vector index, and `search_vector`'s existing over-fetch fallback
keeps queries working, just without in-index filtering. `--migrate` probes `CYPHER 25`
support before the DROP and aborts before touching the live index if the server rejects
it (`RuntimeError: server rejected Cypher 25; migration aborted before any change`);
`--preflight`'s temporary `<index>_v2` creation is not destructive to the live index
either way. Either mode's failure is reported as `{"ok": false, "error": …}` at the CLI
(see the procedure above) rather than a traceback.

**Ollama dependency:** both `--preflight` and a non-`--dry-run` `--migrate` embed a
fixed probe query to run the membership/property checks; if Ollama is unreachable the
script raises rather than running the checks with no vector.

**Already migrated:** `--migrate` (like `--preflight`) returns `{"ok": true,
"already_migrated": true}` without dropping anything when the live index already carries
every requested property, so re-running it is safe.

**CREATE failure after the DROP (a different path from the gate above):** if the
filtered `CREATE` itself is rejected once the old index is gone, the script tries to
recreate a *plain* (unfiltered) vector index under the live name so search keeps
working, then raises `RuntimeError` stating whether that recovery succeeded
(`recovered: yes|no`) and the exact filtered `CREATE` to re-run. A recovered index has
no in-index filters (queries take the over-fetch fallback) until that statement is
re-run; `recovered: no` means there is no vector index at all until you recreate one.

**Index name:** the default `NEO4J_VECTOR_INDEX` is `fact_embeddings` (what
`neo4j_seed.py` creates on a fresh install); the original production graph on Neo4j
2026.04 used `factEmbeddingIndex`. The script resolves `--index` after the workspace
`.env.neo4j` is loaded, so set `NEO4J_VECTOR_INDEX` there — with no env at all the DROP
targets a non-existent name (`IF EXISTS`, a no-op) and the CREATE targets the wrong one.

`validate_schema()` reports `vector_filter_props` (`"ok"`, `"missing: [...]"`, or
`"index not found"`) for the configured index — use it to confirm a migration landed.
`scripts/neo4j_seed.py` now creates the vector index with the filter properties on
fresh installs against a Cypher-25-capable server, so a new install there never needs
this migration; on a Cypher-5-only server it creates the plain (unfiltered) index
instead and this migration is how you later add the filter properties once the server
is upgraded.

`NEO4J_VECTOR_INDEX` must be a plain identifier (`^[A-Za-z_][A-Za-z0-9_]*$`) — it is
inlined (not parameterised) into the `SEARCH` clause. A hyphenated or otherwise
non-identifier name now raises `ValueError` from `search_vector` instead of silently
falling back to `db.index.vector.queryNodes`; rename the index or set
`NEO4J_VECTOR_INDEX` to match.

After migrating, `ai_memory.eval.harness` accepts `--subset all|scoped|unscoped` to
grade the golden set's filtered and unfiltered queries separately (the two-split ship
gate) — `scoped` keeps only golden entries with a non-empty `filters` dict, `unscoped`
keeps only those without one, `all` (the default) keeps everything. `hybrid_search`
(the harness's ranker under test) benefits from in-index filtering; `hybrid_fallback`
remains callable in the same harness run for side-by-side comparison.

### Grok client (phase 4)

The Grok Build TUI's Bolt client (`grok/skills/neo4j-memory/scripts/neo4j_memory.py`)
now searches, writes, and embeds on the same contract as the library (`tests/test_retrieval_contract.py`
enforces it). Its vector leg is the Cypher 25 `SEARCH ... WHERE ... LIMIT $k` clause
with in-index equality filters (`search --assistant/--space/--trust`) — it **requires
the phase-3 migrated index** and has no fallback: on an un-migrated index the vector
leg errors and search degrades to fulltext-only, the same as Ollama being down.

Redeploy an existing `~/.grok` install by copying the **whole** skill directory
(`cp -r grok/skills/neo4j-memory/. ~/.grok/skills/neo4j-memory/`) — `SKILL.md`
changed with the scripts, and it is what Grok reads to learn the commands; hooks
and rules did not change. Do not copy only `neo4j_memory.py` +
`test_neo4j_memory.py`: that two-file recipe (old `grok/README.md`) is the root
cause of live `SKILL.md` lagging the script. Then `diff -rq` the
two trees (step 4 in `grok/README.md`) so a later script-only commit cannot
silently leave `~/.grok` stale. Then run `embed` once so Facts already written by Grok pick up
`embedding_model`, `embedding_dim`, `embedding_text_sha`, and `boilerplate_version`
(prior Grok writes have an embedding but no provenance, so they are picked up by the
"foreign vector" branch of the selection, not just "missing").

### Edge layer (phase 5)

RELATED_TO edges are no longer a shared-word MERGE. Each Fact now picks its own top
5 neighbours by a blend of TF-IDF cosine (over `Word.idf`, tokenized from the
canonical embedding text) and embedding cosine, each z-scored against random-pair
baselines for the corpus and kept only above a floor set at the blend's 99th
percentile over random pairs; near-duplicates (trailing-suffix twins, `SUPERSEDES`
chains, cosine ≥ 0.95) never consume a pick. An edge exists while either endpoint
still picks it, and carries `weight`, `tfidf`, `cos`, `shared_keywords`, `picked_by`,
`via`, `rule_version`.

**If you call `traverse()`/`trace_parameter()` or read `RELATED_TO` directly:**
edges are now sparser (capped at 5 picks per Fact instead of every shared-word pair)
and weighted — sort or filter on `weight` if you want the strongest ones first.
`related_count`'s *definition* is unchanged (outgoing `RELATED_TO` count), but its
*values* are not: edges are sparser and the direction convention moved from creation
order to name order, so the set of Facts that report `related_count == 0` changes —
don't read "semantics unchanged" as "per-Fact values unchanged".

**Cutover order.** `ai-memory nightly [--seed] [--json]` runs, in order: (1) publish
boilerplate + re-embed (the existing phase-2 step), then (2) rebuild the edge layer
and cut over. The rebuild publishes a new `rule_version` on `RetrievalConfig` and
deletes every `RELATED_TO` edge whose `rule_version` doesn't match it. **The first
nightly run after upgrading deletes every legacy shared-word edge** — export them
first if you want a record:

```cypher
MATCH (a)-[r:RELATED_TO]->(b) RETURN a.name, b.name, properties(r)
```

Publishing the new `rule_version`, writing the new edges, and deleting the
stale-`rule_version` edges are three separate statements, not one transaction. A
crash between them can leave `rule_version` bumped with legacy edges still present
and only part of the new layer written; recovery is just re-running `ai-memory
nightly` (it bumps the version again and cuts over everything else), and the
legacy edges were already exported above, so the risk is bounded, not silent data
loss.

`ai-memory edges (--rebuild|--dry-run) [--seed] [--pairs] [--k] [--json]` runs the
same rebuild standalone (`--dry-run` computes and reports without writing).
`ai-memory eval-edges` judges a seeded `RELATED_TO` sample against the edge rubric
(`--legacy`/`--rule-version` to pick the sampled population, `--gate-against
<previous --json>` to gate against an earlier run) — a phase-5-only counterpart to
`ai-memory eval`. `ai-memory stats` now also reports edge health: edge count,
current/stale `rule_version` counts, isolated-Fact percentage, max/p95 degree.

**On-write maintenance is automatic for all three write paths, and needs no extra
install there:** `MemoryClient.write()`/`learn()`, the grok client's
`write`/`write_shared`, and `scripts/neo4j_sync.py` all call
`wordindex.maintain_edges_for` (`ai_memory.learn.maintain_edges_after_write` for
the last two) after each write, re-picking just the written Fact and any
neighbour whose worst pick it now beats. (At the initial phase-5 cutover,
`scripts/neo4j_sync.py` was not wired to this — it MERGEd the Fact and wrote its
embedding but called no tokenizer and no edge maintenance, so Facts written
through it went stale until the next `ai-memory nightly`; a follow-on task wired
it in, and this is no longer the case.) The nightly full rebuild, `ai-memory
edges --rebuild`, and `ai-memory edges --dry-run` are the pieces that need numpy
(all three call into `rebuild_edges`, which imports numpy before its dry-run
early return): `pip install 'ai-memory-system[edges]'` (new `edges` extra).
Without it, on-write maintenance still runs; all three `edges`/`nightly` modes
above raise until numpy is installed.

**The Word index changed.** `HAS_WORD` tokens now come from the canonical embedding
text (`fact_embed_text`, ≤24 tokens per Fact) instead of from name words alone —
`Fact.tfidf_norm` is new. `learn.link_related_facts` and `learn._post_sync_tx` are
removed; the edges they wrote are superseded by `maintain_edges_for`, which
`MemoryClient.write()`/`learn()`, the grok client, and `scripts/neo4j_sync.py`
all call on write.

**Grok client redeploy required.** `organize` no longer MERGEs edges between
Facts sharing ≥2 words — it runs the same on-write rule
(`_maintain_edges_for`, a verbatim port of `wordindex.maintain_edges_for`) over
one mind's Facts, and now requires a published edge rule (run the library's
`ai-memory nightly` first); it refuses with an error if no `RetrievalConfig`
with a `rule_version` exists. Writes also tokenize through the new rule
(`_write_tokens`, replacing the old `_set_words`). Redeploy by copying the
whole skill directory (`cp -r grok/skills/neo4j-memory ~/.grok/skills/`), same as
the phase-4 redeploy above — `SKILL.md` changed again in this phase.

### Duplicate report and supersede (phase 6)

`ai-memory duplicates` finds same-topic re-learnings: Facts whose names share
a base once a trailing time/date suffix is stripped, and Facts whose
embeddings are near-copies (cosine ≥ 0.95 by default — `--cos` to change it),
found with one vector-index `SEARCH` query per embedded Fact and the exact
cosine recomputed server-side. The two signals are merged into groups and
rendered as a markdown report (`--markdown PATH`, or stdout; `--json PATH`
for the machine-readable form). **The report never writes anything** — no
Fact is merged, superseded, or otherwise changed by running it.

A group already needs no action when it has at most one live (non-superseded,
non-removed) member, or its members are already fully connected by
`SUPERSEDES` edges — these are omitted from the report unless you pass
`--include-handled`. For every other group the report suggests a keeper —
newest dated name suffix (a clock-only suffix does not count), else newest
`updated_at`, else newest `created_at`,
else the only live member, else the lexicographically last name — and
prints a ready-to-paste `ai-memory supersede <keeper> <other> --apply` line
per non-keeper member. **A group whose members span more than one `assistant` or
`space` is printed as `needs_owner_decision` with no suggested keeper and no
commands** — cross-mind or cross-space duplicates are an ownership call the
report deliberately declines to make; read the group's table and decide by
hand which Fact (if any) should supersede the others.

`ai-memory supersede` is the only thing that writes. Run it either as
`ai-memory supersede NEW OLD [--apply]` for one pair, or
`ai-memory supersede --from-file decisions.json [--apply]` for a batch, where
`decisions.json` is a JSON list of objects:

```json
[
  {"new": "keeper-name", "old": "duplicate-name", "apply": true},
  {"new": "keeper-name", "old": "another-duplicate", "apply": false}
]
```

`apply` must be a JSON boolean (`true`/`false`), not a string. Every row is
planned and validated first — an unknown `new`/`old` name, `new == old`, an
`old` already superseded by a different Fact, or a pair that would create a
`SUPERSEDES` cycle is refused with a reason and never written, `--apply` or
not. **Without `--apply`, the command only prints the plan and changes
nothing** — run it that way first to check your decisions file; it exits 1
if any row is refused, so a clean exit 0 is itself a validation pass. With
`--apply`, only rows that are both `ok` and `apply: true` are written.

A successful supersede sets `status='superseded'`, `superseded_at`, and
`updated_at` on the old Fact, sets `status` on the new Fact only if it was
unset, and adds a `SUPERSEDES` edge from new to old carrying `at`/`by` — the
same write the grok client's shared-write `--supersede` path already makes,
minus that path's `old.space = 'shared' AND old.status = 'active'`
precondition: library Facts have null `status`/`space`, so `ai-memory
supersede` guards instead by unknown-name, self-pair, already-superseded, and
cycle checks, and will supersede a Fact the grok client would refuse.
Superseded Facts sink in ranking immediately (`rank_adjust`'s same-topic
collapse drops an inactive hit once an active hit on the same `SUPERSEDES`
chain is present) and stop being eligible edge picks (`is_duplicate` excludes
any pair connected by a `SUPERSEDES` chain) from the next write or the next
`ai-memory nightly` — `maintain_edges_for` re-picks on write, and a full
rebuild re-picks everyone.

"Live" means the same thing everywhere in this path — a `status` that is
neither `superseded` nor `removed`, so the NULL `status` every library Fact
carries counts as live. The keeper rule uses that definition too (it used to
require a literal `status = 'active'`, which disagreed with the
already-handled test, the command emission and ranking's `_is_active`).

### Library writers refuse to update another mind's Fact

Both library write paths — `ai_memory.learn` (`write_fact` / `sync_facts` /
`MemoryClient.write` / `MemoryClient.learn`) and `scripts/neo4j_sync.py` —
MERGE a Fact **by name**, which is an in-place overwrite of whatever node
already carries that name. They now read the existing node's `assistant` first
and refuse when it belongs to someone else:

- existing Fact tagged with a different `assistant` than the writer's —
  including a writer that passes none — the write is **refused**: no property
  is set, no words, embedding or edges are touched, one line goes to stderr
  (`Refusing to overwrite Fact 'X' owned by 'Nova' (writer: 'Claude')`), and
  the caller sees it (`write_fact`/`sync_facts` do not count it,
  `write_fact_with_embedding` returns `owner_conflict` and `neo4j_sync`'s run
  summary reports the count).
- existing Fact **untagged** (NULL/blank `assistant`) — allowed, and a tagged
  writer claims it. This is the one deliberate relaxation against the grok
  client's `_owner_blocks_write`, which refuses untagged Facts without
  `--force-assistant`: library Facts are the library's own inherited memory.
- no existing Fact — created as before.

There is no force flag on either side of the tagged case: to write a Fact
whose name is taken by another mind, use a different name (or sync under that
mind's `--assistant`). `MemoryClient.write()` takes a new optional
`assistant=` keyword to supply that writer identity; the default stays
untagged, and an untagged writer cannot update a tagged Fact. `ai-memory
supersede` is unchanged — it stays the deliberately owner-operated tool
described above.
