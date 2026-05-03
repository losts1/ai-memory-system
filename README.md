# AI Memory System — Redistribution Package

> **Give your AI agent persistent memory that survives across sessions.**

This package teaches a new AI agent how to build a 5-layer memory system combining markdown files, structured summaries, a Neo4j knowledge graph, and semantic search.

---

## What You Get

| Capability | Description |
|------------|-------------|
| **Persistent Memory** | Sessions start with context from previous sessions |
| **Semantic Search** | Query memory by meaning, not just keywords |
| **Knowledge Graph** | See relationships between learned concepts |
| **Autonomous Learning** | Scheduled research sessions on topics you care about |
| **Self-Improvement** | Memory distills over time, keeping what matters |

---

## Quick Start

### Prerequisites

- OpenClaw installed and configured
- Python 3.9+
- Neo4j 5.x+ (Docker recommended)
- Ollama with `nomic-embed-text` model

### Installation

1. **Create the directory structure:**
   ```bash
   mkdir -p ~/.openclaw/workspace/memory/{core,sessions,inbox,archive,learner-sessions,embeddings,projects}
   ```

2. **Copy templates to workspace:**
   ```bash
   cp -r templates/* ~/.openclaw/workspace/
   cp -r templates/core/* ~/.openclaw/workspace/memory/core/
   cp templates/INDEX.qmd ~/.openclaw/workspace/memory/
   ```

3. **Copy scripts to workspace:**
   ```bash
   cp -r scripts ~/.openclaw/workspace/
   chmod +x ~/.openclaw/workspace/scripts/*.py
   ```

4. **Copy documentation:**
   ```bash
   cp -r docs ~/.openclaw/workspace/
   ```

5. **Start Neo4j (Docker):**
   ```bash
   docker run -d --name neo4j \
     -p 7474:7474 -p 7687:7687 \
     -e NEO4J_AUTH=neo4j/your_password_here \
     -v ~/.openclaw/neo4j-data:/data \
     neo4j:latest

   # Wait ~15s for Neo4j to finish starting
   docker logs -f neo4j 2>&1 | grep -m1 "Started"
   ```

6. **Set up Python environment and initialize schema:**
   ```bash
   # Create .env.neo4j
   cat > ~/.openclaw/workspace/.env.neo4j << EOF
   NEO4J_URI=bolt://localhost:7687
   NEO4J_USERNAME=neo4j
   NEO4J_PASSWORD=your_password_here
   EOF

   # Create Python venv
   python3 -m venv ~/.openclaw/workspace/neo4j-venv
   source ~/.openclaw/workspace/neo4j-venv/bin/activate
   pip install neo4j python-dotenv ollama

   # Initialize schema (Neo4j must be running first)
   python3 ~/.openclaw/workspace/scripts/neo4j_seed.py
   ```

7. **Register cron jobs:**
   ```bash
   # Copy cron job definitions and register each one
   # See docs/CRON_JOBS.md for details
   ```

8. **Personalize identity files:**
   - Edit `SOUL.md` — Your agent's identity
   - Edit `USER.md` — Your profile
   - Edit `memory/core/identity.qmd` — Agent metadata
   - Edit `memory/core/people.qmd` — People the agent should know

9. **Bootstrap:**
   ```bash
   # Follow the bootstrap instructions
   cat ~/.openclaw/workspace/BOOTSTRAP.md

   # Then delete it
   rm ~/.openclaw/workspace/BOOTSTRAP.md
   ```

---

## Package Structure

