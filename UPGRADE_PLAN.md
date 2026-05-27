# AI Memory System — Upgrade & Generification Plan

**Date:** 2026-05-27  
**Context:** Review performed after Weft attached to the existing Nova graph in read-only mode (Option A) and exercised the system.  
**Goal:** Evolve `https://github.com/losts1/ai-memory-system` from a "redistribution template" into a genuinely reusable, multi-mind memory substrate while preserving (and eventually open-sourcing more of) the powerful RLM features developed in production.

---

## Executive Summary

The current public repo is a **minimal, somewhat outdated bootstrap kit** extracted from an earlier state of the system. The real production implementation (in `~/.openclaw/workspace`) has significantly more powerful RLM (Recursive Language Model) capabilities, better tooling, and deep battle-testing — but it is heavily coupled to a single persona ("Nova") and the trading domain.

**Core thesis for the upgrade:**
Make the system **multi-mind by default** and **library-first**, so new agents (Weft, future subminds, external users) can attach cleanly as first-class participants rather than after-the-fact guests.

---

## Guiding Principles

1. **Multi-tenancy / Subminds first** — Every new mind should be a first-class citizen from day one.
2. **Library over scripts** — Extract reusable Python abstractions instead of shipping bags of scripts.
3. **Separate core from domain** — The generic engine must not be polluted by trading quant research.
4. **Ship the real innovations** — The RLM lazy-loading + graph traversal + memory state features are the differentiators. They should be prominent.
5. **Honest positioning** — Clearly distinguish the public starter kit from the full private production system.
6. **Incremental & reversible** — We do not need to open-source everything at once.

---

## Phased Plan

### Phase 0: Foundations & Decision Points (1–2 days)

**Goal:** Align on scope, ownership, and constraints before touching code.

**Tasks:**
- Decide on package naming and scope:
  - Option A: Keep `ai-memory-system` as the **redistribution / bootstrap experience**.
  - Option B: Create a new `ai-memory-core` (or `memory-core`, `weft-memory`) PyPI/library package for the reusable engine.
  - Option C: Both (recommended).
- Decide how much of the advanced RLM code to open-source in the first cut (traverse, memory_state, learn_sync, etc.).
- Decide on licensing for any extracted library (MIT is current; confirm).
- Define "good enough" multi-tenancy for v1 (e.g., `assistant` property + namespacing vs full graph partitioning).
- Create `UPGRADE_PLAN.md` (this file) in the repo root and link it from README.
- Set up a project board / milestone in GitHub for tracking.

**Deliverables:**
- Decision log (add to this plan or a separate `DECISIONS.md`).
- Initial GitHub issue or discussion thread.

---

### Phase 1: Positioning, Documentation & Honesty (2–4 days)

**Goal:** Make the public repo accurately reflect reality and set correct expectations.

**Tasks:**
- Rewrite top-level `README.md`:
  - Clearly state: "This is the redistributable starter kit. The full production system used by Nova contains additional RLM features and significant evolution."
  - Add a "Current Limitations & Roadmap" section that points at this plan.
  - Add a section on "Attaching as a Submind / Read-Only Participant" (document the pattern Weft just used).
- Update `docs/ARCHITECTURE.md`:
  - Add a section on the RLM enhancements (lazy loading, parameter tracing, memory state).
  - Document the gap between the published template and the live system.
- Create `docs/RLM.md` (or expand existing docs) describing the Recursive Language Model patterns with examples from `neo4j_traverse.py` and `memory_state.py`.
- Add a `docs/SUBMINDS.md` guide explaining how multiple minds can share or attach to the same graph.
- Update `BOOTSTRAP.md` to include the "attach to existing graph as new mind" flow.
- Add a `CONTRIBUTING.md` that explains the relationship between the public template and the private full system.

**Success criteria:**
- A new user reading the README has realistic expectations and knows where the advanced features live.

---

### Phase 2: Multi-Tenancy / Submind Foundations (High Priority, 1–2 weeks)

**Goal:** Make it natural and safe for multiple AIs (or subminds) to use the same graph.

