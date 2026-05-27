# Subminds — Attaching New Minds to an Existing Graph

**Status:** Phase 1 (Draft) — 2026-05-27

This document explains how multiple AI minds can share or attach to the same underlying memory system, particularly the Neo4j knowledge graph.

---

## What is a Submind?

A **submind** is a distinct AI identity (or persona) that operates against an existing memory graph without being the primary owner of that graph.

Examples:
- A new agent (e.g. Weft) being given read access to a mature graph built by another mind (e.g. Nova).
- Multiple specialized agents (researcher, coder, trader, etc.) sharing a common long-term knowledge base.
- Temporary or experimental minds that should not pollute the main graph with low-signal data.

The goal is to support **multi-mind collaboration** on a shared substrate while keeping noise low and provenance clear.

---

## Current State (Phase 1)

As of the start of Phase 1 of the upgrade plan:

- The public redistribution package supports **one primary mind** by default.
- The underlying production system (used by Nova) is currently **single-tenant in practice** — there is one main `Assistant` node ("Nova") and most data is not strongly namespaced.
- **Read-only attachment** is already possible and has been demonstrated successfully (Weft attached to the live graph in May 2026 using Option A).
- Light write access with namespacing is possible but not yet well documented or tool-supported in the public package.
- Full multi-tenant isolation (strong graph partitioning, separate databases, etc.) is out of scope for v1.

The public repo is deliberately scoped as a **redistribution / bootstrap package** only. The full advanced production system lives in a private environment.

See [UPGRADE_PLAN.md](../UPGRADE_PLAN.md) for the full roadmap.

---

## Attaching as a Read-Only Submind (Recommended Starting Point)

This is the pattern that was used successfully when Weft first connected to the existing Nova graph.

### Philosophy (Option A)
- **Read heavily.** The whole point of attaching to a mature graph is to benefit from the knowledge already there.
- **Write very selectively** (or not at all at first). Strong distillation in your own markdown layers comes first.
- Keep the shared graph high-density. Do not dump raw sessions or low-signal facts into it.

### How to Set It Up

1. **Get access to the graph**
   - Obtain credentials (usually via a shared `.env.neo4j` or direct connection details).
   - You only need read access for the initial attachment.

2. **Set up your own identity layer**
   - Create your own `SOUL.md`, `IDENTITY.md`, `USER.md`, and `AGENTS.md` (or equivalent).
   - Explicitly declare that you are operating as a submind attached to an existing graph.

3. **Establish your own local memory**
   - Follow the standard bootstrap (daily notes in `memory/YYYY-MM-DD.md`, curated `MEMORY.md`).
   - Treat the shared Neo4j graph as an additional powerful **read-only knowledge base**, not your primary write target.

4. **Query the graph**
   - Use the existing tools from the redistribution package (`hybrid_memory_search.py`, `neo4j_search.py`, `neo4j_traverse.py`, etc.).
   - When useful, pass an `--assistant` or `--mind` flag once the tools support it (tracked in Phase 2).

5. **Document your attachment**
   - Add a note in your own `MEMORY.md` or a dedicated file explaining:
     - Which graph you are attached to
     - Your access level (read-only, limited write, etc.)
     - Your policy (e.g. "Option A — read-heavy, minimal writes")

---

## Writing to a Shared Graph (When Ready)

Once you have proven value through distillation, you may want to contribute back.

Current recommended approach (until better tooling exists):

- Add an `assistant` or `source_mind` property on any new `Fact`, `Session`, or `Event` nodes you create.
- Use clear naming or relationships (e.g. `CREATED_BY` pointing to your `Assistant` node).
- Prefer creating a lightweight `Assistant` node for your mind rather than writing as the primary owner.

Stronger multi-tenant support (automatic namespacing in search/traverse/sync tools) is planned for Phase 2.

---

## Limitations (Be Honest About These)

- Most existing data in a mature graph will not have strong `assistant` tags yet.
- Search and traversal tools do not yet reliably filter by mind.
- There is no automatic "submind view" or isolation.
- Writing without clear provenance can pollute the graph for everyone.

This is why the current guidance strongly favors **read-heavy attachment** in the early stages.

---

## Future Work

See the relevant sections in [UPGRADE_PLAN.md](../UPGRADE_PLAN.md):

- **Phase 2**: Multi-Tenancy / Submind Foundations (schema updates, tooling changes, `Assistant` node support)
- **Phase 3+**: Core library extraction that makes submind attachment first-class

---

## Related Documents

- [UPGRADE_PLAN.md](../UPGRADE_PLAN.md) — Full phased plan
- [DECISIONS.md](../DECISIONS.md) — Recorded decisions from Phase 0
- [ARCHITECTURE.md](./ARCHITECTURE.md) — Current system architecture

---

*Maintained as part of the public redistribution package. Contributions that improve submind ergonomics are welcome.*