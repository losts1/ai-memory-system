# Examples

This directory contains small, high-quality starter examples for different domains.

The goal of Phase 5 is to make the public redistribution package feel usable by **any** agent, not just one that came from a heavy trading/quant background.

## How to Use These

1. Copy the relevant example files into your `~/.ai-memory/memory/` (or a `projects/` or `domains/` subdirectory).
2. Adapt the structure and tone to your own work.
3. Run the normal sync / learn tools — they are domain-agnostic.

## Domains

| Directory              | Focus                              | Notes |
|------------------------|------------------------------------|-------|
| `research/`            | Academic papers, literature review, concept tracking | Good for researchers, grad students, literature agents |
| `software-engineering/`| Architecture decisions, code patterns, technical debt | Useful for SWE agents and codebases |
| `personal/`            | Life goals, relationships, daily reflection | Everyday personal knowledge management |
| `trading/`             | **Explicit example** from a real production trading agent | Clearly labeled as one possible domain (not the default) |

## Philosophy

- The core system (markdown + Neo4j + RLM tools) is domain-neutral.
- Your **templates** and the content you put into Facts / learner sessions determine the "shape" of the graph.
- We ship a few deliberately varied examples so new users see the system is not secretly a trading bot kit.

See the main [UPGRADE_PLAN.md](../UPGRADE_PLAN.md) for the full Phase 5 context.

---

**Status:** Phase 5 complete for initial seeding. More examples welcome via PRs.