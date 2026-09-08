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

The TUI does not import this checkout. `~/.grok/skills/neo4j-memory/` is a
**copy**. A commit in the repo does nothing until you copy again.

**Root cause (review finding):** an older line here said to redeploy by copying
only `neo4j_memory.py` and `test_neo4j_memory.py` — “no other files need to
move.” That was false once `SKILL.md` changed (phases 4–5). Operators and
agents followed it, so live `SKILL.md` stayed on shared-word `organize` /
missing-only `embed` while the script had already moved on. The same skip
of `diff -rq` (step 4) left `~/.grok` stale again after the #7 script
commit: only `.py` changed that time, but nothing was copied at all.

After **any** change under `grok/skills/neo4j-memory/` (scripts or
`SKILL.md`), copy the whole directory and verify:

```bash
cp -r grok/skills/neo4j-memory/. ~/.grok/skills/neo4j-memory/
chmod +x ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py
diff -rq grok/skills/neo4j-memory ~/.grok/skills/neo4j-memory \
  --exclude=__pycache__ --exclude=.pytest_cache && echo in-sync
```

Do not copy only the two `.py` files. Grok reads `SKILL.md` from `~/.grok`.

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

A production graph in this lineage used `factEmbeddingIndex` (VECTOR, 768-d
cosine), `fact_content` (FULLTEXT on name/content/summary) and `fact_key_points`
(FULLTEXT on the `key_points` list). Put those names in `.env.neo4j` if they
differ from the example. `neo4j_seed.py` does not create `fact_key_points`; if
`SHOW INDEXES` lacks it, create it (search degrades to `fact_content` only
without it):

```cypher
CREATE FULLTEXT INDEX fact_key_points IF NOT EXISTS FOR (f:Fact) ON EACH [f.key_points]
```

CLI search is **hybrid** (fulltext + Ollama
`nomic-embed-text` against the vector index, RRF fusion). The vector leg is a
Cypher 25 `SEARCH ... WHERE ... LIMIT $k` clause; `search --assistant/--space/--trust`
become equality filters evaluated inside the index itself (never `status`), which
requires the phase-3 migrated index — the client has no fallback, so on an
un-migrated index the vector leg errors and search degrades to fulltext-only, the
same as Ollama being down. The prompt hook uses the same hybrid path with a
5s shared deadline (3s embed / 4s Bolt / 1.5s connect, daemon workers) so it
can rewrite `neo4j-hits.md` before the 8s UserPromptSubmit kill.

### 3. Reload hooks

Hooks in a **running** session are the ones loaded at start (or last `/hooks`
reload). After copying:

- New session: automatic.
- Current session: `/hooks` → `r`.

`SessionStart` will not retro-fire. `UserPromptSubmit` starts on the next prompt
after reload.

### 4. Verify (required — do not claim success without this)

```bash
# the deployed copy must match this repo — SKILL.md included, it is what Grok reads
diff -rq grok/skills/neo4j-memory ~/.grok/skills/neo4j-memory --exclude=__pycache__ --exclude=.pytest_cache && echo in-sync
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
| `UserPromptSubmit` | `hook-prompt` | Hybrid-searches the prompt (fulltext + vector), writes `~/.grok/neo4j-hits.md`. Stdout ignored. Fail-open (exit 0). |
| `Stop` (`reason=end_turn` only) | `hook-stop` | Appends `~/.grok/neo4j-inbox.jsonl`. Does **not** write Facts. |

The **home rule** is what actually wires the model: on knowledge questions, read
fresh hits, then CLI-search the real query. Cite Fact names. Hits are leads.

The **skill** (`/neo4j-memory`) is the write/organize path.

```bash
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py search "QUERY"
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py search "QUERY" --mode vector
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py embed --dry-run
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py write \
  --assistant Grok --name "Title" --summary "Durable sentence." --point "detail"
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py organize --assistant Grok
```

Search is unfiltered by default (all minds); `--assistant`/`--space`/`--trust` scope
it. Write MERGEs Fact fields, then embeds via Ollama in a separate
compare-and-set statement that sets `embedding` together with
`embedding_model`, `embedding_dim`, `embedding_text_sha`, and
`boilerplate_version` from the canonical text (name, summary, key points,
content; corpus boilerplate stripped per the live `RetrievalConfig`) — if the
config node is missing, the write stores text only. `embed` backfills Facts
missing `Fact.embedding` **or** carrying a foreign one (no
`embedding_text_sha`), and aborts if `RetrievalConfig` is unreachable.
Write/organize default to `assistant=Grok` and
**refuse** `--assistant` other than Grok unless `--force-assistant`. Write MERGE
on `Fact.name` also refuses if that name is already owned by a different
assistant. `organize` runs the library's on-write edge rule
(`wordindex.maintain_edges_for`) over Grok's Facts — each Fact keeps its top-5
`RELATED_TO` picks by a TF-IDF/embedding blend, not a shared-word count — and
requires a published edge rule (run the library's `ai-memory nightly` first).
It does not rebuild the rest of the graph.

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
- FAISS. Vector search uses Neo4j `factEmbeddingIndex` + local Ollama, not FAISS.
