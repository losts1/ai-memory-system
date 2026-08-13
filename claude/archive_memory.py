#!/usr/bin/env python3
"""Archive a memory — move it out of the loaded index without losing it.

WHY ARCHIVE RATHER THAN DELETE
------------------------------
The cost that motivates removing a resolved memory is *context*, not disk:
MEMORY.md is loaded into every session and was measured at 91% of its 24.4 KB
auto-load cap on 2026-08-13. Archiving pays that cost in full while losing
nothing. Disk is cheap; a deleted observation is unrecoverable, and one day the
information may be wanted.

WHY THIS NEEDS NO OTHER CHANGES
-------------------------------
Every consumer globs non-recursively, verified 2026-08-13:

    distill.py:407   rebuild_index()   MEMORY_DIR.glob("*.md")
    memory_audit.py:66                 glob(join(MEMORY_DIR, "*.md"))
    memory_lint.py                     glob(join(MEMORY_DIR, "*.md"))

So a file moved into archive/ drops out of the index, the freshness audit and
the lint automatically. No code change anywhere.

WHAT QUALIFIES
--------------
Archive only when the condition the memory describes no longer exists AND no
durable lesson, recipe or pointer remains. Reviewed on 2026-08-13, most
"resolved"-looking memories did NOT qualify:

    coinbase_capital_constraint        keep — a diagnostic recipe
    kraken_funded_capital_pnl_baseline keep — opens "DURABLE: the method"
    project_eth_bear_trap_hold         keep — a stated principle
    project_inventory_monitor_logging  keep — carries "to read the next event" / "to turn off"
    project_claudeclaw                 keep — a stale snapshot; needs a volatility tag, not archiving

A stale memory is not an archivable one. Staleness is fixed by re-deriving;
archiving is for facts whose subject is gone.

Usage:
    python3 archive_memory.py <file.md> "reason"
    python3 archive_memory.py --list        # what is archived
    python3 archive_memory.py --restore <file.md>
"""
import datetime
import glob
import os
import re
import shutil
import sys

MEMORY_DIR = os.path.dirname(os.path.abspath(__file__))
ARCHIVE_DIR = os.path.join(MEMORY_DIR, "archive")


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def archive(filename, reason):
    src = os.path.join(MEMORY_DIR, filename)
    if not os.path.isfile(src):
        print(f"error: {filename} not found in {MEMORY_DIR}")
        return 1
    if not reason.strip():
        print("error: a reason is required — an archived memory with no reason is a mystery later")
        return 1

    text = open(src, encoding="utf-8").read()
    if re.search(r"^\s*archived:", text, re.M):
        print(f"error: {filename} already carries an archived: field")
        return 1

    m = re.search(r"^(metadata:[ \t]*\n(?:[ \t]+\S.*\n)*)", text, re.M)
    stamp = f"  archived: {now_iso()}\n  archived_reason: {reason.strip()}\n"
    if m:
        text = text[:m.end(1)] + stamp + text[m.end(1):]
    else:                                    # no metadata block — create one
        fm_end = text.index("---", text.index("---") + 3)
        text = text[:fm_end] + "metadata:\n" + stamp + text[fm_end:]

    # Archiving breaks inbound [[links]] — they resolve against the top-level
    # directory only. Observed 2026-08-13: archiving project_aws_ssh_security
    # left two dangling links in reference_aws_proxy that went unnoticed until a
    # separate link check ran. Warn so the caller can repoint them.
    stem = filename[:-3]
    inbound = []
    for other in sorted(glob.glob(os.path.join(MEMORY_DIR, "*.md"))):
        if os.path.basename(other) == filename:
            continue
        try:
            if f"[[{stem}]]" in open(other, encoding="utf-8").read():
                inbound.append(os.path.basename(other))
        except OSError:
            pass
    if inbound:
        print(f"warning: {len(inbound)} memor{'y' if len(inbound)==1 else 'ies'} link to [[{stem}]] "
              f"and will dangle after this move:")
        for f in inbound:
            print(f"    {f}")
        print("  repoint them to archive/%s before or after archiving." % filename)

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    dst = os.path.join(ARCHIVE_DIR, filename)
    if os.path.exists(dst):
        print(f"error: {dst} already exists")
        return 1
    open(src, "w", encoding="utf-8").write(text)
    shutil.move(src, dst)
    print(f"archived: {filename}\n  -> {dst}\n  reason: {reason.strip()}")
    print("  MEMORY.md drops it on the next distill run (non-recursive glob); "
          "audit and lint skip it immediately.")
    return 0


def restore(filename):
    src = os.path.join(ARCHIVE_DIR, filename)
    if not os.path.isfile(src):
        print(f"error: {filename} not in archive/")
        return 1
    text = open(src, encoding="utf-8").read()
    text = re.sub(r"^\s*archived(_reason)?:.*\n", "", text, flags=re.M)
    dst = os.path.join(MEMORY_DIR, filename)
    open(src, "w", encoding="utf-8").write(text)
    shutil.move(src, dst)
    print(f"restored: {filename} -> {dst}")
    return 0


def listing():
    if not os.path.isdir(ARCHIVE_DIR):
        print("no archive/ directory yet")
        return 0
    files = sorted(f for f in os.listdir(ARCHIVE_DIR) if f.endswith(".md"))
    print(f"{len(files)} archived memor{'y' if len(files) == 1 else 'ies'}")
    for f in files:
        t = open(os.path.join(ARCHIVE_DIR, f), encoding="utf-8").read()
        when = re.search(r"^\s*archived:\s*(\S+)", t, re.M)
        why = re.search(r"^\s*archived_reason:\s*(.+)$", t, re.M)
        print(f"  {f}")
        print(f"      {when.group(1) if when else '?'} — {why.group(1)[:88] if why else '?'}")
    return 0


def main():
    a = sys.argv[1:]
    if not a or a[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if a[0] == "--list":
        return listing()
    if a[0] == "--restore":
        return restore(a[1]) if len(a) > 1 else (print("error: need a filename") or 1)
    if len(a) < 2:
        print("error: need a filename and a reason")
        return 1
    return archive(a[0], " ".join(a[1:]))


if __name__ == "__main__":
    sys.exit(main())
