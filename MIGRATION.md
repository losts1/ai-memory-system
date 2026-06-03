# Migration Guide

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
