# Updating an existing install from the public repository

This is the *how* of taking a newer version of `losts1/ai-memory-system` into an
install you already run. `MIGRATION.md` is the *what*: the per-version schema and
behaviour changes. Read the MIGRATION.md sections for every version you are crossing
before you start, then follow the steps below in order.

> **Coming from 1.3.3?** The 1.4.0 retrieval redesign (hybrid default, canonical
> embeddings, in-index filters, the edge layer, the duplicate report) changes the search
> API and needs an ordered graph migration. Follow [../UPGRADING.md](../UPGRADING.md)
> instead of this file for that crossing; come back here for routine updates after it.

Two install shapes exist and the steps cover both:

- **Checkout + package** — you cloned the repo, ran `pip install -e .`, and call
  `ai-memory …` from that venv. This is the recommended shape.
- **Copied scripts** — you followed README "Installation" steps 3–4 and run
  `~/.ai-memory/scripts/*.py` directly from `~/.ai-memory/neo4j-venv`.

Nothing here touches your personalised files (`SOUL.md`, `USER.md`, `memory/core/*`,
`.env.neo4j`). Only code, docs, and the graph schema change.

---

## 0. Before you start

1. **Note what you are running now**, so you can roll back to it:

   ```bash
   cd /path/to/ai-memory-system
   git describe --tags --always        # e.g. v1.3.3 or a commit sha
   git status --short                   # must be empty, or commit/branch your local edits first
   ```

   Local edits on top of the public code belong on a branch (`git switch -c local-edits`),
   never as uncommitted changes — `git pull` refuses to overwrite them and you lose the
   thread of what was yours.

2. **Back up the graph.** The Docker recipe from `docs/CRON_JOBS.md` ("Backup + Checkin"):

   ```bash
   BACKUP_DIR="$HOME/.ai-memory/neo4j-backups"; DATE=$(date +%Y-%m-%d)
   mkdir -p "$BACKUP_DIR"
   docker cp neo4j:/data "$BACKUP_DIR/neo4j-data-$DATE"
   ```

   If you are crossing into the edge layer (phase 5), also export the legacy
   `RELATED_TO` edges — the first `ai-memory nightly` deletes them:

   ```cypher
   MATCH (a)-[r:RELATED_TO]->(b) RETURN a.name, b.name, properties(r)
   ```

3. **Stop scheduled writers** for the duration, so a cron sync does not run half-old,
   half-new code against a half-migrated schema:

   ```bash
   systemctl --user stop ai-memory-nightly.timer 2>/dev/null   # if installed
   # and pause the docs/CRON_JOBS.md sync/learner jobs in your scheduler
   ```

## 1. Fetch the new code

```bash
cd /path/to/ai-memory-system
git fetch origin --tags
git switch master
git pull --ff-only origin master          # or: git checkout v1.3.3   (a specific release)
git log --oneline <old-sha>..HEAD         # what you are taking in
```

`--ff-only` fails instead of creating a merge commit if you have local commits on
`master`. In that case rebase your branch on the new `master`
(`git switch local-edits && git rebase master`) and resolve conflicts there.

No checkout yet? `git clone https://github.com/losts1/ai-memory-system.git` and
treat the rest of this guide as a fresh `pip install -e .` install.

## 2. Update the Python environment

Use the **same venv your services and cron jobs already point at**. Installing into
a different venv is the most common way to end up "updated" but still running old code.

```bash
# checkout + package shape
/path/to/venv/bin/pip install -e '.[edges]'      # edges extra = numpy, needed by `ai-memory nightly` / `edges`
/path/to/venv/bin/pip install -e '.[edges,rlm]'  # add rlm if you use the learner (ollama client)

# copied-scripts shape — the package must be installed too, not just requirements.txt
~/.ai-memory/neo4j-venv/bin/pip install -e '/path/to/ai-memory-system[edges]'
```

`requirements.txt` alone is no longer enough for the copied-scripts shape: every script
now imports the `ai_memory` package (`scripts/neo4j_sync.py` line 33, `neo4j_seed.py`
line 28, and so on) and none of them adds the checkout to `sys.path`, so a copied script
run from a venv without the package fails with `ModuleNotFoundError: No module named
'ai_memory'`. The editable install keeps pointing at the checkout, so later `git pull`s
are picked up without reinstalling.

