---
name: neo4j-memory
description: >
  Search and write the live Neo4j knowledge graph (markdown+Neo4j memory system).
  Use when recalling prior facts, searching memory, semantic / embedding / vector
  search, writing a durable Fact, organizing Grok-owned graph edges, or the user
  says neo4j, knowledge graph, remember this, save to memory, /neo4j-memory.
---

# Neo4j memory

Live graph at `bolt://localhost:7687`. Creds: `~/.grok/.env.neo4j`.
CLI: `python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py`

**Memory is sacred.** The live graph is production, not a scratchpad. Search **all** minds. Write only durable `assistant=Grok` Facts. Never overwrite Nova, Weft, or untagged names. Never leave probe Facts — `DETACH DELETE` and confirm the token is gone. Do not run `organize` or `--force-assistant` as a test side-effect.

## Search (always hybrid)

```bash
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py search "QUERY"
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py search "QUERY" --max 8
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py search "QUERY" --assistant Grok --space shared --trust high
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py stats
```

Always use the default (hybrid): Lucene `fact_content` (name/summary/content) plus `fact_key_points` (`key_points` lists), fused with Ollama `nomic-embed-text` (768-d cosine) against `factEmbeddingIndex`. The vector leg runs as a Cypher 25 `SEARCH ... WHERE ... LIMIT $k` clause; `--assistant`/`--space`/`--trust` become equality filters evaluated inside the index itself (never `status`), which needs the phase-3 migrated index. Fulltext and embed+KNN run **in parallel** (daemon threads, one shared deadline). The prompt hook waits at most 5s then writes `neo4j-hits.md` before Bolt close, so a hung vector leg cannot skip the 8s UserPromptSubmit kill with a stale hits file. Do not pass `--mode fulltext` or `--mode vector` to save time. Those flags are debug-only. Ollama down or a vector-index error (e.g. an un-migrated index) → fulltext only; this client has no over-fetch fallback for a failed vector leg. Ranking fuses the whole fulltext+vector pool by RRF, then drops a superseded/removed hit when an active hit on the same topic is present (same name once a trailing date suffix is stripped, or linked by `SUPERSEDES`), puts any remaining inactive hits after the active ones, then moves an active hit whose name exactly matches the query to the top. Hits tagged `(lex)` (fulltext and/or key-points leg), `(vec)`, or `(lex+vec)` — the same neutral lexical tag the library emits. Vector-only (Lucene empty): drop neighbors below cosine **0.80** (measured 2026-08-31: true match ~0.88, KeePassXC noise ~0.75). RRF when both legs hit is unfiltered.

If `~/.grok/neo4j-hits.md` is newer than ~20s, read it first (UserPromptSubmit hook already searched the prompt); still run CLI search when the query drifted from that prompt.

Treat hits as leads. Re-derive volatile trading/state claims from live sources.

## Write

```bash
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py write \
  --name "Short unique title" \
  --summary "One durable sentence." \
  --point "detail" --point "detail"
```

Write also embeds via Ollama: `embedding`, `embedding_model`, `embedding_dim`, `embedding_text_sha`, and `boilerplate_version` are set together in one compare-and-set statement, built from the canonical text (name, summary, key points, content) with corpus boilerplate stripped per the live `RetrievalConfig`. `--no-embed` skips embedding entirely. If `RetrievalConfig` is missing, the write stores text only and reports `(no embedding: no RetrievalConfig)`; a concurrent edit to the same Fact's text fields reports `(no embedding: cas_skipped — concurrent edit)`; a Fact whose prepared text strips to empty (e.g. entirely corpus boilerplate) reports `(no embedding: empty text)`; anything else (Ollama down) reports `(no embedding: ollama down)`. Write also rebuilds the Fact's Word index from the same canonical text (≤24 tokens, tokenized the same way as the library's nightly rebuild) instead of from name words, then runs the on-write `RELATED_TO` edge rule for that Fact if a rule is published (skipped, no error, if not — `organize` catches it up later). Write/organize refuse `--assistant` other than Grok unless `--force-assistant`. Untagged Facts (`assistant` NULL) are inherited memory — refuse overwrite unless `--force-assistant`. Nova/Weft names stay blocked even with that flag. Only durable facts: decisions, recipes, measured invariants. No secrets, no API keys, no passwords. Present-tense state that will rot → timestamp the observation in the summary.

## Shared write (dated, no clobber)

Common store for new knowledge every agent may **read**. Writes go to `space=shared`. Nova/Weft/untagged library Facts stay inviolable.

- **Add** a topic: new Fact named `Shared — {title} — YYYY-MM-DD`, `status=active`, `valid_from` set. In-place overwrite of summary is refused.
- **Supersede:** `--supersede` creates a dated successor, sets the old Fact `status=superseded`, adds `(new)-[:SUPERSEDES]->(old)`. Old text stays searchable (`[superseded]` in the teaser).
- **Append:** `--append --point` adds a `[YYYY-MM-DD] …` key_point to the **active** head. Does not change summary.
- **Remove:** `remove --name … --reason …` is a **tombstone** (`status=removed`). Never `DETACH DELETE` shared or library Facts. Probes only: `DETACH DELETE` plus a search that the token is gone.

```bash
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py write \
  --space shared --topic slug --name "Title" --summary "One sentence." --point "detail"

python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py write \
  --space shared --topic slug --supersede --name "Title" --summary "Replacement." --point "what changed"

python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py write \
  --space shared --topic slug --append --point "additive note"

python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py history --topic slug
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py remove \
  --name "Shared — Title — YYYY-MM-DD" --reason "why"
```

This CLI still tags `assistant=Grok` unless `--force-assistant`. Other minds use their own client with the same `--space shared` rules; they create **new dated Facts they own**, they do not MERGE another mind's name. Search all minds as usual.

## Embeddings

```bash
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py embed --dry-run
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py embed
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py embed --limit 50
```

`embed` fills Facts missing `Fact.embedding` **or** carrying a foreign one (`embedding_text_sha IS NULL` — written outside this client) — indexing only, never a content rewrite. Requires the live `RetrievalConfig`; without it, aborts with `embed aborted: no RetrievalConfig — run ai-memory embed --all from the library first`. Needs local Ollama + `nomic-embed-text`; probes it first and aborts with `embed aborted: ollama nomic-embed-text unavailable` if unreachable. Keep dim 768 to match the live vector index. Aborts after 3 consecutive per-Fact failures.

## Organize

```bash
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py organize
```

Runs the library's on-write edge rule (`maintain_edges_for`, a verbatim port) over
Grok-owned Facts: each Fact keeps its top-5 `RELATED_TO` picks by a blend of
TF-IDF cosine (over the tokenized canonical text) and embedding cosine, not a
shared-word count. Requires a published edge rule — run the library's `ai-memory
nightly` first; `organize` refuses with an error if `RetrievalConfig.rule_version`
is unset. Does not rewrite other minds' edges.

## Inbox

Stop hook appends `~/.grok/neo4j-inbox.jsonl`. Do **not** auto-ingest. If the user asks to save this session, pick durable claims and `write` them. SessionEnd curate file: `curratemem`.
