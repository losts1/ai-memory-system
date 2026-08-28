# Grok TUI ↔ Neo4j wiring

This directory is the **Grok Build TUI** integration. `claude/` is the Claude
Code freshness pipeline. Do not mix them.

If you are an LLM asked to wire Grok to the Neo4j knowledge graph, **follow this
file in order**. Do not invent a second client. Do not commit `.env.neo4j`.

Measured on Grok Build TUI 1.0.5 (2026-08-28): `UserPromptSubmit` is
**observe-only** — stdout is ignored, so you cannot inject search hits into the
prompt the way Claude Code `additionalContext` does. Search is a hook that
writes `~/.grok/neo4j-hits.md`, plus a home rule that tells the model to read
it and/or run the CLI.

## What you install

| Source in this folder | Destination |
|---|---|
| `skills/neo4j-memory/` | `~/.grok/skills/neo4j-memory/` |
| `hooks/neo4j-memory.json` | `~/.grok/hooks/neo4j-memory.json` |
| `rules/neo4j-memory.md` | `~/.grok/rules/neo4j-memory.md` |
| `env.neo4j.example` | `~/.grok/.env.neo4j` (create; never commit) |

Grok auto-loads:

- skills from `~/.grok/skills/`
- global hooks from `~/.grok/hooks/*.json`
- home rules from `~/.grok/rules/*.md` (every session)

## Prerequisites

- Grok Build TUI
- Python 3.10+ with `neo4j` and `python-dotenv` (`pip install neo4j python-dotenv`)
- Neo4j 5.x+ reachable over Bolt, with a Fact fulltext index
- A Neo4j user that can read Facts and, if you will write, MERGE Fact/Assistant/Word

## Steps

### 1. Copy files

From the repo root (this package):

```bash
mkdir -p ~/.grok/skills ~/.grok/hooks ~/.grok/rules
cp -r grok/skills/neo4j-memory ~/.grok/skills/
cp grok/hooks/neo4j-memory.json ~/.grok/hooks/
cp grok/rules/neo4j-memory.md ~/.grok/rules/
chmod +x ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py
```

Hook commands use `${HOME}/.grok/...`. Do not bake in a specific username.

### 2. Credentials

```bash
cp grok/env.neo4j.example ~/.grok/.env.neo4j
chmod 600 ~/.grok/.env.neo4j
# edit NEO4J_PASSWORD (required). Set USERNAME/URI to match the server.
```

Discover index names on the live DB (do not assume `fact_embeddings`):

```cypher
SHOW INDEXES YIELD name, type
```

A production graph in this lineage used `factEmbeddingIndex` (VECTOR) and
`fact_content` (FULLTEXT). Put those names in `.env.neo4j` if they differ from
the example. The CLI search path is **fulltext only** (fast enough for a hook).
Vector search is not required for this wiring.

### 3. Reload hooks

Hooks in a **running** session are the ones loaded at start (or last `/hooks`
reload). After copying:

- New session: automatic.
- Current session: `/hooks` → `r`.

`SessionStart` will not retro-fire. `UserPromptSubmit` starts on the next prompt
after reload.

### 4. Verify (required — do not claim success without this)

```bash
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py stats
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py search "a term you know is in the graph"
echo '{"prompt":"a term you know is in the graph please search"}' \
  | python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py hook-prompt
cat ~/.grok/neo4j-hits.md
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py hook-session
cat ~/.grok/neo4j-session.md
```

`stats` must print Fact counts. `search` must return hits for a known term, or
the fulltext index name is wrong. `hook-prompt` must rewrite `neo4j-hits.md`.

Optional write probe (delete it after):

```bash
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py write \
  --assistant Grok \
  --name "Grok wire probe" \
  --summary "Throwaway; delete after verify."
# then MATCH+DELETE that Fact. Never leave probes in a shared graph.
```

A write to a Fact name owned by another assistant **must** exit 3 (`refused`).

## How the pieces work

| Event | Script | Effect |
|---|---|---|
| `SessionStart` | `hook-session` | Writes `~/.grok/neo4j-session.md` (counts + recent names). Stdout ignored. |
| `UserPromptSubmit` | `hook-prompt` | Fulltext-searches the prompt, writes `~/.grok/neo4j-hits.md`. Stdout ignored. Fail-open (exit 0). |
| `Stop` (`reason=end_turn` only) | `hook-stop` | Appends `~/.grok/neo4j-inbox.jsonl`. Does **not** write Facts. |

The **home rule** is what actually wires the model: on knowledge questions, read
fresh hits, then CLI-search the real query. Cite Fact names. Hits are leads.

The **skill** (`/neo4j-memory`) is the write/organize path.

```bash
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py search "QUERY"
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py write \
  --assistant Grok --name "Title" --summary "Durable sentence." --point "detail"
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py organize --assistant Grok
```

`--assistant` is the mind tag (`Grok`, `Weft`, …). Search is unfiltered (all
minds). Write MERGE on `Fact.name` and **refuses** if that name is already owned
by a different assistant. `organize` adds `RELATED_TO` only among that mind's
Facts that share ≥2 HAS_WORD tokens. It does not rebuild the rest of the graph.

## Rules you must keep

- Never commit `~/.grok/.env.neo4j` or print the password.
- Never store API keys, passwords, or tokens as Facts.
- Never auto-ingest `neo4j-inbox.jsonl`. Only `write` when the user asks to save.
- Never overwrite Nova/Weft/other Facts. Prefix new names if collision is likely.
- Dated observations (as-of) beat present-tense state that will rot.
- Hook timeouts: 8s session/prompt, 5s stop. Fail-open. Do not block `Stop`.
- Do not depend on `ai_memory` being pip-installed; this CLI talks to Bolt directly.

## Not in this folder

- Claude Code Stop-hook distillation (`claude/`).
- The `ai_memory` Python package / `ai-memory` CLI (repo root). This Grok wiring
  is a thin Bolt client so a TUI session can search without `pip install -e .`.
- FAISS. Hook search is fulltext only.
