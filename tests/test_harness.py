import json

import pytest
from neo4j import Query
from neo4j.exceptions import Neo4jError

from ai_memory.eval import harness as H

GOLDEN = [
    {"query": "reserve balance guard", "filters": {}, "expect": ["Reserve Guard"]},
    {"query": "grok hybrid floor", "filters": {"assistant": "Grok"}, "expect": ["Floor Fact"]},
    {"query": "nothing here", "filters": {}, "expect": []},
]


def _ranker(table):
    def r(query, filters, k):
        return [{"name": n, "score": 1.0 - i / 10, "via": "vec", "summary": f"about {n}", "key_points": [],
                 "assistant": filters.get("assistant"), "status": None} for i, n in enumerate(table.get(query, []))][:k]
    return r


# --- Fake Neo4j driver/session for exercising make_rankers' real
# search_hybrid path (same pattern as tests/test_search_path.py) ---

class _FakeSession:
    def __init__(self, script):
        self.script = list(script)

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def run(self, query, params=None, **kw):
        text = query.text if isinstance(query, Query) else query
        step = self.script.pop(0)
        return step(text, dict(params or {}, **kw))


class _FakeDriver:
    def __init__(self, script):
        self.session_obj = _FakeSession(script)

    def session(self): return self.session_obj
    def close(self): pass


def test_load_golden_validates_shape(tmp_path):
    p = tmp_path / "g.json"
    p.write_text(json.dumps(GOLDEN))
    assert H.load_golden(p) == GOLDEN
    p.write_text(json.dumps([{"query": "x"}]))
    with pytest.raises(ValueError) as e:
        H.load_golden(p)
    assert "index 0" in str(e.value)


def test_exact_hit_rate():
    hits = [{"name": "A"}, {"name": "B"}]
    assert H.exact_hit_rate(hits, ["A", "C"], 5) == 0.5
    assert H.exact_hit_rate(hits, [], 5) == 1.0          # nothing expected, nothing wrong


def test_evaluate_pools_top_n_and_scores_each_ranker():
    good = _ranker({"reserve balance guard": ["Reserve Guard", "X"], "grok hybrid floor": ["Floor Fact"], "nothing here": ["Y"]})
    bad = _ranker({"reserve balance guard": ["X", "Reserve Guard"], "grok hybrid floor": ["Z"], "nothing here": []})
    judged = []

    def judge(messages):
        user = messages[1]["content"]
        judged.append(user)
        names = [line.split("name: ", 1)[1] for line in user.splitlines() if line.startswith("name: ")]

        def grade(n):
            return 2 if n in ("Reserve Guard", "Floor Fact") else 0

        return json.dumps([{"name": n, "grade": grade(n), "why": ""} for n in names])

    res = H.evaluate(GOLDEN, {"good": good, "bad": bad}, judge, model="m", cache=None, k=5, pool_n=10)
    g, b = res["per_ranker"]["good"], res["per_ranker"]["bad"]
    assert g["exact5"] == 1.0 and b["exact5"] == pytest.approx(2 / 3)
    assert g["ndcg5"] > b["ndcg5"] and g["recall5"] >= b["recall5"] and g["mrr"] > b["mrr"]
    assert g["unjudged"] == [] and b["unjudged"] == []
    assert len(judged) == 3                                # one pooled judging per query, shared by both rankers


def test_evaluate_marks_unjudged_and_gate_fails_closed():
    r = _ranker({"reserve balance guard": ["Reserve Guard"], "grok hybrid floor": ["Floor Fact"], "nothing here": ["Y"]})
    res = H.evaluate(GOLDEN, {"r": r}, lambda m: "garbage", model="m", cache=None)
    assert set(res["per_ranker"]["r"]["unjudged"]) == {g["query"] for g in GOLDEN}
    assert H.gate(res, res, "r") is False


def test_gate_uses_passes_ship_gate_on_golden_and_judged():
    def mk(e, n, rc):
        return {"per_ranker": {"r": {"exact5": e, "ndcg5": n, "recall5": rc, "mrr": 0.5, "unjudged": [],
                                     "judged_queries": 3, "candidates": 5}}}

    assert H.gate(mk(0.5, 0.6, 0.5), mk(0.6, 0.6, 0.5), "r") is True
    assert H.gate(mk(0.5, 0.6, 0.5), mk(0.6, 0.5, 0.5), "r") is False


