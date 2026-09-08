"""
Unit tests for scripts/verify_schema.py.

No Neo4j connection required — tests only the constants and diff logic.
get_live_schema() (which needs a live DB) is not called here.
"""
import sys
from pathlib import Path

import pytest

# Make scripts/ importable without installing as a package
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


# ---------------------------------------------------------------------------
# Constants tests
# ---------------------------------------------------------------------------

def test_expected_indexes_contains_required_entries():
    """EXPECTED_INDEXES must include all indexes created by neo4j_seed.py."""
    from verify_schema import EXPECTED_INDEXES
    required = {
        "fact_created_at_idx",
        "fact_source_file_idx",
        "fact_assistant_idx",
        "fact_source_idx",
        "session_date_idx",
        "session_assistant_idx",
    }
    assert required <= EXPECTED_INDEXES, (
        f"EXPECTED_INDEXES missing: {required - EXPECTED_INDEXES}"
    )


def test_expected_constraints_contains_required_entries():
    """EXPECTED_CONSTRAINTS must include all constraints from neo4j_seed.py."""
    from verify_schema import EXPECTED_CONSTRAINTS
    required = {
        "fact_name_unique",
        "fact_id_unique",
        "session_id_unique",
        "assistant_id_unique",
        "source_name_unique",   # Source provenance nodes written by learn._sync_fact_tx
    }
    assert required <= EXPECTED_CONSTRAINTS, (
        f"EXPECTED_CONSTRAINTS missing: {required - EXPECTED_CONSTRAINTS}"
    )


def test_expected_fulltext_props_includes_summary():
    """fact_content fulltext must cover summary (extended in Task 2)."""
    from verify_schema import EXPECTED_FULLTEXT_PROPS
    assert "summary" in EXPECTED_FULLTEXT_PROPS
    assert "name" in EXPECTED_FULLTEXT_PROPS
    assert "content" in EXPECTED_FULLTEXT_PROPS


def test_expected_runtime_constraints_contains_word_text_unique():
    """word_text_unique is runtime-created (by learn.sync_facts) and must be in
    EXPECTED_RUNTIME_CONSTRAINTS so it doesn't appear as a false extra in --strict."""
    from verify_schema import EXPECTED_RUNTIME_CONSTRAINTS, EXPECTED_CONSTRAINTS
    assert "word_text_unique" in EXPECTED_RUNTIME_CONSTRAINTS
    # Must NOT be in EXPECTED_CONSTRAINTS — it's not created by neo4j_seed.py
    assert "word_text_unique" not in EXPECTED_CONSTRAINTS


# ---------------------------------------------------------------------------
# diff_schema() logic tests
# ---------------------------------------------------------------------------

def test_diff_schema_detects_missing_indexes():
    """diff_schema must flag missing indexes from neo4j_seed.py."""
    from verify_schema import diff_schema
    issues = diff_schema(
        live_constraints={"fact_name_unique", "fact_id_unique",
                          "session_id_unique", "assistant_id_unique"},
        live_indexes={},   # all missing
        live_vector=None,
        live_fulltext=None,
        live_fulltext_kp=None,
    )
    issue_text = " ".join(issues)
    assert "fact_created_at_idx" in issue_text
    assert "fact_source_file_idx" in issue_text


def test_diff_schema_passes_when_all_present():
    """diff_schema returns no issues when live schema matches expected."""
    from verify_schema import (
        diff_schema, EXPECTED_CONSTRAINTS, EXPECTED_INDEXES,
        EXPECTED_VECTOR_INDEX, EXPECTED_VECTOR_DIMS,
        EXPECTED_FULLTEXT, EXPECTED_FULLTEXT_PROPS,
        EXPECTED_FULLTEXT_KP, EXPECTED_FULLTEXT_KP_PROPS,
    )
    live_indexes = {
        name: {"type": "RANGE", "properties": set()}
        for name in EXPECTED_INDEXES
    }
    issues = diff_schema(
        live_constraints=EXPECTED_CONSTRAINTS,
        live_indexes=live_indexes,
        live_vector={"name": EXPECTED_VECTOR_INDEX, "dims": EXPECTED_VECTOR_DIMS},
        live_fulltext={"name": EXPECTED_FULLTEXT, "properties": EXPECTED_FULLTEXT_PROPS},
        live_fulltext_kp={"name": EXPECTED_FULLTEXT_KP, "properties": EXPECTED_FULLTEXT_KP_PROPS},
    )
    assert issues == [], f"Unexpected issues: {issues}"


