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


def test_cli_search_advertises_space_and_mode():
    r = run_cli(["search", "--help"])
    assert r.returncode == 0
    assert "--space" in r.stdout and "--mode" in r.stdout


def test_hybrid_script_advertises_space_and_mode():
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "scripts/hybrid_memory_search.py", "--help"], capture_output=True, text=True)
    assert r.returncode == 0
    assert "--space" in r.stdout and "--mode" in r.stdout


def test_hybrid_script_graph_help_text_is_mode_alias():
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "scripts/hybrid_memory_search.py", "--help"], capture_output=True, text=True)
    assert r.returncode == 0
    assert "alias for --mode hybrid" in r.stdout


def test_cli_eval_help():
    r = run_cli(["eval", "--help"])
    assert r.returncode == 0
    assert "--golden" in r.stdout and "--rankers" in r.stdout and "--label" in r.stdout


def test_cli_eval_dispatches_own_flags_before_argparse(monkeypatch):
    """C1: `ai-memory eval --label` must not hit the top-level argparse, which
    has no --label of its own — dispatch happens on argv[0] == 'eval'."""
    from ai_memory.eval import harness

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    captured = {}

    def fake_main(argv):
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(harness, "main", fake_main)
    rc = cli.main(["eval", "--golden", "x.json", "--label", "--rankers", "legacy"])
    assert rc == 0
    assert captured["argv"] == ["--golden", "x.json", "--label", "--rankers", "legacy"]


def test_cli_eval_edges_dispatches_before_argparse(monkeypatch):
    """`ai-memory eval-edges` must dispatch on argv[0] before the top-level
    argparse parser sees it, the same way `eval` does (C1)."""
    from ai_memory.eval import edges

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    captured = {}

    def fake_main(argv):
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(edges, "main", fake_main)
    rc = cli.main(["eval-edges", "--legacy", "--sample", "10"])
    assert rc == 0
    assert captured["argv"] == ["--legacy", "--sample", "10"]


def test_cli_embed_all_calls_embed_all(monkeypatch, capsys):
    import cli

    import ai_memory.embed as E
    seen = {}
    monkeypatch.setattr(cli, "_open_driver", lambda: object())
    monkeypatch.setattr(E, "embed_all", lambda drv, **kw: seen.update(kw) or {"facts": 2, "embedded": 2, "cas_skipped": 0, "embed_failed": 0, "missing": 0, "skipped_fresh": 0, "config_version": 3, "grams": 5})
    assert cli.main(["embed", "--all", "--keep-prev"]) == 0
    assert seen["keep_prev"] is True and seen["publish"] is True and seen["stale_only"] is False
    out = capsys.readouterr().out
    assert "embedded" in out and "config_version" in out


