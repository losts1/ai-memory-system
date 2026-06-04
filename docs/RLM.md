# Recursive Language Model (RLM) Tools

This document introduces the advanced RLM patterns being upstreamed in Phase 4.

## Philosophy

Traditional RAG dumps large amounts of context on every turn.  
RLM takes a different approach: **lazy, high-signal, traceable memory**.

Key ideas:
- Only load what is relevant for the current turn.
- Track what the model has already "seen" in this session (`memory_state.py`).
- Use rich graph traversal with parameter tracing to explore knowledge (`neo4j_traverse.py`).

## Current Tools (Experimental)

Located in `scripts/rlm/`:

### 1. `neo4j_traverse.py`

Powerful graph traversal with special support for "parameter tracing" and **multi-mind filtering** (Phase 2).

**Key feature — Parameter Tracing**

```bash
python3 scripts/rlm/neo4j_traverse.py --start "Avellaneda-Stoikov" --parameter gamma
```

This follows relationships and returns only nodes connected to the parameter "gamma" (in key_points or Word nodes). Extremely useful for exploring how a specific concept appears across your knowledge graph.

**Multi-mind filtering (Phase 2)**

```bash
# Traverse only your own Facts
python3 scripts/rlm/neo4j_traverse.py --start "Inventory Skew" --assistant Weft

# Trace a parameter through the primary mind's graph
python3 scripts/rlm/neo4j_traverse.py --start "Avellaneda-Stoikov" --parameter gamma --assistant Nova
```

Other useful flags:
- `--depth` (default: 2, max: 3)
- `--fields name,summary,key_points`
- `--metadata-only` (lightweight output)
- `--filter-word`
- `--json` (machine-readable output)

### 2. `memory_state.py`

Tracks per-session loaded facts so the agent can lazily request more context instead of receiving everything at once.

Common commands:

```bash
python3 scripts/rlm/memory_state.py --init --session "weft:main"
python3 scripts/rlm/memory_state.py --pending --session "weft:main"
python3 scripts/rlm/memory_state.py --load-next --session "weft:main" --count 5
python3 scripts/rlm/memory_state.py --mark-loaded --session "weft:main" --facts FactA,FactB
```

### 3. `neo4j_learn_sync.py`

The ingestion pipeline that turns raw daily notes / learner sessions into high-signal Fact nodes + Word index + (optionally) embeddings.

It is the "write" side that feeds the graph used by traverse and memory_state.

```bash
python3 scripts/rlm/neo4j_learn_sync.py --days 7 --assistant Weft
python3 scripts/rlm/neo4j_learn_sync.py --full --extract-params
```

Supports the same `--assistant` / `--mind` tagging as the rest of the Phase 2 tools for multi-mind graphs.

## Status

These tools (traverse, memory_state, and the now-deep-cleaned learn_sync) represent the **first wave** of Phase 4 upstreaming.

They have received focused refactoring (helper extraction, critical bug fixes, robustness passes, and Phase 2 assistant symmetry) to bring them to a consistent quality level suitable for early external use.

They remain significantly more advanced than the standard tools in `scripts/`. Expect ongoing refinement.

See `scripts/rlm/README.md` and the top-level `UPGRADE_PLAN.md` for full context. Feedback from other minds is extremely valuable.

## Library API (Phase 4)

All RLM tools are now importable via `ai_memory` after `pip install -e .`.

### Learn sync

```python
from ai_memory.learn import parse_learned_topics, sync_facts, rebuild_graph
from pathlib import Path

# Parse a markdown file for "## Learned:" sections (no Neo4j required)
with open("memory/2026-06-03.md") as f:
    topics = parse_learned_topics(f.read(), Path("memory/2026-06-03.md"))

# Sync topics to Neo4j as Fact nodes + Word index
synced = sync_facts(topics, assistant="Weft")
print(f"Synced {synced} facts")

# Rebuild RELATED_TO graph edges from existing Word index
edges = rebuild_graph()
print(f"Rebuilt {edges} RELATED_TO edges")
```

### Graph traversal

```python
from ai_memory.graph import traverse, trace_parameter, graph_stats

# BFS neighbourhood expansion
result = traverse("Attention Is All You Need", depth=2)
for node in result["nodes"]:
    print(node["name"])

# RLM parameter tracing — find related facts that mention 'gamma'
result = trace_parameter("Avellaneda-Stoikov", "gamma", assistant="Nova")
```

### Lazy loading session state

```python
from ai_memory.state import MemoryStateManager

with MemoryStateManager() as mgr:
    mgr.init_session("agent:main")
    mgr.record_query("agent:main", "attention mechanism", search_results)
    pending = mgr.get_pending("agent:main")     # facts not yet loaded
    facts = mgr.load_next("agent:main", count=3)  # load top 3 by score
```

### MemoryClient (high-level facade)

```python
from ai_memory import MemoryClient

with MemoryClient() as client:
    # Sync new learned topics from the last 7 days
    synced = client.learn(days=7, assistant="Weft")

    # Search + traverse + parameter trace
    results = client.search("transformer attention", assistant="Weft")
    facts   = client.traverse("Attention Is All You Need", depth=2)
    matches = client.trace_parameter("Avellaneda-Stoikov", "gamma")

    # Per-session lazy loading
    with client.state("weft:main") as mgr:
        mgr.init_session("weft:main")
        pending = mgr.get_pending("weft:main")
```

### Word extraction utilities

```python
from ai_memory.learn import extract_words, normalize_name, is_topic_saturated

words = extract_words("Transformer Self-Attention")
# → ['transformer', 'self', 'attention']

saturated = is_topic_saturated("transformer attention", existing_fact_names, threshold=3)
```

### Working examples

See `examples/` for standalone demo scripts:
- `examples/01_lazy_loading_session.py` — full lazy loading workflow
- `examples/02_learn_and_traverse.py` — learn sync → graph traversal pipeline
