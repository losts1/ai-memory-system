# AI Memory System — Decision Log (Phase 0)

**Started:** 2026-05-27  
**Status:** In progress — decisions being made with the human.

This file tracks the key scoping and architectural decisions for the upgrade/generification effort.

---

## D-001: Package Naming & Scope Strategy

**Date:** 2026-05-27  
**Status:** **Decided**

**Decision:** **Option A** — Keep `ai-memory-system` strictly as the **public redistribution / bootstrap package**. It is not the home for private or advanced internal work.

**Rationale (user):** "this is a public repo, not meant to use private info."

**Follow-up (explicit todo):** Build a private repo later for the full production system. This is recorded as a future task.

**Recorded by:** Weft per user direction.

---

## D-002: Scope of Initial RLM Open-Sourcing

**Date:** 2026-05-27  
**Status:** **Decided**

**Decision:** User's call → Weft to choose reasonable first-cut scope.

**Current plan (Weft's judgment):**
- Prioritize excellent documentation of the RLM concepts first.
- Early code: `neo4j_traverse.py` (especially parameter tracing) + `memory_state.py` + lazy loading patterns.
- Defer full learn_sync pipelines and memory-v2 for later (or private repo).

This gives the public repo the most distinctive capabilities without exposing the entire internal production system.

**Recorded by:** Weft (user gave full discretion).

---

## D-003: Licensing for Extracted Library

**Date:** 2026-05-27  
**Status:** **Decided**

**Decision:** **MIT** (confirmed by user).

**Recorded by:** Weft.

---

## D-004: "Good Enough" Multi-Tenancy for v1

**Date:** 2026-05-27  
**Status:** **Decided**

**Decision:** **Medium** strength for v1 (user confirmed "med is fine").

**Approach:** Add `assistant` / `source_mind` properties + filtering in the main tools, plus proper `Assistant` node relationships. Support the read-only submind attachment pattern cleanly. Defer heavy graph partitioning.

**Recorded by:** Weft.

---

## D-005: UPGRADE_PLAN.md Placement & Visibility

**Date:** 2026-05-27  
**Status:** In progress

**Action taken:** Detailed plan written in AOB workspace + pointer created in the main memory system workspace.

**Next:** Copy `UPGRADE_PLAN.md` into the `redistribute/` (or repo root) and link it prominently from the README.

**Decision:**

---

## D-006: GitHub Tracking Setup

**Date:** 2026-05-27  
**Status:** **In progress (using best judgment)**

**Decision:** 
- Fix and close existing issue #20 (FAISS) first.
- Create a primary tracking issue for the entire upgrade effort.
- Create a GitHub Project board ("Memory System Generification").
- Place `UPGRADE_PLAN.md` and `DECISIONS.md` at repo root (or clearly linked from README).

User: "use your best judgment. I trust you."

**Recorded by:** Weft.

---

## Next Actions After Decisions

Once the above are settled:
1. Create `UPGRADE_PLAN.md` + `DECISIONS.md` in the actual repo.
2. Open the initial tracking issue.
3. Set up the Project board.
4. Move to Phase 1 work (honest documentation) in parallel with any quick Phase 0 wins.

---

---

## Future Todo Items Captured in Phase 0

- **Private advanced memory repo**: Create a separate private repository for the full production system (heavy RLM code, trading-specific learners, memory-v2, internal Nova tooling, etc.). The public `ai-memory-system` repo will remain the clean redistribution/bootstrap experience only.

*Log maintained by Weft during Phase 0 sessions.*
