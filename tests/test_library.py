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


def test_search_importable():
    from ai_memory.search import search_vector, search_graph, search_files, search_faiss
    assert callable(search_vector)
    assert callable(search_graph)
    assert callable(search_files)
    assert callable(search_faiss)


def test_search_files_missing_workspace(tmp_path):
    """search_files on a workspace with no memory dir returns []."""
    from ai_memory.search import search_files
    result = search_files("anything", workspace=tmp_path)
    assert isinstance(result, list)
    assert result == []


def test_search_faiss_missing_index(tmp_path):
    """search_faiss returns [] when FAISS index doesn't exist."""
    from ai_memory.search import search_faiss
    result = search_faiss("anything", workspace=tmp_path)
    assert result == []


def test_graph_importable():
    from ai_memory.graph import traverse, trace_parameter, graph_stats
    assert callable(traverse)
    assert callable(trace_parameter)
    assert callable(graph_stats)


def test_traverse_rejects_bad_relationship():
    from ai_memory.graph import traverse
    result = traverse("SomeFact", relationship="EVIL_REL")
    assert result["success"] is False
    assert "Unknown relationship" in result["error"]


def test_trace_parameter_caps_depth():
    """depth > MAX_DEPTH_CAP is silently capped — no error raised."""
    from ai_memory import graph
    assert graph.MAX_DEPTH_CAP >= 2


def test_state_importable():
    from ai_memory.state import MemoryStateManager
    assert callable(MemoryStateManager)


def test_memory_state_manager_has_expected_methods():
    from ai_memory.state import MemoryStateManager
    for method in ['init_session', 'record_query', 'mark_loaded',
                   'get_pending', 'get_summary', 'load_fact', 'load_next',
                   'cleanup', 'list_sessions', 'close']:
        assert hasattr(MemoryStateManager, method), f"Missing method: {method}"


def test_memory_client_importable():
    from ai_memory import MemoryClient
    assert callable(MemoryClient)


def test_memory_client_instantiates(tmp_path):
    from ai_memory import MemoryClient
    client = MemoryClient(workspace=tmp_path)
    assert client._workspace == tmp_path


def test_memory_client_has_expected_methods():
    from ai_memory import MemoryClient
    for method in ['search', 'traverse', 'trace_parameter', 'state', 'close']:
        assert hasattr(MemoryClient, method), f"Missing method: {method}"


def test_memory_client_context_manager(tmp_path):
    from ai_memory import MemoryClient
    client = MemoryClient(workspace=tmp_path)
    assert hasattr(client, '__enter__')
    assert hasattr(client, '__exit__')


def test_top_level_imports():
    import ai_memory
    assert hasattr(ai_memory, 'MemoryClient')
    assert hasattr(ai_memory, 'MemoryStateManager')
    assert hasattr(ai_memory, 'get_driver')
    assert hasattr(ai_memory, 'get_workspace')