def test_evaluate_tracks_per_ranker_candidates():
    r = _ranker({"reserve balance guard": ["Reserve Guard"], "grok hybrid floor": ["Floor Fact"], "nothing here": ["Y"]})
    res = H.evaluate(GOLDEN, {"r": r}, lambda m: "garbage", model="m", cache=None)
    assert res["per_ranker"]["r"]["candidates"] == 3          # one hit per golden query


def test_gate_fails_closed_when_ranker_returns_nothing():
    """I4: a ranker returning [] for every query must fail the gate even if
    judged_queries happens to be nonzero — 'nothing ran' is not a pass."""
    empty = _ranker({})
    res_empty = H.evaluate(GOLDEN, {"r": empty}, lambda m: "[]", model="m", cache=None)
    assert res_empty["per_ranker"]["r"]["candidates"] == 0
    assert H.gate(res_empty, res_empty, "r") is False

    def judge(messages):
        user = messages[1]["content"]
        names = [line.split("name: ", 1)[1] for line in user.splitlines() if line.startswith("name: ")]
        return json.dumps([{"name": n, "grade": 2, "why": ""} for n in names])

    one_hit = _ranker({"reserve balance guard": ["Reserve Guard"], "grok hybrid floor": ["Floor Fact"], "nothing here": ["Y"]})
    res = H.evaluate(GOLDEN, {"r": one_hit}, judge, model="m", cache=None)
    assert res["per_ranker"]["r"]["candidates"] == 3
    assert res["per_ranker"]["r"]["unjudged"] == []
    assert H.gate(res, res, "r") is True


def test_make_rankers_has_the_three_names():
    names = set(H.make_rankers(driver=object(), workspace=None))
    assert names == {"legacy", "hybrid_fallback", "hybrid_search"}


def test_hybrid_search_ranker_raises_when_index_falls_back(monkeypatch):
    """I3: hybrid_search must fail loudly rather than silently report
    over-fetch-fallback results as if they came from the SEARCH-indexed
    path — that's what makes it a useful pre-migration signal (spec §8).
    hybrid_fallback, which forces the fallback deliberately, must not raise."""
    import ai_memory.search as S
    monkeypatch.setattr(S, "_embed", lambda q: [0.0] * 8)

    def script(t, p):
        if "SEARCH" in t:
            raise Neo4jError._hydrate_neo4j(
                code="Neo.ClientError.Statement.PropertyNotFound",
                message="22ND3: The property `assistant` is not an additional property "
                        "for vector search with filters on the vector index `x`.",
            )
        if "SUPERSEDES" in t:
            return []
        return [{"name": "A", "text": "about A", "key_points": [], "assistant": None,
                 "status": None, "space": None, "s": 0.9}]

    drv = _FakeDriver([script] * 20)
    rankers = H.make_rankers(drv, workspace=None)

    S.reset_fallback()
    with pytest.raises(RuntimeError):
        rankers["hybrid_search"]("q", {}, 5)

    S.reset_fallback()
    hits = rankers["hybrid_fallback"]("q", {}, 5)
    assert hits and hits[0]["name"] == "A"
    S.reset_fallback()


def test_format_table_lists_each_ranker():
    res = {"per_ranker": {"a": {"exact5": 0.5, "ndcg5": 0.4, "recall5": 0.3, "mrr": 0.2, "unjudged": [],
                                "judged_queries": 3, "candidates": 7}}, "pool_size": 3}
    out = H.format_table(res)
    assert "a" in out and "0.50" in out and "ndcg5" in out


def test_format_table_prints_candidates_column_and_m12_label():
    res = {"per_ranker": {"a": {"exact5": 0.5, "ndcg5": 0.4, "recall5": 0.3, "mrr": 0.2, "unjudged": [],
                                "judged_queries": 3, "candidates": 7}}, "pool_size": 3}
    out = H.format_table(res)
    assert "candidates" in out.splitlines()[0]     # header
    assert "7" in out
    assert "pooled candidates: 3" in out           # M12: not "pooled candidates judged: N"


def test_format_table_shows_subset_when_present():
    res = {"per_ranker": {"a": {"exact5": 0.5, "ndcg5": 0.4, "recall5": 0.3, "mrr": 0.2, "unjudged": [],
                                "judged_queries": 3, "candidates": 7}}, "pool_size": 3,
           "subset": "scoped", "queries": 5}
    out = H.format_table(res)
    assert "subset: scoped  queries: 5" in out


