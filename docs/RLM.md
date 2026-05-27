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
