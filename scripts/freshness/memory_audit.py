#!/usr/bin/env python3
"""Memory freshness auditor — deterministic, report-only.

Scans this memory dir for files tagged `volatility:` in frontmatter, flags any
whose `verified:` date is older than its TTL, and runs built-in live probes so
drift is visible next to the flagged memories. Writes .memory_audit_report.md.

Does NOT call an LLM and does NOT edit any memory file — it only reads memories
and writes the report. Re-verification/updates stay human-in-the-loop.

Run: python3 memory_audit.py            # write report
     python3 memory_audit.py --print    # also echo report to stdout
Cron: a daily sibling of distill.py's crontab entry.
"""
import glob
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone

MEMORY_DIR = os.path.dirname(os.path.abspath(__file__))
REPORT = os.path.join(MEMORY_DIR, ".memory_audit_report.md")
TRADES_GLOB = "/home/lost/trading/kraken-maker-*/trades.db"

# Default staleness TTL by volatility (days); per-file `ttl_days:` overrides.
DEFAULT_TTL = {"high": 3, "medium": 7, "low": 30}


def parse_frontmatter(path):
    """Return dict of frontmatter fields (flattened) or {} if none."""
    try:
        with open(path) as f:
            text = f.read()
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    fields, banner = {}, None
    for line in text[3:end].splitlines():
        m = re.match(r"\s*([A-Za-z_]+):\s*(.*)$", line)
        if m:
            fields[m.group(1)] = m.group(2).strip().strip("\"'")
    # First blockquote line in the body = the human-facing live-source hint.
    bm = re.search(r"^>\s*(.+)$", text[end:], re.MULTILINE)
    if bm:
        banner = bm.group(1).strip()
    fields["_banner"] = banner
    return fields


def age_days(verified):
    try:
        d = datetime.strptime(verified, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None
    return (datetime.now(timezone.utc) - d).days


def scan_memories():
    rows = []
    for path in sorted(glob.glob(os.path.join(MEMORY_DIR, "*.md"))):
        if os.path.basename(path) in ("MEMORY.md",):
            continue
        fm = parse_frontmatter(path)
        vol = (fm.get("volatility") or "").lower()
        if vol not in DEFAULT_TTL:
            continue
        ttl = int(fm["ttl_days"]) if fm.get("ttl_days", "").isdigit() else DEFAULT_TTL[vol]
        age = age_days(fm.get("verified"))
        rows.append({
            "file": os.path.basename(path),
            "vol": vol,
            "verified": fm.get("verified", "?"),
            "age": age,
            "ttl": ttl,
            "stale": age is not None and age > ttl,
            "hint": fm.get("_banner") or "(see memory body for live source)",
        })
    return rows


def probe_bot_liveness():
    """Per-bot: hours since last fill + matched cycles in last 24h. Defensive."""
    out = []
    cutoff = time.time() - 24 * 3600
    for db in sorted(glob.glob(TRADES_GLOB)):
        bot = os.path.basename(os.path.dirname(db))
        last_h = matched_24h = None
        try:
            c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
            try:
                r = c.execute("SELECT MAX(timestamp) FROM fills").fetchone()[0]
                if r:
                    last_h = round((time.time() - float(r)) / 3600, 1)
            except sqlite3.Error:
                pass
            try:
                matched_24h = c.execute(
                    "SELECT COUNT(*) FROM matched_trades WHERE matched_at >= ?", (cutoff,)
                ).fetchone()[0]
            except sqlite3.Error:
                pass
            c.close()
        except sqlite3.Error:
            pass
        out.append((bot, last_h, matched_24h))
    return out


def main():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    mems = scan_memories()
    stale = [m for m in mems if m["stale"]]
    fresh = [m for m in mems if not m["stale"]]
    bots = probe_bot_liveness()

    L = []
    L.append(f"# Memory Freshness Audit — {now}")
    L.append("")
    L.append("_Report-only. Re-derive each flagged memory from its live source before asserting; this script never edits memory._")
    L.append("")
    L.append(f"## Stale — verified older than TTL ({len(stale)})")
    if stale:
        L.append("")
        L.append("| memory | vol | verified | age(d) | ttl | live source |")
        L.append("|--------|-----|----------|-------:|----:|-------------|")
        for m in sorted(stale, key=lambda x: -(x["age"] or 0)):
            hint = m["hint"][:90]
            L.append(f"| {m['file']} | {m['vol']} | {m['verified']} | {m['age']} | {m['ttl']} | {hint} |")
    else:
        L.append("\n(none)")
    L.append("")
    L.append(f"## Within TTL ({len(fresh)})")
    for m in sorted(fresh, key=lambda x: x["file"]):
        L.append(f"- {m['file']} — {m['vol']}, verified {m['verified']} (age {m['age']}d ≤ {m['ttl']})")
    L.append("")
    L.append("## Live probes (for eyeball comparison vs stale bot/PnL memories)")
    L.append("")
    L.append("| bot | h since last fill | matched cycles 24h |")
    L.append("|-----|------------------:|-------------------:|")
    for bot, last_h, m24 in bots:
        L.append(f"| {bot} | {last_h if last_h is not None else '—'} | {m24 if m24 is not None else '—'} |")
    L.append("")
    L.append("For full current PnL, run the `kraken-pnl` skill. For balances, `coinbase-balances.py`.")
    report = "\n".join(L) + "\n"

    with open(REPORT, "w") as f:
        f.write(report)
    print(f"[memory-audit] {len(stale)} stale / {len(mems)} volatile memories → {REPORT}")
    if "--print" in sys.argv:
        print("\n" + report)


if __name__ == "__main__":
    main()
