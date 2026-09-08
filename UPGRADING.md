# Upgrading to 1.4.0

1.4.0 is the retrieval redesign. It changes what `search()` returns, adds provenance to
every vector, replaces the `RELATED_TO` edge layer, and ships six new CLI subcommands.
**A `git pull` and `pip install -e .` is not enough** — an existing graph needs an ordered
migration, and code that reads hit dictionaries needs changes.

Three documents, three jobs:

| | |
|---|---|
| **This file** | what breaks, and the migration in order, with the gate for each step |
| [docs/UPDATING.md](docs/UPDATING.md) | the mechanics of any update: fetch, venv, re-copy scripts, seed, restart, verify, roll back |
| [MIGRATION.md](MIGRATION.md) | the per-version detail behind each step here |

Read this file, then work through `docs/UPDATING.md` with step 5 of that guide replaced by
the ordered table below.

---

## What breaks

**Search returns different things.** The default path is now hybrid — the vector leg plus
two fulltext legs (`fact_content`, `fact_key_points`), fused by RRF — regardless of
`graph=`. Rankings and scores are not comparable to 1.3.3's cosine scores.

```python
client.search(q)                 # 1.3.3: vector only     -> 1.4.0: hybrid
client.search(q, mode="vector")  # the old path, plus the supersede sink and 0.80 floor
```

**Hit dictionary keys changed.** `content` and `summary` are gone; `teaser`, `via`,
`vec_score`, `space` and `status` are new. Anything parsing `hit["content"]` or
`hit["summary"]` breaks. On the CLI, `--fields summary` and `--fields content` now select
nothing — use `--fields teaser`; `--metadata-only` reads `teaser` too.

**`graph=True` no longer returns neighbours.** Hybrid already runs the lexical leg, so
`graph=True` is an alias for `mode="hybrid"`, and `search_graph()` returns the same hit
shape as `search_vector()` — no `related_facts`, `relationships` or `related_count`.
Call `client.traverse(name, depth=1)` for neighbours. Passing `graph=True` together with
an explicit `mode="vector"` or `"fulltext"` now raises `ValueError`.

**A missing vector index no longer raises.** 1.3.2 raised `Neo4jIndexNotFoundError`; the
vector leg now logs one warning and returns `[]`, so `search()` silently degrades to the
fulltext legs. Check `ai-memory stats` or `validate_schema()` instead of relying on the
exception. The library's default index name is `fact_embeddings`, the grok client's is
`factEmbeddingIndex` — set `NEO4J_VECTOR_INDEX` in `.env.neo4j` so both agree with the
live graph.

**New keyword arguments:** `space=` on `search`/`search_vector`/`search_graph`, and
`mode=` ("hybrid" | "fulltext" | "vector") on `search` and the new `search_hybrid()`.

**New CLI:** `embed`, `stats`, `edges`, `nightly`, `duplicates`, `supersede`, `eval`,
`eval-edges`. `nightly` and `edges` need the `edges` extra (numpy):
`pip install -e '.[edges]'`.

**Writers embed now.** `MemoryClient.write()` and `learn()` embed when Ollama and the
config node are reachable; they previously never did. The `EMBEDDING_MODEL` override
`neo4j_learn_sync.py` used to honour is gone — every writer embeds with
`ai_memory.embed.EMBED_MODEL` (`nomic-embed-text`, 768-d), because the index is 768-d and
provenance records the model.

---

## Before you start

1. **Back up the graph.** `docs/CRON_JOBS.md` has the Docker recipe.
2. **Export the legacy edges.** The first `ai-memory nightly` deletes every `RELATED_TO`
   with a null `rule_version`, and this export is the only way back:

   ```cypher
   MATCH (a)-[r:RELATED_TO]->(b) RETURN a.name, b.name, properties(r)
   ```
3. **Stop scheduled writers** until the migration finishes, so a cron sync does not run
   old code against a half-migrated schema.
4. **Seed the schema** — `python scripts/neo4j_seed.py` then
   `python scripts/verify_schema.py --strict`. It is idempotent, and it creates the
   `fact_key_points` fulltext index the hybrid path needs.

