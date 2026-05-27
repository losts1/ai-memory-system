# Suggested Update for docs/ARCHITECTURE.md (Overview Table)

Replace the current overview table with this more honest version:

```markdown
## Overview

The AI Memory System is a **hybrid architecture** combining the following layers:

| Layer | Technology | Purpose | Update Frequency | Notes |
|-------|------------|---------|------------------|-------|
| 1 | `MEMORY.md` | Curated long-term memory | Weekly distillation | Always present |
| 2 | `memory/*.md` | Raw session logs | Every session | Always present |
| 3 | `memory/sessions/*.qmd` | Structured QMD summaries | Daily (cron) | Always present |
| 4 | Neo4j | Knowledge graph + vector search | 30-min sync | Core semantic + relational layer |
| 5 | FAISS | Local semantic embeddings (offline fallback) | On-demand | **Optional** — requires manual index building |
```

Add this note right after the table:

> **Note on Layer 5**: FAISS is a best-effort local fallback. It must be explicitly built from existing data and is not automatically available on fresh installs. See the "Layer 5: FAISS Embeddings" section below for caveats and setup instructions.

This change directly addresses the spirit of issue #20 while we do the broader honest-positioning work in Phase 1.
