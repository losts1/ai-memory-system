#!/usr/bin/env python3
"""Memory structural checker — deterministic, report-only.

Checks the health of the memory corpus's *structure* (not freshness — that's
memory_audit.py's job):
  1. MEMORY.md load limits    — LINE_LIMIT (200) is the real truncation point and is
                                enforced silently by the loader; BUDGET (bytes) is a
                                context-cost signal only and does NOT cause truncation
  2. Index ↔ files integrity  — every index entry points to a real file; no orphans
  3. Frontmatter schema drift — nested `metadata:` vs flat `type:`
  4. Wikilink integrity       — [[name]] links that resolve to no memory (advisory)

Does NOT call an LLM and does NOT edit anything. Exits non-zero if a hard check
fails (over budget or broken index integrity) so it can gate a cron/commit.

Run: python3 memory_check.py            # summary
     python3 memory_check.py --print    # summary + full detail
"""
import glob
import os
import re
import sys

MEMORY_DIR = os.path.dirname(os.path.abspath(__file__))
# bytes. CONTEXT-COST budget only — NOT a truncation guard. The "~24.4KB auto-load
# cap" this constant was originally sized against was measured on 2026-08-15 and does
# not exist: the loader does not truncate MEMORY.md on bytes at all (a real 14975-byte
# index loads complete). Truncation is purely by line count — see LINE_LIMIT below,
# which is the check that actually protects the index. Operator-set to 32000 on
# 2026-08-15 (was 24400); tune it for how much context you want the index to cost.
BUDGET = 32000
# The REAL constraint. Measured, not assumed — see the note in main(). One memory =
# one index line, so this caps how many memories can exist at all.
LINE_LIMIT = 200
LINE_WARN = 170         # start nagging with 30 lines of headroom left
NON_MEMORY = {
    "MEMORY.md",
    "README.md",
    "IMPLEMENT-MEMORY-SYSTEM.md",
    # Design/plan docs live alongside memories but are not memories: no frontmatter,
    # not recalled, and indexing them would spend session-load budget on prose.
    "PLAN-2026-08-13-memory-freshness.md",
}


def memory_files():
    return [f for f in sorted(glob.glob(os.path.join(MEMORY_DIR, "*.md")))
            if os.path.basename(f) not in NON_MEMORY]


def main():
    detail = "--print" in sys.argv
    fails, warns, notes = [], [], []
    md_path = os.path.join(MEMORY_DIR, "MEMORY.md")
    md = open(md_path, encoding="utf-8").read()
    size = len(md.encode())

    # 1a. LINE budget — this is the binding constraint, and it fails SILENTLY.
    #
    # Measured 2026-08-15 against claude-code 2.1.220 with a synthetic index loaded
    # via `autoMemoryDirectory`: a 260-line / 1838-byte MEMORY.md was delivered cut
    # at exactly line 200, with NO truncation notice in context. The binary carries
    # a "> This memory file was truncated (...)" marker but does not emit it on this
    # path, so there is no in-context signal that entries were dropped — only this
    # check stands between you and silently losing the tail of the index.
    #
    # Line count, NOT bytes, is what binds: the same run proves the 4096-byte limit
    # does not apply here (a real 14975-byte index loads complete). Do not "fix" a
    # line overflow by shortening lines — only fewer ENTRIES help, i.e. merge or
    # delete memories.
    nlines = len(md.splitlines())
    if nlines > LINE_LIMIT:
        fails.append(f"MEMORY.md {nlines} lines is OVER the {LINE_LIMIT}-line load limit — "
                     f"the last {nlines - LINE_LIMIT} entries are SILENTLY dropped at session "
                     f"start (no marker is shown). Merge or delete memories; shortening "
                     f"lines will not help.")
    elif nlines > LINE_WARN:
        warns.append(f"MEMORY.md {nlines} lines — only {LINE_LIMIT - nlines} short of the "
                     f"{LINE_LIMIT}-line silent-truncation limit. Compact now.")
    else:
        notes.append(f"MEMORY.md {nlines} lines, {LINE_LIMIT - nlines} under the "
                     f"{LINE_LIMIT}-line limit.")

    # 1b. Byte budget — advisory. Retained for context cost, but see above: the
    # loader does not truncate this file on bytes, so this can read "under budget"
    # while the index is being cut on lines.
    if size >= BUDGET:
        fails.append(f"MEMORY.md {size}B is OVER the {BUDGET}B context-cost budget by "
                     f"{size - BUDGET}B. NOTE: this does NOT mean the index is being "
                     f"truncated — truncation is by LINE (see the line check above). "
                     f"This is a context-cost signal only; fix it by pruning memories, "
                     f"not by lowering INDEX_HOOK_MAX (hook length has no effect on "
                     f"either limit).")
    else:
        notes.append(f"MEMORY.md {size}B ({size/1024:.1f}KB), {BUDGET - size}B under budget.")

    # 2. Index <-> files integrity
    refs = {os.path.basename(r) for r in re.findall(r"\(([\w./-]+\.md)\)", md)}
    refs -= NON_MEMORY
    files = {os.path.basename(f) for f in memory_files()}
    dangling = sorted(refs - files)
    orphans = sorted(files - refs)
    if dangling:
        fails.append(f"{len(dangling)} index entries point to missing files: {dangling}")
    if orphans:
        fails.append(f"{len(orphans)} memory files are not in the index (run "
                     f"distill.py --rebuild-index): {orphans}")
    if not dangling and not orphans:
        notes.append(f"Index integrity OK: {len(files)} files, {len(refs)} entries, 0 orphan/dangling.")

    # 3. Frontmatter schema drift (nested metadata.type vs flat type)
    nested = flat = 0
    for f in memory_files():
        head = open(f, encoding="utf-8").read()[:400]
        if re.search(r"^metadata:", head, re.MULTILINE):
            nested += 1
        elif re.search(r"^type:", head, re.MULTILINE):
            flat += 1
    if nested and flat:
        warns.append(f"Frontmatter schema drift: {nested} files use nested `metadata:`, "
                     f"{flat} use flat `type:`. Normalize to one schema.")

    # 4. Wikilink integrity (advisory — slug matching is fuzzy)
    def norm(s):
        return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    valid = {norm(os.path.basename(f)[:-3]) for f in memory_files()}
    for f in memory_files():
        m = re.search(r"^name:\s*(.+)$", open(f, encoding="utf-8").read(), re.MULTILINE)
        if m:
            valid.add(norm(m.group(1)))
    broken = sorted({l for l in re.findall(r"\[\[([^\]]+)\]\]", md) if norm(l) not in valid})
    # also scan bodies for cross-links
    for f in memory_files():
        for l in re.findall(r"\[\[([^\]]+)\]\]", open(f, encoding="utf-8").read()):
            if norm(l) not in valid:
                broken.append(l)
    broken = sorted(set(broken))
    if broken:
        warns.append(f"{len(broken)} [[wikilinks]] resolve to no memory (advisory): {broken[:8]}")

    # Report
    print("=== memory structural check ===")
    for n in notes:
        print(f"  ok   {n}")
    for w in warns:
        print(f"  WARN {w}")
    for e in fails:
        print(f"  FAIL {e}")
    print(f"--- {len(fails)} fail / {len(warns)} warn ---")
    if detail:
        lines = [l for l in md.splitlines() if l.strip().startswith("-")]
        over = sorted((l for l in lines if len(l) > 200), key=len, reverse=True)[:10]
        print("\nlongest index entries:")
        for l in over:
            print(f"  {len(l):>4}  {l[:100]}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
