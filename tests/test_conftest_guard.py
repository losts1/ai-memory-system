from __future__ import annotations


def test_write_never_reaches_a_real_neo4j_driver(monkeypatch, tmp_path):
    """The autouse conftest fixture must point every test at an unreachable
    Neo4j URI, so MemoryClient.write() never attempts a real connection."""
    from neo4j.exceptions import ServiceUnavailable

    from ai_memory import MemoryClient, _config

    recorded = {}

    def fake_driver(uri, *args, **kwargs):
        recorded['uri'] = uri
        raise ServiceUnavailable("guard")

    monkeypatch.setattr(_config.GraphDatabase, "driver", fake_driver)

    with MemoryClient(workspace=tmp_path) as client:
        result = client.write("Guard Fact", summary="never persisted")

    assert result is False
    assert recorded['uri'] == "bolt://127.0.0.1:1"