Check the venv now resolves to the new checkout:

```bash
/path/to/venv/bin/pip show ai-memory-system | grep -i 'editable\|version'
/path/to/venv/bin/ai-memory --help | grep -q nightly && echo new-cli   # prints nothing on pre-edge-layer code
```

## 3. Re-copy scripts and docs (copied-scripts shape only)

README steps 3–4, repeated. Copy `scripts/` and `docs/`; do **not** re-copy
`templates/` over a personalised workspace.

```bash
cp -r scripts/* ~/.ai-memory/scripts/
chmod +x ~/.ai-memory/scripts/*.py
cp -r docs ~/.ai-memory/
```

Files that exist only in `~/.ai-memory/scripts/` (your own additions) are left alone.

## 4. Apply the schema

`neo4j_seed.py` is idempotent — constraints and indexes are `IF NOT EXISTS`, the
`RetrievalConfig` node is `MERGE … ON CREATE` — so it is safe to re-run on a populated
graph. It creates whatever constraints, indexes, and config nodes the new version added
(including the `fact_key_points` fulltext index) and leaves existing data alone. The one
constraint it does not create, `word_text_unique`, is created by the first write path
that runs; `verify_schema.py` knows this and does not count it as missing.

```bash
export AI_MEMORY_DIR=~/.ai-memory                 # directory holding .env.neo4j
/path/to/venv/bin/python scripts/neo4j_seed.py
/path/to/venv/bin/python scripts/verify_schema.py --strict   # prints "schema OK" or the missing items
```

If `verify_schema.py` reports a constraint that failed to install (e.g. `fact_name_unique`
on a graph with duplicate names, or `source_name_unique` where a plain index of the same
name already exists), fix that first — MIGRATION.md has the Cypher for each case — and
re-run both commands.

## 5. Run the version-specific migration steps

In the order MIGRATION.md lists them, oldest first. Skip any section for a version you
already run. The one-line index, with the command each step turns into:

| Crossing into | Do |
|---|---|
| v1.2.0 | Dedupe Facts by name, then seed (constraint on `Fact.name`). |
| v1.3.x | Nothing; provenance and trust filters are additive. |
| hybrid retrieval path | Nothing beyond step 4: the seed run created `fact_key_points`. |
| phase 2 (canonical embeddings) | `ai-memory eval --golden G --rankers legacy,hybrid_fallback --json before.json`, then `ai-memory embed --all --keep-prev`, then the same `eval` to `after.json`; pass = `ndcg5` and `recall5` for `hybrid_fallback` did not drop between the two files. On pass `ai-memory embed --drop-prev`, on fail `ai-memory embed --rollback`. |
| phase 3 (in-index filters) | `python scripts/neo4j_migrate_vector_filters.py --preflight`, read the report, then `--migrate` in a quiet window. |
| phase 4 (grok client) | Redeploy the grok skill (step 6) and run its `embed` once. |
| phase 5 (edge layer) | Export legacy edges (step 0), then `ai-memory nightly`; `ai-memory stats` must show `edges_stale_rule` 0. |
| phase 6 (duplicates) | Optional: `ai-memory duplicates --markdown report.md`, decide, `ai-memory supersede --from-file decisions.json --apply`. |

Each row's detail, gates, and failure modes are in the matching MIGRATION.md section.

The phase-2 gate needs a golden set, `G` above: a JSON list of
`{"query": "...", "expect": ["fact-name", ...], "filters": {...}}` entries that lives
outside the repo (it is your private content), passed with `--golden` or the
`AI_MEMORY_GOLDEN` environment variable. `ai-memory eval` with neither exits with
`error: no golden file`. It also needs an OpenAI-compatible judge LLM: `--judge-url`
and `--judge-model` default to a LAN host from the original deployment, so point them at
your own endpoint. The comparison is by hand (or a short script): open the two `--json`
files and read `per_ranker.hybrid_fallback.ndcg5` / `.recall5`. The `--rankers` override
matters: the default set includes `hybrid_search`, which deliberately raises
`RuntimeError` on an index that has not had the phase-3 migration, and the harness does
not catch it — the whole run aborts and no JSON is written. Gate on `hybrid_search`
only for evals run after phase 3. If you have no golden set,
there is no gate: run the backfill,
check `ai-memory stats` shows no `foreign`/`stale` vectors, spot-check a few searches,
and only then `--drop-prev`.