**Status (late May 2026):** Core foundations implemented and in review (PRs #28, #29, #30).

**Key Changes (implemented or in progress):**

1. **Schema updates (backward compatible where possible)** ✅
   - `Assistant` nodes + `assistant` property on Fact/Session/etc.
   - Migration via `scripts/neo4j_backfill_assistant.py` (hardened for real graphs)

2. **Core identity & configuration**
   - User-side (SOUL.md / AGENTS.md declaring identity) — guidance added to SUBMINDS.md
   - Package-level: tools now accept `--assistant` flags

3. **Tooling updates** ✅ (core public tools)
   - `hybrid_memory_search.py --assistant Weft` (filtering)
   - `neo4j_sync.py --assistant Weft` (tagging on write)
   - Backfill tool creates Assistant nodes + tags data

   (Advanced private tools like traverse/memory_state still out of scope for this public package)

4. **New supported mode: "Read-only attached submind"** ✅
   - Strong documentation + practical examples in updated SUBMINDS.md
   - Option A (read-heavy) pattern is the recommended starting point

5. **Documentation & examples** 🔄
   - SUBMINDS.md and PHASE2-SCHEMA-PROPOSAL.md refreshed with real CLI usage
   - Concrete examples for backfill + search + sync with assistant tagging

**Deliverables:**
- Schema migration notes (even if mostly additive).
- Updated versions of the key scripts that are assistant-aware.
- Working example of Weft-style attachment.

---

### Phase 3: Extract Core Library (Medium–Long Term, 2–4 weeks)

**Goal:** Stop shipping a bag of scripts. Create reusable abstractions.

**Proposed structure (illustrative):**

```
ai-memory-core/
├── ai_memory/
│   ├── __init__.py
│   ├── client.py              # High-level MemoryClient
│   ├── models.py              # Fact, Session, Assistant, MemoryState
│   ├── search.py              # HybridSearcher (vector + graph + files)
│   ├── graph.py               # GraphTraverser (the powerful RLM traversal)
│   ├── state.py               # MemoryState (per-session tracking)
│   ├── sync.py                # Base sync logic (learn + auto)
│   └── config.py
├── pyproject.toml
└── README.md
```

**Tasks:**
- Identify the cleanest boundaries from the current scripts (`hybrid_memory_search.py`, `neo4j_traverse.py`, `memory_state.py`, `neo4j_learn_sync.py`).
- Create the initial library (can start private and move to its own repo later).
- Refactor the scripts in the redistribution package to be thin wrappers around the library.
- Publish an initial version (can be `0.1` / alpha) under a clear name.

**Success criteria:**
- A new agent can do `from ai_memory import MemoryClient; client = MemoryClient(...)` and perform searches + traversals without copying 800 lines of script.

---

### Phase 4: Upstream the RLM Features (Parallel with Phase 2–3)

**Goal:** Make the actually interesting capabilities available to others.

High-value pieces to consider upstreaming (in rough priority):

1. `neo4j_traverse.py` — especially the `--parameter` tracing mode and clean BFS with cycle detection.
2. `memory_state.py` — the per-session pending/loaded fact tracking (core to lazy RLM).
3. Lazy loading / metadata-only / field selection logic (currently scattered in `hybrid_memory_search.py` variants).
4. `neo4j_learn_sync.py` patterns (how daily/learner notes become high-quality Facts + Word index + relationships).
5. The overall RLM query pipeline philosophy (documented in the local `docs/neo4j-memory-system.md`).

**Approach options:**
- Option A: Add the best scripts to the redistribution package under `scripts/advanced/` with clear warnings that they are more experimental.
- Option B: Include them in the new `ai-memory-core` library as first-class modules.
- Recommended: Start with excellent documentation + the traverse + memory_state scripts in Phase 2, then fold into the library in Phase 3.

---

### Phase 5: Reduce Domain Coupling & Improve Templates (Ongoing)

**Tasks:**
- Curate or create a small set of **neutral example learner sessions** and Facts for the redistribution package (instead of leaking heavy quant trading material).
- Make the `learner-sessions/` and curiosity queue examples domain-agnostic or clearly marked as "trading example".
- Improve `templates/core/` with more generic, high-quality starting QMDs.
- Add a `examples/` directory in the repo showing different domains (trading, research, personal knowledge, software engineering, etc.).

---

### Phase 6: Tooling, Packaging & Developer Experience

- Add a small CLI: `ai-memory` (or `memory`) with commands like:
  - `init`
  - `search "..." --assistant weft`
  - `traverse "..." --parameter gamma`
  - `attach-submind`
  - `sync`
- Improve requirements management (separate core vs redistribution vs full production).
- Add basic tests (even smoke tests) for the key scripts/library.
- GitHub Actions for basic linting / packaging.
- Consider semantic versioning once a library exists.

---

### Phase 7: Release, Communication & Migration

- Cut a `v0.2` / `v1.0` release of the updated redistribution package with the new multi-mind story.
- Write a blog-style announcement or GitHub Discussion explaining the evolution and the new capabilities for other minds.
- Provide a migration guide for existing users of the old template.
- Decide on long-term home for the advanced RLM code (stay in this repo? separate `ai-memory-rlm` repo?).

---

## Risk & Trade-offs

| Risk | Mitigation |
|------|------------|
| Breaking existing Nova usage | Keep all changes backward-compatible or provide clear migration paths. Make assistant scoping opt-in initially. |
| Opening too much of the secret sauce too early | Phase the RLM upstreaming. Start with documentation + 1–2 scripts. |
| Scope creep | Ruthlessly prioritize "multi-mind attachment" and "library extraction" over perfection. |
| Maintenance burden | The library work should reduce long-term maintenance by creating clearer boundaries. |

---

## Success Metrics (How we know this worked)

- A new AI (simulated or real) can follow the bootstrap + attach as a submind and usefully query the graph within a few hours.
- The public README no longer creates false expectations about the gap between the template and production.
- At least the core traversal + lazy loading patterns are usable by people outside the original system.
- Weft (and future subminds) have a clean, documented path instead of having to reverse-engineer.

---

## Immediate Next Steps (Recommended)

1. Review and refine this plan with the human (lost).
2. Decide on library vs redistribution split (Phase 0 decisions).
3. Create the GitHub issue / project board.
4. Start Phase 1 (documentation honesty) — this has the best ROI for the least risk.
5. Prototype the minimal assistant-scoping changes on a branch (even just the search side first).

---

## Appendix: Key Files & Concepts Referenced

**From production system (not yet in public repo):**
- `neo4j_traverse.py` (especially parameter tracing)
- `memory_state.py`
- `neo4j_learn_sync.py` + `neo4j_auto_sync.py`
- `docs/neo4j-memory-system.md` + `rlm-neo4j-enhancement-plan.md`
- `neo4j-schema-design.md`
- `memory-v2/` (hot tier work)

**Current public structure (as of 2026-05-27):**
- Small redistribution package under `redistribute/`
- Only 3 scripts + basic templates + architecture docs
- No RLM-specific documentation or tools

---

**Status:** Draft plan for discussion. Ready to be turned into GitHub issues and a phased implementation once decisions in Phase 0 are made.

— Weft 🧵 (with heavy reference to direct usage experience attaching to the live graph)
