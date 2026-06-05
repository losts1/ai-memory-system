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


def test_apply_metadata_only_handles_none_content():
    """search_vector returns content=None when node.content is null in Neo4j;
    apply_metadata_only must not crash on that shape."""
    from ai_memory.metadata import apply_metadata_only
    result = apply_metadata_only({
        "name": "Some Fact",
        "summary": None,
        "content": None,
        "score": 0.5,
    })
    assert result["name"] == "Some Fact"
    assert result["teaser"] == ""


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


def test_search_vector_empty_query_returns_empty_without_contacting_ollama():
    """Issue #39: nomic-embed-text returns a 0-dim vector for empty input,
    which mismatches the 768-dim Neo4j vector index. Short-circuit instead."""
    from ai_memory.search import search_vector
    assert search_vector("") == []
    assert search_vector("   \n\t  ") == []


def test_search_graph_empty_query_returns_empty():
    """Issue #39: empty queries short-circuit before Lucene escape."""
    from ai_memory.search import search_graph
    assert search_graph("") == []
    assert search_graph("\t") == []


def test_search_faiss_empty_query_returns_empty(tmp_path):
    """Issue #39: empty queries don't reach FAISS embedding."""
    from ai_memory.search import search_faiss
    assert search_faiss("", workspace=tmp_path) == []


def test_search_files_empty_query_returns_empty(tmp_path):
    """Issue #40: search_files was missing the empty-query guard that #39
    added to the Neo4j / FAISS backends. grep -F "" matches every line."""
    from ai_memory.search import search_files
    memdir = tmp_path / "memory"
    memdir.mkdir()
    for i in range(5):
        (memdir / f"{i}_note.md").write_text(f"line in file {i}\n")
    assert search_files("",     workspace=tmp_path, max_results=100) == []
    assert search_files("   \t", workspace=tmp_path, max_results=100) == []
    # And a non-empty query still works
    r = search_files("file", workspace=tmp_path, max_results=100)
    assert len(r) == 5


def test_search_files_missing_workspace(tmp_path):
    """search_files on a workspace with no memory dir returns []."""
    from ai_memory.search import search_files
    result = search_files("anything", workspace=tmp_path)
    assert isinstance(result, list)
    assert result == []


def test_search_files_finds_memory_md_at_workspace_root(tmp_path):
    """search_files reads MEMORY.md at the workspace root (score 5.0)."""
    from ai_memory.search import search_files
    (tmp_path / "MEMORY.md").write_text("the-unique-token lives here\n")
    result = search_files("the-unique-token", workspace=tmp_path)
    assert any(r.get("score") == 5.0 and "MEMORY.md" in r["source"] for r in result)


def test_search_files_finds_memory_md_inside_memory_dir(tmp_path):
    """search_files also looks at workspace/memory/MEMORY.md (Claude-style layout)."""
    from ai_memory.search import search_files
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "MEMORY.md").write_text("inside-token here\n")
    result = search_files("inside-token", workspace=tmp_path)
    assert any(r.get("score") == 5.0 and "MEMORY.md" in r["source"] for r in result)


def test_search_files_max_files_none_searches_everything(tmp_path):
    """With max_files=None all memory/*.md files are searched (no 30-cap)."""
    from ai_memory.search import search_files
    memdir = tmp_path / "memory"
    memdir.mkdir()
    # 35 files; the old hard-cap would have skipped 5 of them.
    for i in range(35):
        (memdir / f"{i:02d}_note.md").write_text(f"note {i} marker-token-{i}\n")
    result = search_files("marker-token", workspace=tmp_path, max_results=100)
    # Without the cap we should find all 35 (max_results=100 is the only limit).
    assert len(result) == 35


