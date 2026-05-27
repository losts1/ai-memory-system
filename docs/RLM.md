# Recursive Language Model (RLM) Tools

**Status:** Phase 4 (In Progress) — 2026-05-27

This document describes the **Recursive Language Model** patterns and tools being upstreamed into the public package as part of Phase 4 of the upgrade plan.

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

> **Note:** Some of the more advanced flags shown in this document (such as `--load-fact`, `--fields`, and parameter tracing) are currently only available in the full production system. The public redistribution package contains more basic versions of the tools.

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

### Before vs After (Illustrative)

**Traditional approach:**
- Retrieve top 8 most similar chunks → paste all full content into the prompt.
- Typical result: 4,000–8,000+ tokens of mixed relevance.

**RLM-style approach:**
- Retrieve metadata for 15 candidates (very cheap).
- Agent reviews the list and requests full content for only 2–3 high-value Facts.
- Agent uses graph traversal to pull related parameter context.
- Typical result: 800–1,800 tokens, much higher signal density.

The difference becomes especially noticeable in long-running projects with hundreds of interconnected concepts.

---

## Why This Matters

Traditional RAG is mostly **pull-based and stateless**.

RLM-style memory is more like having an external working memory that the agent can:
- Inspect at different levels of detail (metadata → specific fields → full content)
- Navigate relationally (graph traversal + parameter tracing)
- Maintain state across turns (what have I already loaded this session?)

This becomes especially valuable once your knowledge base grows beyond what comfortably fits in context.

---

## Recommended RLM Query Pattern

When working with a mature graph, the following interactive pattern tends to produce much higher signal density than "retrieve top-k and stuff everything into the prompt":

1. **Browse (metadata only)**  
   Start cheap. See what exists without loading full content.
   ```bash
   python3 hybrid_memory_search.py "your topic" --metadata-only --max-results 15
   ```

2. **Decide**  
   Review teasers, `kp_count`, `related_count`, `top_words`. Choose what actually looks promising.

3. **Load on demand**  
   Pull only what you need.
   ```bash
   python3 hybrid_memory_search.py "x" --load-fact "Specific Fact Name"
   # or
   python3 hybrid_memory_search.py "x" --fields name,key_points,summary
   ```

4. **Trace / Traverse**  
   Follow semantic relationships instead of flat similarity.
   ```bash
   python3 neo4j_traverse.py --start "Promising Fact" --parameter "your-concept" --depth 2
   ```

5. **Maintain session state (optional but powerful)**  
   Use `memory_state.py` so the system remembers what you've already loaded in the current conversation and can suggest the next most useful pending facts.

This pattern turns retrieval from a single blunt operation into a steerable, multi-step reasoning process.

---

## Current Status in the Public Package (Phase 4)

As of late May 2026, core pieces of the RLM pattern have been upstreamed into the public package:

**Available in `scripts/rlm/`:**
- `neo4j_traverse.py` — Graph traversal + `--parameter` tracing
- `memory_state.py` — Per-session lazy state tracking
- `neo4j_learn_sync.py` — High-quality Fact + Word index ingestion from notes
- `metadata.py` — Reusable `apply_metadata_only()` and field selection helpers

**Integrated into main tools:**
- `hybrid_memory_search.py` now supports `--metadata-only` and `--fields`

The high-level **RLM query pipeline philosophy** (lazy metadata → on-demand loading → relational traversal → session state) is documented in this file.

More advanced or infrastructure-heavy pieces (full auto-sync pipelines, specific cron patterns, complete memory-v2 hot tier, etc.) remain in the private production system for now and may be upstreamed later or extracted into a dedicated library (Phase 3 direction).

See `scripts/rlm/README.md` for current tool status and warnings.

---

## Further Reading

- `scripts/rlm/README.md` — Current status and warnings for the tools in this package
- `UPGRADE_PLAN.md` — Full Phase 3/4 roadmap (library extraction + RLM upstreaming)
- Paper: [arXiv:2512.24601](https://arxiv.org/abs/2512.24601) — Zhang, Kraska, Khattab (MIT, ICML 2026)

---

*This is an evolving area. Feedback on what patterns are most useful for new minds is very welcome.*