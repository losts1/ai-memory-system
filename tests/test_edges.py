"""Tests for ai_memory.eval.edges — the judged edge sample for the phase-5 gate.

No network: session and judge call are fake/injected, same pattern as
tests/test_harness.py and tests/test_judge.py.
"""
import json

import pytest

from ai_memory.eval import edges as E


def _row(a_name, b_name, rule_version=None, weight=1.0, a_kp=None, b_kp=None):
    return {
        "a_name": a_name, "a_summary": f"summary {a_name}", "a_key_points": a_kp,
        "a_assistant": "Weft", "a_status": "active",
        "b_name": b_name, "b_summary": f"summary {b_name}", "b_key_points": b_kp,
        "b_assistant": "Grok", "b_status": None,
        "rule_version": rule_version, "weight": weight,
    }


class _FakeSession:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, cypher, params=None):
        self.calls.append((cypher, dict(params or {})))
        return list(self.rows)


ROWS = [_row(f"A{i}", f"B{i}") for i in range(10)]


# ── sample_edges ─────────────────────────────────────────────────────────────

def test_sample_edges_is_reproducible_by_seed():
    s1 = E.sample_edges(_FakeSession(ROWS), 3, 7)
    s2 = E.sample_edges(_FakeSession(ROWS), 3, 7)
    assert [e["a"]["name"] for e in s1] == [e["a"]["name"] for e in s2]
    assert [e["b"]["name"] for e in s1] == [e["b"]["name"] for e in s2]


def test_sample_edges_different_seed_can_differ():
    names = {tuple(e["a"]["name"] for e in E.sample_edges(_FakeSession(ROWS), 3, seed)) for seed in range(10)}
    assert len(names) > 1


def test_sample_edges_caps_at_available_rows():
    out = E.sample_edges(_FakeSession(ROWS[:2]), 10, 1)
    assert len(out) == 2


def test_sample_edges_default_filter_is_true_no_params():
    sess = _FakeSession(ROWS)
    E.sample_edges(sess, 3, 1)
    cypher, params = sess.calls[0]
    assert "WHERE true" in cypher
    assert params == {}


def test_sample_edges_legacy_filter_is_rule_version_is_null():
    sess = _FakeSession(ROWS)
    E.sample_edges(sess, 3, 1, legacy=True)
    cypher, params = sess.calls[0]
    assert "r.rule_version IS NULL" in cypher
    assert params == {}


def test_sample_edges_rule_version_filter_binds_param():
    sess = _FakeSession(ROWS)
    E.sample_edges(sess, 3, 1, rule_version=2)
    cypher, params = sess.calls[0]
    assert "r.rule_version = $rv" in cypher
    assert params == {"rv": 2}


def test_sample_edges_filters_produce_different_cypher():
    sess_all, sess_legacy, sess_rv = _FakeSession(ROWS), _FakeSession(ROWS), _FakeSession(ROWS)
    E.sample_edges(sess_all, 3, 1)
    E.sample_edges(sess_legacy, 3, 1, legacy=True)
    E.sample_edges(sess_rv, 3, 1, rule_version=5)
    cy_all, cy_legacy, cy_rv = sess_all.calls[0][0], sess_legacy.calls[0][0], sess_rv.calls[0][0]
    assert len({cy_all, cy_legacy, cy_rv}) == 3


def test_sample_edges_carries_rule_version_and_weight():
    out = E.sample_edges(_FakeSession([_row("A", "B", rule_version=3, weight=0.7)]), 10, 1)
    assert out[0]["rule_version"] == 3
    assert out[0]["weight"] == 0.7


def test_sample_edges_key_points_normalisation():
    rows = [_row("A", "B", a_kp="single", b_kp=None), _row("C", "D", a_kp=["x", "y"], b_kp=[])]
    out = E.sample_edges(_FakeSession(rows), 10, 1)
    by_a = {e["a"]["name"]: e for e in out}
    assert by_a["A"]["a"]["key_points"] == ["single"]
    assert by_a["A"]["b"]["key_points"] == []
    assert by_a["C"]["a"]["key_points"] == ["x", "y"]
    assert by_a["C"]["b"]["key_points"] == []


