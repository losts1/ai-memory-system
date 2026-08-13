# Claude Code integration

The rest of this repository is the redistribution package, derived from the
OpenClaw memory system. This directory is the **Claude Code implementation** —
the pieces that hook a memory store into Claude Code specifically:
`~/.claude/projects/<slug>/memory/`, driven by Claude Code's `Stop` hook and OS
cron rather than an agent heartbeat.

It is a snapshot of a live deployment, committed here so it is versioned; the
deployment directory itself is not under version control.

Keep it separate from the core: the concepts (decay classes, archive-not-delete,
report-only tooling) are portable, but the wiring — hook names, stdin JSON
shape, `MEMORY.md` auto-load cap — is Claude Code's.

## Why this exists

On 2026-08-12 a `git push` resolved a repository divergence. The memory
describing that divergence asserted it in the present tense and was never
updated. Its `description:` field — the line `MEMORY.md` loads into *every*
session's context — still read:

> Local trading-bots master is 80 commits behind origin/master and has 15
> unpushed local commits

True when written, false eight days later, read and acted on as current. Nothing
detected it: no git hook, no `PreToolUse`/`PostToolUse` hook, and the freshness
audit is age-based so a fact that dies in one instant is invisible to it.

The pipeline below closes that loop, and — more importantly — makes the failure
harder to create in the first place.

## The pieces

| script | when | writes? | role |
|---|---|---|---|
| `memory_watch.py` | **per turn** (`Stop` hook) | no | fingerprints watched state; records movement to `.state_changes` |
| `memory_mark.py` | `:29,:59` | `stale_since:` only | marks memories whose trigger fired |
| `distill.py` | `*/30` | index + distilled memories | rebuilds `MEMORY.md`; renders staleness via `_stale_marker()` |
| `memory_audit.py` | daily 06:00 | no | TTL check on `volatility:`+`verified:` |
| `memory_lint.py` | daily 06:05 | no | flags claims that can silently become false |
| `archive_memory.py` | manual | moves files | retires resolved memories without deleting them |
| `queue_session.sh` | session end | no | queues transcripts for distillation |

The loop that closes the original failure:

```
watcher (per turn)  state moved            -> .state_changes
mark (:29/:59)      decay:event + trigger  -> stale_since:
distill (:00/:30)   rebuild index          -> **STALE since … — re-derive**
next session        reads that warning in the line it loads
```

## Design rules these encode

**Prevention beats detection.** A claim written as a timestamped observation
("On 2026-08-04T09:12:44Z, origin was 80 ahead") cannot become false. A resolved
condition is archived rather than maintained. The detection machinery only
covers the residue — memories that are correctly written, legitimately
present-tense, and change anyway. Measured on the source corpus: exactly **one**
of 171 memories qualified for event-triggered marking.

**Nothing rewrites a memory's claim.** `memory_audit.py` and `memory_lint.py`
are report-only. `memory_mark.py` writes one field, `stale_since:`, and never
touches the body or description. `archive_memory.py` moves files and never
deletes. Marking adds *doubt*; it cannot corrupt a fact. This matters because
the reasoning that decides what is *now true* is fallible, and an unsupervised
process that gets it wrong makes the error authoritative — the next session
reads the corrupted memory as ground truth with no way to know.

**Archive, never delete.** The cost motivating removal is context, not disk:
`MEMORY.md` was measured at 91% of its 24.4 KB auto-load cap. Every consumer
globs non-recursively, so moving a file into `archive/` drops it from the index,
the audit and the lint with **no code change**, while the observation survives.

**Cheap enough to run per turn.** `memory_watch.py` measures ~45 ms. It hashes a
whitelist of *stable* config keys rather than file mtimes — 4 of 8 watched
configs changed mtime within 45 seconds because other processes rewrite them on
a timer, so mtime fingerprinting would have fired on nearly every turn.

## Portability

**These are not generic.** They carry absolute paths from the deployment they
were taken from:

| script | hardcoded paths |
|---|---|
| `memory_lint.py` | 2 |
| `queue_session.sh` | 2 |
| `memory_audit.py` | 1 |
| `memory_watch.py` | 1 |
| `distill.py`, `memory_mark.py`, `archive_memory.py` | 0 — resolve relative to their own location |

`memory_watch.py` and `memory_audit.py` additionally probe a specific trading
fleet (`/home/lost/trading`, `kraken-maker-*` systemd units). Treat those as
worked examples of *what* to fingerprint, not as reusable code.

## Verification

`python3 claude/memory_lint.py --selftest` is the one to run after any change. Its fixtures are
the real pre-rewrite memory that caused the original failure, recovered from
backup, and its corrected form. If the detector stops catching the first or
starts flagging the second, it has regressed.
