# AI Memory System — v1.0.0 Release Announcement

*June 2026*

---

The AI Memory System public package has reached v1.0.0. This post covers what's
new, why it matters, and how to take advantage of the latest capabilities.

---

## What is the AI Memory System?

A hybrid memory architecture for AI agents: markdown session files for human-readable
history, structured QMD summaries for indexed recall, and a Neo4j knowledge graph for
semantic + graph-based search. The package gives a new AI agent everything it needs to
build persistent memory that survives session restarts.

The public redistribution package is a clean bootstrap kit. The full production system
(deeper RLM tooling, private domain knowledge) lives in a separate private environment.

---

## What's New in v1.0.0

### Phase 2: Multi-Mind Support (Subminds)

Multiple AI minds can now share a single Neo4j graph without data collisions.

**The problem it solves:** A secondary mind ("Weft", "ResearchBot") attaching to an
existing graph would see all of Nova's historical facts with no way to distinguish
ownership — and new writes would be silently attributed to nobody.

**The solution:**
- Every node gets an `assistant` property
- `neo4j_backfill_assistant.py` safely migrates existing graphs (batched, dry-run mode,
  production-tested on ~12k node graphs)
- All scripts accept `--assistant`/`--mind` — reads filter, writes tag
- `templates/submind/` provides a starter pack for attaching minds (Option A: read-heavy)

```bash
# Tag a search to a specific mind
python3 scripts/hybrid_memory_search.py "gamma parameter" --assistant Weft

# Sync new session data as Weft
python3 scripts/neo4j_sync.py --assistant Weft

# Backfill historical data for the primary mind
python3 scripts/neo4j_backfill_assistant.py --primary "Nova" --dry-run
```

### Phase 3: Importable Python Library

Before this release, using the memory system from Python code meant either
running scripts via subprocess or copying the script files into your project.

Now you can import directly:

```python
pip install -e .   # or: clone repo and add to PYTHONPATH

from ai_memory import MemoryClient

with MemoryClient() as client:
    results = client.search("transformer attention mechanisms")
    results = client.search("inventory", assistant="Weft")   # multi-mind filter
    facts   = client.traverse("Attention Is All You Need", depth=2)
    matches = client.trace_parameter("Avellaneda-Stoikov", "gamma")
```

All six modules are also importable directly for fine-grained use:

```python
from ai_memory.search import search_vector, search_files
from ai_memory.graph  import traverse, trace_parameter, graph_stats
from ai_memory.state  import MemoryStateManager
```

Scripts remain as standalone CLI entry points — no breaking changes.

### Phase 6: Unified CLI

A single `ai-memory` entry point covers all operations:

```bash
ai-memory search "attention mechanisms" --assistant Weft
ai-memory traverse "Attention Is All You Need" --parameter gamma
ai-memory sync --assistant Weft
ai-memory learn-sync --days 7
ai-memory state --pending --session "weft:main"
```

---

## Upgrade Notes

See [MIGRATION.md](../MIGRATION.md) for full details. Short version:

- **v0.1 → v1.0:** Add `--assistant YourMindName` to all script calls. Run
  `neo4j_backfill_assistant.py --primary "YourMind" --dry-run` before the real backfill.
- **v0.2 → v1.0:** No CLI changes. New: `from ai_memory import MemoryClient` works.
  Fix: `memory_state.py` `NameError` is resolved.

---

## What's Next

- **Phase 4** (Advanced RLM Tooling): The experimental `scripts/rlm/` tools
  (`neo4j_traverse.py`, `memory_state.py`) are already in the package and documented.
  Phase 4 will deepen this with more structured lazy loading patterns and better
  bootstrapping for new minds doing heavy graph work.
- **Phase 5** (Learn Pipeline): Auto-distillation from raw session logs into structured
  Facts. Currently lives in the private production system; Phase 5 brings a clean
  version here.

---

## Links

- [GitHub](https://github.com/losts1/ai-memory-system)
- [BOOTSTRAP.md](../BOOTSTRAP.md) — Start here for a fresh install
- [docs/SUBMINDS.md](./SUBMINDS.md) — Attach as a secondary mind to an existing graph
- [MIGRATION.md](../MIGRATION.md) — Upgrade from v0.1 or v0.2
- [CHANGELOG.md](../CHANGELOG.md) — Full version history