def test_sample_edges_fact_dicts_carry_expected_keys():
    out = E.sample_edges(_FakeSession([_row("A", "B")]), 10, 1)
    assert set(out[0]["a"]) == {"name", "summary", "key_points", "assistant", "status"}
    assert set(out[0]["b"]) == {"name", "summary", "key_points", "assistant", "status"}


# ── judge_edges ──────────────────────────────────────────────────────────────

def _edge(a_name, b_name):
    return {"a": {"name": a_name, "summary": "s", "key_points": [], "assistant": None, "status": None},
            "b": {"name": b_name, "summary": "s", "key_points": [], "assistant": None, "status": None},
            "rule_version": None, "weight": 1.0}


def test_judge_edges_computes_shares_with_one_unjudged():
    sample = [_edge("A1", "B1"), _edge("A2", "B2"), _edge("A3", "B3")]

    def call(messages):
        user = messages[1]["content"]
        if "A3" in user:
            return "garbage"
        for name, grade in (("B1", 2), ("B2", 1)):
            if name in user:
                return json.dumps([{"name": name, "grade": grade, "why": "ok"}])
        return "garbage"

    out = E.judge_edges(sample, call, model="m")
    assert out["n"] == 3
    assert out["judged"] == 2
    assert out["unjudged"] == 1
    assert out["related_share"] == pytest.approx(1.0)   # both judged grades >= 1
    assert out["direct_share"] == pytest.approx(0.5)     # only B1 is grade 2
    assert len(out["grades"]) == 2
    assert {"a": "A1", "b": "B1", "grade": 2, "why": "ok"} in out["grades"]


def test_judge_edges_all_unjudged_gives_none_shares():
    sample = [_edge("A1", "B1")]
    out = E.judge_edges(sample, lambda messages: "garbage", model="m")
    assert out["judged"] == 0
    assert out["unjudged"] == 1
    assert out["related_share"] is None
    assert out["direct_share"] is None
    assert out["grades"] == []


def test_judge_edges_uses_edge_rubric():
    from ai_memory.eval.judge import EDGE_SYSTEM_PROMPT
    seen = []

    def call(messages):
        seen.append(messages[0]["content"])
        return json.dumps([{"name": "B1", "grade": 1, "why": "r"}])

    E.judge_edges([_edge("A1", "B1")], call, model="m")
    assert seen == [EDGE_SYSTEM_PROMPT]


def test_judge_edges_saves_cache_when_given(tmp_path):
    from ai_memory.eval.judge import JudgeCache, judge_fact_text
    path = tmp_path / "c.json"
    cache = JudgeCache(path)
    edge = _edge("A1", "B1")

    def call(messages):
        return json.dumps([{"name": "B1", "grade": 2, "why": "ok"}])

    E.judge_edges([edge], call, model="m", cache=cache)
    assert path.exists()
    reloaded = JudgeCache(path)
    query_text = judge_fact_text(edge["a"])
    hit = reloaded.get("m", query_text, "B1", judge_fact_text(edge["b"]), rubric="edge")
    assert hit == {"grade": 2, "why": "ok"}


# ── edge_gate ────────────────────────────────────────────────────────────────

def _r(judged, related, direct, unjudged=0, n=None):
    n = n if n is not None else judged + unjudged
    return {"n": n, "judged": judged, "unjudged": unjudged, "related_share": related, "direct_share": direct, "grades": []}


def test_edge_gate_passes_when_shares_equal_and_all_judged():
    assert E.edge_gate(_r(5, 0.6, 0.4), _r(5, 0.6, 0.4)) is True


def test_edge_gate_passes_when_shares_rise():
    assert E.edge_gate(_r(5, 0.6, 0.4), _r(5, 0.7, 0.5)) is True


def test_edge_gate_fails_when_any_unjudged():
    assert E.edge_gate(_r(5, 0.6, 0.4), _r(4, 0.6, 0.4, unjudged=1)) is False


def test_edge_gate_fails_when_after_judged_is_zero():
    assert E.edge_gate(_r(5, 0.6, 0.4), _r(0, None, None)) is False


