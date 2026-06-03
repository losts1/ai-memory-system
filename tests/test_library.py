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


def test_metadata_importable():
    from ai_memory.metadata import apply_metadata_only, apply_fields_filter, make_teaser
    assert callable(apply_metadata_only)
    assert callable(apply_fields_filter)
    assert callable(make_teaser)


def test_apply_metadata_only_strips_to_teaser():
    from ai_memory.metadata import apply_metadata_only
    result = apply_metadata_only({
        "name": "Gamma Parameter",
        "summary": "A" * 200,
        "key_points": ["point1", "point2"],
        "score": 0.9,
        "source": "neo4j://Fact/Gamma-Parameter",
    })
    assert result["name"] == "Gamma Parameter"
    assert len(result["teaser"]) <= 153  # 150 chars + "..."
    assert result["kp_count"] == 2
    assert result["score"] == 0.9


def test_apply_fields_filter():
    from ai_memory.metadata import apply_fields_filter
    result = apply_fields_filter({"name": "X", "score": 0.5, "extra": "y"}, ["name", "score"])
    assert result == {"name": "X", "score": 0.5}
    assert "extra" not in result


def test_make_teaser_truncates():
    from ai_memory.metadata import make_teaser
    long = "word " * 100
    t = make_teaser(long)
    assert t.endswith("...")
    assert len(t) <= 153


def test_make_teaser_short_unchanged():
    from ai_memory.metadata import make_teaser
    assert make_teaser("short") == "short"
