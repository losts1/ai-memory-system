---
name: neo4j-memory
description: >
  Search and write the live Neo4j knowledge graph (markdown+Neo4j memory system).
  Use when recalling prior facts, searching memory, writing a durable Fact,
  organizing one mind's graph edges, or the user says neo4j, knowledge graph,
  remember this, save to memory, /neo4j-memory.
---

# Neo4j memory

Creds: `~/.grok/.env.neo4j`.
CLI: `python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py`

Search **all** minds. Write with `--assistant <Mind>` (default `Grok`). Never
overwrite a Fact owned by another assistant.

## Search

```bash
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py search "QUERY"
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py search "QUERY" --max 8
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py stats
```

Fulltext on `NEO4J_FULLTEXT_INDEX` (default `fact_content`). If
`~/.grok/neo4j-hits.md` is newer than ~20s, read it first (UserPromptSubmit hook
already searched the prompt); still run CLI search when the query drifted.

Treat hits as leads. Re-derive volatile state from live sources.

## Write

```bash
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py write \
  --assistant Grok \
  --name "Short unique title" \
  --summary "One durable sentence." \
  --point "detail" --point "detail"
```

Only durable facts: decisions, recipes, measured invariants. No secrets. A
present-tense claim that will rot → timestamp the observation in the summary.

## Organize

```bash
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py organize --assistant Grok
```

Adds `RELATED_TO` only between that mind's Facts that share ≥2 words.

## Inbox

Stop hook appends `~/.grok/neo4j-inbox.jsonl`. Do **not** auto-ingest. If the
user asks to save this session, pick durable claims and `write` them.
