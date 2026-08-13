#!/usr/bin/env bash
#
# Claude Code Stop hook — queues the ending session for distillation.
#
# Claude Code calls this script when a session ends, passing a JSON object
# via stdin that includes "transcript_path" (the session JSONL file).
# This script appends that path to .distill_queue for later processing by
# distill.py --queue (run by ClaudeClaw cron or manually).
#
# Recursion prevention: if "stop_hook_active" is true in the stdin JSON
# (Claude Code sets this when a session is itself running inside a hook),
# we exit immediately to avoid infinite distillation loops.

MEMORY_DIR="/home/lost/.claude/projects/-home-lost/memory"
QUEUE_FILE="$MEMORY_DIR/.distill_queue"
PROCESSED_FILE="$MEMORY_DIR/.distill_processed"
LOG_FILE="$MEMORY_DIR/.queue.log"

# ── Read context from Claude Code stdin ────────────────────────────────────────

STDIN_DATA=$(cat 2>/dev/null)

# Parse fields from the JSON context Claude Code provides.
parse_field() {
    local field="$1"
    echo "$STDIN_DATA" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    val = data.get('$field', '')
    print(val if val is not False else '')
except Exception:
    print('')
" 2>/dev/null
}

TRANSCRIPT_PATH=$(parse_field "transcript_path")
STOP_HOOK_ACTIVE=$(parse_field "stop_hook_active")

# ── Recursion guard ────────────────────────────────────────────────────────────

if [ "$STOP_HOOK_ACTIVE" = "True" ] || [ "$STOP_HOOK_ACTIVE" = "true" ] || [ "$STOP_HOOK_ACTIVE" = "1" ]; then
    exit 0
fi

# ── Resolve JSONL path ─────────────────────────────────────────────────────────

# Prefer the path Claude Code gave us; fall back to the most recently modified
# JSONL in the projects directory.
if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
    TRANSCRIPT_PATH=$(ls -t /home/lost/.claude/projects/*/*.jsonl 2>/dev/null | head -1)
fi

if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [queue] No JSONL found, skipping." >> "$LOG_FILE"
    exit 0
fi

# ── Skip trivially small sessions ─────────────────────────────────────────────

# Sessions under 10KB are almost always system-only, empty, or meta-sessions
# (e.g., distillation prompts recorded as conversations). Skip them to avoid
# wasting a Claude Max API call that will return [].
FILE_SIZE=$(stat -c%s "$TRANSCRIPT_PATH" 2>/dev/null || echo 0)
if [ "$FILE_SIZE" -lt 10240 ]; then
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [queue] Skipping small session (${FILE_SIZE}B): $TRANSCRIPT_PATH" >> "$LOG_FILE"
    exit 0
fi

# ── Check if already processed or queued ──────────────────────────────────────

# Don't re-queue sessions that have already been successfully distilled.
# This prevents the stop hook from re-adding the same session every time
# a new conversation starts in the same Claude Code project.
if [ -f "$PROCESSED_FILE" ] && grep -qF "$TRANSCRIPT_PATH" "$PROCESSED_FILE" 2>/dev/null; then
    exit 0  # Already distilled
fi

if [ -f "$QUEUE_FILE" ] && grep -qF "$TRANSCRIPT_PATH" "$QUEUE_FILE" 2>/dev/null; then
    exit 0  # Already in queue
fi

# ── Enqueue ────────────────────────────────────────────────────────────────────

echo "$TRANSCRIPT_PATH" >> "$QUEUE_FILE"
echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) [queue] Queued: $TRANSCRIPT_PATH" >> "$LOG_FILE"

exit 0
