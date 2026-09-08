# Deployment sync log

This directory is a snapshot. The deployment it was taken from — the memory
directory Claude Code loads, `~/.claude/projects/<project-slug>/memory/` — is not
version controlled, so nothing enforces that the two agree. Drift has run in both
directions: on 2026-09-08 four scripts here were ahead of the deployment and
`distill.py` was two days behind it.

This file records when they were last made to agree, and how that was checked.

## Re-verify

From a checkout, with `D` set to the live memory directory:

```bash
D=~/.claude/projects/<project-slug>/memory
for f in archive_memory.py distill.py memory_audit.py memory_check.py \
         memory_lint.py memory_mark.py memory_watch.py queue_session.sh search.py; do
  a=$(git hash-object "$D/$f"); b=$(git rev-parse HEAD:claude/$f)
  [ "$a" = "$b" ] && echo "  ok   $f" || echo "  DIFF $f"
done
```

A `DIFF` says which direction to look, not which side is wrong. Hash the deployed
file and search the repo's history for a matching blob:

```bash
dep=$(git hash-object "$D/<file>")
for c in $(git log --all --format=%H -- claude/<file>); do
  [ "$(git rev-parse $c:claude/<file>)" = "$dep" ] && echo "deployment matches $c"
done
```

A match on an older commit means the **deployment is behind** — copy the repo's
version out. No match at all means the deployment carries edits that were never
committed, and the repo is behind: read the diff and commit *from* the deployment.
Never assume the repo is authoritative; `distill.py` was the case where it was not.

## Log

| date | files | direction | commit | verified by |
|---|---|---|---|---|
| 2026-09-08 | `memory_audit.py`, `memory_lint.py`, `memory_watch.py`, `queue_session.sh` | repo → deployment | `5bc8e52` | old vs new reports byte-identical on the live corpus; watcher stdout and fingerprint identical in isolated runs; `memory_lint.py --selftest` 5/5; resolved `TRADES_GLOB` / `TRADING` / `MEMORY_DIR` unchanged |
| 2026-09-08 | `distill.py`, `memory_check.py`, `search.py` | deployment → repo (`search.py`'s docstring path scrubbed on the way, then copied back) | `c322d17` | all nine byte-identical afterwards; `memory_check.py` and `search.py --list` run clean from the deployment |
| 2026-09-08 | `distill.py` | repo → deployment | `4ee767c` | live `--rebuild-index` byte-identical at 144 entries; `memory_check.py` 0 fail / 0 warn |

All nine files matched their `HEAD` blobs at the end of 2026-09-08.

## What this does not cover

The memories themselves, `MEMORY.md`, the state files (`.state_fingerprint`,
`.distill_queue`, the `.*.log` cron output) and `archive/` are the deployment's own
content and are deliberately not mirrored here. Only the nine scripts are.
