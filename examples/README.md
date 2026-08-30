# Examples

This directory contains small, high-quality starter examples for different domains.

The goal of Phase 5 is to make the public redistribution package feel usable by **any** agent, not just one that came from a heavy trading/quant background.

## How to Use These

1. Read the README for the domain closest to your work (most domains ship guidance rather than files to copy; `software-engineering/` includes a sample ADR you can copy into `~/.ai-memory/memory/`).
2. Adapt the structure and tone to your own work.
3. Run the normal sync / learn tools — they are domain-agnostic.

Two runnable end-to-end examples also live here: `01_lazy_loading_session.py` and `02_learn_and_traverse.py`.

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