def test_edge_gate_fails_when_before_judged_is_zero():
    assert E.edge_gate(_r(0, None, None), _r(5, 0.6, 0.4)) is False


def test_edge_gate_fails_when_related_share_drops():
    assert E.edge_gate(_r(5, 0.6, 0.4), _r(5, 0.5, 0.4)) is False


def test_edge_gate_fails_when_direct_share_drops():
    assert E.edge_gate(_r(5, 0.6, 0.4), _r(5, 0.6, 0.3)) is False


# ── main ─────────────────────────────────────────────────────────────────────

class _FakeDriver:
    def __init__(self):
        self.closed = False

    def session(self):
        return _FakeSession([])

    def close(self):
        self.closed = True


def test_main_writes_json_and_prints_table(tmp_path, monkeypatch, capsys):
    drv = _FakeDriver()
    monkeypatch.setattr(E, "_open_driver", lambda ws: drv)
    monkeypatch.setattr(E, "sample_edges", lambda session, n, seed, **kw: [_edge("A", "B")])
    result = {"n": 1, "judged": 1, "unjudged": 0, "related_share": 1.0, "direct_share": 1.0,
              "grades": [{"a": "A", "b": "B", "grade": 2, "why": "ok"}]}
    monkeypatch.setattr(E, "judge_edges", lambda sample, call, **kw: result)
    out_path = tmp_path / "o.json"
    rc = E.main(["--json", str(out_path)])
    assert rc == 0
    assert json.loads(out_path.read_text()) == result
    out = capsys.readouterr().out
    assert "n=1" in out and "judged=1" in out
    assert "A" in out and "B" in out
    assert drv.closed is True


def test_main_exits_1_when_zero_judged(monkeypatch):
    monkeypatch.setattr(E, "_open_driver", lambda ws: _FakeDriver())
    monkeypatch.setattr(E, "sample_edges", lambda session, n, seed, **kw: [])
    result = {"n": 0, "judged": 0, "unjudged": 0, "related_share": None, "direct_share": None, "grades": []}
    monkeypatch.setattr(E, "judge_edges", lambda sample, call, **kw: result)
    assert E.main([]) == 1


def test_main_gate_against_pass_and_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "_open_driver", lambda ws: _FakeDriver())
    monkeypatch.setattr(E, "sample_edges", lambda session, n, seed, **kw: [_edge("A", "B")])
    result = {"n": 1, "judged": 1, "unjudged": 0, "related_share": 0.8, "direct_share": 0.5, "grades": []}
    monkeypatch.setattr(E, "judge_edges", lambda sample, call, **kw: result)

    passing_before = tmp_path / "before_pass.json"
    passing_before.write_text(json.dumps({"n": 1, "judged": 1, "unjudged": 0,
                                          "related_share": 0.8, "direct_share": 0.5, "grades": []}))
    assert E.main(["--gate-against", str(passing_before)]) == 0

    failing_before = tmp_path / "before_fail.json"
    failing_before.write_text(json.dumps({"n": 1, "judged": 1, "unjudged": 0,
                                          "related_share": 0.9, "direct_share": 0.5, "grades": []}))
    assert E.main(["--gate-against", str(failing_before)]) == 1


def test_main_legacy_and_rule_version_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        E.main(["--legacy", "--rule-version", "3"])


def test_main_forwards_flags_to_sample_edges(monkeypatch):
    seen = {}
    monkeypatch.setattr(E, "_open_driver", lambda ws: _FakeDriver())

    def fake_sample(session, n, seed, **kw):
        seen.update(n=n, seed=seed, **kw)
        return []

    monkeypatch.setattr(E, "sample_edges", fake_sample)
    result = {"n": 0, "judged": 0, "unjudged": 0, "related_share": None, "direct_share": None, "grades": []}
    monkeypatch.setattr(E, "judge_edges", lambda sample, call, **kw: result)
    E.main(["--sample", "12", "--seed", "3", "--rule-version", "9"])
    assert seen == {"n": 12, "seed": 3, "rule_version": 9, "legacy": False}
