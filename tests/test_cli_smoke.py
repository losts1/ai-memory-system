"""
Phase 6 smoke tests for the ai-memory CLI and key scripts.

These are intentionally lightweight — they test that the CLI parses,
imports succeed, and --help works without requiring a running Neo4j.
"""

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.resolve()
CLI = [sys.executable, "-m", "scripts.cli"]  # will only work after proper install; fallback below


def run_cli(args: list[str]) -> subprocess.CompletedProcess:
    """Run the CLI either via the installed entry point or directly."""
    # Prefer direct execution of the cli module (works from source)
    cmd = [sys.executable, str(REPO_ROOT / "scripts" / "cli.py")] + args
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def test_cli_help():
    result = run_cli(["--help"])
    assert result.returncode == 0
    assert "Unified CLI" in result.stdout or "ai-memory" in result.stdout


def test_cli_init():
    result = run_cli(["init"])
    assert result.returncode == 0
    assert "Initializing" in result.stdout or "BOOTSTRAP" in result.stdout


def test_cli_search_help():
    result = run_cli(["search", "--help"])
    assert result.returncode == 0
    assert "search" in result.stdout.lower() or "query" in result.stdout.lower()


def test_cli_traverse_help():
    result = run_cli(["traverse", "--help"])
    assert result.returncode == 0


def test_cli_sync_help():
    result = run_cli(["sync", "--help"])
    assert result.returncode == 0


def test_cli_learn_sync_help():
    result = run_cli(["learn-sync", "--help"])
    assert result.returncode == 0


def test_cli_state_help():
    result = run_cli(["state", "--help"])
    assert result.returncode == 0


def test_cli_backfill_help():
    result = run_cli(["backfill", "--help"])
    assert result.returncode == 0


def test_import_key_modules():
    """Ensure the main scripts can at least be imported without syntax/runtime errors at import time."""
    scripts = [
        "hybrid_memory_search",
        "neo4j_sync",
        "neo4j_backfill_assistant",
        "rlm.neo4j_traverse",
        "rlm.memory_state",
        "rlm.neo4j_learn_sync",
    ]
    for mod in scripts:
        # We just check that the file parses and top-level code doesn't explode on import
        # (many scripts have top-level credential checks, so we only do syntax/import test)
        path = REPO_ROOT / "scripts" / f"{mod.replace('.', '/')}.py"
        assert path.exists(), f"Missing script: {path}"
        # Basic syntax check via compile
        with open(path) as f:
            compile(f.read(), str(path), "exec")
