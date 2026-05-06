# Cron Jobs — Scheduled Tasks

The memory system uses scheduled jobs for maintenance. Register these with whatever scheduler your platform provides — OS cron, systemd timers, your agent platform's built-in scheduler, or an agent heartbeat loop.

## Required Jobs

### 1. Neo4j Health Check (Daily)

Ensures Neo4j stays running.

**Schedule:** Every 24 hours

**Task for agent:**

> Ensure Neo4j is running. If not, start it:
>
> ```bash
> sudo systemctl start neo4j
> ```
>
> Verify connection:
>
> ```bash
> source ~/.ai-memory/neo4j-venv/bin/activate && python3 -c "
> from neo4j import GraphDatabase
> import os
> from dotenv import load_dotenv
> load_dotenv(os.path.expanduser('~/.ai-memory/.env.neo4j'))
> driver = GraphDatabase.driver(os.getenv('NEO4J_URI'), auth=(os.getenv('NEO4J_USERNAME'), os.getenv('NEO4J_PASSWORD')))
> with driver.session() as s:
>     print('Neo4j OK:', s.run('RETURN 1').single()[0])
> driver.close()
> "
> ```
>
> Report status briefly. If Neo4j was down and restarted, note it.

---

### 2. Neo4j Session Sync (Every 30 Minutes)

Syncs raw session logs to the knowledge graph.

**Schedule:** Every 30 minutes

**Task for agent:**

> Run Neo4j session sync:
>
> ```bash
> source ~/.ai-memory/neo4j-venv/bin/activate
> python3 ~/.ai-memory/scripts/neo4j_sync.py
> ```
>
> Report number of new sessions synced, including any errors. If none, respond briefly.

---

### 3. Learner (Hourly)

Autonomous learning sessions.

**Schedule:** Every hour

**Task for agent:**

> Use the Learner skill to autonomously learn something new. IMPORTANT: First check the topic registry at memory/learner-topics.json to avoid duplicates. Pick a topic from the curiosity queue or one that addresses a knowledge gap in MEMORY.md. Research it briefly (5-15 min), write a session file that passes the quality gate (≥50 lines, ≥2 citations, application section). Update the topic registry after.

---

### 4. QMD Session Sync (Daily at 4am)

Creates structured QMD summaries from raw logs.

**Schedule:** Daily at 4am (adjust timezone as needed)

**Task for agent:**

> Create QMD session summaries from raw session logs:
>
> 1. Find raw logs without QMD summaries:
>
> ```bash
> for f in ~/.ai-memory/memory/[0-9][0-9][0-9][0-9]-*.md; do
>   date=$(basename "$f" .md | grep -oE '^[0-9]{4}-[0-9]{2}-[0-9]{2}')
>   [ -n "$date" ] && [ ! -f ~/.ai-memory/memory/sessions/${date}.qmd ] && echo "$date needs QMD"
> done
> ```
>
> 2. For each missing date, create a QMD summary:
>    - Extract topics from `## Learned:` headers
>    - Count evidence lines
>    - Create structured QMD with YAML frontmatter
>
> 3. Update INDEX.qmd with new entries
>
> Report: dates processed, QMD files created. If none needed, respond briefly.

---

### 5. Memory Health Check (Every 8 Hours)

Verifies memory system integrity.

**Schedule:** Every 8 hours

**Task for agent:**

> Memory system check:
>
> 1. Check MEMORY.md size (should be ≤15,000 chars)
> 2. Check inbox/ is empty
> 3. Verify Neo4j is responsive
> 4. Report any issues
>
> If all OK, respond briefly.

---

### 6. Session Cleanup (Every 6 Hours)

Removes stale subagent sessions.

**Schedule:** Every 6 hours

**Task for agent:**

> Run session cleanup to remove stale subagent sessions older than 4 hours. Adapt the path to your submind skill's session_cleanup.py script, or skip this job if you don't use the submind skill.

---

## Optional Jobs

### Curiosity Queue Refresh (Weekly)

Refreshes learning topic queue.

**Schedule:** Weekly (e.g., Sunday at 5am)

**Task for agent:**

> Refresh the curiosity queue:
>
> 1. Check memory/curiosity-queue.md
> 2. Remove completed items older than 30 days
> 3. Add new topics aligned with current goals
> 4. Prune items that now have learner sessions
> 5. Cap at 10 pending items
>
> Report: items removed, items added, current queue size.

---

### Backup + Checkin (Nightly)

Backs up Neo4j and commits changes.

**Schedule:** Daily at 3am (adjust timezone as needed)

**Task for agent:**

> Backup Neo4j and check in:
>
> 1. Neo4j Backup (Docker-based install):
>
> ```bash
> BACKUP_DIR="$HOME/.ai-memory/neo4j-backups"
> DATE=$(date +%Y-%m-%d)
> mkdir -p "$BACKUP_DIR"
> docker cp neo4j:/data "$BACKUP_DIR/neo4j-data-$DATE" && echo "Backup: $BACKUP_DIR/neo4j-data-$DATE" || echo "Backup failed"
> ```
>
> 2. Workspace Check-in:
>
> ```bash
> cd ~/.ai-memory
> git add -A
> git diff --cached --stat
> git commit -m "Nightly check-in $(date +%Y-%m-%d)" || echo "No changes"
> git push origin master 2>/dev/null || echo "Push skipped"
> ```
>
> Report: backup size, commits made, any errors. If all succeeded, respond briefly.

---

## Registering Jobs

How you register these depends on your platform.

**OS cron (`crontab -e`):**
```
# Neo4j sync every 30 min
*/30 * * * * /path/to/agent-cli --message "source ~/.ai-memory/neo4j-venv/bin/activate && python3 ~/.ai-memory/scripts/neo4j_sync.py"

# QMD sync daily at 4am
0 4 * * * /path/to/agent-cli --message "Create QMD session summaries..."
```

**systemd timer:** Create a `.service` + `.timer` unit pair pointing to your agent CLI.

**Agent heartbeat:** For platforms that support periodic agent invocation, add tasks to `HEARTBEAT.md`. The agent checks this file on each heartbeat poll and executes any listed tasks.

**Platform scheduler:** If your agent platform has a built-in job system, use the task descriptions above as the prompt for each job and set the schedule accordingly.

---

## Cron vs Heartbeat

**Use a dedicated scheduled job when:**
- Exact timing matters ("9:00 AM sharp")
- Task needs isolation from the main session
- Output should go to a channel without session involvement
- One-shot reminders

**Use agent heartbeat when:**
- Multiple checks can batch together in one turn
- Conversational context from recent messages is needed
- Timing can drift slightly (every ~30 min is fine)
- You want to reduce API calls by combining periodic checks

---

## Monitoring

Track job health however your scheduler supports it. Key signals to watch:

- Last execution time (is the job actually running?)
- Last status (success or error?)
- Consecutive error count (may need to re-enable after repeated failures)
- Next scheduled run

---

## Troubleshooting

### Neo4j sync failing
- Verify Neo4j is running: `docker ps`
- Check `.env.neo4j` credentials
- Run `python3 ~/.ai-memory/scripts/neo4j_seed.py` to recreate schema

### QMD sync missing files
- Verify raw logs exist in `~/.ai-memory/memory/`
- Check the glob pattern matches your date format (`YYYY-MM-DD`)

### Job not running
- Verify the schedule syntax for your platform
- Check for repeated errors that may have disabled the job
- Test the task manually before re-enabling
