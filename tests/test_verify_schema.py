"""
Unit tests for scripts/verify_schema.py.

No Neo4j connection required — tests only the constants and diff logic.
get_live_schema() (which needs a live DB) is not called here.
"""
import sys
from pathlib import Path

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
    """EXPECTED_CONSTRAINTS must include the four constraints from neo4j_seed.py."""
    from verify_schema import EXPECTED_CONSTRAINTS
    required = {
        "fact_name_unique",
        "fact_id_unique",
        "session_id_unique",
        "assistant_id_unique",
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
    )
    assert issues == [], f"Unexpected issues: {issues}"


def test_diff_schema_ignores_extra_production_indexes():
    """Production DB has memeEmbeddingIndex, ConversationTurn indexes, etc.
    These must not cause failures in default mode."""
    from verify_schema import (
        diff_schema, EXPECTED_CONSTRAINTS, EXPECTED_INDEXES,
        EXPECTED_VECTOR_INDEX, EXPECTED_VECTOR_DIMS,
        EXPECTED_FULLTEXT, EXPECTED_FULLTEXT_PROPS,
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
    )
    assert issues == [], f"Extra production indexes caused failures: {issues}"


def test_diff_schema_detects_wrong_vector_dims():
    """diff_schema must flag vector indexes with incorrect dimensions."""
    from verify_schema import (
        diff_schema, EXPECTED_CONSTRAINTS, EXPECTED_INDEXES,
        EXPECTED_VECTOR_INDEX, EXPECTED_VECTOR_DIMS,
        EXPECTED_FULLTEXT, EXPECTED_FULLTEXT_PROPS,
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
    )
    assert any("summary" in i for i in issues), (
        f"Expected a 'missing summary' issue, got: {issues}"
    )


def test_diff_schema_detects_vector_dims_none():
    """Vector index exists but dimensions are not configured (dims=None)."""
    from verify_schema import (
        diff_schema, EXPECTED_CONSTRAINTS, EXPECTED_INDEXES,
        EXPECTED_VECTOR_INDEX, EXPECTED_FULLTEXT, EXPECTED_FULLTEXT_PROPS,
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
    )
    expected_msg = f"Vector index {EXPECTED_VECTOR_INDEX!r} has no dimensions configured"
    assert issues == [expected_msg], (
        f"Expected exactly [{expected_msg!r}], got: {issues}"
    )
