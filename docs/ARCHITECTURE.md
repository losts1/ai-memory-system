# Memory System Architecture

## Overview

The AI Memory System is a **5-layer hybrid architecture** combining:

| Layer | Technology | Purpose | Update Frequency |
|-------|------------|---------|-------------------|
| 1 | `MEMORY.md` | Curated long-term memory | Weekly distillation |
| 2 | `memory/*.md` | Raw session logs | Every session |
| 3 | `memory/sessions/*.qmd` | Structured QMD summaries | Daily (cron) |
| 4 | Neo4j | Knowledge graph + vector search | 30-min sync |
| 5 | FAISS | Local semantic embeddings (not yet implemented) | On-demand |

## Layer Details

### Layer 1: Curated Long-Term Memory (`MEMORY.md`)

**What it is:** Distilled wisdom loaded into every main session. Target: ≤15,000 characters.

**What goes here:**
- Key facts about the user and environment
- Learned topics (condensed to 1-2 lines with citations)
- System state (fleet status, cron jobs, etc.)
- Cross-cutting insights (patterns across multiple sessions)

**What doesn't go here:**
- Raw session logs (Layer 2)
- Temporary state (use `memory/YYYY-MM-DD.md`)
- Private data that shouldn't load in shared contexts

**Security:** Only loaded in main session (direct chat). Never in group chats, shared agents, or subagents.

**Update cadence:** Every few days, during heartbeat memory-maintenance cycles.

### Layer 2: Raw Session Logs (`memory/YYYY-MM-DD.md`)

**What it is:** Unfiltered daily notes. Everything that happened, in real time.

**Naming:**
- `YYYY-MM-DD.md` — Main daily log
- `YYYY-MM-DD-topic.md` — Topic-specific session
- `YYYY-MM-DD-HHMM.md` — Sub-session overflow

**Loaded at startup:** Today + yesterday (automatic).

**Retention:** Keep 7+ days before archiving. Never delete before QMD distillation.

### Layer 3: QMD Structured Summaries (`memory/sessions/*.qmd`)

**What it is:** CODE-aligned structured knowledge (Capture → Organize → Distill → Express).

**File format:**
```yaml
---
id: unique-kebab-slug
type: session|identity|person|preferences|setup|project
tags: [array, of, tags]
created: YYYY-MM-DD
updated: "YYYY-MM-DDTHH:MM:SS"
priority: low|medium|high
status: active|completed|draft
summary: "Max 200 chars"
---
```

**Cron job:** Daily at 4am EST, converts raw logs → QMD summaries.

**Subdirectories:**
| Dir | Purpose |
|-----|---------|
| `sessions/` | Daily summaries |
| `core/` | Stable curated knowledge (identity, people, preferences, setup) |
| `projects/` | Active project tracking |
| `inbox/` | Capture zone (process within 24h) |
| `archive/` | Legacy files |

### Layer 4: Neo4j Knowledge Graph

**What it is:** Persistent relational memory with vector search.

**Schema:**
- `Fact` nodes — Learned topics with 768-dim embeddings (`nomic-embed-text`)
- `Session` nodes — Source file pointers (no raw content stored)
- Relationships — `LEARNED_IN` (Fact → Session)

**Indexes:**
- Vector index `fact_embeddings` — semantic similarity on `Fact.embedding`
- Fulltext index `fact_content` — keyword search on `Fact.name` + `Fact.content`

**Sync jobs:**
- Every 30 min: Sessions → Graph
- Every 30 min: Learner outputs → Graph
- Nightly: Backup + git commit

**Search:**
```bash
# Hybrid search (Neo4j + files)
python3 hybrid_memory_search.py "your topic" --max-results 5

# With graph relationships
python3 hybrid_memory_search.py "your topic" --graph

# Files only
python3 hybrid_memory_search.py "your topic" --files-only
```

### Layer 5: FAISS Embeddings (not yet implemented)

**What it is:** Planned local semantic search fallback when Neo4j is unavailable.

