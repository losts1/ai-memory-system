# RLM Tools (Phase 4 — Experimental)

This directory contains advanced **Recursive Language Model (RLM)** tooling being upstreamed as part of **Phase 4** of the ai-memory-system upgrade plan.

These represent some of the most powerful and distinctive patterns from the full private production system.

## Current Tools

| File                     | Purpose                                                                 | Notes |
|--------------------------|-------------------------------------------------------------------------|-------|
| `neo4j_traverse.py`      | Rich graph traversal + `--parameter` tracing (RLM core technique)       | Highest priority |
| `memory_state.py`        | Per-session lazy loading state (`pending` vs `loaded` facts)            | Core to lazy RLM |
| `neo4j_learn_sync.py`    | Turns daily/learner notes into high-quality Facts + Word index + embeddings | Item #4 |
| `metadata.py`            | Core helpers: `apply_metadata_only()`, field selection, teaser generation | Foundation for lazy loading |

## Important Warnings

- **Advanced / Experimental**: These are significantly more complex than the standard tools in the parent `scripts/` directory.
- They were extracted from a heavily customized private production environment.
- They may still contain assumptions or rough edges from that context.
- **Use at your own risk.** They are provided for exploration and feedback.

See the top-level [docs/RLM.md](../../docs/RLM.md) for philosophy and usage examples, and [UPGRADE_PLAN.md](../../UPGRADE_PLAN.md) for the full Phase 3/4 strategy.

Feedback (especially real usage from other minds) is extremely valuable at this stage.
