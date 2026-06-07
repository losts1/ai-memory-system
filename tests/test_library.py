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
    for method in ['search', 'traverse', 'trace_parameter', 'graph_stats',
                   'state', 'learn', 'close']:
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


def test_config_expected_constraints_matches_verify_schema():
    """_config.py EXPECTED_CONSTRAINTS must be consistent with scripts/verify_schema.py.
    Mirrors the indexes parity guard — constraints have two separate hardcoded sets."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    from ai_memory._config import EXPECTED_CONSTRAINTS as config_constraints
    from verify_schema import EXPECTED_CONSTRAINTS as vs_constraints
    assert set(config_constraints) == vs_constraints, (
        f"_config.py EXPECTED_CONSTRAINTS diverged from verify_schema.py:\n"
        f"  only in _config:      {set(config_constraints) - vs_constraints}\n"
        f"  only in verify_schema: {vs_constraints - set(config_constraints)}"
    )


def test_config_expected_indexes_matches_verify_schema():
    """_config.py EXPECTED_INDEXES must be consistent with scripts/verify_schema.py.
    Two schema validators diverged; this is the regression guard."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    from ai_memory._config import EXPECTED_INDEXES as config_indexes
    from verify_schema import EXPECTED_INDEXES as vs_indexes
    assert set(config_indexes) == vs_indexes, (
        f"_config.py EXPECTED_INDEXES diverged from verify_schema.py:\n"
        f"  only in _config:      {set(config_indexes) - vs_indexes}\n"
        f"  only in verify_schema: {vs_indexes - set(config_indexes)}"
    )


def test_config_expected_fulltext_props_matches_verify_schema():
    """_config.py EXPECTED_FULLTEXT_PROPS must match verify_schema.py."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    from ai_memory._config import EXPECTED_FULLTEXT_PROPS as config_props
    from verify_schema import EXPECTED_FULLTEXT_PROPS as vs_props
    assert config_props == vs_props, (
        f"_config.py EXPECTED_FULLTEXT_PROPS {config_props} != "
        f"verify_schema.py {vs_props}"
    )


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
    """PREREQUISITE_OF and HAS_WORD were removed from _ALLOWED_RELS:
    PREREQUISITE_OF — no edges exist; HAS_WORD — depth=1 yields Word nodes
    (not :Fact), depth=2 is an expensive RELATED_TO equivalent with undefined semantics."""
    from ai_memory.graph import _ALLOWED_RELS
    assert "PREREQUISITE_OF" not in _ALLOWED_RELS, \
        "PREREQUISITE_OF was dead code — it must not appear in _ALLOWED_RELS"
    assert "HAS_WORD" not in _ALLOWED_RELS, \
        "HAS_WORD has confusing Fact-traversal semantics — it must not appear in _ALLOWED_RELS"
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


# ---------------------------------------------------------------------------
# Task 2 — Frontmatter provenance parsing
# ---------------------------------------------------------------------------

def test_parse_provenance_frontmatter_full():
    """Nested provenance: block is extracted into a Provenance object."""
    from ai_memory.learn import _parse_provenance_frontmatter
    content = """\
---
name: Test Fact
provenance:
  source: web_fetch
  trust: suspicious
  risk_score: 47
  risk_band: medium
  signals: [exfiltration, embedded_command]
  original_source: https://example.com
  assistant: Weft
---
Body content here.
"""
    prov = _parse_provenance_frontmatter(content)
    assert prov is not None
    assert prov.source == "web_fetch"
    assert prov.trust == "suspicious"
    assert prov.risk_score == 47
    assert prov.risk_band == "medium"
    assert "exfiltration" in prov.signals
    assert prov.original_source == "https://example.com"
    assert prov.assistant == "Weft"


def test_parse_provenance_frontmatter_missing_returns_none():
    from ai_memory.learn import _parse_provenance_frontmatter
    content = """\
---
name: Plain Fact
description: No provenance here.
---
Body.
"""
    assert _parse_provenance_frontmatter(content) is None


def test_parse_provenance_frontmatter_no_frontmatter_returns_none():
    from ai_memory.learn import _parse_provenance_frontmatter
    assert _parse_provenance_frontmatter("Just a plain markdown file.\n") is None


def test_parse_frontmatter_topic_attaches_provenance(tmp_path):
    """parse_frontmatter_topic includes a Provenance object when block is present."""
    from ai_memory.learn import parse_frontmatter_topic
    from ai_memory.provenance import Provenance
    content = """\
---
name: Provenance Test Fact
description: A fact with provenance.
provenance:
  source: bash
  trust: untrusted
---
- key point one
"""
    filepath = tmp_path / "2026-06-07.md"
    topics = parse_frontmatter_topic(content, filepath)
    assert len(topics) == 1
    prov = topics[0].get("provenance")
    assert isinstance(prov, Provenance)
    assert prov.source == "bash"
    assert prov.trust == "untrusted"


def test_parse_frontmatter_topic_no_provenance_block(tmp_path):
    """parse_frontmatter_topic still works when no provenance block is present."""
    from ai_memory.learn import parse_frontmatter_topic
    content = """\
---
name: Plain Fact
description: No provenance.
---
- point
"""
    filepath = tmp_path / "2026-06-07.md"
    topics = parse_frontmatter_topic(content, filepath)
    assert len(topics) == 1
    assert topics[0].get("provenance") is None


def test_parse_provenance_frontmatter_blank_line_in_block():
    """Blank line inside provenance block must not truncate remaining fields."""
    from ai_memory.learn import _parse_provenance_frontmatter
    content = """\
---
name: Test
provenance:
  source: web_fetch

  trust: suspicious
