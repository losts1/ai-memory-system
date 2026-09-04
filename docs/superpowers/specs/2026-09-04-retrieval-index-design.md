# Retrieval index redesign — design spec

**Date:** 2026-09-04
**Status:** design approved section by section; awaiting spec review
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
2. Superseded and removed Facts sink below all active ones; superseded siblings of an active hit on the same topic are collapsed *(review)*.
3. Exact case-insensitive name match moves to the top **among active hits only** *(review: boost-after-sink resurrected superseded Facts)*.
4. Ties on fused score break by `updated_at` descending, then name. No other recency effect *(review: "within one RRF step" was ill-defined and `updated_at` is sparse and mixed)*.
5. Vector-only results (lexical leg empty) drop below cosine 0.80 (measured 2026-08-31).

**Fallback (B).** On GQL 22ND3 (index lacks filter properties) the library latches a process-wide fallback flag and re-runs the same call on `db.index.vector.queryNodes($index, $pool2, $vec)` with `pool2 = min(50k, 2000)`, filters after `YIELD`, `LIMIT k`, logging one warning that names the migration script. Latch **only** on 22ND3 or an explicit unsupported-syntax error from a pre-2026.01 server; never on populating, index-not-found, timeouts or other errors *(review)*. The flag re-probes after 10 minutes and whenever `NEO4J_VECTOR_INDEX` changes *(review lead: long-lived hooks must recover after migration)*. The grok client has no fallback; on 22ND3 it reports one line and continues fulltext-only.

**Timeout.** All queries run as `neo4j.Query(text, timeout=get_query_timeout())`; passing `timeout=` to `session.run` never applied (it became a Cypher parameter).

**Output per hit.** `name, teaser, key_points, assistant, status, space, score, via` (leg tags).

## 4. Canonical embedding text and the vector index

**Prepared text.** `ai_memory.embed.fact_embed_text(name, summary, key_points, content)` produces name, summary, key points as dash lines, content; whitespace-normalised; capped at 2,000 characters; then corpus boilerplate is removed (§7.2). Summary precedes key points because 349 Facts lack a summary. The grok client carries a verbatim copy; a test asserts byte-equality on shared fixtures.

**Writers.** All five in-repo writers use it: `scripts/neo4j_sync.py`, `scripts/rlm/neo4j_learn_sync.py`, grok `_fact_text`, `MemoryClient.write()`, `MemoryClient.learn()`. Nova's private tooling cannot be changed; the scheduled backfill makes its vectors canonical within one interval.

