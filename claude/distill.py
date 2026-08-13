#!/usr/bin/env python3
"""
Session distillation: extract long-term memories from a Claude Code session.

Reads a session JSONL file, extracts the conversation, calls the local Claude Max
proxy (localhost:3456, claude-haiku-4) with a structured distillation prompt, and
writes the resulting memories to this directory. Also rebuilds MEMORY.md after each
run. Falls back to local Ollama (qwen3.5:9b-128k) if the Claude Max proxy is down.

Usage:
    python3 distill.py <session.jsonl>   # process one session directly
    python3 distill.py --queue           # process all sessions in .distill_queue
    python3 distill.py --rebuild-index   # just rebuild MEMORY.md, no distillation

Called automatically by user crontab (every 30 min) or manually after a session.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────────────

MEMORY_DIR      = Path(__file__).parent
QUEUE_FILE      = MEMORY_DIR / ".distill_queue"
PROCESSED_FILE  = MEMORY_DIR / ".distill_processed"
FAILURES_FILE   = MEMORY_DIR / ".distill_failures"   # path → failure count (JSON)
LOCK_FILE       = MEMORY_DIR / ".distilling"

# Sessions that fail this many times are skipped to unblock the queue.
MAX_RETRIES = 3

# Files that live here but are not memories.
NON_MEMORY = {
    "MEMORY.md", "README.md", "IMPLEMENT-MEMORY-SYSTEM.md",
    "search.py", "distill.py", "queue_session.sh",
    "memory_audit.py", ".memory_audit_report.md",
}

# Max conversation chars sent to distillation prompt (~12k tokens).
MAX_CONV_CHARS = 50_000

# Max messages extracted from JSONL (last N user+assistant pairs).
MAX_MESSAGES = 80

# Timeout for Claude Max proxy HTTP request (seconds).
DISTILL_TIMEOUT = 180


# ── JSONL parsing ─────────────────────────────────────────────────────────────

def _content_to_text(content) -> str:
    """
    Claude Code stores message content as either a plain string or a list of
    content blocks ({"type": "text", "text": "..."}, {"type": "tool_use", ...}).
    Extract only the text parts.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            block.get("text", "")
            for block in content
            if block.get("type") == "text"
        )
    return str(content)


def extract_conversation(jsonl_path: Path) -> str:
    """
    Parse a Claude Code session JSONL and return a readable conversation string.

    Includes only user and assistant messages. Skips:
    - Slash command invocations (<command-message> / <command-name>)
    - Tool call blocks (noisy, not useful for distillation)
    - Empty messages

    Truncates to MAX_MESSAGES × 2 entries and MAX_CONV_CHARS total.
    """
    messages = []

    with open(jsonl_path, encoding="utf-8") as f:
        for raw_line in f:
            raw_line = raw_line.strip()
            if not raw_line:
                continue
            try:
                entry = json.loads(raw_line)
            except json.JSONDecodeError:
                continue

            entry_type = entry.get("type")
            msg = entry.get("message", {})

            if entry_type == "user":
                text = _content_to_text(msg.get("content", "")).strip()
                # Skip internal command messages
                if not text or text.startswith("<command"):
                    continue
                messages.append(f"USER: {text[:1_000]}")

            elif entry_type == "assistant":
                text = _content_to_text(msg.get("content", "")).strip()
                if not text:
                    continue
                messages.append(f"ASSISTANT: {text[:600]}")

    # Take last MAX_MESSAGES entries, then truncate by char count
    tail = messages[-MAX_MESSAGES:]
    joined = "\n\n".join(tail)
    return joined[:MAX_CONV_CHARS]


# ── Distillation prompt ───────────────────────────────────────────────────────