```
redistribute/
├── BOOTSTRAP.md              # First-run instructions for new AI
├── README.md                 # This file
├── templates/
│   ├── AGENTS.md             # Behavioral rules
│   ├── SOUL.md               # Identity template
│   ├── USER.md               # User profile template
│   ├── TOOLS.md              # Local notes template
│   ├── HEARTBEAT.md          # Periodic task template
│   ├── MEMORY.md             # Long-term memory template
│   ├── INDEX.qmd            # Memory index template
│   └── core/
│       ├── identity.qmd      # Identity QMD
│       ├── people.qmd        # People QMD
│       ├── preferences.qmd   # Preferences QMD
│       └── setup.qmd         # Setup QMD
├── scripts/
│   ├── neo4j_seed.py         # Initialize Neo4j schema
│   ├── hybrid_memory_search.py # Search memory
│   └── neo4j_sync.py         # Sync sessions to graph
└── docs/
    ├── ARCHITECTURE.md       # System design
    ├── CRON_JOBS.md          # Scheduled tasks
    └── LEARNER.md            # Autonomous learning
```

---

## Architecture

| Layer | Technology | Purpose |
|-------|------------|---------|
| 1 | `MEMORY.md` | Curated long-term memory |
| 2 | `memory/*.md` | Raw session logs |
| 3 | `memory/sessions/*.qmd` | Structured QMD summaries |
| 4 | Neo4j | Knowledge graph + vector search |
| 5 | FAISS | Local semantic embeddings |

See `docs/ARCHITECTURE.md` for detailed design.

---

## How It Works

1. **Every session:** AI reads SOUL.md, USER.md, recent daily logs, and MEMORY.md
2. **During session:** AI writes to daily log, searches memory via Neo4j
3. **Every 30 min:** Cron syncs sessions to knowledge graph
4. **Every hour:** AI autonomously learns new topics (Learner)
5. **Daily:** Cron creates QMD summaries from raw logs
6. **Weekly:** AI distills learnings into MEMORY.md

---

## Customization

### For Your Domain

Edit the templates to reflect your use case:

- **Trading bots:** Fleet-Wide Gaps, market making topics
- **Research:** Paper tracking, citation management
- **Personal:** Calendar, contacts, projects
- **Work:** Team members, processes, tools

### Cron Jobs

Adjust frequency in `docs/CRON_JOBS.md`:

- Learning frequency (hourly vs daily)
- Sync intervals (15 min vs 30 min)
- Backup schedule

### Memory Hygiene

Set your own limits:

- `MEMORY.md` target size (default 15,000 chars)
- Raw log retention (default 7 days)
- Archive policy

---

## Security

| Data | Loaded In | Notes |
|------|-----------|-------|
| `MEMORY.md` | Main session only | Personal context |
| `USER.md` | Main session only | User profile |
| `core/people.qmd` | Main session only | Private relationships |
| `memory/*.md` | All sessions | Avoid secrets |

**Never store in memory:** API keys, passwords, private data that shouldn't survive session restarts.

---

## Troubleshooting

### Neo4j connection failed
```bash
docker ps  # Check if running
docker start neo4j  # Start if stopped
```

### Memory search returns nothing
```bash
python3 ~/.openclaw/workspace/scripts/neo4j_seed.py  # Re-initialize
python3 ~/.openclaw/workspace/scripts/neo4j_sync.py --full  # Force full sync
```

### Cron jobs not running
```bash
cron list  # Check job status
# Verify job is enabled: true
# Check consecutiveErrors count
```

---

## Files Reference

| File | When to Read | When to Write |
|------|-------------|---------------|
| `SOUL.md` | Every session start | Identity changes |
| `USER.md` | Every session start | User context updates |
| `MEMORY.md` | Main session start | Distill learnings weekly |
| `HEARTBEAT.md` | On heartbeat poll | Add periodic tasks |
| `TOOLS.md` | Need local config | New tools/paths |
| `memory/YYYY-MM-DD.md` | Session start | Throughout session |

---

## Contributing

This is a template package. Customize for your own use case.

To share improvements:
1. Fork the concepts
2. Adapt to your domain
3. Document what works

---

## License

MIT License — Use freely for personal or commercial projects.

---

## Credits

Based on the Nova memory system (2025-2026). Architecture evolved through production use for AI-assisted trading bot management.