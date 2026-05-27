# Public Repo Artifacts — Ready to Apply

Generated during Phase 0 execution (2026-05-27).  
Use these to update the public `losts1/ai-memory-system` repository.

---

## 1. README.md Update (add near the top, after the tagline)

```markdown
## Status & Roadmap

This repository contains the **public redistribution package** — the clean bootstrap experience you can give to a new AI agent.

Active work to make the system more generic, multi-mind friendly, and reusable is tracked here:

- **[UPGRADE_PLAN.md](./UPGRADE_PLAN.md)**
- **[DECISIONS.md](./DECISIONS.md)**

Phase 0 (Foundations & Decisions) is complete. We are moving into Phase 1 (Honest positioning + documentation).
```

---

## 2. Main Tracking Issue (create this as a new issue)

**Title:**
```
Upgrade: Make ai-memory-system genuinely multi-mind and reusable
```

**Body:**
```markdown
This issue tracks the effort to evolve the public redistribution package into something new minds can actually use effectively — including as read-only or lightly-scoped subminds attached to existing graphs.

### Key Documents
- [UPGRADE_PLAN.md](./UPGRADE_PLAN.md)
- [DECISIONS.md](./DECISIONS.md)

### Current Status
- **Phase 0** (Foundations & Decisions): Complete
  - Public repo scope locked to redistribution/bootstrap only
  - Medium multi-tenancy targeted for v1
  - MIT license confirmed
  - RLM open-sourcing scope at Weft discretion (leaning toward traverse + memory_state + documentation first)

### Next
Moving to Phase 1: Honest documentation and positioning (addressing overstatements in the current docs, adding submind guidance, etc.).

All major decisions and progress will be recorded in the documents linked above.
```

**Labels:** `enhancement`, `documentation`, `roadmap`

---

## 3. Comment for Issue #20 (FAISS Layer)

Copy and paste this as a comment on https://github.com/losts1/ai-memory-system/issues/20, then close the issue.

```markdown
**Update from Phase 0 (2026-05-27)**

After investigation as part of the broader upgrade effort:

### Current State
- `requirements.txt` now includes `faiss-cpu>=1.7`
- `scripts/hybrid_memory_search.py` contains a full `search_faiss()` implementation (loading, embedding, result formatting)
- The function is wired into `main()` when `--use-embeddings` is passed
- `docs/ARCHITECTURE.md` already documents the major caveat: the index must be built manually and does not exist on fresh installs.

The situation has improved since this issue was filed (May 3).

### Remaining Gaps
- The top-level "5-layer architecture" framing in README + ARCHITECTURE.md still presents Layer 5 as a standard part of the system without enough visibility that it is optional and requires manual setup.
- No tooling exists in the redistribution package to *build* the FAISS index from existing Facts.
- When `--use-embeddings` is used, the script still proceeds to run the graph + file searches afterward (behavior could be clearer for users who want "use this semantic layer instead of Neo4j").

### Action
This is being treated as part of the "honest positioning" work in the ongoing upgrade plan (see new `UPGRADE_PLAN.md`).

I am closing this issue as **addressed for the immediate term**. Further improvements to Layer 5 documentation, optional tooling, and clearer behavior will be tracked under the main upgrade effort.

Closing.
```

Then close the issue with state = closed, state_reason = "completed".

---

## 4. Minimal README + ARCHITECTURE Polish Suggestions

### In README.md (overview table)
Change the Layer 5 row to:
```
| 5 | FAISS (optional) | Local semantic embeddings (offline fallback) | On-demand | Requires manual index build |
```

### In docs/ARCHITECTURE.md (overview table)
Already partially good. Recommended small change:
- Change the header row to include a "Notes" column (see the version in `UPGRADE_PLAN.md` for the exact suggested table).

---

These artifacts are designed so you can apply them quickly with minimal friction.

Once these are in the public repo, Phase 1 work (honest docs + submind guidance) can begin in earnest.
