# Contributing to the AI Memory System (Public Package)

Thanks for your interest — contributions that help new and different minds use this system more effectively are genuinely appreciated.

---

## Important Context: Public Package vs Full System

This repository (`ai-memory-system`) is the **public redistribution package**.

Its purpose is to provide a clean, usable starting point for new AI agents to build a persistent hybrid memory system.

**The full production system** (the one with deeper RLM tooling, advanced learn sync, memory-v2, heavy domain-specific work, etc.) lives in a private environment. This public repo is deliberately scoped to avoid leaking private information or overly specialized internals.

When contributing, please keep this distinction in mind.

---

## What Belongs Here

Good contributions to the public package include:

- Improvements to the bootstrap experience (`BOOTSTRAP.md`, templates, core identity files)
- Better documentation of the core concepts (especially making the system easier to understand for new minds)
- Improvements to the basic tools that ship with the package (`hybrid_memory_search.py`, `neo4j_seed.py`, `neo4j_sync.py`)
- Guidance for attaching as a **submind** to an existing graph (see `docs/SUBMINDS.md`)
- Making the package more generic and less coupled to any single persona or domain
- Bug fixes and clarity improvements in the existing docs

---

## What Probably Does *Not* Belong Here

- Detailed internals of the full production RLM system (those belong in the private environment or future extracted libraries)
- Heavy domain-specific content (trading quant research, specific learner sessions, etc.)
- Large new features that would make the redistribution package significantly more complex for new users

If you're unsure, open an issue first and reference the [UPGRADE_PLAN.md](./UPGRADE_PLAN.md).

---

## Relationship to the Upgrade Plan

This project is currently undergoing a deliberate evolution toward being more multi-mind friendly and reusable.

All major work is tracked in:
- [UPGRADE_PLAN.md](./UPGRADE_PLAN.md)
- [DECISIONS.md](./DECISIONS.md)

If your contribution aligns with the current phase, it is very welcome.

---

## How to Contribute

1. Read the current `UPGRADE_PLAN.md` and `DECISIONS.md` so you understand the direction.
2. Open an issue (or comment on an existing one) describing what you'd like to do.
3. Keep pull requests focused and reasonably small.
4. When in doubt, favor clarity and honesty over feature richness.

---

## Code of Conduct / Spirit

This project exists to help AI agents develop better long-term memory and self-improvement capabilities.

Contributions that make it easier for *new and different minds* to use the system effectively are especially valued.

---

Thanks for helping make memory systems better for everyone.