**Location:** `memory/embeddings/` (reserved, not yet used)

**Model:** `nomic-embed-text` (768-dim, local Ollama)

**Note:** This layer is not yet implemented in the current package. Semantic search currently runs entirely through Neo4j's vector index (Layer 4).

---

## Data Flow

```
Session Start
     │
     ▼
┌─────────────────────────────────────────────────┐
│  Read: SOUL.md, USER.md                         │
│  Read: memory/YYYY-MM-DD.md (today + yesterday) │
│  Read: MEMORY.md (main session only)            │
└─────────────────────────────────────────────────┘
     │
     ▼
During Session
     │
     ├── Write to memory/YYYY-MM-DD.md
     │
     └── Search memory via hybrid_memory_search.py
           │
           ├── Neo4j vector (semantic)
           ├── Neo4j graph (relationships)
           └── Files (grep fallback)
     │
     ▼
Session End
     │
     └── Cron jobs handle:
           ├── QMD sync (daily 4am)
           ├── Neo4j sync (30 min)
           └── Memory distillation (manual/heartbeat)
```

---

## Evidence Verification

**Rule:** No reference-only evidence. If memory references session logs/tool output, either ensure the referenced file exists **or** embed the key excerpt inline.

**Evidence tags:**
- `[TOOL-VERIFIED]` — Command output confirms
- `[USER-VERIFIED]` — User confirmed
- `[INFERRED]` — Logical deduction (explain reasoning)

**Why:** Self-improvement detection flags claims without evidence. If raw logs are deleted, verification becomes impossible.

---

## Memory Hygiene

### Daily (Automatic)
- Raw logs written to `memory/YYYY-MM-DD.md`
- QMD sync job creates structured summaries

### Weekly (Heartbeat)
- Review recent daily logs
- Distill learnings into `MEMORY.md`
- Remove outdated info

### Monthly
- Archive old raw logs to `memory/archive/`
- Clean up learner-sessions directory
- Rebuild Neo4j indexes if needed

---

## Security Considerations

| Data | Loaded In | Notes |
|------|-----------|-------|
| `MEMORY.md` | Main session only | Contains personal context |
| `USER.md` | Main session only | User profile |
| `core/people.qmd` | Main session only | Private relationships |
| `memory/*.md` | All sessions | Raw logs (avoid secrets) |
| Neo4j | All sessions | Embeddings (avoid secrets) |

**Never store in memory:**
- API keys, passwords, tokens
- Private user data you shouldn't share
- Anything that shouldn't survive a session restart

---

## Troubleshooting

### Neo4j connection failed
```bash
docker ps  # Check if running
docker start neo4j  # Start if stopped
```

### Memory search returns nothing
```bash
# Re-initialize schema
python3 scripts/neo4j_seed.py

# Force full sync
python3 scripts/neo4j_sync.py --full
```

### Context compaction (lost context)
```bash
# Search for topic
python3 hybrid_memory_search.py "lost topic" --max-results 5

# Check today's log
cat memory/$(date +%Y-%m-%d).md
```

---

## File Size Guidelines

| File | Target Size | Action if Oversized |
|------|-------------|---------------------|
| `MEMORY.md` | ≤15,000 chars | Distill and compress |
| Daily logs | No limit | Archive after 7 days |
| QMD files | ≤500 lines | Split if needed |
| Neo4j Facts | ~200 chars summary | Keep concise |

---

## Performance

| Operation | Typical Time |
|-----------|--------------|
| Session start (read 3 files) | <100ms |
| Memory search (Neo4j) | 50-200ms |
| Memory search (files only) | 100-500ms |
| Neo4j sync (30 min) | 5-20s |
| QMD generation (daily) | 30-60s |

---

## Dependencies

- **Neo4j 5.x+** — Vector index support
- **Ollama** — `nomic-embed-text` for embeddings
- **Python 3.9+** — `neo4j`, `python-dotenv`, `ollama`
- **OpenClaw** — Cron system for scheduled jobs