Do not point a 1.3.3 writer at a graph that has crossed step 3 below. 1.3.3 has no
ownership guard and still MERGEs unversioned `RELATED_TO` edges, which the next
`ai-memory nightly` deletes. 1.3.3 *readers* are fine — different ranking, no errors.

---

## The migration, in order

| # | Step | Gate before moving on |
|---|---|---|
| 1 | **Canonical embeddings.** `ai-memory eval --golden G --rankers legacy,hybrid_fallback --json before.json`, then `ai-memory embed --all --keep-prev`, then the same eval to `after.json` | `per_ranker.hybrid_fallback.ndcg5` and `.recall5` did not drop. Pass → `ai-memory embed --drop-prev`. Fail → `ai-memory embed --rollback`. |
| 2 | **In-index filters.** `python scripts/neo4j_migrate_vector_filters.py --preflight`, read the report, then `--migrate` in a quiet window | preflight reports no blockers; searches still return hits afterwards |
| 3 | **Edge layer.** Legacy edges exported, then `ai-memory nightly` | `ai-memory stats` shows `edges_stale_rule` 0 |
| 4 | **Duplicates (optional).** `ai-memory duplicates --markdown report.md`, decide, `ai-memory supersede --from-file decisions.json --apply` | your call; the report never merges anything itself |

Step 1 takes about 10 s per 1,500 Facts on local Ollama. It re-embeds every Fact from the
canonical text and publishes a new `RetrievalConfig` version; vectors written by tooling
outside this repo have no `embedding_text_sha` and show as `foreign` in `ai-memory stats`
until it runs.

Two traps in step 1:

- **The golden set is yours.** `G` is a JSON list of
  `{"query": "...", "expect": ["fact-name", ...], "filters": {...}}` that lives outside the
  repo — pass `--golden` or set `AI_MEMORY_GOLDEN`. `ai-memory eval` with neither exits
  `error: no golden file`. Without a golden set there is no gate: run the backfill, confirm
  `ai-memory stats` reports no `foreign`/`stale` vectors, spot-check searches, then
  `--drop-prev`. `eval` also needs an OpenAI-compatible judge endpoint — `--judge-url`
  defaults to `http://localhost:8080/v1/chat/completions`, or set `AI_MEMORY_JUDGE_URL`.
- **Keep `--rankers legacy,hybrid_fallback` until step 2 is done.** The default set
  includes `hybrid_search`, which raises `RuntimeError` on an index that has not had the
  step-2 migration; the harness does not catch it, so the run aborts and no JSON is
  written.

`--drop-prev` is the point of no return for vectors: it removes `embedding_prev`, and with
it the only rollback that is not a graph restore.

Step 2 exists because the vector leg used to filter `assistant`/`space`/`provenance_trust`
by over-fetching a pool and post-filtering in Cypher. Neo4j 2026.04 can filter inside the
index when the index declares those properties — but a vector index's property list is
fixed at `CREATE`, so it must be rebuilt once.

---

## Verify

```bash
pytest tests/ -q                  # offline suite, no Neo4j needed
ai-memory stats                   # foreign/stale vectors 0; edges_stale_rule 0
ai-memory search "a term you know is in the graph"
```

`ai-memory stats` is what catches a half-done migration: `foreign`/`stale` vectors mean
step 1 did not finish, `edges_stale_rule > 0` means step 3 did not. Both are fixed by
re-running that step; both are idempotent.

If you run the grok client, redeploy the whole skill directory — `SKILL.md` changed
alongside the scripts and a two-file copy leaves the live skill text lagging. `grok/README.md`
has the steps and the `diff -rq` check.

## Rolling back

Restore the vectors **first, while 1.4.0 is still checked out**: `embed` does not exist in
the 1.3.3 CLI, and an editable install follows the checkout, so once you check out the old
tree `ai-memory embed --rollback` is an argparse "invalid choice" and the new vectors stay
live.

```bash
ai-memory embed --rollback        # only while embedding_prev survives, i.e. before --drop-prev
git checkout v1.3.3
pip install -e .
```

Schema additions are harmless to 1.3.3 and can stay. The edge cutover is the one thing it
cannot undo — re-create the legacy `RELATED_TO` edges from the export above if the old
traversal behaviour matters to you.
