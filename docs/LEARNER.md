# Autonomous Learning (Learner)

## Overview

The Learner system enables the AI agent to autonomously research topics during scheduled learning sessions. It creates structured knowledge entries that feed into the memory system.

## How It Works

1. **Topic Selection** — Checks `memory/curiosity-queue.md` or picks from Fleet-Wide Gaps
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
| 1 | Topic Name | Trading/Math | Application to fleet |
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
| Fleet impact section | Required |
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

## Fleet Impact
[How this applies to the trading bots]

## References
1. [Citation]
2. [Citation]

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
    "message": "Use the Learner skill to autonomously learn something new. IMPORTANT: First check the topic registry at memory/learner-topics.json to avoid duplicates. Pick a topic that addresses a Fleet-Wide Gap from MEMORY.md if possible. Research it briefly (5-15 min), write a session file that passes the quality gate (≥50 lines, ≥2 citations, fleet impact section). Update the topic registry after."
  }
}
```

## Topic Prioritization

1. **Fleet-Wide Gaps** — Issues affecting multiple trading bots
2. **Curiosity Queue** — User-defined learning priorities
3. **Follow-ups** — Topics referenced but not yet explored
4. **Random** — Serendipitous exploration

## Evidence Integration

Learner sessions contribute to `MEMORY.md` during distillation:

1. **Heartbeat** checks recent learner sessions
2. **Distills** key insights into MEMORY.md
3. **References** the learner session file
4. **Updates** Fleet-Wide Gaps if applicable

Example MEMORY.md entry:

```markdown
### Queue Position
Moallemi-Yuan 2016: front-of-queue ≈0.26 ticks above average. Queue position IS adverse selection filter. Garriott 2025: inventory shocks reduce later-in-queue liquidity. [Learner: 2026-05-03_12-01]
```

## Integration with Neo4j

Learner sessions sync to the knowledge graph via the standard sync script.
Learner session files follow the same `## Learned:` header format as daily logs,
so `neo4j_sync.py` picks them up automatically:

```bash
# Run manually or via the Neo4j Session Sync cron job
source ~/.openclaw/workspace/neo4j-venv/bin/activate
python3 ~/.openclaw/workspace/scripts/neo4j_sync.py
```

Creates:
- `Fact` nodes for each `## Learned:` section
- `LEARNED_IN` relationships linking Facts to their source Session

## Searching Learned Topics

```bash
# Semantic search
python3 hybrid_memory_search.py "adverse selection" --max-results 5

# With relationships
python3 hybrid_memory_search.py "market making" --graph

# Files only
python3 hybrid_memory_search.py "fill probability" --files-only
```

---

## Example Session

**Input (from cron):**
> Use the Learner skill to autonomously learn something new...

**Process:**
1. Check `memory/curiosity-queue.md` — next topic is "Queue Position Valuation"
2. Search web for academic papers on queue position in limit order books
3. Find Moallemi-Yuan 2016, Garriott et al. 2025
4. Synthesize key findings
5. Write session file with citations

**Output:** `memory/learner-sessions/2026-05-03_12-01_queue-position-valuation.md`

**Synced to Neo4j:** Creates `Fact` nodes for "Queue Position Valuation", "Moallemi-Yuan Model", "Garriott Crowding-Out", with relationships.

---

## Customization

### Topic Sources

Modify the cron job message to change topic sources:

- **From Fleet-Wide Gaps:** "Pick a topic from MEMORY.md Fleet-Wide Gaps"
- **From Curiosity Queue:** "Check memory/curiosity-queue.md"
- **Specific Domain:** "Pick a topic in [domain]"

### Quality Gate Adjustments

Change minimum thresholds:

- **Lines:** ≥50 (more detail)
- **Citations:** ≥2 (academic rigor)
- **Fleet impact:** Required (applied learning)

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