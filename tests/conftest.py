from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _no_live_neo4j(monkeypatch):
    """Point every test at an unreachable Neo4j and a neutral workspace.

    The test suite must never reach a live Neo4j instance. This fixture runs
    before each test and:
      - forces NEO4J_URI to a port nothing listens on, so any driver that
        does get constructed fails fast with a connection error instead of
        writing to the production graph;
      - supplies a placeholder NEO4J_PASSWORD only when one isn't already
        set, so get_driver() doesn't hard-fail before making a connection
        attempt;
      - removes AI_MEMORY_DIR so no test silently resolves the operator's
        live workspace (and its real .env.neo4j) as its default workspace.

    Tests that need different behavior call monkeypatch.setenv/delenv
    themselves after this fixture runs, which wins.
    """
    monkeypatch.setenv("NEO4J_URI", "bolt://127.0.0.1:1")
    if "NEO4J_PASSWORD" not in os.environ:
        monkeypatch.setenv("NEO4J_PASSWORD", "test")
    monkeypatch.delenv("AI_MEMORY_DIR", raising=False)