def test_format_table_omits_subset_line_when_absent():
    res = {"per_ranker": {"a": {"exact5": 0.5, "ndcg5": 0.4, "recall5": 0.3, "mrr": 0.2, "unjudged": [],
                                "judged_queries": 3, "candidates": 7}}, "pool_size": 3}
    out = H.format_table(res)
    assert "subset:" not in out


def test_pool_candidates_skeleton_has_empty_expect_and_provenance():
    r1 = _ranker({"q1": ["A", "B"]})
    r2 = _ranker({"q1": ["B", "C"]})
    sk = H.pool_candidates([{"query": "q1", "filters": {"assistant": "Grok"}}], {"r1": r1, "r2": r2}, n=10)
    assert sk[0]["expect"] == [] and sk[0]["filters"] == {"assistant": "Grok"}
    names = {c["name"]: c["seen_in"] for c in sk[0]["candidates"]}
    assert names == {"A": ["r1"], "B": ["r1", "r2"], "C": ["r2"]}


def test_pool_candidates_skeleton_projects_exact_field_set():
    """M6: candidates carry exactly {name, teaser, assistant, status, seen_in}
    — no score, via, or summary leaking through from the ranker's raw hit."""
    r1 = _ranker({"q1": ["A"]})
    sk = H.pool_candidates([{"query": "q1", "filters": {}}], {"r1": r1}, n=10)
    candidate = sk[0]["candidates"][0]
    assert set(candidate) == {"name", "teaser", "assistant", "status", "seen_in"}


