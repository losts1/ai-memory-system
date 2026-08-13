# Plan — memory freshness: prevention first, detection for the residue

**Date:** 2026-08-13
**Status:** plan — not implemented
**Scope:** `MEMORY.md` index, `distill.py`, memory frontmatter schema, one new `Stop` hook.

---

## 1. The failure this exists for

On 2026-08-12 a `git push` resolved a long-standing local/origin divergence.
`local_master_divergence_2026_08_04.md` described that divergence in the present tense and was
not updated. Its `description:` field — the line `MEMORY.md` carries into **every session's
context** — read:

```
Local trading-bots master is 80 commits behind origin/master and has 15 unpushed local commits
```

It was true when written on 2026-08-04. It was false from 2026-08-12. It was read and acted on
as current at the start of the next session, and was wrong for nine days.

**Nothing would have caught it.** Verified 2026-08-13:

| mechanism | caught it? |
|---|---|
| git hooks in `trading/.git/hooks` | none active — only `.sample` templates |
| `PreToolUse` (4 configured) | no — `rm -rf` guard, prompt-guard ×2, introspection |
| `PostToolUse` (5 configured) | no — skills-observer, prompt-guard, ruff, eslint |
| `Stop` (2 configured) | no — queues the session for distillation; *adds* memories, never invalidates |
| `memory_audit.py`, 06:00 daily | no — age-based, and the memory was untagged |
| `CLAUDE.md` "update the related memory in the same turn" | a behavioural instruction. It was violated. |

## 2. Principle

**Prevention beats detection, and it is an order of magnitude cheaper.**

A memory written in the past tense with the instant it was observed cannot become false. A
memory describing a *resolved* transient condition should not sit in the loaded index — it is
archived, not deleted. Detection machinery is only justified for what remains after both.

Measured build/runtime cost against what each approach achieves:

| approach | build | runtime | effect |
|---|---|---|---|
| Past-tense + observation date | ~1h (lint written and validated) | 0 | **prevents** the class |
| Archive memories on resolution | ~0 — a policy, no code | 0 | **prevents** the class |
| Slim the index (phase A) | ~30m | 0 | 1,580 tokens back per session, every session |
| Per-turn watcher + marking | ~4h | 45 ms/turn | **detects**, and only for what it fingerprints |

The watcher was the first thing proposed and has the worst ratio. It is kept, but scoped down
to the residue that prevention genuinely cannot reach (§6).

## 3. Phase A — MEMORY.md becomes a pointer index

**File:** `distill.py` → `rebuild_index()`

Drop the description hook. Line format becomes `- [P] [Title](file.md) (date)`.

Measured 2026-08-13 across 171 entries:

| component | chars | share |
|---|---:|---:|
| description hook | 6,331 | **31%** |
| title | 5,731 | 28% |
| filename | 5,629 | 28% |
| date | 1,495 | 7% |
| type tag | 684 | 3% |

`MEMORY.md` is **22,316 bytes — 91% of the 24.4 KB auto-load cap** noted at `distill.py:383`.
Dropping descriptions returns it to ~15,985 bytes (**65%**) and saves ~1,580 tokens of every
session's context.

**Cost, stated honestly:** descriptions currently help route to the right memory. Titles must
carry that alone. The description is not deleted — it stays in each file's frontmatter, one
`Read` away. Expect slightly more file-opening in exchange for the context saving.

**Verify:** run `rebuild_index()` against a *copy* of the memory directory, diff the output,
confirm 171 entries and the byte target before it touches the live file.

## 4. Phase B — decay classes, not a single TTL

The current model is `volatility: high|medium|low` + `verified:` with TTLs 3/7/30 days. It
cannot express the case that actually failed.

**Time is the wrong axis for event-triggered facts.** "Local master is 80 ahead" did not decay
— it was true for nine days and became false in one instant, when a push happened. A 30-day TTL
misses that entirely; a 3-day TTL false-positives constantly on something that only moves when
you act.

