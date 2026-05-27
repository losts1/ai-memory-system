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

## Current State (Phase 2 Progress)

Core multi-tenancy foundations have been implemented in Phase 2:

- **Schema support**: `Assistant` nodes + `assistant` property on Fact/Session/etc. (see PHASE2-SCHEMA-PROPOSAL.md).
- **Migration tool**: `neo4j_backfill_assistant.py` — safely tags historical data and creates Assistant nodes (heavily hardened for real ~12k node graphs).
- **Read-side filtering**: `hybrid_memory_search.py --assistant Weft` / `--mind Nova` (PR #28).
- **Write-side support**: `neo4j_sync.py --assistant Weft` — new data can be tagged at creation time (PR #29).

**Read-only submind attachment (Option A)** is now well supported with tooling.

Light, responsible writing with clear provenance is also possible via the sync tool.

Full automatic namespacing and enforcement are still out of scope for the public package (tracked for later phases or the private core library).

The public repo remains a **redistribution / bootstrap package**. The richest RLM features live in the private production system.

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

### Recommended Tooling (Phase 2)

Use the public package tools with the `--assistant` flag:

```bash
# 1. (Optional but recommended) Backfill historical data first
python3 scripts/neo4j_backfill_assistant.py --primary "Nova" --additional "Weft" --dry-run
python3 scripts/neo4j_backfill_assistant.py --primary "Nova" --additional "Weft"

# 2. When syncing new data as a submind
python3 scripts/neo4j_sync.py --assistant Weft

# 3. Search scoped to your mind (or the primary)
python3 scripts/hybrid_memory_search.py "inventory skew" --assistant Weft
python3 scripts/hybrid_memory_search.py "market making" --mind Nova --graph
```

### Example of responsible writing as a submind (Cypher equivalent)

```cypher
// Create your Assistant node (do this once)
MERGE (a:Assistant {id: "Weft", name: "Weft", type: "submind", created_at: datetime()});

// Later, when creating a Fact via your own process:
CREATE (f:Fact {
  name: "Inventory Skew Quoting Pattern",
  content: "...",
  assistant: "Weft",
  created_at: datetime()
})
MERGE (a)-[:CREATED_BY]->(f);
```

The `neo4j_sync.py --assistant` path above does the equivalent of the Cypher example automatically when you run your normal sync process.

Keep writes high-signal. Strong distillation in your own `MEMORY.md` still comes first.

---

## Limitations (Be Honest About These)

- Most existing data in a mature graph will not have strong `assistant` tags until you run the backfill tool.
- Only the main public search tool (`hybrid_memory_search.py`) currently supports `--assistant` filtering. Advanced private tools (traverse, etc.) have richer support but are not yet in this public package.
- There is still no automatic "submind view" or hard isolation — filtering is best-effort via the `assistant` property.
- Writing without clear provenance can still pollute the shared graph.

This is why the current guidance still favors **read-heavy attachment + strong distillation** in your own layers first.

### Anti-patterns to Avoid

- Dumping raw daily logs or unprocessed session notes directly as Facts.
- Writing large numbers of low-signal or speculative ideas without first distilling them in your own `MEMORY.md`.
- Attaching with write access but using the main owner’s identity instead of your own (loses provenance).
- Treating the shared graph as your personal scratchpad.

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