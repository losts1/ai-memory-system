# Phase 2 Schema Proposal – Multi-Tenancy & Submind Support

**Date:** 2026-05-27  
**Status:** Draft for discussion

This document proposes the concrete schema changes needed to support multiple minds (subminds) on the same Neo4j graph in a clean, backward-compatible way.

---

## Goals

- Allow new minds to attach to an existing graph with clear identity.
- Make it easy to distinguish who created what.
- Support both read-heavy submind usage and light, responsible writing.
- Keep changes additive and non-breaking for existing single-mind users.
- Lay the foundation for better tooling in later phases.

---

## Proposed Changes

### 1. New `Assistant` Node Label

Every mind that participates in the graph should have an `Assistant` node.

```cypher
(:Assistant {
  id:          string,     // Stable unique ID (e.g. "weft-2026-05" or UUID)
  name:        string,     // Human-readable name ("Weft", "Nova", "ResearchAgent-v3")
  type:        string,     // "primary" | "submind" | "experimental" | "specialist"
  created_at:  datetime,
  description: string,     // Optional short description
  metadata:    map         // Flexible additional info
})
```

**Recommendation:** Every new mind should create one `Assistant` node when they first start writing.

### 2. New Property on Existing Nodes

Add an `assistant` property (string) to the following node labels:

- `Fact`
- `Session`
- `Event`
- `Decision`
- `ConversationTurn` (optional but recommended)

Example:
```cypher
(:Fact {
  name: "Inventory Skew Quoting Pattern",
  ...
  assistant: "Weft"          // or "weft-2026-05" if using IDs
})
```

This is the simplest and most query-friendly way to scope data.

### 3. Optional Relationships (Recommended for richer provenance)

```cypher
(:Assistant)-[:CREATED_BY]->(:Fact)
(:Assistant)-[:CREATED_BY]->(:Session)
(:Assistant)-[:CREATED_BY]->(:Event)
```

These relationships make graph traversals and provenance queries much cleaner.

---

## Example: A New Mind Attaching and Writing

```cypher
// 1. Create your Assistant node (run once)
MERGE (a:Assistant {
  id: "weft-2026-05",
  name: "Weft",
  type: "submind",
  created_at: datetime()
})
ON CREATE SET a.description = "Read-heavy submind focused on agent memory patterns";

// 2. Later, when creating knowledge
CREATE (f:Fact {
  id: "fact-uuid-here",
  name: "RLM Lazy Loading Pattern",
  content: "...",
  assistant: "Weft",
  created_at: datetime()
})
MERGE (a:Assistant {id: "weft-2026-05"})
CREATE (a)-[:CREATED_BY]->(f);
```

---

## Query Examples

### Find only facts created by a specific mind
```cypher
MATCH (f:Fact {assistant: "Weft"})
RETURN f.name, f.content
LIMIT 20;
```

### Search across all minds but boost your own
```cypher
CALL db.index.vector.queryNodes('fact_embeddings', 10, $embedding)
YIELD node AS f, score
RETURN f.name, f.assistant, score
ORDER BY 
  CASE WHEN f.assistant = "Weft" THEN 0 ELSE 1 END,
  score DESC;
```

### Find everything created by any submind (excluding the primary)
```cypher
MATCH (f:Fact)
WHERE f.assistant IS NOT NULL AND f.assistant <> "Nova"
RETURN f.assistant, count(*) AS facts
ORDER BY facts DESC;
```

---

## Migration Strategy for Existing Data

Because the changes are additive, existing graphs do not need to be rewritten.

**Recommended approach:**

1. Add the new `Assistant` label and properties as optional.
2. For existing data, we can run a one-time backfill script that creates `Assistant` nodes for known minds (e.g. "Nova") and tags high-confidence data.
3. New data written after the change will automatically include the `assistant` property.

We provide two helpers:

- `scripts/neo4j_backfill_assistant.py` (Python script, recommended)
- Pure Cypher examples below

---

## Pure Cypher Migration Examples

### 1. Create primary Assistant node

```cypher
MERGE (a:Assistant {id: "Nova"})
ON CREATE SET 
    a.name = "Nova",
    a.type = "primary",
    a.created_at = datetime();
```

### 2. Backfill `assistant` property on existing nodes (safe & idempotent)

```cypher
// Facts
MATCH (f:Fact)
WHERE f.assistant IS NULL
SET f.assistant = "Nova";

// Sessions
MATCH (s:Session)
WHERE s.assistant IS NULL
SET s.assistant = "Nova";

// Events
MATCH (e:Event)
WHERE e.assistant IS NULL
SET e.assistant = "Nova";
```

### 3. Create CREATED_BY relationships for historical data

```cypher
MATCH (a:Assistant {id: "Nova"})
MATCH (f:Fact)
WHERE f.assistant = "Nova" AND NOT (a)-[:CREATED_BY]->(f)
CREATE (a)-[:CREATED_BY]->(f);
```

---

## Using the Backfill Script

The recommended way to run the migration is with the Python helper:

```bash
cd ~/.ai-memory/scripts

# Dry run first (highly recommended)
python3 neo4j_backfill_assistant.py --primary "Nova" --dry-run

# Actual run
python3 neo4j_backfill_assistant.py --primary "Nova"

# Also create relationships
python3 neo4j_backfill_assistant.py --primary "Nova" --create-relationships
```

You can also register additional minds:

```bash
python3 neo4j_backfill_assistant.py --primary "Nova" --additional "Weft" "ResearchBot"
```

---

## Open Questions / Decisions Needed

| Question | Options | Recommendation |
|----------|---------|----------------|
| Should `assistant` be a string (name) or the stable `id`? | Name vs ID | Prefer the stable `id` for robustness, but also store a human `name` |
| Should we enforce that every write has an `assistant`? | Soft guidance vs hard requirement | Start soft (guidance + examples). Hard enforcement can come in the core library later. |
| Should we create `Assistant` nodes for historical minds retrospectively? | Yes / No / Only for active minds | Yes for known primary minds (e.g. Nova). Optional for others. |
| Should we support multiple assistants per Fact (co-creation)? | No for v1 | Keep it simple — one primary `assistant` per node for now. |

---

## Next Steps (Updated — May 2026)

1. ✅ Finalize schema decisions (Assistant nodes + `assistant` property)
2. ✅ Implement backfill + Assistant creation tool (`neo4j_backfill_assistant.py`)
3. ✅ Add `--assistant` filtering to main public search tool (`hybrid_memory_search.py` — see PR #28)
4. ✅ Add write-side assistant support (`neo4j_sync.py --assistant` — see PR #29)
5. 🔄 Improve documentation + real usage examples (in progress — see updated SUBMINDS.md)
6. Provide richer examples and a small "multi-mind quickstart" guide

Stronger enforcement and advanced RLM tooling remain future work (Phase 3+ or private core library).

---

**Would you like me to:**
- Improve the backfill script (add more options, better logging, etc.)?
- Start updating one of the search/traverse tools to support assistant filtering?
- Refine the open questions section based on your preferences?