```yaml
metadata:
  observed: 2026-08-13T14:30:00Z     # when the claim was checked true
  decay: event                        # never | event | slow | fast | live
  trigger: git-push                   # required when decay: event
  recheck: git -C /home/lost/trading rev-list --left-right --count origin/master...HEAD
```

| class | semantics | flagged when |
|---|---|---|
| `never` | past-tense record — an incident, a decision, a measurement | never |
| `event` | true until a specific action occurs | the watcher observes its trigger — **no time component** |
| `slow` | hosts, repo layout, API shapes | 90 d |
| `fast` | deployment state, probation decisions | 7 d |
| `live` | balances, PnL, prices, bot up/down | always — render as "derive, don't trust" |

**ISO-8601, not raw unix.** Same precision, and `modified:` already uses that format; raw
integers would be the only unreadable field in a file both humans and Claude read. Parsing is
one `datetime.fromisoformat()` either way.

**Staleness renders in the index**, because the index is what loads into context — a queue
nobody reads would not have prevented this failure:

```
- [P] [Local/origin divergence](local_master_divergence_2026_08_04.md)  **STALE since 2026-08-12 — re-derive**
```

Paid for by phase A: the marker costs ~40 chars on the few entries that carry it, against
6,331 chars saved.

**Verify:** unit-test `_stale_marker()` on fixtures for each class before wiring it in — a
memory with `stale_since` renders the marker; `decay: never` never renders; a `fast` memory
one day past TTL renders, one day inside does not.

## 5. Phase C — per-turn state watcher

**The trigger already exists.** `Stop` fires per-turn — `queue_session.sh:75` collapses it to
once per session by deduping against `.distill_processed`. This session appeared **once** in
`.queue.log` despite ~100 turns. A second `Stop` hook that does not dedupe gives per-turn
firing with no new infrastructure.

**New file:** `memory_watch.py`, registered as a second `Stop` hook.

**Cost: 45 ms measured** (git divergence + `rev-parse HEAD` + active kraken units + maker
config mtimes, hashed). At ~100 turns that is 4.5 s per session.

Fingerprint watched state → compare against `.state_fingerprint` → on change, append
`{ts, subsystem, old, new}` to `.state_changes`. Exit silently when unchanged, which is the
common case.

**It does not write new memories.** The fingerprint sees `git divergence 80/15 → 0/2` and has
no way to judge whether that is worth remembering. Writing one memory per state change would
have produced dozens in this session alone — 8 restarts, ~12 commits, continuous config churn.
171 curated memories are useful; 10,000 auto-generated deltas are a log, and `.state_changes`
already *is* that log. Selection is what makes a memory valuable, and 45 ms of hashing cannot
do selection. `distill.py` already does it, from full transcripts.

**One permitted write:** when a `recheck` command runs and still returns the expected value,
refresh `observed:`. That creates no knowledge — it refreshes confidence in existing knowledge,
and prevents false staleness accumulating.

**Verify:** make a commit, confirm a `git` entry lands in `.state_changes`; take an idle turn,
confirm nothing is appended.

## 6. Phase D — marking, scoped to the residue

Consumes `.state_changes`. For memories with `decay: event` whose `trigger` matches the changed
subsystem, write **only** `stale_since:` into frontmatter. Never the body.

Marking adds doubt; it cannot corrupt a fact. This is the boundary both existing components
already respect — `memory_audit.py` states *"this script never edits memory"*, and `distill.py`
writes a suggestion sidecar rather than overwriting a hand-edited file (`:347`). The rule holds
here for a further reason: the reasoning that decides *what is now true* is exactly the
reasoning that produced roughly eight derivation errors on 2026-08-13, each caught by a human
or a later check. An unsupervised process with that error rate does not merely err — it makes
the error authoritative, because the next session reads the corrupted memory as ground truth.

**Scope:** only memories carrying `decay: event` **and** a `recheck` command. Today that is a
handful, not 171. Prevention (§7) handles the rest.

## 7. Prevention — do this first, it is nearly free

