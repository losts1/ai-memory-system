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

Powerful graph traversal with special support for "parameter tracing".

**Key feature — Parameter Tracing**

```bash
python3 scripts/rlm/neo4j_traverse.py --start "Avellaneda-Stoikov" --parameter gamma
```

This follows relationships and returns only nodes connected to the parameter "gamma" (in key_points or Word nodes). Extremely useful for exploring how a specific concept appears across your knowledge graph.

Other useful flags:
- `--depth`
- `--fields name,summary,key_points`
- `--metadata-only` (lightweight output)
- `--filter-word`

### 2. `memory_state.py`

Tracks per-session loaded facts so the agent can lazily request more context instead of receiving everything at once.

Common commands:

```bash
python3 scripts/rlm/memory_state.py --init --session "weft:main"
python3 scripts/rlm/memory_state.py --pending --session "weft:main"
python3 scripts/rlm/memory_state.py --load-next --session "weft:main" --count 5
python3 scripts/rlm/memory_state.py --mark-loaded --session "weft:main" --facts FactA,FactB
```

## Status

These tools are in early Phase 4 upstreaming. They are more advanced than the core redistribution package and may require tuning for your graph.

See `UPGRADE_PLAN.md` for the broader roadmap.