_PROMPT_PREFIX = """\
You are extracting long-term memories from a completed Claude Code session.

A "memory" is information that will genuinely help Claude assist this user in a
FUTURE session — not in the session being analyzed. Ask yourself: "Would a Claude
instance with no other context benefit from knowing this?"

=== SAVE: what qualifies ===
• User preferences, expertise, working style that generalise across all sessions
• Explicit corrections ("don't do X", "stop Y") — highest priority
• Confirmed approaches the user validated ("yes exactly", "perfect")
• Ongoing project context: key decisions, constraints, external deadlines
• Reference pointers: where specific information lives (service URLs, file paths
  to configs, external tools) that Claude would otherwise have to rediscover

=== DO NOT SAVE ===
• Which file was edited or which bug was fixed (ephemeral task state)
• Things already documented in CLAUDE.md (codebase structure, commands)
• Things obvious from the code itself
• Generic coding advice or obvious best practices
• Session-specific details irrelevant in 2 weeks

=== MEMORY TYPES ===
user       — who the user is: role, expertise, preferences, working style
feedback   — how Claude should behave: corrections + validated approaches
project    — ongoing work: decisions, architecture, external constraints
reference  — where to find things in external systems

=== OUTPUT FORMAT — CRITICAL ===
Output ONLY a raw JSON array. No prose, no explanations, no markdown, no text before or after.
If nothing is worth saving, output exactly two characters: []
Do not explain your reasoning. Do not describe what you found. Only output the JSON array.

Each element when saving:
{
  "action": "create",
  "type": "user|feedback|project|reference",
  "filename": "descriptive_slug.md",
  "name": "Memory name (≤60 chars)",
  "description": "One-line index entry (≤120 chars)",
  "body": "Full memory body.\\nFor feedback/project types include **Why:** and **How to apply:** lines."
}

=== SESSION ===
"""

def build_prompt(conversation: str, session_date: str = "") -> str:
    # Concatenate directly — do NOT use .format() or f-strings here.
    # Sessions routinely contain JSON/code with {curly braces} which would
    # cause KeyError if passed through str.format().
    header = f"Session date: {session_date}\n\n" if session_date else ""
    return _PROMPT_PREFIX + header + conversation


# ── Calling distillation backends ─────────────────────────────────────────────

# Primary: local Claude Max proxy (OpenAI-compatible, backed by Claude.ai Max subscription).
CLAUDE_MAX_URL = "http://localhost:3456/v1/chat/completions"
DISTILL_MODEL  = "claude-haiku-4"

# Fallback: local Ollama model — used when Claude Max proxy is unreachable.
OLLAMA_FALLBACK_URL   = "http://localhost:11434/api/chat"
OLLAMA_FALLBACK_MODEL = "qwen3.5:9b-128k"
OLLAMA_TIMEOUT        = 300  # seconds — local inference is slower than the proxy


def _parse_distillation_output(output: str, backend: str) -> list[dict] | None:
    """
    Parse raw LLM text into a list of memory action dicts.

    Returns:
      list[dict]  — actions extracted (may be [])
      None        — unrecoverable parse failure
    """
    # Strip outermost markdown code fence if present (not re.MULTILINE — avoids
    # stripping backticks inside JSON body fields containing code examples).
    output = re.sub(r"\A\s*```(?:json)?\s*\n", "", output)
    output = re.sub(r"\n```\s*\Z",             "", output)
    output = output.strip()

    try:
        actions = json.loads(output)
        if not isinstance(actions, list):
            print(f"[distill] [{backend}] Unexpected response shape (not a list)", file=sys.stderr)
            return None
        return actions  # may be [] — "nothing to save" is a valid LLM response
    except json.JSONDecodeError as e:
        # If the output contains no JSON array bracket at all, the model returned
        # prose instead of JSON — treat as "nothing to save" rather than a hard failure.
        if "[" not in output:
            print(f"[distill] [{backend}] Model returned prose (no JSON array) — treating as nothing to save.")
            return []
        print(f"[distill] [{backend}] JSON parse error: {e}", file=sys.stderr)
        print(f"[distill] [{backend}] Raw output: {output[:500]}", file=sys.stderr)
        return None


