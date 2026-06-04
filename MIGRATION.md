# Migration Guide

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
