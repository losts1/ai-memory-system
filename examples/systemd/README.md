# Scheduling `ai-memory nightly` with a systemd user timer

`ai-memory nightly` publishes the boilerplate set, re-embeds every Fact against it, rebuilds
the `RELATED_TO` edge layer under a new `rule_version` and deletes edges from older rules
(spec §7.5). Nothing schedules it by default; these two units run it daily at 03:30 local time.

## Install

```bash
# 1. numpy is needed by the edge rebuild (optional extra `edges`)
/path/to/ai-memory-system/venv/bin/pip install 'ai-memory-system[edges]'

# 2. copy the units and adjust the three paths in the .service file
mkdir -p ~/.config/systemd/user
cp examples/systemd/ai-memory-nightly.service examples/systemd/ai-memory-nightly.timer ~/.config/systemd/user/
#    - Environment=AI_MEMORY_DIR=…   directory holding .env.neo4j
#    - WorkingDirectory=…            your checkout
#    - ExecStart=…/venv/bin/ai-memory the venv with the edges extra

# 3. preview the edge rebuild, run the job once by hand, then enable the timer.
#    `ai-memory nightly` has NO dry mode: `systemctl start` below is a real re-embed
#    and edge cutover. The only preview is the edge step alone:
/path/to/ai-memory-system/venv/bin/ai-memory edges --dry-run
systemctl --user daemon-reload
systemctl --user start ai-memory-nightly.service     # REAL run (publish + re-embed + cutover); watch with journalctl
systemctl --user enable --now ai-memory-nightly.timer
systemctl --user list-timers ai-memory-nightly.timer
```

## Check

```bash
journalctl --user -u ai-memory-nightly.service -n 40
ai-memory stats        # edges_stale_rule must be 0 and rule_version incremented after each run
```

The JSON report of the last run lands in `~/.ai-memory/nightly-last.json`.
A failed re-embed stops the job before the edge rebuild (exit 1); the previous edges stay in place.
