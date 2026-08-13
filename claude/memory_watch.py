#!/usr/bin/env python3
"""Per-turn state watcher — records when watched state moves.

WHY
---
On 2026-08-12 a `git push` resolved a local/origin divergence and the memory
describing it silently became false. Nothing connected the action to the memory:
no git hook, no PreToolUse/PostToolUse hook, and `memory_audit.py` is age-based
so a fact that dies in one instant is invisible to it.

This closes that gap on the detection side. It answers one question cheaply —
"did anything watched move since the last turn?" — and records the answer. It
does not decide which memories are affected; that is phase D.

WHY PER-TURN IS AFFORDABLE
--------------------------
Claude Code's `Stop` hook fires per-turn. `queue_session.sh` collapses that to
once per session by deduping against `.distill_processed`; this script does not
dedupe. Measured cost 2026-08-13: ~45 ms, at ~100 turns/session = ~4.5 s total.

WHAT IS AND IS NOT FINGERPRINTED
--------------------------------
Config *mtimes* are deliberately NOT used. Measured 2026-08-13: 4 of 8 maker
configs changed mtime within 45 seconds, because USDManager rewrites
`order_size_usd` and the inventory monitor rewrites the pad fields continuously.
Fingerprinting mtime would fire on nearly every turn and drown the signal.

Instead a whitelist of *stable* keys is hashed — settings a human or a deploy
changes, not ones a bot rewrites on a timer.

WHAT THIS NEVER DOES
--------------------
It never writes a memory. The fingerprint sees `git 80/15 -> 0/2` and has no way
to judge whether that is worth remembering; writing one memory per state change
would have produced dozens in a single session (8 restarts, ~12 commits,
continuous config churn). 171 curated memories are useful; 10,000 auto-generated
deltas are a log — and `.state_changes` already is that log. Selection is what
makes a memory valuable, and 45 ms of hashing cannot do selection.

It must also never break a session: every failure path exits 0.

Usage (normally invoked as a Stop hook with JSON on stdin):
    python3 memory_watch.py --show     # current fingerprint, no write
    python3 memory_watch.py --log      # recent recorded changes
"""
import glob
import hashlib
import json
import os
import subprocess
import sys
import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
FINGERPRINT = os.path.join(HERE, ".state_fingerprint")
CHANGES = os.path.join(HERE, ".state_changes")
TRADING = "/home/lost/trading"

# Config keys a human or a deploy changes. Deliberately excludes order_size_usd
# (USDManager) and buy_pad_bps/sell_pad_bps (inventory monitor) — those are
# rewritten on a timer and would fire the watcher constantly.
STABLE_KEYS = (
    "spread_bps", "blaze_enabled", "blaze_distance_bps", "blaze_timeout",
    "max_probes", "cycle_max_age_s", "reserve_enabled", "reserve_balance_usd",
    "maker_fee_bps", "trail_distance_bps", "min_profit_bps",
    "post_sell_guard_enabled", "post_sell_guard_start_bps",
)


def _sh(cmd, timeout=3):
    try:
        return subprocess.run(cmd, shell=True, capture_output=True,
                              text=True, timeout=timeout).stdout.strip()
    except Exception:
        return ""


def fingerprint():
    """Cheap snapshot of watched state. Values are human-readable so a change
    can be reported as old -> new rather than as two opaque hashes."""
    fp = {}

    div = _sh(f"git -C {TRADING} rev-list --left-right --count origin/master...HEAD")
    head = _sh(f"git -C {TRADING} rev-parse --short HEAD")
    fp["git"] = f"{div.replace(chr(9), '/')}@{head}" if div or head else "?"

    units = _sh("systemctl --user list-units 'kraken-*' --state=active "
                "--no-legend --plain | awk '{print $1}' | sort | tr '\\n' ','")
    fp["services"] = units or "?"

    stable = {}
    for path in sorted(glob.glob(os.path.join(TRADING, "kraken-maker-*", "config.json"))):
        bot = os.path.basename(os.path.dirname(path))
        try:
            cfg = json.load(open(path))
            stable[bot] = {k: cfg.get(k) for k in STABLE_KEYS if k in cfg}
        except Exception:
            stable[bot] = "unreadable"
    blob = json.dumps(stable, sort_keys=True)
    fp["config"] = hashlib.sha256(blob.encode()).hexdigest()[:12]
    return fp


def _load(path, default):
    try:
        return json.load(open(path))
    except Exception:
        return default


def _atomic_write(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, sort_keys=True)
    os.replace(tmp, path)


def run(session_id=""):
    now = fingerprint()
    prev = _load(FINGERPRINT, None)

    if prev is None:                       # first run: establish a baseline silently
        _atomic_write(FINGERPRINT, now)
        return 0

    moved = {k: (prev.get(k), v) for k, v in now.items() if prev.get(k) != v}
    if moved:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(CHANGES, "a") as f:
            for subsystem, (old, new) in moved.items():
                f.write(json.dumps({
                    "ts": stamp, "subsystem": subsystem,
                    "old": old, "new": new, "session": session_id,
                }) + "\n")
        _atomic_write(FINGERPRINT, now)
    return 0


def main():
    if "--show" in sys.argv:
        print(json.dumps(fingerprint(), indent=2))
        return 0
    if "--log" in sys.argv:
        if not os.path.exists(CHANGES):
            print("no changes recorded yet")
            return 0
        for line in open(CHANGES).readlines()[-20:]:
            try:
                d = json.loads(line)
                print(f"  {d['ts']}  {d['subsystem']:<9} {str(d['old'])[:36]} -> {str(d['new'])[:36]}")
            except Exception:
                pass
        return 0

    # Stop-hook mode: JSON on stdin, same shape queue_session.sh consumes.
    session_id = ""
    try:
        data = json.loads(sys.stdin.read() or "{}")
        if data.get("stop_hook_active"):    # recursion guard, as in queue_session.sh
            return 0
        session_id = data.get("session_id", "") or ""
    except Exception:
        pass
    return run(session_id)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        # A watcher must never break a session. Failures are silent by design.
        sys.exit(0)