**7a. Past-tense convention, with a full timestamp.** State claims are written as observations,
not assertions:

> On 2026-08-04T09:12:44Z, origin was 80 ahead and local held 15 unpushed commits.

not

> Local master is 80 commits behind origin.

A past-tense claim carrying the instant it was observed **cannot become false** — it is a
measurement, not a prediction. Date alone is not enough: state can change several times within
a day (this fleet restarts bots and rewrites configs hourly), so a bare `2026-08-04` cannot
distinguish an observation made before a change from one made after it. Full ISO-8601 with
timezone, matching `observed:` in §4.

A lint for this is written and validated:

- flags a live-state claim with no way to re-check it
- flags **23/171 (13%)** — tractable, not a flood
- validated against the **real pre-rewrite file** recovered from backup: **CAUGHT**
- the rewritten version, which carries a `recheck` command: **clean**

**7b. Archive on resolution — do not purge.** The failing memory described a transient
condition. Once merged and pushed it no longer described anything current.

**Move it, do not delete it.** The cost that motivated pruning is *context*, not disk —
`MEMORY.md` at 91% of its auto-load cap. Archiving pays that cost in full while losing nothing,
and one day the information may be wanted. Disk is cheap; a deleted observation is
unrecoverable.

**This is free — verified 2026-08-13, no code change required.** Every consumer globs
non-recursively:

| consumer | glob | effect of `archive/` |
|---|---|---|
| `distill.py:407` `rebuild_index()` | `MEMORY_DIR.glob("*.md")` | archived files vanish from `MEMORY.md` |
| `memory_audit.py:66` | `glob(join(MEMORY_DIR, "*.md"))` | archived files stop being audited |
| `distill.py:407` (write path) | same | distiller ignores them |

So the whole operation is `mkdir -p archive && git mv/mv <file> archive/`. Nothing else changes.

Archived memories keep an added frontmatter line recording why and when:

```yaml
  archived: 2026-08-13T14:52:00Z
  archived_reason: divergence merged and pushed 2026-08-12; no longer describes current state
```

*Caveat:* the §7a lint and any future tooling must use the same non-recursive glob, or they
will start reporting on archived content. `distill.py:383` frames this as pruning — *"pruning
resolved memories remains the long-term fix"* — which is the right instinct with the wrong
verb.

**7c. Recheck commands.** Only **39/171 (22%)** of memories carry a command that re-derives
their claim. Those are the memories that survived scrutiny on 2026-08-13; the ones that misled
were in the other 78%. Raising this number is the single highest-value change to how memories
are written, and it costs one line per memory.

## 8. Sequencing

1. **7a + 7b** — convention and pruning policy. No code, no runtime cost, prevents the class.
2. **A + B together** — one function, one edit, one rollback point in `distill.py`.
3. **C** — self-contained, writes nothing.
4. **D** — last, the only component that writes, and only to `stale_since:`.

## 9. Risks

- **`distill.py` is live on a 30-minute cron.** A syntax error silently stops index rebuilds.
  Back it up, `py_compile` after editing, dry-run `rebuild_index()` against a copied directory.
- **Fingerprint scope is a hard limit.** Git, systemd units and maker configs are covered.
  Coinbase state, exchange balances, prices and external APIs are **not**, and will not be
  caught. Adding them means adding API calls to a per-turn hook — reconsider the 45 ms budget
  before doing so.
- **`subsystem changed` ≠ `this memory is wrong`.** A push does not invalidate every
  git-related memory. The mapping is keyword-based and will drift.
- **The memory directory is not version controlled** (`/home/lost` is not a git repo), so
  these changes have no history and no rollback beyond manual backups.

## 10. What is deliberately not being built

- Autonomous rewriting of memory bodies (§6).
- Deletion of any memory. Resolved memories are archived (§7b), never purged.
- Memory creation by the watcher (§5).
- An LLM call per turn — the per-turn budget is 45 ms; a model call is 10⁴ times that and
  nondeterministic on a path that runs ~100 times per session.
