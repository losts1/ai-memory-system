# Cron Jobs — Scheduled Tasks

The memory system uses OpenClaw's cron system for scheduled maintenance.

## Required Cron Jobs

### 1. Neo4j Health Check (Daily)

Ensures Neo4j stays running.

```json
{
  "name": "Neo4j Health Check",
  "schedule": {"kind": "every", "everyMs": 86400000},
  "payload": {
    "kind": "agentTurn",
    "message": "Ensure Neo4j is running. If not, start it:\n\n```bash\nsudo systemctl start neo4j\n```\n\nVerify connection:\n```bash\nsource ~/.openclaw/workspace/neo4j-venv/bin/activate && python3 -c \"\nfrom neo4j import GraphDatabase\nimport os\nfrom dotenv import load_dotenv\nload_dotenv(os.path.expanduser('~/.openclaw/workspace/.env.neo4j'))\ndriver = GraphDatabase.driver(os.getenv('NEO4J_URI'), auth=(os.getenv('NEO4J_USERNAME'), os.getenv('NEO4J_PASSWORD')))\nwith driver.session() as s:\n    print('Neo4j OK:', s.run('RETURN 1').single()[0])\ndriver.close()\n\"\n```\n\nReport status briefly. If Neo4j was down and restarted, note it.",
    "timeoutSeconds": 60
  }
}
```

### 2. Neo4j Session Sync (Every 30 Minutes)

Syncs raw session logs to the knowledge graph.

```json
{
  "name": "Neo4j Session Sync",
  "schedule": {"kind": "every", "everyMs": 1800000},
  "payload": {
    "kind": "agentTurn",
    "message": "Run Neo4j session sync:\n\n```bash\nsource ~/.openclaw/workspace/neo4j-venv/bin/activate\npython3 ~/.openclaw/workspace/scripts/neo4j_sync.py\n```\n\nReport number of new sessions synced, including any errors. If none, respond briefly."
  }
}
```

### 3. Learner (Hourly)

Autonomous learning sessions.

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

### 4. QMD Session Sync (Daily at 4am)

Creates structured QMD summaries from raw logs.

```json
{
  "name": "QMD Session Sync",
  "schedule": {"kind": "cron", "expr": "0 4 * * *", "tz": "America/New_York"},
  "payload": {
    "kind": "agentTurn",
    "message": "Create QMD session summaries from raw session logs:\n\n1. Find raw logs without QMD summaries:\n```bash\nfor f in ~/.openclaw/workspace/memory/2026-*.md; do\n  date=$(basename \"$f\" .md | grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2}')\n  [ -n \"$date\" ] && [ ! -f ~/.openclaw/workspace/memory/sessions/${date}.qmd ] && echo \"$date needs QMD\"\ndone\n```\n\n2. For each missing date, create a QMD summary:\n- Extract topics from '## Learned:' headers\n- Count evidence lines\n- Create structured QMD with YAML frontmatter\n\n3. Update INDEX.qmd with new entries\n\nReport: dates processed, QMD files created. If none needed, respond briefly.",
    "timeoutSeconds": 120
  }
}
```

### 5. Memory Health Check (Every 8 Hours)

Verifies memory system integrity.

```json
{
  "name": "Memory Health Check",
  "schedule": {"kind": "every", "everyMs": 28800000},
  "payload": {
    "kind": "agentTurn",
    "message": "Memory system check:\n\n1. Check MEMORY.md size (should be ≤15,000 chars)\n2. Check inbox/ is empty\n3. Verify Neo4j is responsive\n4. Report any issues\n\nIf all OK, respond briefly."
  }
}
```

### 6. Session Cleanup (Every 6 Hours)

Removes stale subagent sessions.

```json
{
  "name": "session-cleanup",
  "schedule": {"kind": "every", "everyMs": 21600000},
  "payload": {
    "kind": "agentTurn",
    "message": "Run session cleanup to remove stale subagent sessions older than 4 hours. Adapt the path to your submind skill's session_cleanup.py script, or skip this job if you don't use the submind skill.",
    "timeoutSeconds": 60
  }
}
```

---

## Optional Cron Jobs

### Curiosity Queue Refresh (Weekly)

Refreshes learning topic queue.

```json
{
  "name": "Curiosity Queue Refresh",
  "schedule": {"kind": "cron", "expr": "0 5 * * 0", "tz": "America/New_York"},
  "payload": {
    "kind": "agentTurn",
    "message": "Refresh the curiosity queue:\n\n1. Check memory/curiosity-queue.md\n2. Remove completed items older than 30 days\n3. Add new topics aligned with current goals\n4. Prune items that now have learner sessions\n5. Cap at 10 pending items\n\nReport: items removed, items added, current queue size."
  }
}
```

### Backup + Checkin (Nightly)

Backs up Neo4j and commits changes.

```json
{
  "name": "Neo4j Backup + Checkin",
  "schedule": {"kind": "cron", "expr": "0 3 * * *", "tz": "America/New_York"},
  "payload": {
    "kind": "agentTurn",
    "message": "Backup Neo4j and check in:\n\n1. Neo4j Backup (Docker-based install):\n```bash\nBACKUP_DIR=\"$HOME/.openclaw/neo4j-backups\"\nDATE=$(date +%Y-%m-%d)\nmkdir -p \"$BACKUP_DIR\"\ndocker cp neo4j:/data \"$BACKUP_DIR/neo4j-data-$DATE\" && echo \"Backup: $BACKUP_DIR/neo4j-data-$DATE\" || echo \"Backup failed\"\n```\n\n2. Workspace Check-in:\n```bash\ncd ~/.openclaw/workspace\ngit add -A\ngit diff --cached --stat\ngit commit -m \"Nightly check-in $(date +%Y-%m-%d)\" || echo \"No changes\"\ngit push origin master 2>/dev/null || echo \"Push skipped\"\n```\n\nReport: backup size, commits made, any errors. If all succeeded, respond briefly.",
    "timeoutSeconds": 300
  }
}
```

---

## Registering Cron Jobs

Use OpenClaw's cron system:

```bash
# List current jobs
cron list

# Add a job
cron add '{
  "name": "Job Name",
  "schedule": {"kind": "every", "everyMs": 3600000},
  "payload": {
    "kind": "agentTurn",
    "message": "Task description..."
  }
}'

# Remove a job
cron remove <job-id>
```

---

## Cron vs Heartbeat

**Use cron for:**
- Exact timing matters ("9:00 AM sharp")
- Task needs isolation from main session
- Different model or thinking level
- One-shot reminders
- Output to channel without session involvement

**Use heartbeat for:**
- Multiple checks batch together
- Conversational context needed
- Timing can drift slightly
- Reduce API calls by combining

---

## Monitoring

Check cron job status:

```bash
cron list
```

Each job shows:
- `lastRunAtMs` — Last execution time
- `lastStatus` — "ok" or "error"
- `consecutiveErrors` — Error count
- `nextRunAtMs` — Next scheduled run

---

## Troubleshooting

### Job keeps timing out
- Increase `timeoutSeconds`
- Simplify the task
- Split into multiple jobs

### Job not running
- Check `enabled: true`
- Verify schedule syntax
- Check `consecutiveErrors` — may be disabled after failures

### Neo4j sync failing
- Verify Neo4j is running
- Check `.env.neo4j` credentials
- Run `neo4j_seed.py` to recreate schema