def _call_claude_max(prompt: str) -> str:
    """
    Call the Claude Max proxy. Returns the raw text response.
    Raises on any error (connection failure, HTTP error, bad response shape).
    """
    import urllib.request
    payload = json.dumps({
        "model": DISTILL_MODEL,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        CLAUDE_MAX_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=DISTILL_TIMEOUT) as resp:
        data = json.loads(resp.read())

    if "error" in data:
        raise RuntimeError(f"proxy error: {data['error']}")
    if not data.get("choices"):
        raise RuntimeError(f"no choices in response: {str(data)[:200]}")
    return data["choices"][0]["message"]["content"].strip()


def _call_ollama(prompt: str) -> str:
    """
    Call the local Ollama fallback model. Returns the raw text response.
    Raises on any error.
    """
    import urllib.request
    payload = json.dumps({
        "model": OLLAMA_FALLBACK_MODEL,
        "stream": False,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()
    req = urllib.request.Request(
        OLLAMA_FALLBACK_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=OLLAMA_TIMEOUT) as resp:
        data = json.loads(resp.read())

    return data["message"]["content"].strip()


def run_distillation(conversation: str, session_date: str = "") -> list[dict] | None:
    """
    Distill a conversation into memory actions.

    Tries the Claude Max proxy first; if that raises any exception (proxy down,
    connection refused, timeout), falls back to the local Ollama model.

    Returns:
      list[dict]  — memory actions (may be empty if LLM found nothing to save)
      None        — both backends failed (caller should NOT mark as processed)
    """
    prompt = build_prompt(conversation, session_date)

    # --- Primary: Claude Max proxy ---
    try:
        output = _call_claude_max(prompt)
        return _parse_distillation_output(output, "claude-max")
    except Exception as e:
        print(f"[distill] Claude Max proxy unavailable ({e}), trying Ollama fallback…", file=sys.stderr)

    # --- Fallback: local Ollama ---
    try:
        output = _call_ollama(prompt)
        return _parse_distillation_output(output, "ollama")
    except Exception as e:
        print(f"[distill] Ollama fallback also failed: {e}", file=sys.stderr)
        return None


# ── Applying memory actions ───────────────────────────────────────────────────

_MEMORY_TEMPLATE = """\
---
name: {name}
description: {description}
metadata:
  type: {type}
---

{body}
"""


def apply_action(action: dict, session_mtime: float | None = None) -> bool:
    """
    Write or update a memory file. Returns True if a file was written.

    Validates required fields. If the target file already exists AND was modified
    after the session being distilled (i.e. a human/agent hand-edited it since),
    the distillation is treated as a *suggestion*, not an overwrite: it is written
    to a `<filename>.suggested` sidecar and the live file is left untouched.

    Rationale: distillation regenerates bodies from a lossy, tool-stripped
    transcript, so exact paths/commands that lived in tool I/O are frequently lost
    and hallucinated back to conventional defaults. A later hand-edit is
    authoritative and must not be clobbered.
    """
    required = {"action", "type", "filename", "name", "description", "body"}
    if not required.issubset(action):
        missing = required - action.keys()
        print(f"[distill] Skipping action with missing fields: {missing}", file=sys.stderr)
        return False

    mem_type = action["type"]
    if mem_type not in {"user", "feedback", "project", "reference"}:
        print(f"[distill] Unknown memory type: {mem_type!r}", file=sys.stderr)
        return False

    filename = action["filename"]
    # Safety: keep only safe filename characters
    filename = re.sub(r"[^\w\-.]", "_", filename)
    if not filename.endswith(".md"):
        filename += ".md"

    target = MEMORY_DIR / filename
    content = _MEMORY_TEMPLATE.format(
        name        = action["name"],
        description = action["description"],
        type        = mem_type,
        body        = action["body"].strip(),
    )

    # Protect files hand-edited after the session — write a suggestion sidecar
    # instead of overwriting. (Ongoing sessions whose JSONL is still being appended
    # are the one case this can't distinguish; distilling a live session is itself
    # the anomaly.)
    if (
        target.exists()
        and session_mtime is not None
        and target.stat().st_mtime > session_mtime
    ):
        sidecar = target.with_name(target.name + ".suggested")
        sidecar.write_text(content, encoding="utf-8")
        print(
            f"[distill] Skipped (hand-edited after session): {filename} "
            f"→ wrote {sidecar.name} for review"
        )
        return False

    verb = "Updated" if target.exists() else "Created"
    target.write_text(content, encoding="utf-8")
    print(f"[distill] {verb}: {filename}")
    return True


# ── MEMORY.md index ───────────────────────────────────────────────────────────

_TYPE_TAG = {
    "user":      "[U]",
    "feedback":  "[F]",
    "project":   "[P]",
    "reference": "[R]",
}

# MEMORY.md is auto-loaded into every session and truncated past ~24.4KB, so the
# index hook is capped here. Full description stays in the memory file; the index
# is only a pointer. Cap chosen to keep the whole index under budget at ~145 files
# (was 85 at ~130 files; lowered 2026-07-13 when 140 files overflowed the budget).
INDEX_HOOK_MAX = 42  # lowered 55→42 on 2026-08-04: reclaim durable margin under the 24.4KB auto-load cap; titles dominate line length, pruning resolved memories remains the long-term fix.


def _index_hook(description: str) -> str:
    """Trim a memory's description to a short index hook at a word boundary."""
    d = description.strip()
    if len(d) >= 2 and d[0] == d[-1] == '"':   # drop wrapping quotes the distiller adds
        d = d[1:-1].strip()
    if len(d) <= INDEX_HOOK_MAX:
        return d
    return d[:INDEX_HOOK_MAX].rsplit(" ", 1)[0].rstrip(" ,;.—-") + "…"


def _stale_marker(content: str) -> str:
    """Return a visible confidence flag for the index line, or "".

    The failure this exists for: on 2026-08-12 a push resolved a repo divergence
    and the memory describing it stayed present-tense. Its index line still read
    "is 80 commits behind origin", and MEMORY.md is what loads into every
    session — so the stale claim was the first thing read and acted on. A queue
    nobody reads would not have prevented that; the warning has to be where the
    reading happens.

    Three sources, checked in order of authority:

      1. `stale_since:`         written by the state watcher when a fingerprint moves
      2. `decay:` + `observed:` decay classes (never/event/slow/fast/live)
      3. `volatility:`+`verified:`  legacy scheme, still used by memory_audit.py

    Both schemes are supported so migration can be gradual; 13 memories carried
    the legacy fields on 2026-08-13 and are not broken by this.

    This never alters the memory's claim — it only marks confidence in it.
    """
    import datetime as _dt

    m = re.search(r"^\s*stale_since:\s*(\S+)", content, re.M)
    if m:
        return f"  **STALE since {m.group(1)[:10]} — re-derive**"

    def _age(field):
        v = re.search(rf"^\s*{field}:\s*(\S+)", content, re.M)
        if not v:
            return None
        try:
            d = _dt.datetime.fromisoformat(v.group(1).replace("Z", "+00:00"))
        except ValueError:
            return None
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return (_dt.datetime.now(_dt.timezone.utc) - d).days

    decay = re.search(r"^\s*decay:\s*(\w+)", content, re.M)
    if decay:
        cls = decay.group(1).lower()
        if cls == "never":
            return ""                       # a past-tense record cannot rot
        if cls == "event":
            return ""                       # only stale_since flags these; no time component
        if cls == "live":
            return "  **LIVE — derive, do not trust this line**"
        ttl = {"fast": 7, "slow": 90}.get(cls)
        age = _age("observed")
        if ttl and age is not None and age > ttl:
            return f"  **UNVERIFIED {age}d (ttl {ttl}d)**"
        return ""

    vol = re.search(r"^\s*volatility:\s*(\w+)", content, re.M)
    if vol:
        ttl = {"high": 3, "medium": 7, "low": 30}.get(vol.group(1).lower())
        age = _age("verified")
        if ttl and age is not None and age > ttl:
            return f"  **UNVERIFIED {age}d (ttl {ttl}d)**"
    return ""


def rebuild_index() -> None:
    """
    Rebuild MEMORY.md from all .md files in the memory directory.

    Each entry is prefixed with a type tag ([U]ser, [F]eedback, [P]roject, [R]eference)
    so Claude can scan the index without opening files. Project entries also include
    the file's modification date so stale memories are immediately visible.
    """
    import datetime

    # Non-recursive by design: archive/ holds resolved memories and must not be
    # indexed. PLAN-* are design documents that live here for convenience.
    files = sorted(
        f for f in MEMORY_DIR.glob("*.md")
        if f.name not in NON_MEMORY and not f.name.startswith("PLAN-")
    )

    entries = []
    for f in files:
        content = f.read_text(encoding="utf-8")
        name        = f.stem.replace("_", " ")
        description = ""
        mem_type    = ""

        if content.startswith("---"):
            name_m = re.search(r"^name:\s*(.+)$",        content, re.MULTILINE)
            desc_m = re.search(r"^description:\s*(.+)$", content, re.MULTILINE)
            type_m = re.search(r"^[ \t]*type:\s*(.+)$",  content, re.MULTILINE)
            if name_m:
                name = name_m.group(1).strip()
            if desc_m:
                description = desc_m.group(1).strip()
            if type_m:
                mem_type = type_m.group(1).strip()

        tag = _TYPE_TAG.get(mem_type, "   ")

        # Show a date for project and feedback memories — both decay over time.
        # Reference and user memories are more timeless so dates add noise there.
        #
        # Prefer the date the FACT was observed over the file's mtime. mtime is a
        # file property, not a claim property: editing a memory for any reason —
        # adding a tag, fixing a typo — resets it and makes a stale fact look
        # fresh. Measured 2026-08-13: coinbase_zec_bot_stopped carried
        # `verified: 2026-05-09` while its index line read (2026-08-13), because
        # it had been touched that morning to add a volatility tag. 96 days of
        # staleness rendered as same-day.
        date_suffix = ""
        if mem_type in ("project", "feedback"):
            observed = re.search(r"^\s*(?:observed|verified):\s*(\d{4}-\d\d-\d\d)",
                                 content, re.MULTILINE)
            mdate = (observed.group(1) if observed
                     else datetime.date.fromtimestamp(f.stat().st_mtime))
            date_suffix = f" ({mdate})"

        # MEMORY.md is a POINTER index, not a summary. Measured 2026-08-13: the
        # description hook was 31% of the file's bytes (6,331 of 19,870) and pushed
        # it to 91% of the 24.4KB auto-load cap. Dropping it returns the file to
        # ~65% and saves ~1,580 tokens of EVERY session's context. Nothing is lost —
        # the description still lives in each memory's frontmatter, one Read away.
        line = f"- {tag} [{name}]({f.name}){date_suffix}{_stale_marker(content)}"
        entries.append(line)

    index_content = "\n".join(entries) + "\n"
    (MEMORY_DIR / "MEMORY.md").write_text(index_content, encoding="utf-8")
    print(f"[distill] MEMORY.md rebuilt: {len(entries)} entr{'y' if len(entries)==1 else 'ies'}")


# ── Session processing ────────────────────────────────────────────────────────

def process_session(jsonl_path: Path) -> bool:
    """
    Distill one session JSONL → apply memory actions → return True on success.
    """
    if not jsonl_path.exists():
        print(f"[distill] JSONL not found: {jsonl_path}", file=sys.stderr)
        return False

    print(f"[distill] Processing: {jsonl_path.name}")

    # Derive session date from the JSONL file's modification time.
    import datetime
    session_date = datetime.date.fromtimestamp(jsonl_path.stat().st_mtime).isoformat()

    conversation = extract_conversation(jsonl_path)
    if not conversation.strip():
        print("[distill] No conversation content extracted, skipping.")
        return True  # Not an error — just an empty/system-only session

    actions = run_distillation(conversation, session_date)
    if actions is None:
        print("[distill] Distillation failed (request/parse error).", file=sys.stderr)
        return False  # Don't mark as processed — allow retry
    if not actions:
        print("[distill] No memories to save.")
        rebuild_index()  # Always rebuild — manual deletions must be reflected
        return True

    session_mtime = jsonl_path.stat().st_mtime
    saved = 0
    for action in actions:
        if apply_action(action, session_mtime):
            saved += 1

    print(f"[distill] Saved {saved} memory file(s).")
    rebuild_index()  # Always rebuild regardless of save count
    return True


# ── Queue processing ──────────────────────────────────────────────────────────

def process_queue() -> None:
    """
    Process all sessions listed in .distill_queue that haven't been processed yet.
    Uses a lock file to prevent concurrent runs. Marks processed sessions in
    .distill_processed so they're skipped on subsequent runs.
    """
    if LOCK_FILE.exists():
        age = time.time() - LOCK_FILE.stat().st_mtime
        if age < 600:  # 10 min — stale lock if older
            print("[distill] Another distillation is running, exiting.")
            return
        print("[distill] Removing stale lock file.")
        LOCK_FILE.unlink()

    if not QUEUE_FILE.exists():
        print("[distill] Queue is empty.")
        return

    queued = [
        l.strip() for l in QUEUE_FILE.read_text().splitlines()
        if l.strip()
    ]

    processed = set()
    if PROCESSED_FILE.exists():
        processed = set(PROCESSED_FILE.read_text().splitlines())

    pending = [p for p in queued if p not in processed]
    if not pending:
        # Queue may still have stale entries even if nothing is pending —
        # prune it now so it doesn't grow indefinitely.
        still_queued = [p for p in queued if p not in processed]
        if len(still_queued) < len(queued):
            QUEUE_FILE.write_text("\n".join(still_queued) + ("\n" if still_queued else ""))
        print("[distill] All queued sessions already processed.")
        return

    # Load per-session failure counts so repeatedly failing sessions don't block the queue.
    failures: dict[str, int] = {}
    if FAILURES_FILE.exists():
        try:
            failures = json.loads(FAILURES_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            failures = {}

    print(f"[distill] Processing {len(pending)} queued session(s).")
    LOCK_FILE.write_text(str(time.time()))

    try:
        newly_processed = []
        for path_str in pending:
            fail_count = failures.get(path_str, 0)
            if fail_count >= MAX_RETRIES:
                print(
                    f"[distill] Giving up on {Path(path_str).name} "
                    f"(failed {fail_count}×) — marking processed.",
                    file=sys.stderr,
                )
                newly_processed.append(path_str)
                failures.pop(path_str, None)
                continue

            success = process_session(Path(path_str))
            if success:
                newly_processed.append(path_str)
                failures.pop(path_str, None)  # reset on success
            else:
                failures[path_str] = fail_count + 1
                print(
                    f"[distill] {Path(path_str).name} failed "
                    f"({failures[path_str]}/{MAX_RETRIES})",
                    file=sys.stderr,
                )

        # Persist updated failure counts.
        if failures:
            FAILURES_FILE.write_text(json.dumps(failures, indent=2))
        elif FAILURES_FILE.exists():
            FAILURES_FILE.unlink()

        if newly_processed:
            processed.update(newly_processed)
            still_pending = [p for p in queued if p not in processed]
            QUEUE_FILE.write_text("\n".join(still_pending) + ("\n" if still_pending else ""))
            PROCESSED_FILE.write_text("\n".join(sorted(processed)) + "\n")

    finally:
        LOCK_FILE.unlink(missing_ok=True)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Distill Claude Code session memories",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "session",
        nargs="?",
        type=Path,
        help="Path to session .jsonl file to process directly",
    )
    parser.add_argument(
        "--queue",
        action="store_true",
        help="Process all sessions in .distill_queue",
    )
    parser.add_argument(
        "--rebuild-index",
        action="store_true",
        help="Rebuild MEMORY.md without distilling",
    )
    args = parser.parse_args()

    if args.rebuild_index:
        rebuild_index()
        return

    if args.queue:
        process_queue()
        return

    if args.session:
        ok = process_session(args.session)
        sys.exit(0 if ok else 1)

    parser.print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
