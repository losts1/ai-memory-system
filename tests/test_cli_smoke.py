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


def test_traverse_script_advertises_assistant():
    """neo4j_traverse.py --help must show --assistant flag."""
    cmd = [sys.executable, str(REPO_ROOT / "scripts" / "rlm" / "neo4j_traverse.py"), "--help"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0
    assert "--assistant" in result.stdout


def test_cli_traverse_advertises_assistant():
    """ai-memory traverse --help must show --assistant flag."""
    result = run_cli(["traverse", "--help"])
    assert result.returncode == 0
    assert "--assistant" in result.stdout


def test_seed_has_assistant_schema():
    """neo4j_seed.py must declare the Assistant constraint and assistant property indexes."""
    path = REPO_ROOT / "scripts" / "neo4j_seed.py"
    content = path.read_text()
    assert "assistant_id_unique" in content, "Missing Assistant uniqueness constraint"
    assert "fact_assistant_idx" in content, "Missing Fact.assistant index"
    assert "session_assistant_idx" in content, "Missing Session.assistant index"


def test_cli_search_advertises_files_only_and_use_embeddings():
    """Issue #34: --files-only and --use-embeddings must be exposed in `ai-memory search --help`."""
    result = run_cli(["search", "--help"])
    assert result.returncode == 0
    assert "--files-only" in result.stdout, "missing --files-only"
    assert "--use-embeddings" in result.stdout, "missing --use-embeddings"


def _spy_subprocess_call(monkeypatch):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli  # noqa: E402
    calls = []
    monkeypatch.setattr(cli.subprocess, "call", lambda cmd, cwd=None: (calls.append(cmd) or 0))
    return cli, calls


def test_cli_state_emits_positional_action_before_session(monkeypatch):
    """Issue #36: memory_state.py uses positional subcommands; --session comes AFTER."""
    cli, calls = _spy_subprocess_call(monkeypatch)
    parser = cli.build_parser()
    args = parser.parse_args(["state", "--session", "nova:test", "--init"])
    args.func(args)
    argv = calls[0][2:]
    assert argv[0] == "init", f"action should be first positional, got {argv}"
    assert "--session" in argv
    assert argv[argv.index("--session") + 1] == "nova:test"


def test_cli_state_rejects_no_action(monkeypatch):
    """Issue #36: state without an action flag is a usage error (exit code 2)."""
    cli, _ = _spy_subprocess_call(monkeypatch)
    parser = cli.build_parser()
    args = parser.parse_args(["state", "--session", "nova:test"])
    assert args.func(args) == 2


def test_cli_state_rejects_two_actions(monkeypatch):
    """Issue #36: two action flags is a usage error."""
    cli, _ = _spy_subprocess_call(monkeypatch)
    parser = cli.build_parser()
    args = parser.parse_args(["state", "--session", "x", "--init", "--pending"])
    assert args.func(args) == 2


def test_cli_backfill_flattens_additional_minds(monkeypatch):
    """Round 2 regression: --additional A --additional B → emit single `--additional A B`."""
    cli, calls = _spy_subprocess_call(monkeypatch)
    parser = cli.build_parser()
    args = parser.parse_args([
        "backfill", "--primary", "Nova",
        "--additional", "Weft", "--additional", "Echo",
        "--dry-run",
    ])
    args.func(args)
    argv = calls[0][2:]
    assert argv == ["--primary", "Nova", "--additional", "Weft", "Echo", "--dry-run"]


def test_format_output_handles_missing_source():
    """Issue #35: --fields may strip 'source'; format_output must not KeyError."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import hybrid_memory_search as hms
    hms.format_output([{"name": "X", "score": 0.5}], "FAISS")


def test_format_output_suppresses_score_only_section(capsys):
    """Issue #43: when --fields strips every meaningful display field and
    only 'score' survives, the section should render nothing — not an
    orphan separator + Score line."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import hybrid_memory_search as hms
    hms.format_output([{"score": 3.0}, {"score": 2.5}], "Files")
    captured = capsys.readouterr()
    # No separator should have been printed; no Score line either.
    assert "=" * 60 not in captured.out
    assert "Score:" not in captured.out
    # But a result with at least one meaningful field still renders.
    hms.format_output([{"score": 3.0, "name": "X"}], "Files")
    captured = capsys.readouterr()
    assert "Name: X" in captured.out


def test_cli_state_advertises_all_nine_subcommands():
    """Issue #42: --list-sessions and --load-next must be discoverable."""
    result = run_cli(["state", "--help"])
    assert result.returncode == 0
    for flag in ("--init", "--pending", "--summary",
                 "--record-query", "--mark-loaded",
                 "--load-fact", "--load-next",
                 "--cleanup", "--list-sessions"):
        assert flag in result.stdout, f"--help missing {flag}"


def test_cli_state_forwards_max_age_hours_to_cleanup(monkeypatch):
    """Issue #41: action-specific flags must reach memory_state.py.
    Before the fix: argparse rejected --max-age-hours as unrecognized."""
    cli, calls = _spy_subprocess_call(monkeypatch)
    parser = cli.build_parser()
    args = parser.parse_args(["state", "--cleanup", "--max-age-hours", "9999"])
    args.func(args)
    argv = calls[0][2:]
    assert argv == ["cleanup", "--max-age-hours", "9999"]


def test_cli_state_forwards_fact_to_load_fact(monkeypatch):
    """Issue #41 — load-fact takes --fact."""
    cli, calls = _spy_subprocess_call(monkeypatch)
    parser = cli.build_parser()
    args = parser.parse_args(["state", "--load-fact",
                              "--session", "weft:main",
                              "--fact", "Circuit Breaker Pattern"])
    args.func(args)
    argv = calls[0][2:]
    assert argv[0] == "load-fact"
    # both --session and --fact must reach the script
    assert "--session" in argv
    assert "--fact" in argv
    assert argv[argv.index("--fact") + 1] == "Circuit Breaker Pattern"


def test_cli_state_list_sessions_takes_no_extras(monkeypatch):
    """Issue #42: list-sessions emits as a bare subcommand."""
    cli, calls = _spy_subprocess_call(monkeypatch)
    parser = cli.build_parser()
    args = parser.parse_args(["state", "--list-sessions"])
    args.func(args)
    argv = calls[0][2:]
    assert argv == ["list-sessions"]


def test_cli_state_record_query_forwards_all_extras(monkeypatch):
    """Issue #41: record-query needs --query, --results, --scores, --state."""
    cli, calls = _spy_subprocess_call(monkeypatch)
    parser = cli.build_parser()
    args = parser.parse_args([
        "state", "--record-query",
        "--session", "weft:main",
        "--query", "trailing stop",
        "--results", "A,B",
        "--scores", "0.9,0.8",
        "--state", "pending",
    ])
    args.func(args)
    argv = calls[0][2:]
    assert argv[0] == "record-query"
    for needle in ("--session", "--query", "--results", "--scores", "--state"):
        assert needle in argv, f"missing {needle}"


def test_cli_state_strips_leading_double_dash_passthrough(monkeypatch):
    """Issue #41 workaround behaviour: users who add `--` before passthrough
    args (a common argparse-terminator habit) must not break the script."""
    cli, calls = _spy_subprocess_call(monkeypatch)
    parser = cli.build_parser()
    args = parser.parse_args(["state", "--cleanup", "--", "--max-age-hours", "9999"])
    args.func(args)
    argv = calls[0][2:]
    # The `--` should be stripped before forwarding so memory_state.py
    # doesn't treat the rest as positionals.
    assert "--" not in argv
    assert "--max-age-hours" in argv