def test_main_label_mode_prints_skeleton(tmp_path, capsys, monkeypatch):
    p = tmp_path / "queries.json"
    p.write_text(json.dumps([{"query": "q1", "filters": {}, "expect": []}]))
    monkeypatch.setattr(H, "make_rankers", lambda driver, workspace: {"r": _ranker({"q1": ["A"]})})
    monkeypatch.setattr(H, "_open_driver", lambda workspace: object())
    assert H.main(["--golden", str(p), "--label"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out[0]["candidates"][0]["name"] == "A"


def test_main_reports_missing_golden(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv(H.GOLDEN_ENV, raising=False)
    assert H.main([]) == 2
    assert "golden" in capsys.readouterr().err.lower()


def test_filter_golden_subsets():
    g = [{"query": "a", "filters": {}, "expect": []}, {"query": "b", "filters": {"assistant": "Weft"}, "expect": []}]
    assert H.filter_golden(g, "all") == g
    assert H.filter_golden(g, "scoped") == [g[1]]
    assert H.filter_golden(g, "unscoped") == [g[0]]
    with pytest.raises(ValueError):
        H.filter_golden(g, "nope")


def test_main_subset_flag_reaches_evaluate(monkeypatch, tmp_path):
    golden = tmp_path / "g.json"
    golden.write_text(json.dumps([{"query": "a", "filters": {}, "expect": []},
                                   {"query": "b", "filters": {"space": "shared"}, "expect": []}]))
    seen = {}
    monkeypatch.setattr(H, "_open_driver", lambda ws: object())
    monkeypatch.setattr(H, "make_rankers", lambda drv, ws: {"r": lambda q, f, k: []})
    monkeypatch.setattr(H, "evaluate", lambda g, *a, **k: seen.update(n=len(g)) or {
        "per_ranker": {"r": {"exact5": 0, "ndcg5": 0, "recall5": 0, "mrr": 0, "unjudged": [],
                              "judged_queries": 0, "candidates": 0}}, "pool_size": 0})
    out = tmp_path / "o.json"
    assert H.main(["--golden", str(golden), "--rankers", "r", "--subset", "scoped", "--json", str(out)]) == 0
    assert seen["n"] == 1
    written = json.loads(out.read_text())
    assert written["subset"] == "scoped" and written["queries"] == 1


def test_main_rankers_selects_in_label_mode_and_falls_back_with_warning(tmp_path, capsys, monkeypatch):
    p = tmp_path / "queries.json"
    p.write_text(json.dumps([{"query": "q1", "filters": {}, "expect": []}]))
    ranker_a = _ranker({"q1": ["A"]})
    ranker_b = _ranker({"q1": ["B"]})
    monkeypatch.setattr(H, "make_rankers", lambda driver, workspace: {"a": ranker_a, "b": ranker_b})
    monkeypatch.setattr(H, "_open_driver", lambda workspace: object())

    assert H.main(["--golden", str(p), "--label", "--rankers", "a"]) == 0
    out = json.loads(capsys.readouterr().out)
    names = {c["name"]: c["seen_in"] for c in out[0]["candidates"]}
    assert names == {"A": ["a"]}

    assert H.main(["--golden", str(p), "--label", "--rankers", "zzz"]) == 0
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    names = {c["name"]: c["seen_in"] for c in out[0]["candidates"]}
    assert names == {"A": ["a"], "B": ["b"]}
    assert "warning" in captured.err.lower() and "zzz" in captured.err


def test_evaluate_empty_pool_is_unjudged_not_zero():
    """review #3: a query no ranker retrieved used to count as judged with nDCG 0."""
    r = _ranker({"reserve balance guard": ["Reserve Guard"], "grok hybrid floor": ["Floor Fact"]})   # "nothing here" -> []

    def judge(messages):
        user = messages[1]["content"]
        names = [line.split("name: ", 1)[1] for line in user.splitlines() if line.startswith("name: ")]
        return json.dumps([{"name": n, "grade": 2, "why": ""} for n in names])

    res = H.evaluate(GOLDEN, {"r": r}, judge, model="m", cache=None)
    pr = res["per_ranker"]["r"]
    assert pr["unjudged"] == ["nothing here"]
    assert pr["judged_queries"] == 2
    assert H.gate(res, res, "r") is False          # unjudged golden fails closed


def test_evaluate_exact5_shares_denominator_with_judged_metrics():
    """review #3: exact5 was averaged over every golden row while ndcg5/recall5/mrr
    skipped unjudged rows, so the columns had different denominators."""
    # one judged hit, one judged miss (exact5 over judged rows = 0.5); the unjudged row must not move it
    r = _ranker({"reserve balance guard": ["Reserve Guard"], "grok hybrid floor": ["Z"], "nothing here": ["Y"]})

    def judge(messages):
        user = messages[1]["content"]
        if "nothing here" in user:
            return "garbage"                                   # this one query is unjudged
        names = [line.split("name: ", 1)[1] for line in user.splitlines() if line.startswith("name: ")]
        return json.dumps([{"name": n, "grade": 2, "why": ""} for n in names])

    res = H.evaluate(GOLDEN, {"r": r}, judge, model="m", cache=None, k=5)
    pr = res["per_ranker"]["r"]
    assert pr["unjudged"] == ["nothing here"] and pr["judged_queries"] == 2
    judged = [g for g in GOLDEN if g["query"] != "nothing here"]
    expected = sum(H.exact_hit_rate(r(g["query"], g["filters"], 10), g["expect"], 5) for g in judged) / len(judged)
    assert pr["exact5"] == pytest.approx(expected)


def _canned(exact, ndcg, recall):
    return {"per_ranker": {"r": {"exact5": exact, "ndcg5": ndcg, "recall5": recall, "mrr": 0.5,
                                 "unjudged": [], "judged_queries": 2, "candidates": 4}}, "pool_size": 4}


def test_main_gate_against_applies_ship_gate_and_exit_code(monkeypatch, tmp_path, capsys):
    """review #3: gate() existed but no CLI path reached it; mirror eval-edges --gate-against."""
    golden = tmp_path / "g.json"
    golden.write_text(json.dumps([{"query": "a", "filters": {}, "expect": []}]))
    before = tmp_path / "before.json"
    before.write_text(json.dumps(_canned(0.5, 0.6, 0.5)))
    monkeypatch.setattr(H, "_open_driver", lambda ws: object())
    monkeypatch.setattr(H, "make_rankers", lambda drv, ws: {"r": lambda q, f, k: []})

    monkeypatch.setattr(H, "evaluate", lambda g, *a, **k: _canned(0.5, 0.6, 0.5))
    assert H.main(["--golden", str(golden), "--rankers", "r", "--gate-against", str(before), "--gate-ranker", "r"]) == 0
    assert "gate: PASS" in capsys.readouterr().out

    monkeypatch.setattr(H, "evaluate", lambda g, *a, **k: _canned(0.5, 0.5, 0.5))   # ndcg5 dropped
    assert H.main(["--golden", str(golden), "--rankers", "r", "--gate-against", str(before), "--gate-ranker", "r"]) == 1
    assert "gate: FAIL" in capsys.readouterr().out