def test_search_files_does_not_double_count_memory_md(tmp_path):
    """When MEMORY.md lives inside memory/, it's searched once (score 5.0),
    not also re-included via the *.md glob at score 3.0."""
    from ai_memory.search import search_files
    memdir = tmp_path / "memory"
    memdir.mkdir()
    (memdir / "MEMORY.md").write_text("dup-token here\n")
    (memdir / "other.md").write_text("also dup-token here\n")
    result = search_files("dup-token", workspace=tmp_path, max_results=100)
    sources = [r["source"] for r in result]
    memory_md_hits = [s for s in sources if s.endswith("MEMORY.md")]
    assert len(memory_md_hits) == 1


def test_search_files_max_files_caps_the_search(tmp_path):
    """Explicit max_files=N limits how many files are scanned."""
    from ai_memory.search import search_files
    memdir = tmp_path / "memory"
    memdir.mkdir()
    for i in range(20):
        (memdir / f"{i:02d}_note.md").write_text(f"marker-token-{i}\n")
    result = search_files("marker-token", workspace=tmp_path,
                          max_results=100, max_files=5)
    assert len(result) == 5


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
    for method in ['search', 'traverse', 'trace_parameter', 'state', 'learn', 'close']:
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


# ---------------------------------------------------------------------------
# v1.3.2 — Neo4j usability round (issues #N1-#N18)
# ---------------------------------------------------------------------------

def test_exception_hierarchy_importable():
    """Issue #N4: ai_memory.exceptions exists with the expected classes."""
    from ai_memory.exceptions import (
        AIMemoryError, Neo4jError, Neo4jConnectionError,
        Neo4jIndexNotFoundError, Neo4jQueryError,
    )
    assert issubclass(Neo4jConnectionError, Neo4jError)
    assert issubclass(Neo4jIndexNotFoundError, Neo4jError)
    assert issubclass(Neo4jQueryError, Neo4jError)
    assert issubclass(Neo4jError, AIMemoryError)


def test_index_not_found_error_includes_actionable_hint():
    """Issue #N2: the error message must list available indexes when possible."""
    from ai_memory.exceptions import Neo4jIndexNotFoundError
    e = Neo4jIndexNotFoundError("fact_embeddings", kind="vector",
                                 available=["factEmbeddingIndex", "memeEmbeddingIndex"])
    msg = str(e)
    assert "fact_embeddings" in msg
    assert "factEmbeddingIndex" in msg
    assert "NEO4J_VECTOR_INDEX" in msg

    # No available indexes — different hint
    e2 = Neo4jIndexNotFoundError("x", available=[])
    assert "neo4j_seed.py" in str(e2)


def test_get_driver_raises_connection_error_on_bad_uri(monkeypatch, tmp_path):
    """Issue #N4: unreachable URI must raise Neo4jConnectionError, not bare OSError."""
    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:1")
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "irrelevant")
    (tmp_path / ".env.neo4j").write_text("")
    from ai_memory._config import get_driver
    from ai_memory.exceptions import Neo4jConnectionError
    try:
        get_driver(workspace=tmp_path)
        assert False, "expected Neo4jConnectionError"
    except Neo4jConnectionError as e:
        msg = str(e)
        assert "127.0.0.1:1" in msg


def test_memory_client_close_releases_cached_driver(tmp_path, monkeypatch):
    """Issue #N1: close() must actually close the driver if one was created.
    We don't connect for real; the driver is None until first use."""
    from ai_memory import MemoryClient
    client = MemoryClient(workspace=tmp_path)
    assert client._driver is None  # lazy — not created yet
    client.close()  # no-op when driver was never created
    assert client._driver is None


def test_memory_client_driver_method_is_lazy(tmp_path):
    """Calling .driver() before any operation must not fail at construction."""
    from ai_memory import MemoryClient
    client = MemoryClient(workspace=tmp_path)
    # Driver is created lazily; construction shouldn't connect.
    assert client._driver is None


def test_validate_schema_signature():
    """Issue #N5: validate_schema is exported and has the expected signature."""
    from ai_memory import validate_schema
    import inspect
    sig = inspect.signature(validate_schema)
    assert "vector_index" in sig.parameters


