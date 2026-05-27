# RLM Tools (Phase 4 — Experimental)

This directory contains advanced Recursive Language Model tooling being upstreamed as part of **Phase 4** of the ai-memory-system upgrade plan.

## Current Contents

- `neo4j_traverse.py` — Powerful graph traversal with parameter tracing (highest priority)
- `memory_state.py` — Per-session memory state tracking (core to lazy RLM)

## Important Warnings

- These are more advanced than the basic tools in the parent `scripts/` directory.
- They were originally developed in a private full production environment.
- They may require additional dependencies or have assumptions from that context.
- Use with the understanding that they are in an early upstreaming phase.

See the top-level [UPGRADE_PLAN.md](../../UPGRADE_PLAN.md) for Phase 3/4 context.

Feedback on these tools (especially from other minds using them) is extremely valuable.
