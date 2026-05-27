# RLM Tools (Phase 4 — Experimental)

This directory contains advanced **Recursive Language Model (RLM)** tooling being upstreamed as part of **Phase 4** of the upgrade plan ("Upstream the RLM Features").

These are some of the most powerful and distinctive parts of the original private system.

## Current Tools

| File                    | Purpose                                      | Priority | Notes |
|-------------------------|----------------------------------------------|----------|-------|
| `neo4j_traverse.py`     | Rich graph traversal + parameter tracing     | Highest  | Core RLM technique |
| `memory_state.py`       | Per-session lazy loading state tracking      | High     | Enables true lazy RLM |

## Key Concepts

- **Parameter Tracing**: Instead of generic search, follow relationships while focusing on a specific concept/parameter (e.g. "gamma", "inventory skew", "kill switch"). This is one of the highest-signal ways to explore a mature graph.
- **Lazy Memory State**: Track which facts have already been loaded into the current session so the agent can request more context on demand rather than receiving everything upfront.

## Important Warnings

- **Advanced / Experimental**: These tools are significantly more complex than the standard scripts in `../`.
- They were extracted from a heavily customized private production environment.
- They may still contain assumptions, extra dependencies, or patterns that need further adaptation for general use.
- **Use at your own risk**. They are provided for early exploration and feedback.

## Recommended Reading

- `docs/RLM.md` (in the repo root) — High-level explanation and usage examples
- `UPGRADE_PLAN.md` — Full Phase 3/4 roadmap and philosophy

## Feedback

If you are a submind or external user experimenting with these, your experience reports are extremely valuable for the next iteration of the upstreaming effort.

---

**Status**: Early Phase 4 seeding. Expect further refinement.
