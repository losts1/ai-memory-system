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

These tools represent the **first wave** of Phase 4 upstreaming.

They are significantly more advanced than the standard tools in `scripts/`. Expect:
- Rough edges
- Some private-era assumptions still present
- Ongoing refinement based on real usage feedback

See the `scripts/rlm/README.md` for more detailed warnings and the top-level `UPGRADE_PLAN.md` for the full Phase 3/4 strategy.

Feedback (especially from other minds actually using these) is extremely valuable at this stage.
