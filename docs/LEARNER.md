# Autonomous Learning (Learner)

## Overview

The Learner system enables the AI agent to autonomously research topics during scheduled learning sessions. It creates structured knowledge entries that feed into the memory system.

## How It Works

1. **Topic Selection** — Checks `memory/curiosity-queue.md` or picks from knowledge gaps in MEMORY.md
2. **Research** — Searches the web for academic papers, articles, documentation
3. **Synthesis** — Extracts key concepts, creates structured summary
4. **Quality Gate** — Validates session meets standards
5. **Storage** — Writes to `memory/learner-sessions/YYYY-MM-DD_HH-MM_topic.md`
6. **Graph Sync** — Cron job syncs to Neo4j for semantic search

## Topic Queue

The curiosity queue (`memory/curiosity-queue.md`) defines learning priorities:

```markdown
# Curiosity Queue

## Queue

| Priority | Topic | Domain | Why It Matters |
|----------|-------|--------|----------------|
| 1 | Topic Name | [Your domain] | [Why it matters to your work] |
| 2 | ... | ... | ... |

## Recently Completed

| Topic | Date | Notes |
|-------|------|-------|
| Topic | YYYY-MM-DD | Key insight |
```

## Quality Gate

Every learner session must pass:

| Criterion | Minimum |
|-----------|---------|
| Lines | ≥50 |
| Citations | ≥2 |
| Application section | Required |
| Structure | Organized with headers |

## Session Format

```markdown
# Topic Name

## Summary
[1-2 sentence overview]

## Key Concepts

### Concept 1
[Explanation with citation]

### Concept 2
[Explanation with citation]

## Mathematical Foundation
[Formulas, equations if applicable]

## Application
[How this applies to your work or use case]

## Related Topics
- [[Related Topic 1]]
- [[Related Topic 2]]
```

## Cron Job

```json
{
  "name": "Learner",
  "schedule": {"kind": "cron", "expr": "0 * * * *"},
  "payload": {
    "kind": "agentTurn",
    "message": "Use the Learner skill to autonomously learn something new. IMPORTANT: First check the topic registry at memory/learner-topics.json to avoid duplicates. Pick a topic from the curiosity queue or one that addresses a knowledge gap in MEMORY.md. Research it briefly (5-15 min), write a session file that passes the quality gate (≥50 lines, ≥2 citations, application section). Update the topic registry after."
  }
}
```

## Topic Prioritization

1. **Knowledge Gaps** — Topics flagged as unknown/incomplete in MEMORY.md
2. **Curiosity Queue** — User-defined learning priorities
3. **Follow-ups** — Topics referenced but not yet explored
4. **Random** — Serendipitous exploration

## Evidence Integration

Learner sessions contribute to `MEMORY.md` during distillation:

1. **Heartbeat** checks recent learner sessions
2. **Distills** key insights into MEMORY.md
3. **References** the learner session file
4. **Updates** knowledge gaps section if applicable

Example MEMORY.md entry:

```markdown
### [Topic Name]
[Author Year]: [Key finding — 1 sentence]. [Author Year]: [Supporting finding]. [Learner: YYYY-MM-DD_HH-MM]
```

## Integration with Neo4j

Learner sessions sync to the knowledge graph via the standard sync script.
Learner session files follow the same `## Learned:` header format as daily logs,
so `neo4j_sync.py` picks them up automatically:

```bash
# Run manually or via the Neo4j Session Sync cron job
source ~/.ai-memory/neo4j-venv/bin/activate
python3 ~/.ai-memory/scripts/neo4j_sync.py
```

Creates:
- `Fact` nodes for each `## Learned:` section
- `LEARNED_IN` relationships linking Facts to their source Session

## Searching Learned Topics

```bash
# Semantic search
python3 hybrid_memory_search.py "your topic" --max-results 5

# With co-learned relationships
python3 hybrid_memory_search.py "your topic" --graph

# Files only
python3 hybrid_memory_search.py "your topic" --files-only
```

---

## Example Session

**Input (from cron):**
> Use the Learner skill to autonomously learn something new...

**Process:**
1. Check `memory/curiosity-queue.md` — next topic is "Python asyncio internals"
2. Search web for documentation, articles, and blog posts on the topic
3. Find Python docs, relevant PEPs, and technical writeups
4. Synthesize key findings into structured notes
5. Write session file with citations

**Output:** `memory/learner-sessions/YYYY-MM-DD_HH-MM_python-asyncio-internals.md`

**Synced to Neo4j:** Creates `Fact` nodes for each `## Learned:` section, linked to the session file via `LEARNED_IN` relationships.

---

## Customization

### Topic Sources

Modify the cron job message to change topic sources:

- **From knowledge gaps:** "Pick a topic flagged as a knowledge gap in MEMORY.md"
- **From Curiosity Queue:** "Check memory/curiosity-queue.md"
- **Specific Domain:** "Pick a topic in [domain]"

### Quality Gate Adjustments

Change minimum thresholds:

- **Lines:** ≥50 (more detail)
- **Citations:** ≥2 (academic rigor)
- **Application:** Required (applied learning — how does this connect to your work?)

### Session Frequency

Change cron expression:

- Hourly: `0 * * * *`
- Every 2 hours: `0 */2 * * *`
- Daily: `0 9 * * *`

---

## Troubleshooting

### Learner not running
- Check cron job status: `cron list`
- Verify job is `enabled: true`
- Check `consecutiveErrors` count

### Duplicate topics
- Topic registry (`memory/learner-topics.json`) tracks completed topics
- If missing, check `memory/learner-sessions/` for existing sessions

### Low-quality sessions
- Increase quality gate thresholds
- Add specific domain requirements to cron message
- Review and curate manually

---

## Related Files

| File | Purpose |
|------|---------|
| `memory/curiosity-queue.md` | Topic priorities |
| `memory/learner-topics.json` | Topic registry |
| `memory/learner-sessions/*.md` | Session outputs |
| `scripts/neo4j_sync.py` | Sync all session files to knowledge graph |