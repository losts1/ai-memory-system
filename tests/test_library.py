from pathlib import Path


def test_config_importable():
    from ai_memory._config import get_workspace, get_driver
    assert callable(get_workspace)
    assert callable(get_driver)


def test_get_workspace_default(monkeypatch):
    monkeypatch.delenv("AI_MEMORY_DIR", raising=False)
    from ai_memory._config import get_workspace
    ws = get_workspace()
    assert isinstance(ws, Path)
    assert ws == Path.home() / ".ai-memory"


def test_get_workspace_explicit(tmp_path):
    from ai_memory._config import get_workspace
    ws = get_workspace(str(tmp_path))
    assert ws == tmp_path


def test_get_workspace_from_path_object(tmp_path):
    from ai_memory._config import get_workspace
    ws = get_workspace(tmp_path)
    assert ws == tmp_path


def test_get_driver_raises_without_password(monkeypatch, tmp_path):
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)
    (tmp_path / ".env.neo4j").write_text("")  # empty — no password
    from ai_memory._config import get_driver
    try:
        get_driver(workspace=tmp_path)
        assert False, "expected ValueError"
    except (ValueError, Exception) as e:
        assert "NEO4J_PASSWORD" in str(e) or "password" in str(e).lower() or "neo4j" in str(e).lower()
