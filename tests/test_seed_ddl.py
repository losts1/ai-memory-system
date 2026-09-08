"""
Unit tests for scripts/neo4j_seed.py's vector-index DDL builder.

No Neo4j connection required — only exercises the DDL string construction.
"""
import sys
from pathlib import Path

# Make scripts/ importable without installing as a package
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))


def test_seed_vector_index_ddl_has_if_not_exists_and_filter_props(monkeypatch, tmp_path):
    # neo4j_seed.py reads AI_MEMORY_DIR (and loads <dir>/.env.neo4j) at import time;
    # point it at an empty tmp dir so this test doesn't pick up real credentials/config.
    monkeypatch.setenv("AI_MEMORY_DIR", str(tmp_path))
    import neo4j_seed

    ddl = neo4j_seed.seed_vector_index_ddl()
    assert "IF NOT EXISTS" in ddl
    assert "WITH [f.assistant, f.space, f.status, f.provenance_trust]" in ddl


def test_seed_vector_index_ddl_plain_has_no_cypher25_or_with(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_MEMORY_DIR", str(tmp_path))
    import neo4j_seed

    ddl = neo4j_seed.seed_vector_index_ddl(with_filters=False)
    assert "CYPHER 25" not in ddl
    assert "WITH [" not in ddl
    assert "IF NOT EXISTS" in ddl


def test_create_vector_index_falls_back_to_plain_ddl_on_cypher5_only_server(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_MEMORY_DIR", str(tmp_path))
    import neo4j_seed
    from neo4j.exceptions import Neo4jError

    class FakeSession:
        def __init__(self):
            self.calls = []

        def run(self, q, **kw):
            self.calls.append(q)
            if len(self.calls) == 1:
                raise Neo4jError._hydrate_neo4j(
                    code="Neo.ClientError.Statement.SyntaxError",
                    message="Invalid input 'WITH': expected 'OPTIONS'",
                )

    s = FakeSession()
    result = neo4j_seed.create_vector_index(s)
    assert result == "plain"
    assert len(s.calls) == 2
    assert "WITH [" in s.calls[0]
    assert "WITH [" not in s.calls[1]


def test_create_vector_index_reraises_unrelated_client_errors(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_MEMORY_DIR", str(tmp_path))
    import neo4j_seed
    from neo4j.exceptions import Neo4jError

    class FakeSession:
        def run(self, q, **kw):
            raise Neo4jError._hydrate_neo4j(
                code="Neo.ClientError.Schema.EquivalentSchemaRuleAlreadyExists",
                message="An equivalent index already exists",
            )

    import pytest
    with pytest.raises(Neo4jError):
        neo4j_seed.create_vector_index(FakeSession())