**Vector provenance on the node**, written in the same statement as `embedding`: `embedding_model`, `embedding_dim`, `embedding_text_sha` (16 hex of the prepared text), `boilerplate_version`. Drift checks: stale (sha ≠ recomputed), foreign (embedding without sha — Nova's 1,261 today), wrong model. `ai-memory stats` reports the three counts.

**Compare-and-set** *(review)*: the embed write carries a `WHERE` on the exact text fields it read (name, summary, key_points, content); a Fact edited between read and write is skipped and repaired on the next pass. Writers that set text and vector separately are changed to one statement where the write is theirs.

**Backfill.** `ai-memory embed --all` re-embeds every Fact via local Ollama (measured: 8–11 s for the whole graph). The nightly run re-embeds everything rather than trusting sha-skip; sha-skip serves incremental writes only. The first full run keeps the previous vector in `embedding_prev` until the phase-2 gate passes.

**Index rebuild** *(review: the name-preserving v2-then-swap sequence is impossible — Neo4j rejects an index equivalent to an existing one under another name, and there is no rename)*:
1. Pre-flight: create `factEmbeddingIndex_v2 … WITH [f.assistant, f.space, f.status, f.provenance_trust]`; measure population time; answer the open checks (§10); drop it.
2. Scheduled window: drop `factEmbeddingIndex`; create it with the same `WITH` list; wait until `SHOW INDEXES` reports online at 100%. Nova's `queryNodes` calls fail hard during the window (seconds at this size), so the window is scheduled and announced.
3. Seed script creates the index with filter properties on fresh installs; `verify_schema` and `_config.EXPECTED_*` learn to check them (subject to §10).

The 165 untagged Facts stay untagged: excluded from scoped searches, visible to unscoped ones.

## 5. Library implementation

| unit | responsibility |
|---|---|
| `ai_memory/embed.py` (new) | `fact_embed_text`, boilerplate detector (§7.2), sha helper, `embed_fact(session, …)` writing embedding + provenance in one CAS statement |
| `ai_memory/retrieval.py` (new) | pure functions: `pool_size`, `fuse_rrf`, `rank_adjust`, the `SEARCH` and fallback Cypher builders; no I/O |
| `ai_memory/search.py` (changed) | `search_vector`/`search_graph` keep signatures and exception semantics, gain `space`, run on `neo4j.Query`; new `search_hybrid`; `MemoryClient.search` delegates and gains `space=None`, `mode="hybrid"` (`graph=True` becomes an alias for the lexical leg) |
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
Recompute the written Fact's picks; then re-pick **any live Fact whose fifth-best score is beaten by its score to the written Fact** *(review: re-picking only the chosen neighbours misses inbound membership)*. Measured on 100 held-out Facts inserted onto a complete graph: this reproduces the full rebuild exactly (Jaccard 1.00, ~4 re-picks per insert); insert-only reaches 0.92.

### 7.5 Nightly rebuild
Re-embed everything (~10 s), recompute boilerplate, IDF, baselines, floor and all picks; write edges under a new `rule_version`, then delete the previous version's edges in one transaction *(review: batched delete+rebuild exposed a half-built graph)*. `_sync_fact_tx` stops swallowing `TransientError` so the driver's retry works.

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

`ai_memory/eval/harness.py` (to build): golden JSON `[{query, filters, expect:[names]}]` from a path in one environment variable (private content, outside the repo); rankers `legacy` (current path, kept callable) and `hybrid`; exact-hit metrics; pooled judging of the top 10 per ranker; per-rule edge sample of 30 for §7. CLI: `ai-memory eval --golden PATH --rankers legacy,hybrid`.

## 9. Rollout, operations, labelling

| phase | delivers | gate |
|---|---|---|
| 0 | golden set, harness, baseline for `legacy` | calibration passes on owner labels; baseline recorded |
| 1 | §3 + §5 library path (fallback active until phase 3), timeout fix, `fact_key_points` in seed/verify | ship gate vs baseline |
| 2 | §4 canonical text, boilerplate, provenance, `ai-memory embed`, full backfill with `embedding_prev` | golden nDCG@5 and Recall@5 do not drop |
| 3 | pre-flight v2, open checks, scheduled drop-and-recreate | scoped queries return k; Nova observed working |
| 4 | §6 grok port, contract test, deploy | contract test green; live smoke |
| 5 | §7 edge layer, on-write, nightly with version cutover | isolation, hubs and the judged edge sample beat the baseline row |
| 6 | duplicate/supersede report | owner review; no automatic merges |

Phases 1 and 2 run in parallel. Phase 3 is the only schema change and the only scheduled downtime.

**Labelling session (~1 h).** 30 queries in the owner's own words: ~10 unscoped, ~10 scoped to a mind or the shared space, 5 answered by a specific Fact, 5 with no good answer. The harness presents the pooled top 10 from `legacy` and from the offline simulation; the owner marks expected Facts. Labels are appended, never rewritten. The owner's edge labels replace the reviewer's 30.

**Operations.** Nightly: rebuild (§7.5) and re-embed. `ai-memory stats`: foreign, stale, wrong-model vectors and isolated Facts. Rollback: `embedding_prev` until the phase-2 gate; previous `rule_version` until cutover; the index can be recreated without filter properties in the same-sized window. The judge calibration re-runs whenever the AI server changes model.

**Known risks.** Judge permissiveness (0.83 on reviewer labels); one local Ollama for all embedding (writes land without vectors, nightly catches up); two retrieval code paths in the public package (contract test and harness keep them honest); Nova's window during phase 3.

## 10. Open verifications (each one read-only query on the pre-flight index)
1. Whether `SEARCH … WHERE` on 2026.04 accepts `IS NULL` (docs: `IN` arrived in 2026.06; equality assumed).
2. Whether `SHOW INDEXES` exposes the filter properties so `verify_schema` can assert them.
3. Whether nodes missing a declared filter property are indexed at all.
4. Population time for the full index on this host.
5. Confirmation that recreating an equivalent index under another name is rejected (near-certain from the schema-rule descriptor; determines nothing in §4 either way).

## Appendix A — evidence index
- Live spike: `SEARCH` on the un-migrated index → 22ND3; post-filter path returned 1/5 for Grok and Weft.
- Judge calibrations: retrieval 25/25 (4 pools); edge 0.83 (30 pairs, reviewer labels).
- Simulation artefacts (scratchpad, not committed): `graph_snapshot.pkl`, `canon_emb.pkl`, `round2_sets.pkl`, `edge_sim2.py`, `round2_judge.log`, `boilerplate_judge.log`.
- Review findings applied: design review 1 (8 findings, 7 verified); edge review (7/7); corrected simulation round.
