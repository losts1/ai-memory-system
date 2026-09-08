from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


def test_migrate_cli_dispatch_and_exit_codes(monkeypatch, tmp_path, capsys):
    import neo4j_migrate_vector_filters as M
    seen = {}
    monkeypatch.setattr(M, "_open_driver", lambda: object())
    monkeypatch.setattr(M.VI, "preflight", lambda drv, index, props, **kw: seen.update(kind="preflight", index=index, props=tuple(props)) or {"ok": True, "index": index})
    monkeypatch.setattr(M.VI, "migrate", lambda drv, index, props, **kw: seen.update(kind="migrate", dry=kw.get("dry_run")) or {"ok": False, "index": index})
    assert M.main(["--preflight", "--index", "idx", "--props", "assistant,space"]) == 0
    assert seen == {"kind": "preflight", "index": "idx", "props": ("assistant", "space")}
    out = tmp_path / "r.json"
    assert M.main(["--migrate", "--dry-run", "--index", "idx", "--json", str(out)]) == 1
    assert seen["kind"] == "migrate" and seen["dry"] is True
    assert json.loads(out.read_text())["ok"] is False


def test_migrate_cli_requires_exactly_one_mode():
    import neo4j_migrate_vector_filters as M
    with pytest.raises(SystemExit):
        M.main([])
    with pytest.raises(SystemExit):
        M.main(["--preflight", "--migrate"])


def test_migrate_cli_dry_run_rejected_with_preflight(monkeypatch, capsys):
    import neo4j_migrate_vector_filters as M
    monkeypatch.setattr(M, "_open_driver", lambda: object())
    with pytest.raises(SystemExit) as ei:
        M.main(["--preflight", "--dry-run"])
    assert ei.value.code == 2
    assert "--dry-run applies to --migrate only" in capsys.readouterr().err


def test_migrate_cli_rejects_empty_props(monkeypatch, capsys):
    import neo4j_migrate_vector_filters as M
    monkeypatch.setattr(M, "_open_driver", lambda: object())
    with pytest.raises(SystemExit) as ei:
        M.main(["--migrate", "--props", "  ,  "])
    assert ei.value.code == 2
    assert "--props must name at least one property" in capsys.readouterr().err


def test_migrate_cli_reports_json_and_exits_1_on_exception_without_traceback(monkeypatch, tmp_path, capsys):
    import neo4j_migrate_vector_filters as M
    monkeypatch.setattr(M, "_open_driver", lambda: object())

    def boom(drv, index, props, **kw):
        raise RuntimeError("index dropped, boom")

    monkeypatch.setattr(M.VI, "migrate", boom)
    out = tmp_path / "err.json"
    rc = M.main(["--migrate", "--index", "idx", "--json", str(out)])
    assert rc == 1
    printed = json.loads(capsys.readouterr().out)
    assert printed == {"ok": False, "mode": "migrate", "index": "idx", "error": "index dropped, boom"}
    assert json.loads(out.read_text()) == printed


def test_migrate_cli_index_default_resolves_after_driver_loads_env(monkeypatch):
    """--index must default from NEO4J_VECTOR_INDEX as loaded by get_driver()'s
    load_dotenv(<workspace>/.env.neo4j), not from the environment at argparse time
    (which runs before the driver is opened)."""
    import neo4j_migrate_vector_filters as M
    seen = {}

    def fake_open_driver():
        monkeypatch.setenv("NEO4J_VECTOR_INDEX", "liveIdx")
        return object()

    monkeypatch.setattr(M, "_open_driver", fake_open_driver)
    monkeypatch.setattr(M.VI, "preflight", lambda drv, index, props, **kw: seen.update(index=index) or {"ok": True, "index": index})

    assert M.main(["--preflight"]) == 0
    assert seen["index"] == "liveIdx"

    assert M.main(["--preflight", "--index", "cli"]) == 0
    assert seen["index"] == "cli"
