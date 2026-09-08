from __future__ import annotations

import pytest

from ai_memory.retrieval_config import (
    RETRIEVAL_CONFIG_ID,
    RetrievalConfig,
    load_retrieval_config,
    publish_retrieval_config,
)


class FakeResult:
    def __init__(self, rows):
        self._rows = rows
    def single(self):
        return self._rows[0] if self._rows else None
    def __iter__(self):
        return iter(self._rows)


class FakeSession:
    def __init__(self, rows_by_call=None):
        self.calls = []
        self.rows_by_call = list(rows_by_call or [])
    def run(self, cypher, params=None, **kw):
        p = dict(params or {}); p.update(kw)
        self.calls.append((cypher, p))
        return FakeResult(self.rows_by_call.pop(0) if self.rows_by_call else [])


def test_load_returns_none_when_no_node():
    s = FakeSession([[]])
    assert load_retrieval_config(s) is None
    cypher, params = s.calls[0]
    assert "RetrievalConfig" in cypher and params["id"] == RETRIEVAL_CONFIG_ID


def test_load_parses_node():
    s = FakeSession([[{"version": 3, "boilerplate": ["a b c d", "b c d e"], "updated_at": "2026-09-04T00:00:00Z"}]])
    cfg = load_retrieval_config(s)
    assert cfg == RetrievalConfig(version=3, boilerplate=frozenset({"a b c d", "b c d e"}), updated_at="2026-09-04T00:00:00Z")


def test_load_tolerates_null_boilerplate():
    s = FakeSession([[{"version": 1, "boilerplate": None, "updated_at": None}]])
    assert load_retrieval_config(s).boilerplate == frozenset()


def test_load_parses_edge_fields_when_present():
    s = FakeSession([[{
        "version": 3, "boilerplate": [], "updated_at": "T",
        "rule_version": 2, "edge_floor": 0.5, "t_mean": 0.1, "t_std": 0.2,
        "c_mean": 0.3, "c_std": 0.4, "baseline_pairs": 40000, "baseline_seed": 0, "n_facts": 100,
    }]])
    cfg = load_retrieval_config(s)
    assert cfg.rule_version == 2
    assert cfg.edge_floor == pytest.approx(0.5)
    assert cfg.t_mean == pytest.approx(0.1) and cfg.t_std == pytest.approx(0.2)
    assert cfg.c_mean == pytest.approx(0.3) and cfg.c_std == pytest.approx(0.4)
    assert cfg.baseline_pairs == 40000 and cfg.baseline_seed == 0 and cfg.n_facts == 100


def test_load_edge_fields_default_none_when_absent():
    s = FakeSession([[{"version": 1, "boilerplate": [], "updated_at": None}]])
    cfg = load_retrieval_config(s)
    assert cfg.rule_version is None and cfg.edge_floor is None
    assert cfg.t_mean is None and cfg.t_std is None and cfg.c_mean is None and cfg.c_std is None
    assert cfg.baseline_pairs is None and cfg.baseline_seed is None and cfg.n_facts is None


def test_publish_increments_version_and_writes_sorted_grams():
    s = FakeSession([[{"version": 4, "updated_at": "T"}]])
    cfg = publish_retrieval_config(s, {"z y x w", "a b c d"}, now="T")
    cypher, params = s.calls[0]
    assert "MERGE (c:RetrievalConfig {id: $id})" in cypher
    assert "ON CREATE SET c.version = 0" in cypher
    assert "c.version = c.version + 1" in cypher
    assert params["grams"] == ["a b c d", "z y x w"]
    assert params["now"] == "T"
    assert cfg.version == 4 and cfg.boilerplate == frozenset({"z y x w", "a b c d"})