## 6. Redeploy the Grok skill (if you use it)

The grok client is a copy under `~/.grok`, not a package; it does not update with `pip`.
Copy the whole skill directory, not just the two scripts — `SKILL.md` is what Grok
loads to learn the commands, and it changed alongside the scripts in phases 4 and 5.
The two-file copy (`neo4j_memory.py` + `test_neo4j_memory.py` only) is the root
cause of a review finding (live skill text lagging the script). From the repo root:

```bash
cp -r grok/skills/neo4j-memory ~/.grok/skills/
chmod +x ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py
diff -rq grok/skills/neo4j-memory ~/.grok/skills/neo4j-memory --exclude=__pycache__ --exclude=.pytest_cache && echo in-sync
```

Hooks and rules only need re-copying when the CHANGELOG says they changed. In a
running Grok session reload with `/hooks` → `r`; new sessions pick the copy up
automatically. Then verify (grok/README.md step 4):

```bash
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py stats
python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py search "a term you know is in the graph"
```

## 7. Restart schedulers

```bash
systemctl --user daemon-reload
systemctl --user start ai-memory-nightly.timer          # if installed (examples/systemd/)
systemctl --user list-timers ai-memory-nightly.timer
# resume the docs/CRON_JOBS.md jobs you paused in step 0
```

If the service unit's `WorkingDirectory`/`ExecStart` point at a different checkout or
venv than the one you just updated, fix the unit — the timer will otherwise keep
running the old code on schedule while your shell runs the new one.

## 8. Verify

```bash
/path/to/venv/bin/pip install pytest                      # not a package dependency; one-time
/path/to/venv/bin/pytest tests/ -q                        # offline suite; no Neo4j needed
/path/to/venv/bin/ai-memory stats                         # vectors: foreign/stale 0; edges: edges_stale_rule 0
/path/to/venv/bin/ai-memory search "a term you know is in the graph"
journalctl --user -u ai-memory-nightly.service -n 20      # after the next scheduled run
```

`stats` is the one that catches a half-done update: `foreign`/`stale` vectors mean the
phase-2 backfill did not run to completion, `edges_stale_rule > 0` means the phase-5
cutover did not finish. Both are fixed by re-running the command for that row of the
table in step 5; both are idempotent.

## Rolling back

Code rolls back with git; the graph rolls back with the backup or, for vectors, the
`embedding_prev` safety net — which only exists until you run `--drop-prev`.

**Restore the vectors first, while the new code is still checked out.** The `embed`
subcommand does not exist in v1.3.3 (its CLI has no `embed` at all), and an editable
install follows the checkout, so once you `git checkout` the old tree `ai-memory embed
--rollback` is an argparse "invalid choice" and the new vectors stay live.

```bash
cd /path/to/ai-memory-system
ai-memory embed --rollback               # restores previous vectors if embedding_prev is still present
git checkout <old-sha-or-tag>            # from step 0
/path/to/venv/bin/pip install -e .       # re-resolve the editable install to that tree
```

Schema additions (new constraints, indexes, `RetrievalConfig`) are harmless to the
old code and can stay. The one thing the old code cannot undo is the edge cutover:
legacy `RELATED_TO` edges are gone after the first `nightly`, so re-create them from
the export in step 0 if the old traversal behaviour matters to you.

---

## Keeping a private fork current

If you carry private work on top of the public repo (a second remote, a long-lived
branch), pull public changes into it rather than the other way round:

```bash
git fetch origin                                  # public
git switch master && git pull --ff-only origin master
git switch <private-branch> && git merge master   # or: git rebase master, if the branch is unpublished
git push private <private-branch>
```

`git diff --stat master <private-branch>` before the merge shows what is yours; a
merge that touches none of those files is safe to take without a review pass.
