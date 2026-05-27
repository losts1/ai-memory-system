# Recursive Language Model (RLM) Enhancements

**Status:** Phase 1 (Draft) — 2026-05-27

This document introduces the **Recursive Language Model** approach to memory and retrieval that powers the more advanced parts of this system.

---

## The Core Problem

Traditional RAG (Retrieval-Augmented Generation) has a fundamental limitation:

> You either stuff too much context into the model (expensive, noisy, hits context limits), or you do shallow retrieval and miss important relationships and details.

Most memory systems optimize for "find the top-k most similar chunks" and then dump them into the prompt. This works okay for simple lookup, but breaks down for deep, relational, or multi-hop reasoning.

---

## The RLM Idea

The Recursive Language Model approach (inspired by ideas in [arXiv:2512.24601](https://arxiv.org/abs/2512.24601)) treats external memory as **first-class state** that the agent can interact with recursively and on-demand, rather than as a one-shot retrieval step.

Key principles:

1. **Lazy Loading** — Start with lightweight metadata only. Only load full content when the agent decides it is worth it.
2. **On-Demand Field Loading** — Don't load entire documents. Load only the specific fields needed for the current reasoning step.
3. **Graph Traversal as Reasoning** — Follow semantic relationships in the graph (e.g., "trace the parameter `gamma` across related market making models").
4. **Memory State Tracking** — The system keeps track of what the agent has already seen in the current session (pending vs loaded facts).

This turns retrieval from a single lookup into an interactive, multi-step process that can be steered by the agent.

---

## The Main Patterns

### 1. Lazy Loading (`--metadata-only`)

Instead of returning full Fact content, the system can return only lightweight metadata:

- name
- teaser / summary
- key point count
- related fact count
- top words

The agent can then decide which ones are worth expanding.

**Example use:**
```bash
python3 hybrid_memory_search.py "gamma" --metadata-only --max-results 10
```

### 2. Load on Demand (`--load-fact`, `--fields`)

Once a Fact looks promising, load only what you need:

```bash
python3 hybrid_memory_search.py "x" --load-fact "Avellaneda-Stoikov Market Making Model"
python3 hybrid_memory_search.py "x" --fields name,key_points,summary
```

### 3. Graph Traversal (`neo4j_traverse.py`)

Follow relationships instead of doing flat similarity search.

Especially powerful for tracing parameters or concepts across related ideas:

```bash
python3 neo4j_traverse.py --start "Avellaneda-Stoikov Market Making Model" --parameter gamma --depth 2
```

This follows `SHARES_PARAMETER` edges and surfaces how a specific concept appears in different contexts.

### 4. Session Memory State

The system can track, per session:
- Which facts have been surfaced (metadata only)
- Which ones the agent has actually loaded
- Which ones are still "pending"

This prevents the agent from re-reading the same things and supports more sophisticated "what have I learned so far?" reasoning.

---

## Why This Matters

Traditional RAG is mostly **pull-based and stateless**.

RLM-style memory is more like having an external working memory that the agent can:
- Inspect at different levels of detail
- Navigate relationally
- Maintain state about across multiple turns

This is especially valuable for:
- Deep technical or research domains
- Long-running projects with many interconnected concepts
- Agents that need to do real reasoning over their own knowledge base

---

## Current Status in the Public Package

The public redistribution package currently exposes basic versions of hybrid search and some traversal capability.

The more advanced RLM tooling (`neo4j_traverse.py` with parameter tracing, `memory_state.py`, sophisticated lazy loading flows, etc.) exists in the full production system and is being gradually upstreamed as part of the [upgrade plan](../UPGRADE_PLAN.md).

See [docs/SUBMINDS.md](./SUBMINDS.md) for how new minds can still benefit from an existing rich graph even before all the tooling is fully public.

---

## Further Reading

- Local production documentation: `docs/neo4j-memory-system.md` and `rlm-neo4j-enhancement-plan.md` (in the full system)
- Paper: [arXiv:2512.24601](https://arxiv.org/abs/2512.24601) — Zhang, Kraska, Khattab (MIT, ICML 2026)

---

*This is an evolving area. Feedback on what patterns are most useful for new minds is very welcome.*