---
Body.
"""
    prov = _parse_provenance_frontmatter(content)
    assert prov is not None
    assert prov.source == "web_fetch"
    assert prov.trust == "suspicious"


def test_parse_provenance_frontmatter_url_with_port():
    """original_source with a port number (colon in value) must round-trip intact."""
    from ai_memory.learn import _parse_provenance_frontmatter
    content = """\
---
name: Test
provenance:
  source: web_fetch
  original_source: https://example.com:8080/path
---
Body.
"""
    prov = _parse_provenance_frontmatter(content)
    assert prov is not None
    assert prov.original_source == "https://example.com:8080/path"


def test_parse_provenance_frontmatter_float_risk_score_silently_dropped():
    """A float string for risk_score is silently dropped (int() raises ValueError)."""
    from ai_memory.learn import _parse_provenance_frontmatter
    content = """\
---
name: Test
provenance:
  source: api
  risk_score: 3.7
---
Body.
"""
    prov = _parse_provenance_frontmatter(content)
    assert prov is not None
    assert prov.risk_score is None  # silently dropped, not corrupted


# ---------------------------------------------------------------------------
# Task 3 — Neo4j provenance write
# ---------------------------------------------------------------------------

def test_sync_fact_tx_writes_provenance_props():
    """_sync_fact_tx must pass provenance_* kwargs to tx.run when provenance is set."""
    from unittest.mock import MagicMock
    from ai_memory.learn import _sync_fact_tx
    from ai_memory.provenance import Provenance

    captured_params = {}

    def fake_run(cypher, **params):
        captured_params.update(params)
        mock_result = MagicMock()
        mock_result.single.return_value = {"name": "Test"}
        return mock_result

    tx = MagicMock()
    tx.run.side_effect = fake_run

    topic = {
        'name': 'Test Fact',
        'summary': 'A test.',
        'key_points': ['point one'],
        'source_file': 'test.md',
        'created_at': '2026-06-07T00:00:00Z',
        'provenance': Provenance(
            source='web_fetch',
            trust='suspicious',
            risk_score=47,
            risk_band='medium',
            signals=['exfiltration'],
        ),
    }
    _sync_fact_tx(tx, topic)

    assert captured_params.get('prov_source') == 'web_fetch'
    assert captured_params.get('prov_trust') == 'suspicious'
    assert captured_params.get('prov_risk_score') == 47
    assert captured_params.get('prov_risk_band') == 'medium'
    assert captured_params.get('prov_signals') == ['exfiltration']


def test_sync_fact_tx_no_provenance_no_prov_params():
    """When provenance is None, no prov_* params are passed to tx.run."""
    from unittest.mock import MagicMock
    from ai_memory.learn import _sync_fact_tx

    captured_params = {}

    def fake_run(cypher, **params):
        captured_params.update(params)
        mock_result = MagicMock()
        mock_result.single.return_value = {"name": "Test"}
        return mock_result

    tx = MagicMock()
    tx.run.side_effect = fake_run

    topic = {
        'name': 'Plain Fact',
        'summary': 'No provenance.',
        'key_points': [],
        'source_file': 'test.md',
        'created_at': '2026-06-07T00:00:00Z',
        'provenance': None,
    }
    _sync_fact_tx(tx, topic)
    assert not any(k.startswith('prov_') for k in captured_params)


def test_write_fact_returns_false_on_driver_failure(monkeypatch, tmp_path):
    """write_fact must return False (not raise) when Neo4j is unreachable."""
    monkeypatch.setenv('NEO4J_URI', 'bolt://127.0.0.1:1')  # nothing listens on port 1
    monkeypatch.setenv('NEO4J_PASSWORD', 'test')
    from ai_memory.learn import write_fact
    topic = {
        'name': 'Test',
        'summary': 'test',
        'key_points': [],
        'source_file': 'api',
        'created_at': '2026-06-07T00:00:00Z',
        'provenance': None,
    }
    # get_driver raises Neo4jConnectionError — write_fact must catch it and return False
    result = write_fact(topic, workspace=tmp_path)
    assert result is False


def test_write_fact_does_not_close_supplied_driver():
    """write_fact must not close a driver supplied by the caller."""
    from unittest.mock import MagicMock
    from ai_memory.learn import write_fact

    mock_driver = MagicMock()
    mock_session = MagicMock()
    mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)
    mock_session.execute_write.return_value = True

    topic = {
        'name': 'Test',
        'summary': 'test',
        'key_points': [],
        'source_file': 'api',
        'created_at': '2026-06-07T00:00:00Z',
        'provenance': None,
    }
    result = write_fact(topic, driver=mock_driver)
    assert result is True
    mock_driver.close.assert_not_called()


# ---------------------------------------------------------------------------
# Task 4 — MemoryClient.write() and Provenance export
# ---------------------------------------------------------------------------

def test_memory_client_has_write_method():
    from ai_memory import MemoryClient
    assert hasattr(MemoryClient, 'write')
    assert callable(MemoryClient.write)


def test_provenance_importable_from_ai_memory():
    from ai_memory import Provenance
    assert callable(Provenance)


def test_memory_client_write_returns_bool_without_neo4j(tmp_path):
    """write() must return a bool without raising when Neo4j is unreachable."""
    import os
    os.environ.setdefault('NEO4J_PASSWORD', 'test')
    from ai_memory import MemoryClient, Provenance
    with MemoryClient(workspace=tmp_path) as client:
        result = client.write(
            "Test Fact",
            summary="A test fact.",
            key_points=["point one"],
            provenance=Provenance(source="api", trust="trusted"),
        )
    assert isinstance(result, bool)