def test_cli_embed_prints_cas_skipped_names_after_table(monkeypatch, capsys):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    import ai_memory.embed as E
    monkeypatch.setattr(cli, "_open_driver", lambda: object())
    result = {"facts": 2, "embedded": 1, "cas_skipped": 1, "embed_failed": 0, "missing": 0,
              "skipped_fresh": 0, "config_version": 3, "grams": 5, "cas_skipped_names": ["Foo", "Bar"]}
    monkeypatch.setattr(E, "embed_all", lambda drv, **kw: result)
    assert cli.main(["embed", "--all"]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert not any(line.startswith("cas_skipped_names") for line in lines)
    assert lines[-2:] == ["Foo", "Bar"]


def test_cli_embed_stale_only_implies_no_publish(monkeypatch, capsys):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    import ai_memory.embed as E
    seen = {}
    monkeypatch.setattr(cli, "_open_driver", lambda: object())
    monkeypatch.setattr(E, "embed_all", lambda drv, **kw: seen.update(kw) or {"facts": 1, "embedded": 0, "cas_skipped": 0, "embed_failed": 0, "missing": 0, "skipped_fresh": 1, "config_version": 5, "grams": 3})
    assert cli.main(["embed", "--stale-only"]) == 0
    assert seen["publish"] is False and seen["stale_only"] is True and seen["keep_prev"] is False


def test_cli_embed_modes_are_exclusive_and_required():
    import cli
    import pytest
    with pytest.raises(SystemExit):
        cli.main(["embed"])
    with pytest.raises(SystemExit):
        cli.main(["embed", "--all", "--rollback"])


def test_cli_embed_drop_and_rollback(monkeypatch, capsys):
    import cli

    import ai_memory.embed as E
    monkeypatch.setattr(cli, "_open_driver", lambda: object())
    monkeypatch.setattr(E, "drop_prev", lambda drv: 7)
    monkeypatch.setattr(E, "rollback_prev", lambda drv: 4)
    assert cli.main(["embed", "--drop-prev"]) == 0 and "7" in capsys.readouterr().out
    assert cli.main(["embed", "--rollback"]) == 0 and "4" in capsys.readouterr().out


def test_cli_stats_prints_table_and_json(monkeypatch, capsys, tmp_path):
    import json

    import cli

    import ai_memory.embed as E
    st = {"facts": 10, "with_embedding": 9, "without_embedding": 1, "foreign": 3, "wrong_model": 0, "stale": 2, "with_prev": 0, "isolated": 4, "config_version": 2}
    monkeypatch.setattr(cli, "_open_driver", lambda: object())
    monkeypatch.setattr(E, "vector_stats", lambda drv: st)
    out_path = tmp_path / "s.json"
    assert cli.main(["stats", "--json", str(out_path)]) == 0
    text = capsys.readouterr().out
    assert "foreign" in text and "isolated" in text
    assert json.loads(out_path.read_text()) == st


def test_cli_edges_modes_are_exclusive_and_required():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli
    import pytest
    with pytest.raises(SystemExit):
        cli.main(["edges"])
    with pytest.raises(SystemExit):
        cli.main(["edges", "--rebuild", "--dry-run"])


def test_cli_edges_dry_run_passes_dry_run_true_and_defaults(monkeypatch, capsys):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    import ai_memory.wordindex as W
    seen = {}
    report = {"n_facts": 5, "vocab": 10, "edge_floor": 0.5, "base": {}, "edges": 3, "edge_list": [],
              "isolated": 1, "isolated_pct": 20.0, "max_degree": 2, "p95_degree": 1.5,
              "dry_run": True, "rule_version_next": 4}
    monkeypatch.setattr(cli, "_open_driver", lambda: object())
    monkeypatch.setattr(W, "rebuild_edges", lambda drv, **kw: seen.update(kw) or report)
    assert cli.main(["edges", "--dry-run"]) == 0
    assert seen == {"seed": 0, "pairs": 40000, "k": 5, "dry_run": True}
    out = capsys.readouterr().out
    assert "rule_version_next" in out and "edges_deleted" not in out


def test_cli_edges_rebuild_passes_dry_run_false_and_custom_params(monkeypatch, capsys):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    import ai_memory.wordindex as W
    seen = {}
    report = {"n_facts": 5, "vocab": 10, "edge_floor": 0.5, "base": {}, "edges": 3, "edge_list": [],
              "isolated": 0, "isolated_pct": 0.0, "max_degree": 2, "p95_degree": 1.5,
              "dry_run": False, "rule_version": 9, "edges_written": 3, "edges_deleted": 2, "words_orphaned": 0}
    monkeypatch.setattr(cli, "_open_driver", lambda: object())
    monkeypatch.setattr(W, "rebuild_edges", lambda drv, **kw: seen.update(kw) or report)
    assert cli.main(["edges", "--rebuild", "--seed", "7", "--pairs", "1000", "--k", "3"]) == 0
    assert seen == {"seed": 7, "pairs": 1000, "k": 3, "dry_run": False}
    out = capsys.readouterr().out
    assert "rule_version" in out and "edges_deleted" in out and "rule_version_next" not in out


def test_cli_edges_writes_json_report(monkeypatch, tmp_path):
    import json

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    import ai_memory.wordindex as W
    report = {"n_facts": 2, "edges": 1, "isolated": 0, "isolated_pct": 0.0, "max_degree": 1,
              "p95_degree": 1.0, "edge_floor": 0.1, "dry_run": True, "rule_version_next": 1}
    monkeypatch.setattr(cli, "_open_driver", lambda: object())
    monkeypatch.setattr(W, "rebuild_edges", lambda drv, **kw: report)
    out_path = tmp_path / "e.json"
    assert cli.main(["edges", "--dry-run", "--json", str(out_path)]) == 0
    assert json.loads(out_path.read_text()) == report


def test_cli_edges_runtime_error_exits_1_no_traceback(monkeypatch, capsys):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    import ai_memory.wordindex as W

    def raise_it(drv, **kw):
        raise RuntimeError("numpy is required for the nightly edge rebuild")

    monkeypatch.setattr(cli, "_open_driver", lambda: object())
    monkeypatch.setattr(W, "rebuild_edges", raise_it)
    assert cli.main(["edges", "--dry-run"]) == 1
    err = capsys.readouterr().err
    assert "numpy is required for the nightly edge rebuild" in err


def test_cli_nightly_calls_embed_all_then_rebuild_edges_in_order(monkeypatch, capsys):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    import ai_memory.embed as E
    import ai_memory.wordindex as W
    order = []
    embed_report = {"facts": 2, "embedded": 2, "cas_skipped": 0, "embed_failed": 0, "missing": 0,
                     "skipped_fresh": 0, "config_version": 3, "grams": 5, "cas_skipped_names": []}
    edges_report = {"n_facts": 2, "edges": 1, "isolated": 0, "isolated_pct": 0.0, "max_degree": 1,
                     "p95_degree": 1.0, "edge_floor": 0.1, "dry_run": False, "rule_version": 4,
                     "edges_written": 1, "edges_deleted": 0, "words_orphaned": 0}

    def fake_embed_all(drv, **kw):
        order.append("embed")
        assert kw == {"publish": True}
        return embed_report

    def fake_rebuild(drv, **kw):
        order.append("edges")
        assert kw == {"seed": 0}
        return edges_report

    monkeypatch.setattr(cli, "_open_driver", lambda: object())
    monkeypatch.setattr(E, "embed_all", fake_embed_all)
    monkeypatch.setattr(W, "rebuild_edges", fake_rebuild)
    assert cli.main(["nightly"]) == 0
    assert order == ["embed", "edges"]
    out = capsys.readouterr().out
    assert "embedded" in out and "rule_version" in out


def test_cli_nightly_writes_combined_json(monkeypatch, tmp_path):
    import json

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    import ai_memory.embed as E
    import ai_memory.wordindex as W
    embed_report = {"facts": 1, "embedded": 1, "cas_skipped": 0, "embed_failed": 0, "missing": 0,
                     "skipped_fresh": 0, "config_version": 1, "grams": 0, "cas_skipped_names": []}
    edges_report = {"n_facts": 1, "edges": 0, "isolated": 1, "isolated_pct": 100.0, "max_degree": 0,
                     "p95_degree": 0.0, "edge_floor": 0.0, "dry_run": False, "rule_version": 1,
                     "edges_written": 0, "edges_deleted": 0, "words_orphaned": 0}
    monkeypatch.setattr(cli, "_open_driver", lambda: object())
    monkeypatch.setattr(E, "embed_all", lambda drv, **kw: embed_report)
    monkeypatch.setattr(W, "rebuild_edges", lambda drv, **kw: edges_report)
    out_path = tmp_path / "n.json"
    assert cli.main(["nightly", "--seed", "9", "--json", str(out_path)]) == 0
    data = json.loads(out_path.read_text())
    assert data == {"embed": embed_report, "edges": edges_report}


def test_cli_nightly_embed_failure_stops_before_rebuild(monkeypatch, capsys):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    import ai_memory.embed as E
    import ai_memory.wordindex as W
    called = []

    def raise_it(drv, **kw):
        raise RuntimeError("no RetrievalConfig node")

    monkeypatch.setattr(cli, "_open_driver", lambda: object())
    monkeypatch.setattr(E, "embed_all", raise_it)
    monkeypatch.setattr(W, "rebuild_edges", lambda drv, **kw: called.append(1))
    assert cli.main(["nightly"]) == 1
    assert called == []
    err = capsys.readouterr().err
    assert "no RetrievalConfig node" in err


def test_cli_nightly_edges_failure_reports_which_step(monkeypatch, capsys):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    import ai_memory.embed as E
    import ai_memory.wordindex as W
    embed_report = {"facts": 1, "embedded": 1, "cas_skipped": 0, "embed_failed": 0, "missing": 0,
                     "skipped_fresh": 0, "config_version": 1, "grams": 0, "cas_skipped_names": []}

    def raise_it(drv, **kw):
        raise RuntimeError("edge rebuild needs at least 2 embedded Facts")

    monkeypatch.setattr(cli, "_open_driver", lambda: object())
    monkeypatch.setattr(E, "embed_all", lambda drv, **kw: embed_report)
    monkeypatch.setattr(W, "rebuild_edges", raise_it)
    assert cli.main(["nightly"]) == 1
    err = capsys.readouterr().err
    assert "edge rebuild needs at least 2 embedded Facts" in err
    assert "edges" in err.lower()


def test_cli_nightly_help_documents_order():
    result = run_cli(["nightly", "--help"])
    assert result.returncode == 0
    assert "publish boilerplate + re-embed, then rebuild edges and cut over" in result.stdout


def test_hybrid_script_prints_semantic_result_header(monkeypatch, capsys):
    """M5: the Neo4j/hybrid path must go through the same section-header
    helper as the FAISS path, so 'Semantic result (...)' prints again —
    with the assistant tag, matching the pre-branch header text."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import hybrid_memory_search as hms

    monkeypatch.setattr(hms, "search_hybrid",
                        lambda *a, **kw: [{"name": "X", "teaser": "about X", "score": 1.0, "via": "vec"}])
    monkeypatch.setattr(hms, "search_files", lambda *a, **kw: [])
    monkeypatch.setattr(sys, "argv", ["hybrid_memory_search.py", "q", "--assistant", "Grok"])
    hms.main()
    out = capsys.readouterr().out
    assert "Semantic result (hybrid [Grok])" in out


# --- duplicates / supersede (phase 6, task 3) ---

class _FakeSession:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeDriver:
    def __init__(self):
        self.closed = False

    def session(self):
        return _FakeSession()

    def close(self):
        self.closed = True


def _canned_report(**summary_overrides):
    summary = {
        "groups": 1, "facts": 2, "handled": 0, "needs_owner_decision": 0,
        "suggested_commands": 1, "near_copy_pairs": 1, "supersedes_loaded": 0,
    }
    summary.update(summary_overrides)
    return {
        "generated_at": "2026-09-05T00:00:00+00:00",
        "n_facts": 2,
        "groups": [],
        "summary": summary,
        "params": {"threshold": 0.95, "k": 3, "index": "fact_embeddings"},
    }


def test_cli_duplicates_help():
    result = run_cli(["duplicates", "--help"])
    assert result.returncode == 0
    for flag in ("--cos", "--k", "--include-handled", "--index", "--json", "--markdown"):
        assert flag in result.stdout, f"--help missing {flag}"


def test_cli_supersede_help():
    result = run_cli(["supersede", "--help"])
    assert result.returncode == 0
    for flag in ("--from-file", "--by", "--apply"):
        assert flag in result.stdout, f"--help missing {flag}"


def test_cli_duplicates_passes_threshold_k_include_handled_index(monkeypatch, capsys):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    import ai_memory.duplicates as D
    seen = {}
    monkeypatch.setattr(cli, "_open_driver", lambda: _FakeDriver())
    monkeypatch.setattr(D, "duplicate_report", lambda drv, **kw: seen.update(kw) or _canned_report())
    rc = cli.main([
        "duplicates", "--cos", "0.9", "--k", "5", "--include-handled", "--index", "my_idx",
    ])
    assert rc == 0
    assert seen == {"threshold": 0.9, "k": 5, "include_handled": True, "index": "my_idx"}
    out = capsys.readouterr().out
    assert (
        "groups=1 facts=2 handled=0 needs_owner_decision=0 "
        "suggested_commands=1 near_copy_pairs=1 supersedes_loaded=0"
    ) in out


def test_cli_duplicates_defaults(monkeypatch):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    import ai_memory.duplicates as D
    seen = {}
    monkeypatch.setattr(cli, "_open_driver", lambda: _FakeDriver())
    monkeypatch.setattr(D, "duplicate_report", lambda drv, **kw: seen.update(kw) or _canned_report())
    assert cli.main(["duplicates"]) == 0
    assert seen == {"threshold": D.DUP_COS, "k": 3, "include_handled": False, "index": None}


def test_cli_duplicates_prints_markdown_when_no_markdown_flag(monkeypatch, capsys):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    import ai_memory.duplicates as D
    monkeypatch.setattr(cli, "_open_driver", lambda: _FakeDriver())
    monkeypatch.setattr(D, "duplicate_report", lambda drv, **kw: _canned_report())
    assert cli.main(["duplicates"]) == 0
    out = capsys.readouterr().out
    assert "# Duplicate Facts report" in out


def test_cli_duplicates_writes_json_and_markdown_files(monkeypatch, capsys, tmp_path):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import json

    import cli

    import ai_memory.duplicates as D
    report = _canned_report()
    monkeypatch.setattr(cli, "_open_driver", lambda: _FakeDriver())
    monkeypatch.setattr(D, "duplicate_report", lambda drv, **kw: report)
    json_path = tmp_path / "r.json"
    md_path = tmp_path / "r.md"
    rc = cli.main(["duplicates", "--json", str(json_path), "--markdown", str(md_path)])
    assert rc == 0
    assert json.loads(json_path.read_text()) == report
    assert "# Duplicate Facts report" in md_path.read_text()
    out = capsys.readouterr().out
    # summary line always prints; the markdown body must not also go to stdout
    # once it has been written to --markdown.
    assert "groups=1" in out
    assert "# Duplicate Facts report" not in out


def test_cli_duplicates_runtime_error_exits_1_no_traceback(monkeypatch, capsys):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    import ai_memory.duplicates as D

    def raise_it(drv, **kw):
        raise RuntimeError("no vector index")

    monkeypatch.setattr(cli, "_open_driver", lambda: _FakeDriver())
    monkeypatch.setattr(D, "duplicate_report", raise_it)
    assert cli.main(["duplicates"]) == 1
    err = capsys.readouterr().err
    assert "no vector index" in err


def test_cli_supersede_requires_pair_or_from_file():
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli
    assert cli.main(["supersede"]) == 2


def test_cli_supersede_pair_and_from_file_are_mutually_exclusive(tmp_path):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli
    decisions = tmp_path / "d.json"
    decisions.write_text("[]", encoding="utf-8")
    assert cli.main(["supersede", "A", "B", "--from-file", str(decisions)]) == 2


def test_cli_supersede_dry_run_prints_plan_and_exits_1_on_refused(monkeypatch, capsys, tmp_path):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    import ai_memory.duplicates as D
    decisions = tmp_path / "d.json"
    decisions.write_text(
        '[{"new": "A", "old": "B", "apply": true}, {"new": "C", "old": "D", "apply": true}]',
        encoding="utf-8",
    )
    plan = [
        {"new": "A", "old": "B", "ok": True, "reason": None},
        {"new": "C", "old": "D", "ok": False, "reason": "unknown old"},
    ]
    monkeypatch.setattr(cli, "_open_driver", lambda: _FakeDriver())
    monkeypatch.setattr(D, "load_supersedes_strict", lambda session: {})
    monkeypatch.setattr(D, "plan_supersedes", lambda session, pairs, supersedes: plan)
    called = []
    monkeypatch.setattr(D, "supersede_fact", lambda *a, **kw: called.append((a, kw)))
    rc = cli.main(["supersede", "--from-file", str(decisions)])
    assert rc == 1
    assert called == []
    out = capsys.readouterr().out
    assert "ok  A -> B  (apply)" in out
    assert "REFUSED  C -> D  [unknown old]  (skip)" in out


def test_cli_supersede_dry_run_exits_0_when_all_ok(monkeypatch, capsys):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    import ai_memory.duplicates as D
    plan = [{"new": "A", "old": "B", "ok": True, "reason": None}]
    monkeypatch.setattr(cli, "_open_driver", lambda: _FakeDriver())
    monkeypatch.setattr(D, "load_supersedes_strict", lambda session: {})
    monkeypatch.setattr(D, "plan_supersedes", lambda session, pairs, supersedes: plan)
    assert cli.main(["supersede", "A", "B"]) == 0


def test_cli_supersede_apply_calls_supersede_fact_only_for_ok_and_apply_rows(monkeypatch, capsys, tmp_path):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    import ai_memory.duplicates as D
    decisions = tmp_path / "d.json"
    decisions.write_text(
        '[{"new": "A", "old": "B", "apply": true}, '
        '{"new": "C", "old": "D", "apply": false}, '
        '{"new": "E", "old": "F", "apply": true}]',
        encoding="utf-8",
    )
    plan = [
        {"new": "A", "old": "B", "ok": True, "reason": None},
        {"new": "C", "old": "D", "ok": True, "reason": None},
        {"new": "E", "old": "F", "ok": False, "reason": "same fact"},
    ]
    monkeypatch.setattr(cli, "_open_driver", lambda: _FakeDriver())
    monkeypatch.setattr(D, "load_supersedes_strict", lambda session: {})
    monkeypatch.setattr(D, "plan_supersedes", lambda session, pairs, supersedes: plan)
    calls = []

    def fake_supersede_fact(session, new, old, *, by="ai-memory", now=None):
        calls.append((new, old, by))
        return {"new": new, "old": old, "at": "2026-09-05T00:00:00+00:00"}

    monkeypatch.setattr(D, "supersede_fact", fake_supersede_fact)
    rc = cli.main(["supersede", "--from-file", str(decisions), "--apply", "--by", "owner"])
    # E->F is refused (never applied); C->D is ok but apply:false (never applied);
    # only A->B is ok and apply:true.
    assert calls == [("A", "B", "owner")]
    assert rc == 1  # the refused E->F row still fails the run
    out = capsys.readouterr().out
    assert "applied  A -> B" in out


def test_cli_supersede_apply_positional_pair_counts_as_apply_flagged(monkeypatch, capsys):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    import ai_memory.duplicates as D
    plan = [{"new": "A", "old": "B", "ok": True, "reason": None}]
    monkeypatch.setattr(cli, "_open_driver", lambda: _FakeDriver())
    monkeypatch.setattr(D, "load_supersedes_strict", lambda session: {})
    monkeypatch.setattr(D, "plan_supersedes", lambda session, pairs, supersedes: plan)
    calls = []
    monkeypatch.setattr(
        D, "supersede_fact",
        lambda session, new, old, **kw: calls.append((new, old, kw)) or {"new": new, "old": old, "at": "t"},
    )
    rc = cli.main(["supersede", "A", "B", "--apply"])
    assert rc == 0
    assert calls == [("A", "B", {"by": "ai-memory"})]


# --- fix round 1: strict decisions-file validation, apply-flag strictness,
# per-row error naming, clean errors from load_supersedes_strict/plan_supersedes ---

def _assert_no_driver_opened(monkeypatch):
    """Fails the test if _open_driver is ever called."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    def boom():
        raise AssertionError("driver must not be opened for an invalid decisions file")

    monkeypatch.setattr(cli, "_open_driver", boom)
    return cli


def test_cli_supersede_from_file_invalid_json_errors_without_opening_driver(monkeypatch, capsys, tmp_path):
    cli = _assert_no_driver_opened(monkeypatch)
    bad = tmp_path / "d.json"
    bad.write_text("{not json", encoding="utf-8")
    rc = cli.main(["supersede", "--from-file", str(bad)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "decisions file" in err


def test_cli_supersede_from_file_not_a_list_errors_without_opening_driver(monkeypatch, capsys, tmp_path):
    cli = _assert_no_driver_opened(monkeypatch)
    bad = tmp_path / "d.json"
    bad.write_text('{"new": "A", "old": "B"}', encoding="utf-8")
    rc = cli.main(["supersede", "--from-file", str(bad)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "decisions file" in err


def test_cli_supersede_from_file_row_missing_old_errors_without_opening_driver(monkeypatch, capsys, tmp_path):
    cli = _assert_no_driver_opened(monkeypatch)
    bad = tmp_path / "d.json"
    bad.write_text('[{"new": "A"}]', encoding="utf-8")
    rc = cli.main(["supersede", "--from-file", str(bad)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "decisions file" in err
    assert "row 1" in err
    assert "old" in err


def test_cli_supersede_from_file_non_bool_apply_errors_without_opening_driver(monkeypatch, capsys, tmp_path):
    cli = _assert_no_driver_opened(monkeypatch)
    bad = tmp_path / "d.json"
    bad.write_text('[{"new": "A", "old": "B", "apply": "false"}]', encoding="utf-8")
    rc = cli.main(["supersede", "--from-file", str(bad)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "decisions file" in err
    assert "row 1" in err
    assert "apply" in err


def test_cli_supersede_from_file_invalid_input_never_calls_supersede_fact_even_with_apply(monkeypatch, tmp_path):
    cli = _assert_no_driver_opened(monkeypatch)
    import ai_memory.duplicates as D
    calls = []
    monkeypatch.setattr(D, "supersede_fact", lambda *a, **kw: calls.append((a, kw)))
    bad = tmp_path / "d.json"
    bad.write_text('[{"new": "A", "old": "B", "apply": "false"}]', encoding="utf-8")
    rc = cli.main(["supersede", "--from-file", str(bad), "--apply"])
    assert rc == 1
    assert calls == []


def test_cli_supersede_apply_flag_is_identity_true_not_truthiness(monkeypatch, tmp_path):
    """apply must be `is True`, not Python truthiness — a JSON string "true" or a
    nonempty-but-non-bool value must never be treated as an apply request."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    import ai_memory.duplicates as D
    decisions = tmp_path / "d.json"
    decisions.write_text('[{"new": "A", "old": "B", "apply": 1}]', encoding="utf-8")
    monkeypatch.setattr(cli, "_open_driver", lambda: _FakeDriver())
    monkeypatch.setattr(D, "load_supersedes_strict", lambda session: {})
    plan = [{"new": "A", "old": "B", "ok": True, "reason": None}]
    monkeypatch.setattr(D, "plan_supersedes", lambda session, pairs, supersedes: plan)
    calls = []
    monkeypatch.setattr(D, "supersede_fact", lambda *a, **kw: calls.append((a, kw)))
    rc = cli.main(["supersede", "--from-file", str(decisions)])
    # "apply": 1 is not valid JSON-bool per the strict validator, so this must be
    # a clean validation error, not a silently-applied row.
    assert rc == 1
    assert calls == []


def test_cli_supersede_apply_error_names_the_failing_row(monkeypatch, capsys):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    import ai_memory.duplicates as D
    plan = [{"new": "A", "old": "B", "ok": True, "reason": None}]
    monkeypatch.setattr(cli, "_open_driver", lambda: _FakeDriver())
    monkeypatch.setattr(D, "load_supersedes_strict", lambda session: {})
    monkeypatch.setattr(D, "plan_supersedes", lambda session, pairs, supersedes: plan)

    def raise_it(session, new, old, **kw):
        raise ValueError("old already superseded by X")

    monkeypatch.setattr(D, "supersede_fact", raise_it)
    rc = cli.main(["supersede", "A", "B", "--apply"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "A -> B" in err
    assert "old already superseded by X" in err


def test_cli_supersede_load_supersedes_strict_runtime_error_exits_1_no_traceback(monkeypatch, capsys):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    import ai_memory.duplicates as D

    def raise_it(session):
        raise RuntimeError("could not read SUPERSEDES map")

    monkeypatch.setattr(cli, "_open_driver", lambda: _FakeDriver())
    monkeypatch.setattr(D, "load_supersedes_strict", raise_it)
    rc = cli.main(["supersede", "A", "B"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "could not read SUPERSEDES map" in err


def test_cli_supersede_plan_supersedes_neo4j_error_exits_1_no_traceback(monkeypatch, capsys):
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli
    from neo4j.exceptions import ServiceUnavailable

    import ai_memory.duplicates as D

    monkeypatch.setattr(cli, "_open_driver", lambda: _FakeDriver())
    monkeypatch.setattr(D, "load_supersedes_strict", lambda session: {})

    def raise_it(session, pairs, supersedes):
        raise ServiceUnavailable("neo4j is down")

    monkeypatch.setattr(D, "plan_supersedes", raise_it)
    rc = cli.main(["supersede", "A", "B"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "neo4j is down" in err


def test_cli_nightly_embed_failed_count_stops_before_rebuild(monkeypatch, capsys):
    """review #1: a re-embed that reports failures must not cut over the edge layer
    (examples/systemd/README.md promises exit 1 before the rebuild)."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    import ai_memory.embed as E
    import ai_memory.wordindex as W
    called = []
    embed_report = {"facts": 3, "embedded": 1, "cas_skipped": 0, "embed_failed": 2, "missing": 0,
                     "skipped_fresh": 0, "config_version": 1, "grams": 0, "cas_skipped_names": []}
    monkeypatch.setattr(cli, "_open_driver", lambda: object())
    monkeypatch.setattr(E, "embed_all", lambda drv, **kw: embed_report)
    monkeypatch.setattr(W, "rebuild_edges", lambda drv, **kw: called.append(1))
    assert cli.main(["nightly"]) == 1
    assert called == []
    err = capsys.readouterr().err
    assert "embed_failed" in err and "2" in err


def test_cli_nightly_json_omits_edge_list_unless_dump_edges(monkeypatch, tmp_path):
    """review #1: the nightly report dumped the full edge list (2.6 MB on 4.5k edges)."""
    import json

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    import ai_memory.embed as E
    import ai_memory.wordindex as W
    embed_report = {"facts": 1, "embedded": 1, "cas_skipped": 0, "embed_failed": 0, "missing": 0,
                     "skipped_fresh": 0, "config_version": 1, "grams": 0, "cas_skipped_names": []}
    edges_report = {"n_facts": 2, "edges": 1, "edge_list": [{"a": "A", "b": "B", "weight": 1.0}],
                     "isolated": 0, "isolated_pct": 0.0, "max_degree": 1, "p95_degree": 1.0,
                     "edge_floor": 0.0, "dry_run": False, "rule_version": 1,
                     "edges_written": 1, "edges_deleted": 0, "words_orphaned": 0}
    monkeypatch.setattr(cli, "_open_driver", lambda: object())
    monkeypatch.setattr(E, "embed_all", lambda drv, **kw: embed_report)
    monkeypatch.setattr(W, "rebuild_edges", lambda drv, **kw: dict(edges_report))
    out_path = tmp_path / "n.json"
    assert cli.main(["nightly", "--json", str(out_path)]) == 0
    data = json.loads(out_path.read_text())
    assert "edge_list" not in data["edges"] and data["edges"]["edges"] == 1
    assert cli.main(["nightly", "--json", str(out_path), "--dump-edges"]) == 0
    assert json.loads(out_path.read_text())["edges"]["edge_list"] == edges_report["edge_list"]


def test_cli_eval_edges_is_a_real_subparser():
    """review #3: eval-edges was only an argv[0] intercept, absent from --help."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import cli

    ns = cli.build_parser().parse_args(["eval-edges"])
    assert ns.func is cli.cmd_eval_edges and ns.args == []
    result = run_cli(["--help"])
    assert "eval-edges" in result.stdout
