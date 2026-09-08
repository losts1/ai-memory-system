# TODO

Open items carried past the 1.4.0 release. The numbers match the `review #N` markers in
the code and tests, which is where the reasoning for each verdict lives.

## Needs a decision

- [ ] **review #1 headline — skip the nightly publish when boilerplate is unchanged.** The
  spec (§7.2, §7.5) mandates a full re-embed and edge rebuild every night, so this is a
  spec amendment, not a bug. Decide whether "equal grams + same model/dim + same
  boilerplate version" should short-circuit `publish_retrieval_config`, and what
  `rebuild_edges` should do when embeddings did not change.
- [ ] **review #5 — `--force` on `neo4j_migrate_vector_filters.py --migrate`.** Declined
  for now (preflight + dry-run + explicit mode already gate it, and the already-migrated
  no-op removed the main footgun). Revisit if a second operator ever runs it.

## Code follow-ups (small, no decision needed)

- [ ] **review #6 — fold the `HAS_WORD` words block into `learn._sync_fact_tx`'s first
  statement** and drop the `return False` after a partial Cypher run. Not a live defect
  (the driver refuses to commit after a failed `tx.run`), but the two-call shape is
  fragile and `neo4j_sync.py` already does it in one statement. `tests/test_learn.py`
  pins two calls.
- [ ] **review #6 — `neo4j_learn_sync.py` writes text then vector in two transactions**
  (`sync_facts(embed_fn=None)` then `embed_fact`). Spec §4 wants one statement; the
  window is bounded (CAS skips, nightly re-embed closes it). Recorded as a ruling in
  `tests/test_learn_sync_embed.py`.
- [ ] **review #7 nits:** `as_supersedes_multimap` legacy shim is exercised only by older
  tests (`ai_memory/retrieval.py`); one `supersedes` read in `ai_memory/duplicates.py`
  bypasses it; a dead `effective_mode` local in `MemoryClient.search` after the new
  `graph`/`mode` guard; a distinct `(kp)` tag for key-points-only hits would need
  `merge_rrf`/`fuse_rrf` changes in both clients.
- [ ] **review #3 — `harness.evaluate` pool-empty vs judge-failure** are both "unjudged";
  the JSON could say which. Cosmetic.
- [ ] **review #9 — optional in-skill self-check** ("if organize still says shared-word,
  the copy is stale"); `grok/README.md` step 4's `diff -rq` covers it today.