def test_diff_schema_ignores_extra_production_indexes():
    """Production DB has memeEmbeddingIndex, ConversationTurn indexes, etc.
    These must not cause failures in default mode."""
    from verify_schema import (
        diff_schema, EXPECTED_CONSTRAINTS, EXPECTED_INDEXES,
        EXPECTED_VECTOR_INDEX, EXPECTED_VECTOR_DIMS,
        EXPECTED_FULLTEXT, EXPECTED_FULLTEXT_PROPS,
        EXPECTED_FULLTEXT_KP, EXPECTED_FULLTEXT_KP_PROPS,
    )
    live_indexes = {
        name: {"type": "RANGE", "properties": set()}
        for name in EXPECTED_INDEXES
    }
    # Extras from production
    live_indexes["factEmbeddingIndex"] = {"type": "VECTOR", "properties": set()}
    live_indexes["memeEmbeddingIndex"] = {"type": "VECTOR", "properties": set()}
    live_indexes["turn_content_idx"]   = {"type": "FULLTEXT", "properties": set()}

    issues = diff_schema(
        live_constraints=EXPECTED_CONSTRAINTS,
        live_indexes=live_indexes,
        live_vector={"name": EXPECTED_VECTOR_INDEX, "dims": EXPECTED_VECTOR_DIMS},
        live_fulltext={"name": EXPECTED_FULLTEXT, "properties": EXPECTED_FULLTEXT_PROPS},
        live_fulltext_kp={"name": EXPECTED_FULLTEXT_KP, "properties": EXPECTED_FULLTEXT_KP_PROPS},
    )
    assert issues == [], f"Extra production indexes caused failures: {issues}"


def test_diff_schema_detects_wrong_vector_dims():
    """diff_schema must flag vector indexes with incorrect dimensions."""
    from verify_schema import (
        diff_schema, EXPECTED_CONSTRAINTS, EXPECTED_INDEXES,
        EXPECTED_VECTOR_INDEX, EXPECTED_VECTOR_DIMS,
        EXPECTED_FULLTEXT, EXPECTED_FULLTEXT_PROPS,
        EXPECTED_FULLTEXT_KP, EXPECTED_FULLTEXT_KP_PROPS,
    )
    live_indexes = {
        name: {"type": "RANGE", "properties": set()}
        for name in EXPECTED_INDEXES
    }
    issues = diff_schema(
        live_constraints=EXPECTED_CONSTRAINTS,
        live_indexes=live_indexes,
        live_vector={"name": EXPECTED_VECTOR_INDEX, "dims": 1536},  # wrong
        live_fulltext={"name": EXPECTED_FULLTEXT, "properties": EXPECTED_FULLTEXT_PROPS},
        live_fulltext_kp={"name": EXPECTED_FULLTEXT_KP, "properties": EXPECTED_FULLTEXT_KP_PROPS},
    )
    expected_msg = (
        f"Vector index {EXPECTED_VECTOR_INDEX!r} has wrong dimensions: "
        f"expected {EXPECTED_VECTOR_DIMS}, got 1536"
    )
    assert issues == [expected_msg], (
        f"Expected exactly [{expected_msg!r}], got: {issues}"
    )


def test_diff_schema_detects_missing_summary_in_fulltext():
    """Detects when fact_content index was not upgraded to include summary."""
    from verify_schema import (
        diff_schema, EXPECTED_CONSTRAINTS, EXPECTED_INDEXES,
        EXPECTED_VECTOR_INDEX, EXPECTED_VECTOR_DIMS, EXPECTED_FULLTEXT,
        EXPECTED_FULLTEXT_KP, EXPECTED_FULLTEXT_KP_PROPS,
    )
    live_indexes = {
        name: {"type": "RANGE", "properties": set()}
        for name in EXPECTED_INDEXES
    }
    issues = diff_schema(
        live_constraints=EXPECTED_CONSTRAINTS,
        live_indexes=live_indexes,
        live_vector={"name": EXPECTED_VECTOR_INDEX, "dims": EXPECTED_VECTOR_DIMS},
        live_fulltext={
            "name": EXPECTED_FULLTEXT,
            "properties": {"name", "content"},  # old definition, missing summary
        },
        live_fulltext_kp={"name": EXPECTED_FULLTEXT_KP, "properties": EXPECTED_FULLTEXT_KP_PROPS},
    )
    assert any("summary" in i for i in issues), (
        f"Expected a 'missing summary' issue, got: {issues}"
    )


