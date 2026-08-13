#!/usr/bin/env python3
"""Mark memories stale when the state they describe has moved.

WHAT IT DOES
------------
Reads `.state_changes` (written per-turn by memory_watch.py). For every memory
tagged `decay: event` whose `trigger:` matches a changed subsystem, it writes a
single frontmatter field:

    stale_since: 2026-08-13T20:31:00Z

`distill.py`'s `_stale_marker()` then renders that in MEMORY.md as
**STALE since ... — re-derive**, so the warning appears in the line that loads
into every session. That is the whole point: the 2026-08-12 failure was a stale
claim being read at session start, and a queue nobody reads would not have
prevented it.

WHAT IT NEVER DOES
------------------
It never touches a memory's body, its description, or any other field. Marking
adds *doubt*; it cannot corrupt a fact. Both sibling scripts hold the same line —
`memory_audit.py` states "this script never edits memory", and `distill.py`
writes a suggestion sidecar rather than overwriting a hand-edited file.

The reason matters here specifically: deciding what is *now true* is exactly the
reasoning that produced eight derivation errors on 2026-08-13, each caught by a
human or a later check. An unsupervised process with that error rate does not
merely make mistakes — it makes them authoritative, because the next session
reads the result as ground truth with no way to know.

So the strongest thing this is allowed to say is "this may have moved, go and
look", never "this is now X".

SCOPE — HONEST LIMITS
---------------------
Only memories that opt in via `decay: event` + `trigger:` are ever touched.
The watcher fingerprints three subsystems: `git` (the trading repo),
`services` (kraken-* systemd units) and `config` (stable maker config keys).
Coinbase services, exchange balances, prices and external APIs are NOT watched,
so memories about those will never be marked by this path.

A trigger firing does not prove a memory is wrong — a push does not invalidate
every git-related memory. The mark means "re-derive before relying on this".

Usage:
    python3 memory_mark.py --dry-run    # show what would be marked
    python3 memory_mark.py              # apply
    python3 memory_mark.py --clear FILE # remove a stale_since after re-verifying
"""
import datetime
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CHANGES = os.path.join(HERE, ".state_changes")
CURSOR = os.path.join(HERE, ".state_changes_cursor")
NON_MEMORY = {"MEMORY.md", "README.md", "IMPLEMENT-MEMORY-SYSTEM.md"}


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def memories():
    """(path, raw_text) for real memories only — non-recursive, so archive/ is excluded."""
    for path in sorted(glob.glob(os.path.join(HERE, "*.md"))):
        fn = os.path.basename(path)
        if fn in NON_MEMORY or fn.startswith("PLAN-"):
            continue
        try:
            yield path, open(path, encoding="utf-8").read()
        except OSError:
            continue


def unread_changes():
    """New lines in .state_changes since the last run, plus the new cursor."""
    if not os.path.exists(CHANGES):
        return [], 0
    lines = open(CHANGES).read().splitlines()
    try:
        seen = int(open(CURSOR).read().strip())
    except Exception:
        seen = 0
    if seen > len(lines):                      # log was truncated/rotated
        seen = 0
    out = []
    for ln in lines[seen:]:
        try:
            out.append(json.loads(ln))
        except Exception:
            pass
    return out, len(lines)


def mark(path, text, subsystem, when, dry):
    """Insert stale_since: into the metadata block. Returns a status string."""
    if re.search(r"^\s*stale_since:", text, re.M):
        return "already marked"
    m = re.search(r"^(metadata:[ \t]*\n(?:[ \t]+\S.*\n)*)", text, re.M)
    if not m:
        return "no metadata block — skipped"
    if dry:
        return f"WOULD mark (trigger {subsystem})"
    new = m.group(1).rstrip("\n") + f"\n  stale_since: {when}\n"
    updated = text[:m.start(1)] + new + text[m.end(1):]
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(updated)
    os.replace(tmp, path)
    return f"marked (trigger {subsystem})"


def clear(filename):
    path = os.path.join(HERE, filename)
    if not os.path.isfile(path):
        print(f"error: {filename} not found")
        return 1
    text = open(path, encoding="utf-8").read()
    if not re.search(r"^\s*stale_since:", text, re.M):
        print(f"{filename}: no stale_since to clear")
        return 0
    open(path, "w", encoding="utf-8").write(
        re.sub(r"^\s*stale_since:.*\n", "", text, flags=re.M))
    print(f"{filename}: stale_since cleared — re-derived and confirmed current")
    return 0


def main():
    dry = "--dry-run" in sys.argv
    if "--clear" in sys.argv:
        i = sys.argv.index("--clear")
        return clear(sys.argv[i + 1]) if len(sys.argv) > i + 1 else 1

    changes, cursor = unread_changes()
    if not changes:
        print("no new state changes")
        return 0

    moved = {c.get("subsystem") for c in changes}
    when = now_iso()
    print(f"{len(changes)} new change(s); subsystems moved: {', '.join(sorted(moved))}")

    hits = 0
    for path, text in memories():
        decay = re.search(r"^\s*decay:\s*(\w+)", text, re.M)
        trig = re.search(r"^\s*trigger:\s*(\S+)", text, re.M)
        if not decay or decay.group(1).lower() != "event" or not trig:
            continue
        if trig.group(1).lower() not in moved:
            continue
        status = mark(path, text, trig.group(1), when, dry)
        print(f"  {os.path.basename(path):<48} {status}")
        hits += 1

    if hits == 0:
        print("  no memories opted in for these subsystems (decay: event + trigger:)")
    if not dry:
        with open(CURSOR, "w") as f:
            f.write(str(cursor))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"memory_mark error: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(0)