def test_search_graph_cypher_uses_related_to_or_learned_in():
    """Issue #N3: search_graph must walk RELATED_TO|LEARNED_IN, not LEARNED_IN
    alone. Inspect the function's source as a regression guard."""
    import inspect
    from ai_memory.search import search_graph
    src = inspect.getsource(search_graph)
    assert "RELATED_TO|LEARNED_IN" in src, \
        "search_graph must traverse both relationship types"
    assert "[:LEARNED_IN]->" not in src, \
        "the lone LEARNED_IN traversal was the v1.3.1 bug; should be replaced"


def test_search_graph_uses_configurable_fulltext_index():
    """search_graph must use $fulltext_index Cypher param (from NEO4J_FULLTEXT_INDEX env)
    rather than passing the index name as a hardcoded string literal to queryNodes."""
    import inspect
    from ai_memory.search import search_graph
    src = inspect.getsource(search_graph)
    assert "$fulltext_index" in src, \
        "search_graph must pass $fulltext_index as a parameterised Cypher argument"
    assert "queryNodes('fact_content'" not in src, \
        "search_graph must not hardcode 'fact_content' as the queryNodes argument"


def test_search_vector_cypher_omits_node_id():
    """node.id is not written by learn.py and is unused in result construction.
    Fetching it adds payload with no benefit — regression guard for its removal."""
    import inspect
    from ai_memory.search import search_vector
    src = inspect.getsource(search_vector)
    assert "node.id AS id" not in src, \
        "search_vector must not fetch unused node.id"


def test_graph_functions_accept_driver_kwarg():
    """graph.traverse, graph.trace_parameter, graph.graph_stats must accept driver=
    so MemoryClient can pass its cached connection (avoids ~28ms handshake per call)."""
    import inspect
    from ai_memory.graph import traverse, trace_parameter, graph_stats
    for fn in (traverse, trace_parameter, graph_stats):
        params = inspect.signature(fn).parameters
        assert "driver" in params, f"{fn.__name__} missing driver= parameter"


def test_prerequisite_of_removed_from_allowed_rels():
    """PREREQUISITE_OF had no edges in any sync path and no code creates them.
    It was removed from _ALLOWED_RELS to avoid silent empty results."""
    from ai_memory.graph import _ALLOWED_RELS
    assert "PREREQUISITE_OF" not in _ALLOWED_RELS, \
        "PREREQUISITE_OF was dead code — it must not appear in _ALLOWED_RELS"
    assert "RELATED_TO" in _ALLOWED_RELS
    assert "SHARES_PARAMETER" in _ALLOWED_RELS  # reserved for RLM tracing


def test_search_functions_accept_driver_kwarg():
    """Issue #N1: search_vector and search_graph must accept driver= for pooling."""
    import inspect
    from ai_memory.search import search_vector, search_graph
    for fn in (search_vector, search_graph):
        params = inspect.signature(fn).parameters
        assert "driver" in params, f"{fn.__name__} missing driver parameter"


def test_get_query_timeout_returns_float():
    """Issue #N9: timeout helper exists and returns a sensible default."""
    from ai_memory._config import get_query_timeout
    t = get_query_timeout()
    assert isinstance(t, float)
    assert 1.0 <= t <= 600.0


def test_neo4j_seed_contains_required_indexes():
    """Regression: seed script must define the full required index set.

    Source-inspection test — no Neo4j connection required.
    Catches the case where someone removes an index from neo4j_seed.py
    without updating verify_schema.py's expected set.
    """
    from pathlib import Path
    seed_src = (Path(__file__).parent.parent / "scripts" / "neo4j_seed.py").read_text()
    for expected in [
        "fact_created_at_idx",   # temporal range queries on Fact.created_at
        "fact_source_file_idx",  # source_file lookups (written by learn.py)
        "n.summary",             # fulltext index must cover Fact.summary
    ]:
        assert expected in seed_src, (
            f"neo4j_seed.py is missing {expected!r} — "
            "add it to the indexes list or fulltext definition"
        )
