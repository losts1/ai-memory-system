#!/usr/bin/env python3
"""Memory lint — flag claims that can silently become false.

WHAT THIS EXISTS FOR
--------------------
On 2026-08-12 a `git push` resolved a local/origin divergence. The memory
describing that divergence asserted it in the present tense and was not updated:

    "Local trading-bots master is 80 commits behind origin/master and has 15
     unpushed local commits"

That text was in the `description:` field, which `MEMORY.md` carries into every
session's context. It was true on 2026-08-04, false from 2026-08-12, and was
read and acted on as current for nine days. Nothing detected it: no git hook, no
PreToolUse/PostToolUse hook, and `memory_audit.py` is age-based and the memory
carried no `volatility:` tag.

THE CONVENTION THIS ENFORCES
----------------------------
A state claim is written as an OBSERVATION with the instant it was taken, not as
a standing assertion:

    good:  On 2026-08-04T09:12:44Z, origin was 80 ahead and local held 15
           unpushed commits.
    bad:   Local master is 80 commits behind origin.

A measurement with a timestamp cannot become false. A bare date is not enough on
this fleet — bots restart and configs are rewritten hourly, so `2026-08-04`
cannot distinguish an observation taken before a change from one taken after it.

Where a present-tense claim is genuinely wanted, it must carry a way to re-derive
itself, so a reader can settle the question in one command instead of trusting
a line of prose.

REPORT ONLY
-----------
This never edits a memory, matching `memory_audit.py` ("this script never edits
memory") and `distill.py`, which writes a suggestion sidecar rather than
overwriting a hand-edited file. The reasoning that decides what is *now* true is
the same reasoning that produced eight derivation errors on 2026-08-13; it does
not get unsupervised write access to the durable store.

Run:  python3 memory_lint.py            # report
      python3 memory_lint.py --selftest # validate the detector against fixtures
"""
import glob
import os
import re
import sys

MEMORY_DIR = os.path.dirname(os.path.abspath(__file__))
NON_MEMORY = {"MEMORY.md", "README.md", "IMPLEMENT-MEMORY-SYSTEM.md"}

# A live-state claim: asserts something is presently true of a system.
CLAIM = re.compile(
    r"\b(is|are|has|have|remains?|currently|now)\b[^.\n]{0,60}"
    r"\b(running|stopped|active|disabled|deployed|live|ahead|behind|unpushed|"
    r"diverged|enabled|in production|holds?|pending)\b",
    re.I,
)

# A way for the reader to settle it themselves.
RECHECK = re.compile(
    r"```(bash|sh)|`(git|systemctl|python3|dig|sqlite3|curl|grep|ls) |"
    r"re-derive|derive it live|live-check|do NOT cache",
    re.I,
)

# An observation instant. Full ISO-8601 preferred; a bare date is weaker but counted.
STAMP_FULL = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d(:\d\d)?(Z|[+-]\d\d:?\d\d)")
STAMP_DATE = re.compile(r"\d{4}-\d\d-\d\d")


# Pointer rot: a memory naming a path that no longer exists. This is its OWN
# class, deliberately checked even for `reference` memories — pointers are
# exactly what reference memories carry, and paths move. Found 2026-08-13:
# reference_ai_memory_repo named /home/lost/.openclaw/workspace as the clone of
# ai-memory-system (it is Nova2.0), and feedback_dashboard_path asserts a git
# repo at /home/lost/.git which does not exist.
PATHISH = re.compile(r"(?<![\w-])(/home/[a-z]+/[A-Za-z0-9_./-]{3,}|~/[A-Za-z0-9_./-]{3,})")
# Skip anything templated or globbed — those are patterns, not paths.
PLACEHOLDER = re.compile(r"[*?<>{}]|\b(PAIR|BASE|NAME|slug)\b")


def dead_paths(text):
    """Absolute paths named in a memory that do not exist on disk."""
    import os as _os
    out = set()
    for raw in set(PATHISH.findall(text)):
        cand = raw.rstrip(".,;:)`'\"")
        # A trailing separator means the regex clipped a glob or placeholder —
        # e.g. ".../logs/maker_" from "maker_*.jsonl". Those are patterns, not paths.
        if PLACEHOLDER.search(cand) or cand.endswith(("-", "_")):
            continue
        if not _os.path.exists(_os.path.expanduser(cand)):
            out.add(cand)              # set: the same path may appear twice in one memory
    return sorted(out)


def body_of(text):
    """Strip YAML frontmatter; return (frontmatter, body)."""
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    return (m.group(1), m.group(2)) if m else ("", text)


# An invariant is a rule about how a category behaves; a state claim is an
# observation of one instance at one time. Only the latter can silently rot.
# Measured 2026-08-13: without this, claim-in-description was 3/3 false positives
# ("sealed + NULL are normal pending state", "DURABLE PRINCIPLE: ... is an
# accepted hold", "TOZO A1 earbuds advertise a single BLE MAC") — all permanently
# true. `feedback` and `reference` memories are rules and facts by definition.
INVARIANT_TYPES = {"feedback", "reference", "user"}
INVARIANT_WORDS = re.compile(
    r"DURABLE|PRINCIPLE|by design|are normal|is normal|always|never\b|"
    r"regardless of|expected behaviou?r",
    re.I,
)


