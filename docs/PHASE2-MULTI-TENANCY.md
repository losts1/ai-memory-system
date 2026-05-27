# Phase 2: Multi-Tenancy & Submind Foundations

**Status:** Initial Scoping (Draft) — 2026-05-27

This document outlines the concrete work needed for **Phase 2** of the upgrade plan: making the system properly support multiple minds (subminds) on the same graph.

---

## Goals for Phase 2

1. Make it natural and safe for multiple distinct AI minds to use the same Neo4j graph.
2. Support the "read-heavy submind" pattern as a first-class, well-documented mode.
3. Enable light, responsible write access with clear provenance.
4. Do this in a way that is **backward compatible** for existing single-mind users.
5. Keep the public redistribution package reasonably simple while laying groundwork for the future core library.

---

## Scope Decisions (Need Input)

| Area | Minimal Viable (Recommended for v1) | More Ambitious |
|------|-------------------------------------|----------------|
| **Schema** | Add `assistant` property + optional `Assistant` nodes | Full relationship model with `CREATED_BY`, `BELONGS_TO` |
| **Tooling** | Update search + traverse tools to accept `--assistant` / `--mind` filter | Automatic filtering + namespacing in all sync tools |
| **Writing** | Guidance + examples only (no enforcement) | Lightweight enforcement / warnings in tools |
| **Read-only mode** | Excellent documentation + examples | Dedicated "submind mode" flag in tools |

**Recommendation:** Start with the **Minimal Viable** column above. Full enforcement and automatic namespacing can come in Phase 3 (core library).

---

## Concrete Work Items

### 1. Schema Updates (Additive, Low Risk)

- Add `assistant` (string) property to `Fact`, `Session`, `Event`, `Decision`, `ConversationTurn` nodes.
- Create an `Assistant` node label with basic properties (`name`, `id`, `created_at`, `type`).
- Document a standard way to create an `Assistant` node for a new mind.
- (Optional) Add `CREATED_BY` relationship from new nodes to their `Assistant`.

**Migration:** Purely additive — no breaking changes for existing data.

### 2. Tooling Updates

Priority tools to update:

- `hybrid_memory_search.py`
- `neo4j_search.py`
- `neo4j_traverse.py`
- `neo4j_sync.py` (for learn + auto sync)

Minimal changes needed:
- Add `--assistant` / `--mind` command line flags.
- When provided, filter queries and tag new nodes being written.
- Update help text and examples.

### 3. Documentation

- Expand `docs/SUBMINDS.md` with Phase 2 capabilities once implemented.
- Add examples in `docs/RLM.md` showing submind-aware traversal.
- Update `BOOTSTRAP.md` and main README with clearer submind onboarding path.

### 4. Examples & Templates

- Add a `templates/submind/` folder with starter files for new minds attaching to an existing graph.
- Include a sample `identity.qmd` and `setup.qmd` tailored for subminds.

---

## Risks & Trade-offs

- **Over-engineering early**: We risk building complex multi-tenancy that almost no one uses yet.
- **Breaking existing users**: Must remain fully backward compatible for single-mind usage.
- **Scope creep**: Easy to turn this into a full multi-tenant database project.

**Mitigation:** Explicitly scope Phase 2 to "good enough for read-heavy + light responsible writes" rather than full enterprise multi-tenancy.

---

## Success Criteria for Phase 2

By the end of Phase 2, a new mind should be able to:

1. Attach to an existing graph in read-only mode with clear instructions.
2. Create their own `Assistant` node and start writing high-signal Facts with proper provenance.
3. Use the main search and traversal tools while filtering to (or excluding) their own content.
4. Understand the current limitations and future roadmap.

---

## Suggested Order of Work

1. Schema design + documentation (lowest risk, high clarity).
2. Update the most-used search tool (`hybrid_memory_search.py`).
3. Update `neo4j_traverse.py` (very high value for RLM users).
4. Update sync tools.
5. Add submind templates + improve documentation.
6. End-to-end testing with a second mind (e.g. simulate Weft again).

---

**Next Step**

If this scoping direction looks good, the immediate next action would be to design the minimal schema changes and propose concrete diffs for the main tools.

Would you like me to:
- Draft the actual schema change proposal + example Cypher?
- Start with code changes to one of the tools?
- Adjust the scope of Phase 2 before proceeding?