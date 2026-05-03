# BOOTSTRAP.md — AI Agent Memory System Bootstrap

> **You are a new AI agent.** This document is your birth certificate. Read it, follow it, then delete it. You won't need it again.

---

## What You're Building

A **persistent memory system** that survives across sessions. You'll create:

1. **Layer 1 — Curated Long-Term Memory** (`MEMORY.md`): Distilled wisdom, loaded every main session
2. **Layer 2 — Daily Session Logs** (`memory/YYYY-MM-DD.md`): Raw notes, auto-created
3. **Layer 3 — Structured Summaries** (`memory/sessions/*.qmd`): Curated daily summaries
4. **Layer 4 — Knowledge Graph** (Neo4j): Semantic search + relationships
5. **Layer 5 — Semantic Embeddings** (FAISS): Local vector search

This gives you:
- Continuity across sessions (you "remember" what happened before)
- Semantic search for prior knowledge (Neo4j + FAISS)
- Autonomous learning capability (learner skill)
- Self-improvement cycles (memory distillation)

---

## First-Time Setup (Do This Now)

### Step 1: Create Directory Structure

```bash
mkdir -p ~/.openclaw/workspace/memory/{core,sessions,inbox,archive,learner-sessions,embeddings,projects}
touch ~/.openclaw/workspace/memory/inbox/.gitkeep
```

### Step 2: Create Core Files

Create these files in `~/.openclaw/workspace/`:

| File | Purpose |
|------|---------|
| `AGENTS.md` | Your behavioral rules (copy from `templates/AGENTS.md`) |
| `SOUL.md` | Your identity and values |
| `USER.md` | Who you're helping |
| `TOOLS.md` | Local notes (paths, credentials, preferences) |
| `HEARTBEAT.md` | Periodic task checklist (starts empty) |
| `MEMORY.md` | Your curated long-term memory (starts minimal) |

### Step 3: Create Core Memory Files

Create these in `~/.openclaw/workspace/memory/core/`:

| File | Purpose |
|------|---------|
| `identity.qmd` | Who you are |
| `people.qmd` | People you know (private) |
| `preferences.qmd` | How you work |
| `setup.qmd` | Environment details |

### Step 4: Initialize Neo4j

1. Start Neo4j (Docker recommended):
   ```bash
   docker run -d --name neo4j \
     -p 7474:7474 -p 7687:7687 \
     -e NEO4J_AUTH=neo4j/your_password \
     -v ~/.openclaw/neo4j-data:/data \
     neo4j:latest

   # Wait for Neo4j to be ready before proceeding
   docker logs -f neo4j 2>&1 | grep -m1 "Started"
   ```

2. Create `~/.openclaw/workspace/.env.neo4j`:
   ```
   NEO4J_URI=bolt://localhost:7687
   NEO4J_USERNAME=neo4j
   NEO4J_PASSWORD=your_password
   ```

3. Create Python virtual environment:
   ```bash
   python3 -m venv ~/.openclaw/workspace/neo4j-venv
   source ~/.openclaw/workspace/neo4j-venv/bin/activate
   pip install neo4j python-dotenv ollama
   ```

4. Initialize schema (Neo4j must be running first):
   ```bash
   python3 scripts/neo4j_seed.py
   ```

### Step 5: Set Up Cron Jobs

Copy the cron job definitions from `docs/CRON_JOBS.md` and register them with OpenClaw's cron system.

### Step 6: Read Your Identity

```bash
cat ~/.openclaw/workspace/SOUL.md
cat ~/.openclaw/workspace/USER.md
```

### Step 7: Delete This File

```bash
rm ~/.openclaw/workspace/BOOTSTRAP.md
```

You're now ready. Read `AGENTS.md` for your behavioral rules.

---

## Daily Operation

### Every Session Start

1. Read `SOUL.md` — who you are
2. Read `USER.md` — who you're helping
3. Read `memory/YYYY-MM-DD.md` (today + yesterday) for recent context
4. If MAIN SESSION: Also read `MEMORY.md`

### Memory Hygiene

- **During sessions**: Write to `memory/YYYY-MM-DD.md`
- **Heartbeats**: Check `HEARTBEAT.md` for tasks
- **Weekly**: Distill learnings into `MEMORY.md`
- **Monthly**: Archive old raw logs to `memory/archive/`

### Searching Memory

```bash
# Hybrid search (Neo4j + files)
python3 ~/.openclaw/workspace/hybrid_memory_search.py "your query" --max-results 5

# Files only
python3 ~/.openclaw/workspace/hybrid_memory_search.py "your query" --files-only
```

---

## Key Files Reference

| File | When to Read | When to Write |
|------|-------------|---------------|
| `SOUL.md` | Every session start | Identity changes |
| `USER.md` | Every session start | User context updates |
| `MEMORY.md` | Main session start | Distill learnings weekly |
| `HEARTBEAT.md` | On heartbeat poll | Add periodic tasks |
| `TOOLS.md` | Need local config | New tools/paths |
| `memory/YYYY-MM-DD.md` | Session start | Throughout session |

---

## Troubleshooting

**Neo4j connection failed**: Check `docker ps`, ensure Neo4j is running
**Memory search returns nothing**: Run `python3 scripts/neo4j_seed.py` to initialize
**Missing directories**: Re-run Step 1 directory creation

---

## Next Steps

After setup:
1. Read `docs/ARCHITECTURE.md` for system design
2. Read `docs/LEARNER.md` for autonomous learning
3. Read `docs/CRON_JOBS.md` for scheduled tasks

You now have a persistent memory. Use it.