def inspect(text):
    """Return the list of findings for one memory's raw text."""
    fm, body = body_of(text)
    # Pointer rot is checked for EVERY type, before the invariant exclusions:
    # a reference memory cannot assert an invariant about a path that is gone.
    found = [("dead-path", f"names a path that does not exist: {p}")
             for p in dead_paths(text)]

    mtype = re.search(r"^\s*type:\s*(\w+)", fm, re.M)
    if mtype and mtype.group(1).lower() in INVARIANT_TYPES:
        return found                   # rules and reference facts do not rot — but paths do
    if INVARIANT_WORDS.search(body):
        return found                   # self-declared invariant
    # `description:` matters most: it is the line MEMORY.md loads into context.
    desc = re.search(r"^description:\s*(.+)$", fm, re.M)
    desc = desc.group(1) if desc else ""
    if CLAIM.search(body) and not RECHECK.search(body):
        found.append(("no-recheck", "live-state claim with no way to re-derive it"))
    if CLAIM.search(desc):
        found.append(("claim-in-description",
                      "present-tense claim in description: — this is the line MEMORY.md loads"))
    if CLAIM.search(body) and not STAMP_FULL.search(body):
        weak = "only a bare date" if STAMP_DATE.search(body) else "no timestamp at all"
        found.append(("undated-claim", f"state claim with {weak}; full ISO-8601 preferred"))
    return found


# ── fixtures: the real failure, and its corrected form ────────────────────────
# Verbatim from local_master_divergence_2026_08_04.md as it stood before the
# 2026-08-13 rewrite (recovered from backup). If the detector stops catching
# this, it has regressed and the lint is worthless.
FIXTURE_BAD = """---
name: 15 local vs 80 upstream commits
description: Local trading-bots master is 80 commits behind origin/master and has 15 unpushed local commits — needs reconciliation
metadata:
  type: project
---

As of 2026-08-04, local master in /home/lost/trading has diverged significantly from origin/master (losts1/trading-bots).
**Current state:**
- Origin/master: 80 commits ahead, contains real production code
- Local master: 15 unpushed local-only commits
"""

FIXTURE_GOOD = """---
name: Local/origin divergence — RESOLVED 2026-08-12
description: RESOLVED 2026-08-12 — divergence was merged and pushed; kept as a drift failure-mode record
metadata:
  type: project
---

On 2026-08-04T09:12:44Z, origin was 80 commits ahead and local held 15 unpushed commits.
Re-derive before acting: `git -C /home/lost/trading rev-list --left-right --count origin/master...HEAD`
"""


def selftest():
    bad, good = inspect(FIXTURE_BAD), inspect(FIXTURE_GOOD)
    bad_kinds = {k for k, _ in bad}
    ok = True
    print("memory_lint self-test")
    for want in ("no-recheck", "claim-in-description", "undated-claim"):
        hit = want in bad_kinds
        ok &= hit
        print(f"  known failure flags {want:<22} {'PASS' if hit else 'FAIL'}")
    clean = not good
    ok &= clean
    print(f"  corrected form is clean{'':<17} {'PASS' if clean else 'FAIL — ' + str(good)}")
    return 0 if ok else 1


def main():
    if "--selftest" in sys.argv:
        return selftest()

    # Non-recursive: archived memories live in archive/ and are deliberately excluded.
    files = sorted(glob.glob(os.path.join(MEMORY_DIR, "*.md")))
    rows = []
    for path in files:
        fn = os.path.basename(path)
        if fn in NON_MEMORY or fn.startswith("PLAN-"):
            continue
        try:
            found = inspect(open(path, encoding="utf-8").read())
        except OSError:
            continue
        if found:
            rows.append((fn, found))

    total = len([f for f in files
                 if os.path.basename(f) not in NON_MEMORY
                 and not os.path.basename(f).startswith("PLAN-")])
    print(f"# Memory lint — {len(rows)}/{total} memories flagged\n")
    print("_Report only; this script never edits memory. "
          "A flag is a prompt to re-derive, not proof the claim is false._\n")

    by_kind = {}
    for fn, found in rows:
        for kind, msg in found:
            by_kind.setdefault(kind, []).append((fn, msg))

    for kind in ("dead-path", "claim-in-description", "no-recheck", "undated-claim"):
        hits = by_kind.get(kind, [])
        if not hits:
            continue
        print(f"## {kind} ({len(hits)})\n")
        if kind == "dead-path":
            # Each finding names a different path, so show it per line.
            print("_Path named in the memory no longer exists. Either the thing moved "
                  "(update the pointer) or it was removed (archive the memory)._\n")
            for fn, msg in hits:
                print(f"- `{fn}` — {msg.split(': ', 1)[-1]}")
        else:
            print(f"_{hits[0][1]}_\n")
            for fn, _ in hits:
                print(f"- {fn}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