def test_diff_schema_detects_vector_dims_none():
    """Vector index exists but dimensions are not configured (dims=None)."""
    from verify_schema import (
        diff_schema, EXPECTED_CONSTRAINTS, EXPECTED_INDEXES,
        EXPECTED_VECTOR_INDEX, EXPECTED_FULLTEXT, EXPECTED_FULLTEXT_PROPS,
        EXPECTED_FULLTEXT_KP, EXPECTED_FULLTEXT_KP_PROPS,
    )
    live_indexes = {
        name: {"type": "RANGE", "properties": set()}
        for name in EXPECTED_INDEXES
    }
    issues = diff_schema(
        live_constraints=EXPECTED_CONSTRAINTS,
        live_indexes=live_indexes,
        live_vector={"name": EXPECTED_VECTOR_INDEX, "dims": None},
        live_fulltext={"name": EXPECTED_FULLTEXT, "properties": EXPECTED_FULLTEXT_PROPS},
        live_fulltext_kp={"name": EXPECTED_FULLTEXT_KP, "properties": EXPECTED_FULLTEXT_KP_PROPS},
    )
    expected_msg = f"Vector index {EXPECTED_VECTOR_INDEX!r} has no dimensions configured"
    assert issues == [expected_msg], (
        f"Expected exactly [{expected_msg!r}], got: {issues}"
    )


def test_expected_key_points_fulltext_index_declared():
    import scripts.verify_schema as vs
    assert vs.EXPECTED_FULLTEXT_KP == "fact_key_points"
    assert vs.EXPECTED_FULLTEXT_KP_PROPS == {"key_points"}


def test_diff_schema_requires_live_fulltext_kp():
    """M11: live_fulltext_kp must be required, not silently defaulted to
    None — a caller that forgets it would otherwise get a false-negative
    'fact_key_points missing' report only when it's actually absent, never
    a loud error when the caller itself forgot to pass it."""
    from verify_schema import (
        EXPECTED_CONSTRAINTS,
        EXPECTED_FULLTEXT,
        EXPECTED_FULLTEXT_PROPS,
        EXPECTED_INDEXES,
        EXPECTED_VECTOR_DIMS,
        EXPECTED_VECTOR_INDEX,
        diff_schema,
    )
    live_indexes = {name: {"type": "RANGE", "properties": set()} for name in EXPECTED_INDEXES}
    with pytest.raises(TypeError):
        diff_schema(
            live_constraints=EXPECTED_CONSTRAINTS,
            live_indexes=live_indexes,
            live_vector={"name": EXPECTED_VECTOR_INDEX, "dims": EXPECTED_VECTOR_DIMS},
            live_fulltext={"name": EXPECTED_FULLTEXT, "properties": EXPECTED_FULLTEXT_PROPS},
        )


# ---------------------------------------------------------------------------
# _config.validate_schema — retrieval_config reporting
# ---------------------------------------------------------------------------

def test_validate_schema_reports_retrieval_config(monkeypatch):
    from ai_memory import _config
    class R(dict):
        def __getitem__(self, k): return dict.__getitem__(self, k)
    class Sess:
        def __init__(self, cfg_rows): self.cfg_rows = cfg_rows
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def run(self, q, **kw):
            if "RetrievalConfig" in q:
                return iter(self.cfg_rows)
            return iter([])
    class Drv:
        def __init__(self, rows): self.rows = rows
        def session(self): return Sess(self.rows)
    out = _config.validate_schema(Drv([R(version=2)]))
    assert out["retrieval_config"] == "version 2"
    out = _config.validate_schema(Drv([]))
    assert out["retrieval_config"] == "missing"


# ---------------------------------------------------------------------------
# _config.validate_schema — vector_filter_props reporting
# ---------------------------------------------------------------------------

def test_validate_schema_reports_vector_filter_props():
    from ai_memory import _config
    class Sess:
        def __init__(self, props): self.props = props
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def run(self, q, **kw):
            if "type = 'VECTOR' AND name = $name" in q:
                return iter([{"properties": self.props}]) if self.props is not None else iter([])
            if "RetrievalConfig" in q:
                return iter([])
            return iter([])
    class Drv:
        def __init__(self, props): self.props = props
        def session(self): return Sess(self.props)
    assert _config.validate_schema(Drv(["embedding", "assistant", "space", "status", "provenance_trust"]), vector_index="idx")["vector_filter_props"] == "ok"
    assert _config.validate_schema(Drv(["embedding"]), vector_index="idx")["vector_filter_props"] == "missing: ['assistant', 'provenance_trust', 'space', 'status']"
    assert _config.validate_schema(Drv(None), vector_index="idx")["vector_filter_props"] == "index not found"


def test_retrieval_config_missing_is_an_issue():
    """review #8: a missing RetrievalConfig printed a cross but never failed the exit code."""
    import verify_schema as vs

    assert vs.retrieval_config_issues("version 3") == []
    issues = vs.retrieval_config_issues("missing")
    assert len(issues) == 1 and "RetrievalConfig" in issues[0] and "neo4j_seed.py" in issues[0]
