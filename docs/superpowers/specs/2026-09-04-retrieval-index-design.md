# Retrieval index redesign — design spec

**Date:** 2026-09-04
**Status:** design approved section by section; spec reviewed externally (Grok 4.6, 8 findings, all applied below) — awaiting owner review
**Scope:** the Neo4j Fact graph's retrieval layer in `ai_memory` and the grok Bolt client, plus the RELATED_TO edge layer
**Goal order (owner's):** recall quality first, then the Word index / RELATED_TO layer
**Reviews:** two external design reviews (Grok 4.6, 2026-09-04) and one corrected simulation round; every change they forced is marked *(review)*

## 1. Summary

Scoped semantic search on the live graph returns a subset of what it is asked for because filters run after the vector index's top-k. Vectors are not comparable across writers. Edges are built from name-only tokens with a cutoff that produces no edges on small graphs and template cliques on this one. The query timeout has never applied.

This spec replaces the retrieval path with one contract implemented twice (library and grok client), rebuilds the vector index with filter properties so filtering happens in-index, re-embeds every Fact from one canonical text with provenance recorded on the node, and replaces the edge rule with a length-normalised two-signal score maintained on write. An offline judge and a golden set gate every phase.

Approach: **A** (native filtered vector search via Cypher 25 `SEARCH` plus a shared ranking contract) with **B** (client-side over-fetch) as the library's automatic fallback for servers without `SEARCH` or an un-migrated index. **C** (sidecar index) rejected.

Non-goals: indexing ConversationTurn, Meme, Event, Session or Decision nodes; automatic merging of duplicate Facts; a neighbour-expansion retrieval leg (untested, see §7.7); stemming.

## 2. Measured context (read-only, 2026-09-04)

- Neo4j 2026.04.0 Community; Cypher 5 is the server default, Cypher 25 available per statement. Ollama `nomic-embed-text` (768-d) local.
- 1,502 Facts: Nova 1,261, untagged 165, Grok 65, Weft 11. 1,501 have `embedding`. 349 lack `summary`; 70 have `content`; 1,421 have `key_points`.
- Vector index `factEmbeddingIndex` (768-d cosine) was created without filter properties; `SEARCH … WHERE f.assistant = $a` fails with GQL 22ND3. Fulltext `fact_content` (name, content, summary) and `fact_key_points` (key_points) exist; the seed script creates only the former.
- Same query, Grok-scoped and Weft-scoped: 1 result each of 5 requested on the current path.
- Edges: 5,102 RELATED_TO; 25% of Facts isolated, 88% of Grok's; degree max 93; 80% of edges rest on exactly two shared words; tokenizer leaves `[learner` and `30]` as Words (173 edges from that cron suffix alone).
- Each writer embeds a different text (sync: name + content[:500]; learn-sync: key points only; grok: name+summary+content+points; library: nothing; Nova's private tooling: unknown). No Fact records model or text used. Re-embedding from one canonical text moves 769 of 1,501 vectors by more than 0.2 cosine.
- Boilerplate: 108 token 4-grams occur in ≥16 Facts (learner-template phrases); 1,497 Facts contain at least one.
- Duplicates (trailing-suffix rule): 58 name groups, 118 Facts, 62 pairs; 43 further pairs at cosine ≥ 0.95.

## 3. Retrieval contract

Implemented by `ai_memory.search.search_hybrid` and by the grok client; both return the same hit dict; a contract test in this repo asserts they agree on fixtures (§6).

**Inputs.** `query`, `k` (default 5), optional `assistant`, `space`, `trust`; `mode` ∈ {hybrid (default), fulltext, vector}.

**Vector leg.** Every statement is prefixed `CYPHER 25` *(review)*:

```
CYPHER 25
MATCH (f:Fact)
SEARCH f IN (VECTOR INDEX $index FOR $vec WHERE <filters> LIMIT $pool) SCORE AS s
RETURN f.name AS name, …, s
```

`<filters>` is built only from the inputs that are set, joined by `AND`, equality only; `status` is never filtered in-index *(review: SEARCH WHERE accepts AND-joined predicates only; `IS NULL`/`OR` shapes cannot be ported; a status filter would exclude the 1,453 Facts with no status)*. `pool = max(4k, 16)`, applied after in-index filtering, so it only widens fusion.

**Lexical leg.** `fact_content` and `fact_key_points` fulltext, Lucene-escaped, `AND`/`OR`/`NOT` lower-cased, same filters as a post-`WHERE` (fulltext has no top-k cliff). If `fact_key_points` is absent the leg degrades to `fact_content` with one warning *(review)*.

**Fusion and ranking, in order.**
1. Reciprocal rank fusion, k = 60, over the legs present.
2. Superseded and removed Facts sink below all active ones; a superseded or removed hit is collapsed when an active hit on the same topic is present. **Same topic** means: connected by a `SUPERSEDES` chain, or sharing a name after the trailing time/date suffix of §7.6 is stripped. The predicate lives in `retrieval.py` and is copied verbatim into the grok client, whose current `Fact.topic`-based collapse is a no-op for library Facts *(review)*.
3. Exact case-insensitive name match moves to the top **among active hits only** *(review: boost-after-sink resurrected superseded Facts)*.
4. Ties on fused score break by name only, as the grok client's `merge_rrf` already does. No recency effect anywhere in ranking *(review: "within one RRF step" was ill-defined; `updated_at` is sparse and mixed; a second review found the `updated_at` tie-break contradicted §6)*.
5. Vector-only results (lexical leg empty) drop below cosine 0.80 (measured 2026-08-31).

**Fallback (B).** On GQL 22ND3 (index lacks filter properties) the library latches a process-wide fallback flag and re-runs the same call on `db.index.vector.queryNodes($index, $pool2, $vec)` with `pool2 = min(50k, 2000)`, filters after `YIELD`, and returns the first `pool` survivors — the same width the `SEARCH` path feeds to fusion, so the two paths differ only in how candidates are found *(review)* — logging one warning that names the migration script. `pool2`'s 2,000 cap is a documented ceiling: past roughly 20,000 Facts a sub-1% mind can fall short of `k`, which is another reason phase 3 is not optional. Latch **only** on 22ND3 or an explicit unsupported-syntax error from a pre-2026.01 server; never on populating, index-not-found, timeouts or other errors *(review)*. An index-not-found or populating error makes **that call** lexical-only with a warning and no latch, so the phase-3 window degrades instead of raising *(review)*. The flag re-probes after 10 minutes and whenever `NEO4J_VECTOR_INDEX` changes *(review lead: long-lived hooks must recover after migration)*. The grok client has no fallback; on 22ND3 it reports one line and continues fulltext-only.

**Timeout.** All queries run as `neo4j.Query(text, timeout=get_query_timeout())`; passing `timeout=` to `session.run` never applied (it became a Cypher parameter).

**Output per hit.** `name, teaser, key_points, assistant, status, space, score, via` (leg tags).

## 4. Canonical embedding text and the vector index

**Prepared text.** `ai_memory.embed.fact_embed_text(name, summary, key_points, content, boilerplate)` produces name, summary, key points as dash lines, content; whitespace-normalised; capped at 2,000 characters; then the supplied corpus boilerplate grams are removed (§7.2). `boilerplate` is read from the **retrieval-config singleton**, a `(:RetrievalConfig {version})` node holding the boilerplate gram set, the z-score baselines and the edge floor for the current rule version; both the library and the grok client read it over Bolt before embedding *(review: a verbatim copy of the function cannot see nightly corpus state)*. A writer that cannot read the singleton stores the text and skips the embedding; the nightly run supplies it. IDF lives on `Word.idf`. Summary precedes key points because 349 Facts lack a summary. The grok client carries a verbatim copy; a test asserts byte-equality on shared fixtures.

**Writers.** All five in-repo writers use it: `scripts/neo4j_sync.py`, `scripts/rlm/neo4j_learn_sync.py`, grok `_fact_text`, `MemoryClient.write()`, `MemoryClient.learn()`. Nova's private tooling cannot be changed; the scheduled backfill makes its vectors canonical within one interval.

**Vector provenance on the node**, written in the same statement as `embedding`: `embedding_model`, `embedding_dim`, `embedding_text_sha` (16 hex of the prepared text), `boilerplate_version` (the singleton version the text was prepared with). Drift checks: stale (sha ≠ recomputed), foreign (embedding without sha — Nova's 1,261 today), wrong model. `ai-memory stats` reports the three counts.

**Compare-and-set** *(review)*: the embed write carries a `WHERE` on the exact text fields it read (name, summary, key_points, content); a Fact edited between read and write is skipped and repaired on the next pass. Writers that set text and vector separately are changed to one statement where the write is theirs; this explicitly includes `MemoryClient.write()`, whose text MERGE and embedding become a single statement.

**Backfill.** `ai-memory embed --all` re-embeds every Fact via local Ollama (measured: 8–11 s for the whole graph). The nightly run re-embeds everything rather than trusting sha-skip; sha-skip serves incremental writes only. The first full run keeps the previous vector in `embedding_prev` until the phase-2 gate passes.

**Index rebuild** *(review: the name-preserving v2-then-swap sequence is impossible — Neo4j rejects an index equivalent to an existing one under another name, and there is no rename)*:
1. Pre-flight: create `factEmbeddingIndex_v2 … WITH [f.assistant, f.space, f.status, f.provenance_trust]`; measure population time; answer the open checks (§10). **The pre-flight decides the final `WITH` list**: a property is declared only if §10.3 shows nodes lacking it are still indexed, or after a sentinel value has been written to every Fact lacking it (`space`, `status` and `provenance_trust` are present on 49, 49 and 1 of 1,502 Facts) *(review: recreating with the full list while §10.3 is open could silently drop most of the graph from the index)*. Drop v2.
2. Scheduled window: drop `factEmbeddingIndex`; create it with the decided `WITH` list; wait until `SHOW INDEXES` reports online at 100%. Nova's `queryNodes` calls fail hard during the window, whose length is the pre-flight's measured population time (§10.4), so the window is scheduled and announced; library callers degrade to lexical-only for the duration (§3).
3. Gate: indexed-node count equals the count of Facts with `embedding`; a `SEARCH … WHERE` probe per declared property returns rows; the ship gate passes on both unscoped and scoped golden queries *(review: "scoped queries return k" alone cannot detect a membership collapse, because the fallback already returns k on this graph)*.
4. Seed script creates the index with the decided filter properties on fresh installs; `verify_schema` and `_config.EXPECTED_*` learn to check them (subject to §10.2).

The 165 untagged Facts stay untagged: excluded from scoped searches, visible to unscoped ones.

## 5. Library implementation

| unit | responsibility |
|---|---|
| `ai_memory/embed.py` (new) | `fact_embed_text`, boilerplate detector (§7.2), sha helper, `embed_fact(session, …)` writing embedding + provenance in one CAS statement |
| `ai_memory/retrieval.py` (new) | pure functions: `pool_size`, `fuse_rrf`, `rank_adjust`, the `SEARCH` and fallback Cypher builders; no I/O |
| `ai_memory/search.py` (changed) | `search_vector`'s **body is replaced** by the `SEARCH`-plus-fallback path (signature and exception semantics kept, `space` added), so every existing caller — `hybrid_memory_search.py`, `ai-memory search`, `MemoryClient.search` — leaves the top-k cliff without code changes *(review)*; `search_graph` gains `space` and the `fact_key_points` leg; both run on `neo4j.Query`; new `search_hybrid` implements §3 fusion; `MemoryClient.search` delegates and gains `space=None`, `mode="hybrid"`. `graph=True` changes meaning from "append `related_facts`" to "lexical leg on", a **breaking change recorded in MIGRATION.md** |
| `scripts/hybrid_memory_search.py`, `scripts/cli.py` (changed) | pass `space` and `mode` through; call `search_hybrid` |
| `ai_memory/wordindex.py` (new) | tokenizer, edge rule, on-write maintenance (§7) |
| `scripts/neo4j_migrate_vector_filters.py` (new) | pre-flight, window steps, verification prints, `--dry-run` |
| `scripts/neo4j_seed.py`, `scripts/verify_schema.py`, `_config.py` | create and assert `fact_key_points` and the vector index filter properties; no new edge or node types |

Tests (mocked): Cypher text for both builders including the `CYPHER 25` prefix; fallback trigger on a mocked 22ND3 `ClientError` asserting the second query, the single warning and non-latching on other errors; fusion and rank rules on hand-built lists; `fact_embed_text` byte-equality against the grok client; `embed_fact` writes four properties in one CAS statement; `_sync_fact_tx` no longer swallows `TransientError`.

## 6. Grok client port and the contract test

`neo4j_memory.py` stays a single-file Bolt tool. Changes: the `CYPHER 25` `SEARCH` shape with set-only filters and no status predicate; `merge_rrf` keeps exact-tie-then-name; `_rank_hits` = sink, sibling collapse, then active-only exact-name boost; `_fact_text` = verbatim `fact_embed_text` including boilerplate removal; writes set the provenance properties in the same CAS statement as `embedding`; `fact_key_points` keeps swallow-and-degrade; no fallback path.

`tests/test_retrieval_contract.py` imports both implementations and asserts identical prepared-text bytes, identical vector-leg Cypher modulo parameter names, identical fused-and-ranked order on shared hit lists (including the superseded-name and exact-tie cases), and identical hit dict keys.

Rollout of the client is unchanged: edit in repo, test, commit, copy to `~/.grok`, live smoke.

## 7. Edge layer (RELATED_TO)

Purpose: edges drive `traverse`, `trace_parameter`, the `related_facts` column and grok `organize`. Fusion does not use them.

### 7.1 Tokenizer
Alphanumeric runs only (fixes `[learner`, `30]`, `v2_fact_0`); stopwords limited to function words, timezones, months and mind names — domain frequency is handled by IDF, not a list *(review: the inherited list stripped `selection`, `market`, `making`, `trading`)*; digits-only tokens dropped; length ≥ 3 except an allow-list; name tokens always kept; cap 24 per Fact by frequency; source text = the prepared text of §4.

### 7.2 Boilerplate
Token 4-grams present in ≥ max(10, 1% of N) Facts, recomputed at each nightly rebuild and stored with `boilerplate_version`. Only **runs of two or more consecutive** template grams are removed *(review: an absolute df=10 threshold and single-gram stripping deleted "probability of informed trading"; the run rule preserves it in all 20 Facts)*. The version is part of `embedding_text_sha`; a gram is never promoted mid-day.

### 7.3 Pair score
Two length-normalised similarities: TF-IDF cosine over prepared tokens (T) and embedding cosine over prepared text (C). Each is z-scored against its own random-pair distribution for the corpus, then `score = (zT + zC) / 2` *(review: raw 0.5/0.5 was 78% embedding because T's random median is 0.0 and C's is ~0.6)*. Each Fact picks its top 5 above a floor; the floor is the score's own 99th percentile over random pairs, re-derived at each rebuild *(review: a hardcoded cosine median was circular)*. Edges are undirected; an edge exists while either endpoint picks it. Edge properties: `weight, tfidf, cos, shared_keywords, via, rule_version`.

### 7.4 On-write maintenance
Recompute the written Fact's picks; then re-pick **any live Fact whose current worst pick is beaten by its score to the written Fact**, where a Fact with fewer than five picks has a worst pick of minus infinity, so any Fact under the cap re-picks whenever the new Fact clears the floor for it *(review: "fifth-best" was undefined for the degree-under-5 quarter of the graph)*. The scan is one similarity row against all N Facts, which the written Fact's own picks already compute. Measured on 100 held-out Facts inserted onto a complete graph: this reproduces the full rebuild exactly (Jaccard 1.00, ~4 re-picks per insert); insert-only reaches 0.92. Phase 5 re-measures on the real 25%-isolated snapshot before the rule is trusted.

### 7.5 Nightly rebuild
Order *(review)*: recompute boilerplate and publish the new `RetrievalConfig` version → re-embed everything against it (~10 s) → recompute IDF, baselines, floor and all picks → write edges under the new `rule_version` → one transaction deletes every RELATED_TO edge whose `rule_version` is null or differs from the new one. Live edges carry no `rule_version` today, so the first cutover's delete clause is what retires all 5,102 legacy edges *(review)*. The legacy edge writers are retired in the same change: `learn.link_related_facts` and `_post_sync_tx` are replaced by the §7.3–7.4 rule inside `sync_facts`/`rebuild_graph`, and grok `organize` is re-pointed at the same rule; otherwise the next learn-sync would wipe the new edges and restore the Word-index cliques *(review)*. `_sync_fact_tx` stops swallowing `TransientError` so the driver's retry works.

### 7.6 Duplicates
Same-topic re-learnings are detected by a trailing time/date suffix on the name (`(HH:MM …)`, `— YYYY-MM-DD`) or cosine ≥ 0.95, excluded from a Fact's edge budget, and listed by a report for a supersede pass using the shared-space status mechanism. No automatic merges.

### 7.7 Results on this graph (offline simulation, corrected round)
| rule | edges | isolated | Grok isolated | max degree | judged related | judged direct |
|---|---|---|---|---|---|---|
| current | 5,102 | 25% | 57/65 | 93 | 94% | 57% |
| words top-5 | 4,709 | 2% | 0 | 26 | 96% | 68% |
| vector top-5 (cleaned) | 3,992 | 8% | 12 | 21 | 100% | 77% |
| union | 6,777 | 1% | 0 | 32 | 97% | 68% |
| **z-blend top-5 @ p99 (chosen)** | 4,551 | 2% | 1 | 26 | 99% | 71% |

Judge: relatedness rubric, calibrated at 0.83 on 30 owner-side labels (permissive: all disagreements are judge-1 vs human-0), 196 pooled pairs from 14 sources; differences of a few points are noise. The union is the documented coverage alternative. Dropped after simulation: IDF-sum (edge explosion), mutual kNN (re-isolates 14–22%), a neighbour-expansion retrieval leg (the sibling proxy could not test it; status: untested, not disproven).

The 35 Facts still isolated under the chosen rule are mostly Grok "Shared — ntr" index stubs that are 64% template by token count and keep 2–8 real tokens; that is a content report for the writer, not an edge defect.

## 8. Judge and harness

`ai_memory/eval/judge.py` (committed: 73038ca, e76acdc, 0213dcd). Local Qwen (currently `qwen3.8-27b-q6k`), temperature 0, `think: false`. Two rubrics: `retrieval` (does the Fact answer the query; calibrated on the grade-2 boundary, 25/25 on four live pools) and `edge` (relatedness; calibrated on the related boundary, 0.83). Candidates show name, summary, ≤3 key points, assistant, status; never scores or ranker identity; shuffled by seed. JSON-only replies; an omitted candidate makes the reply malformed (retry once, then unjudged). Cache keyed on sha256(rubric prompt hash, model, query, name, fact text). Metrics: nDCG@5 with the ideal over the whole judged pool, Recall@5 over grade 2, MRR. `passes_ship_gate`: golden and judged splits may not drop on nDCG@5 or Recall@5.

`ai_memory/eval/harness.py` (to build): golden JSON `[{query, filters, expect:[names]}]` from a path in one environment variable (private content, outside the repo); rankers `legacy` (current path, kept callable), `hybrid_fallback` (§3 fusion over the over-fetch path) and `hybrid_search` (§3 fusion over `SEARCH`; runnable offline before phase 3 by filtering then taking top-k over the snapshot's cosine matrix, and live after it) *(review: `hybrid` alone would never put the `SEARCH` path under test)*; exact-hit metrics; pooled judging of the top 10 per ranker; per-rule edge sample of 30 for §7. A golden query the judge leaves unjudged after retry **fails the gate** rather than being skipped *(review)*. CLI: `ai-memory eval --golden PATH --rankers legacy,hybrid`.

## 9. Rollout, operations, labelling

| phase | delivers | gate |
|---|---|---|
| 0 | golden set, harness, baseline for `legacy` | calibration passes on owner labels; baseline recorded |
| 1 | §3 + §5 library path (fallback active until phase 3), timeout fix, `fact_key_points` in seed/verify | `passes_ship_gate` on golden and judged splits, `hybrid_fallback` vs `legacy`, on today's vectors |
| 2 | §4 canonical text, boilerplate, `RetrievalConfig`, provenance, `ai-memory embed`, full backfill with `embedding_prev` | `passes_ship_gate` on both splits, `hybrid_fallback` after re-embed vs before |
| 3 | pre-flight v2, open checks, decided `WITH` list, scheduled drop-and-recreate | §4 step 3: indexed-node count, per-property probe, ship gate on unscoped and scoped queries with `hybrid_search`; Nova observed working |
| 4 | §6 grok port, contract test, deploy | contract test green; live smoke |
| 5 | §7 edge layer, legacy writers retired, on-write, nightly with version cutover | zero RELATED_TO edges without the current `rule_version`; isolation ≤ 3% and max degree ≤ 30 (simulated 2% / 26; baseline 25% / 93); judged edge sample not below the baseline row |
| 6 | duplicate/supersede report | owner review; no automatic merges |

Phases run in order; phase 2's gate compares against phase 1's result, so they are not parallel *(review)*. Phase 3 is the only schema change and the only scheduled downtime.

**Labelling session (~1 h).** 30 queries in the owner's own words: ~10 unscoped, ~10 scoped to a mind or the shared space, 5 answered by a specific Fact, 5 with no good answer. The harness presents the pooled top 10 from `legacy`, `hybrid_fallback` and the offline `hybrid_search` simulation, so `SEARCH`-only hits are labelled too *(review)*; the owner marks expected Facts. Labels are appended, never rewritten. The owner's edge labels replace the reviewer's 30.

**Operations.** Nightly: rebuild (§7.5) and re-embed. `ai-memory stats`: foreign, stale, wrong-model vectors and isolated Facts. Rollback: `embedding_prev` until the phase-2 gate; previous `rule_version` until cutover; the index can be recreated without filter properties in the same-sized window. The judge calibration re-runs whenever the AI server changes model.

**Known risks.** Judge permissiveness (0.83 on reviewer labels); one local Ollama for all embedding (writes land without vectors, nightly catches up); two retrieval code paths in the public package (contract test and harness keep them honest); Nova's window during phase 3.

## 10. Open verifications (each one read-only query on the pre-flight index)
1. Whether `SEARCH … WHERE` on 2026.04 accepts `IS NULL` (docs: `IN` arrived in 2026.06; equality assumed).
2. Whether `SHOW INDEXES` exposes the filter properties so `verify_schema` can assert them.
3. Whether nodes missing a declared filter property are indexed at all — **blocking for §4 step 1**; decides the `WITH` list.
4. Population time for the full index on this host.
5. Confirmation that recreating an equivalent index under another name is rejected (near-certain from the schema-rule descriptor; determines nothing in §4 either way).

## Appendix A — evidence index
- Spec review (Grok 4.6, 2026-09-04): 8 findings, all verified and applied — §3/§6 tie-break contradiction, undefined "same topic", `WITH` list vs §10.3, missing `rule_version` on live edges and un-retired legacy writers, harness never testing `SEARCH`, config state invisible to the grok copy, undefined fifth-best under the cap, `search_vector` callers and `graph=True`.
- Live spike: `SEARCH` on the un-migrated index → 22ND3; post-filter path returned 1/5 for Grok and Weft.
- Judge calibrations: retrieval 25/25 (4 pools); edge 0.83 (30 pairs, reviewer labels).
- Simulation artefacts (scratchpad, not committed): `graph_snapshot.pkl`, `canon_emb.pkl`, `round2_sets.pkl`, `edge_sim2.py`, `round2_judge.log`, `boilerplate_judge.log`.
- Review findings applied: design review 1 (8 findings, 7 verified); edge review (7/7); corrected simulation round.
