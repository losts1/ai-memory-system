from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ai_memory.embed import (
    EMBED_CHARS,
    EMBED_DIM,
    EMBED_MODEL,
    EMBED_PARAM_NAMES,
    EMBED_TIMEOUT_S,
    build_embed_subquery,
    detect_boilerplate,
    drop_prev,
    embed_all,
    embed_fact,
    embed_params,
    embed_text,
    fact_embed_text,
    gram_tokens,
    normalize_ws,
    read_fact_text,
    rollback_prev,
    strip_boilerplate,
    text_sha,
    vector_stats,
)
from ai_memory.retrieval_config import RetrievalConfig

FIX = Path(__file__).parent / "fixtures" / "embed_text_cases.json"


def test_normalize_ws_collapses_all_whitespace():
    assert normalize_ws("  a\n\n b\t c  ") == "a b c"


def test_fact_embed_text_order_and_dash_lines():
    t = fact_embed_text("Name", "Sum.", ["p1", "", "p2"], "Body text", ())
    assert t == "Name Sum. - p1 - p2 Body text"


def test_fact_embed_text_skips_missing_parts():
    assert fact_embed_text("N", None, None, None, ()) == "N"
    assert fact_embed_text("N", "", [], "", ()) == "N"


def test_fact_embed_text_string_key_points_is_one_point():
    assert fact_embed_text("N", "S", "abc", None, ()) == "N S - abc"


def test_fact_embed_text_caps_before_stripping():
    long = "x" * 5000
    t = fact_embed_text("N", long, None, None, ())
    assert len(t) == EMBED_CHARS


def test_gram_tokens_alnum_lowercase():
    assert gram_tokens("Order-Flow (OFI) 30]") == ["order", "flow", "ofi", "30"]


def test_detect_boilerplate_threshold_is_max_10_or_one_percent():
    tpl = "topic selection gap filling memory md covers"
    texts = [f"{tpl} fact {i}" for i in range(9)] + ["unrelated words here only once"]
    assert detect_boilerplate(texts) == frozenset()          # 9 < max(10, 1)
    texts.append(f"{tpl} fact 9")
    grams = detect_boilerplate(texts)                        # 10 >= 10
    assert "topic selection gap filling" in grams
    assert "fact 9 unrelated words" not in grams
    # 1% rule: N = 2000 -> threshold 20
    many = [f"{tpl} z {i}" for i in range(15)] + [f"noise {i} {i} {i} {i}" for i in range(1985)]
    assert detect_boilerplate(many) == frozenset()          # 15 < 20


def test_strip_boilerplate_removes_runs_only():
    grams = {"topic selection gap filling", "selection gap filling memory", "probability of informed trading"}
    # two consecutive grams -> a run of 6 tokens is removed; the single gram survives
    text = "Topic Selection Gap Filling memory. VPIN is the probability of informed trading."
    out = strip_boilerplate(text, grams)
    assert out == "VPIN is the probability of informed trading."


def test_strip_boilerplate_preserves_case_and_punctuation_of_survivors():
    grams = {"a b c d", "b c d e"}
    assert strip_boilerplate("Keep, THIS! a b c d e Then-more.", grams) == "Keep, THIS! Then-more."


def test_strip_boilerplate_no_grams_is_identity_modulo_ws():
    assert strip_boilerplate("  hello   world ", frozenset()) == "hello world"


def test_strip_boilerplate_no_punctuation_leak():
    """Punctuation inside or after a removed run is also removed."""
    grams = {"a b c d", "b c d e"}
    assert strip_boilerplate("keep0 a, b c d e keep1", grams) == "keep0 keep1"


def test_strip_boilerplate_punctuation_after_run_at_end():
    """Punctuation after a run that ends the text is removed."""
    grams = {"w x y z", "x y z zz"}
    assert strip_boilerplate("start w x y z zz!", grams) == "start"


def test_strip_boilerplate_run_at_start_of_text():
    """A run of exactly min_run at the start is removed."""
    grams = {"a b c d", "b c d e"}
    assert strip_boilerplate("a b c d e keep", grams) == "keep"


def test_text_sha_includes_version():
    a = text_sha("same text", 1)
    b = text_sha("same text", 2)
    assert a != b and len(a) == 16
    assert a == hashlib.sha256(b"1\nsame text").hexdigest()[:16]


def test_fixture_cases_byte_equal():
    cases = json.loads(FIX.read_text(encoding="utf-8"))
    assert len(cases) >= 6
    for c in cases:
        got = fact_embed_text(c["name"], c["summary"], c["key_points"], c["content"], frozenset(c["boilerplate"]))
        assert got == c["expected"], c["id"]


class FakeResult:
    def __init__(self, rows): self._rows = rows
    def single(self): return self._rows[0] if self._rows else None
    def __iter__(self): return iter(self._rows)


class FakeSession:
    def __init__(self, rows_by_call=None):
        self.calls = []; self.rows_by_call = list(rows_by_call or [])
    def run(self, cypher, params=None, **kw):
        p = dict(params or {}); p.update(kw); self.calls.append((cypher, p))
        return FakeResult(self.rows_by_call.pop(0) if self.rows_by_call else [])


CFG = RetrievalConfig(version=7, boilerplate=frozenset())
VEC = [0.1] * EMBED_DIM


def test_embed_text_returns_none_when_ollama_missing(monkeypatch):
    import builtins
    real = builtins.__import__
    def fake(name, *a, **k):
        if name == "ollama":
            raise ImportError("no ollama")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    assert embed_text("hello") is None


def test_embed_text_rejects_wrong_dimension(monkeypatch):
    import sys
    import types

    class FakeClient:
        def __init__(self, timeout=None): pass
        def embeddings(self, model, prompt): return {"embedding": [0.0] * 10}
    monkeypatch.setitem(sys.modules, "ollama", types.SimpleNamespace(Client=FakeClient))
    assert embed_text("hello") is None

    class FakeClientOk:
        def __init__(self, timeout=None): pass
        def embeddings(self, model, prompt): return {"embedding": [0.5] * EMBED_DIM}
    monkeypatch.setitem(sys.modules, "ollama", types.SimpleNamespace(Client=FakeClientOk))
    assert embed_text("hello") == [0.5] * EMBED_DIM


def test_embed_text_uses_client_with_bounded_timeout(monkeypatch):
    import sys
    import types
    seen = {}

    class FakeClient:
        def __init__(self, timeout=None): seen["timeout"] = timeout
        def embeddings(self, model, prompt): return {"embedding": [0.5] * EMBED_DIM}
    monkeypatch.setitem(sys.modules, "ollama", types.SimpleNamespace(Client=FakeClient))
    assert embed_text("hello") == [0.5] * EMBED_DIM
    assert seen["timeout"] == EMBED_TIMEOUT_S == 30.0


def test_embed_text_non_numeric_same_length_vector_returns_none(monkeypatch):
    import sys
    import types

    class FakeClient:
        def __init__(self, timeout=None): pass
        def embeddings(self, model, prompt): return {"embedding": ["x"] * EMBED_DIM}
    monkeypatch.setitem(sys.modules, "ollama", types.SimpleNamespace(Client=FakeClient))
    assert embed_text("hello") is None


def test_build_embed_subquery_shape():
    q = build_embed_subquery(["summary", "key_points", "content"])
    assert q.startswith("CALL {") and q.rstrip().endswith("}")
    assert "WITH f WHERE coalesce(f.summary, '') = $cas_summary" in q
    assert "coalesce(f.key_points, []) = $cas_key_points" in q
    assert "coalesce(f.content, '') = $cas_content" in q
    for p in EMBED_PARAM_NAMES:
        assert f"f.{p} = ${p}" in q
    assert "RETURN count(f) AS embedded" in q
    assert "embedding_prev" not in q
    assert "f.embedding_prev = f.embedding," in build_embed_subquery(["content"], keep_prev=True)


def test_build_embed_subquery_rejects_unknown_field():
    with pytest.raises(ValueError):
        build_embed_subquery(["name"])


def test_embed_params_fills_defaults():
    p = embed_params(VEC, "abcd" * 4, 7, cas={"summary": None, "key_points": None, "content": "c"})
    assert p["embedding"] == VEC and p["embedding_model"] == EMBED_MODEL and p["embedding_dim"] == EMBED_DIM
    assert p["embedding_text_sha"] == "abcd" * 4 and p["boilerplate_version"] == 7
    assert p["cas_summary"] == "" and p["cas_key_points"] == [] and p["cas_content"] == "c"


def test_cas_default_returns_a_fresh_value_each_call():
    from ai_memory.embed import _cas_default
    assert _cas_default("summary") == "" and _cas_default("content") == ""
    a, b = _cas_default("key_points"), _cas_default("key_points")
    assert a == [] and b == [] and a is not b


def test_embed_params_key_points_string_passes_through_unwrapped():
    p = embed_params(VEC, "sha", 7, cas={"key_points": "abc"})
    assert p["cas_key_points"] == "abc"
    p = embed_params(VEC, "sha", 7, cas={"key_points": ["a", "b"]})
    assert p["cas_key_points"] == ["a", "b"]


def test_embed_fact_writes_one_cas_statement():
    s = FakeSession([[{"name": "N", "summary": "S", "key_points": ["k"], "content": None}], [{"embedded": 1}]])
    out = embed_fact(s, "N", CFG, embed_fn=lambda t: VEC)
    assert out == "embedded"
    assert len(s.calls) == 2
    cypher, params = s.calls[1]
    assert cypher.startswith("MATCH (f:Fact {name: $name})")
    assert "CALL {" in cypher and "$cas_summary" in cypher and "$cas_key_points" in cypher and "$cas_content" in cypher
    assert params["cas_summary"] == "S" and params["cas_key_points"] == ["k"] and params["cas_content"] == ""
    assert params["embedding_text_sha"] == text_sha("N S - k", 7)
    assert params["boilerplate_version"] == 7


def test_embed_fact_reports_cas_skip_and_failures():
    s = FakeSession([[{"name": "N", "summary": "S", "key_points": [], "content": None}], [{"embedded": 0}]])
    assert embed_fact(s, "N", CFG, embed_fn=lambda t: VEC) == "cas_skipped"
    s = FakeSession([[{"name": "N", "summary": "S", "key_points": [], "content": None}]])
    assert embed_fact(s, "N", CFG, embed_fn=lambda t: None) == "embed_failed"
    assert len(s.calls) == 1                       # no write attempted
    s = FakeSession([[]])
    assert embed_fact(s, "gone", CFG, embed_fn=lambda t: VEC) == "missing"


def test_embed_fact_empty_text_returns_embed_failed_without_calling_embed_fn():
    calls = []
    s = FakeSession([[{"name": "", "summary": None, "key_points": [], "content": None}]])
    assert embed_fact(s, "", CFG, embed_fn=lambda t: calls.append(t) or VEC) == "embed_failed"
    assert calls == []                              # embed_fn never called on empty text
    assert len(s.calls) == 1                        # only the read


def test_embed_fact_keep_prev_flag_reaches_cypher():
    s = FakeSession([[{"name": "N", "summary": "S", "key_points": [], "content": None}], [{"embedded": 1}]])
    embed_fact(s, "N", CFG, embed_fn=lambda t: VEC, keep_prev=True)
    assert "f.embedding_prev = f.embedding," in s.calls[1][0]


def test_read_fact_text_shape():
    s = FakeSession([[{"name": "N", "summary": None, "key_points": None, "content": "c"}]])
    assert read_fact_text(s, "N") == {"name": "N", "summary": None, "key_points": None, "content": "c"}
    assert read_fact_text(FakeSession([[]]), "x") is None


class FakeDriver:
    def __init__(self, session): self._s = session
    def session(self): return self._s
    def close(self): pass


class Sess(FakeSession):
    def __enter__(self): return self
    def __exit__(self, *a): return False


ROWS = [{"name": "A", "summary": "topic selection gap filling one", "key_points": [], "content": None},
        {"name": "B", "summary": "topic selection gap filling two", "key_points": ["k"], "content": None}]


def test_embed_all_publishes_then_embeds_every_fact():
    # call order: (1) load all facts, (2) publish config, then per fact: read, write
    s = Sess([ROWS, [{"version": 2, "updated_at": "T"}],
              [ROWS[0]], [{"embedded": 1}],
              [ROWS[1]], [{"embedded": 0}]])
    out = embed_all(FakeDriver(s), embed_fn=lambda t: VEC, log=lambda *a: None)
    assert out["facts"] == 2 and out["embedded"] == 1 and out["cas_skipped"] == 1 and out["config_version"] == 2
    assert "RetrievalConfig" in s.calls[1][0] and "c.version = c.version + 1" in s.calls[1][0]
    assert isinstance(out["grams"], int)
    assert out["cas_skipped_names"] == ["B"]


def test_embed_all_without_publish_uses_current_config_and_fails_closed_when_missing():
    s = Sess([ROWS, [{"version": 5, "boilerplate": [], "updated_at": None}],
              [ROWS[0]], [{"embedded": 1}], [ROWS[1]], [{"embedded": 1}]])
    out = embed_all(FakeDriver(s), publish=False, embed_fn=lambda t: VEC, log=lambda *a: None)
    assert out["config_version"] == 5 and out["embedded"] == 2
    s = Sess([ROWS, []])
    with pytest.raises(RuntimeError):
        embed_all(FakeDriver(s), publish=False, embed_fn=lambda t: VEC, log=lambda *a: None)


def test_embed_all_stale_only_with_publish_raises_before_any_session_call():
    s = Sess([])
    with pytest.raises(ValueError, match="stale_only requires publish=False"):
        embed_all(FakeDriver(s), stale_only=True, publish=True, embed_fn=lambda t: VEC, log=lambda *a: None)
    assert s.calls == []


def test_embed_all_stale_only_skips_matching_sha():
    cfg_rows = [{"version": 5, "boilerplate": [], "updated_at": None}]
    fresh_sha = text_sha(fact_embed_text("A", ROWS[0]["summary"], [], None, frozenset()), 5)
    rows = [dict(ROWS[0], embedding_text_sha=fresh_sha, boilerplate_version=5), dict(ROWS[1], embedding_text_sha="stale", boilerplate_version=5)]
    s = Sess([rows, cfg_rows, [ROWS[1]], [{"embedded": 1}]])
    out = embed_all(FakeDriver(s), publish=False, stale_only=True, embed_fn=lambda t: VEC, log=lambda *a: None)
    assert out["skipped_fresh"] == 1 and out["embedded"] == 1


def test_embed_all_keep_prev_reaches_writes():
    s = Sess([ROWS[:1], [{"version": 2, "updated_at": "T"}], [ROWS[0]], [{"embedded": 1}]])
    embed_all(FakeDriver(s), keep_prev=True, embed_fn=lambda t: VEC, log=lambda *a: None)
    assert "f.embedding_prev = f.embedding," in s.calls[3][0]


def test_embed_all_caps_cas_skipped_names_at_20():
    many_rows = [{"name": f"F{i}", "summary": "s", "key_points": [], "content": None} for i in range(25)]
    calls = [many_rows, [{"version": 1, "boilerplate": [], "updated_at": None}]]
    for r in many_rows:
        calls.append([r])
        calls.append([{"embedded": 0}])
    s = Sess(calls)
    out = embed_all(FakeDriver(s), publish=False, embed_fn=lambda t: VEC, log=lambda *a: None)
    assert out["cas_skipped"] == 25
    assert len(out["cas_skipped_names"]) == 20


def test_drop_and_rollback_prev():
    s = Sess([[{"n": 3}]])
    assert drop_prev(FakeDriver(s)) == 3
    assert "REMOVE f.embedding_prev" in s.calls[0][0] and "embedding_prev IS NOT NULL" in s.calls[0][0]
    s = Sess([[{"n": 2}]])
    assert rollback_prev(FakeDriver(s)) == 2
    q = s.calls[0][0]
    assert "SET f.embedding = f.embedding_prev" in q
    for p in ("embedding_prev", "embedding_model", "embedding_dim", "embedding_text_sha", "boilerplate_version"):
        assert f"f.{p}" in q.split("REMOVE", 1)[1]


def test_vector_stats_counts():
    cfg = [{"version": 5, "boilerplate": [], "updated_at": None}]
    good_sha = text_sha(fact_embed_text("A", "s", [], None, frozenset()), 5)
    rows = [
        {"name": "A", "summary": "s", "key_points": [], "content": None, "has_emb": True, "model": EMBED_MODEL, "sha": good_sha, "has_prev": False},
        {"name": "B", "summary": "s", "key_points": [], "content": None, "has_emb": True, "model": None, "sha": None, "has_prev": True},
        {"name": "C", "summary": "s", "key_points": [], "content": None, "has_emb": True, "model": "other", "sha": "old", "has_prev": False},
        {"name": "D", "summary": "s", "key_points": [], "content": None, "has_emb": False, "model": None, "sha": None, "has_prev": False},
    ]
    # edge_stats(session) issues two more queries: the rule/edge-count row, then per-Fact degrees.
    edge_cfg_row = [{"rule_version": 3, "edges": 10, "edges_current_rule": 7}]
    degree_rows = [{"degree": 0}, {"degree": 2}, {"degree": 2}, {"degree": 4}]
    s = Sess([cfg, rows, edge_cfg_row, degree_rows])
    st = vector_stats(FakeDriver(s))
    assert st == {"facts": 4, "with_embedding": 3, "without_embedding": 1, "foreign": 1, "wrong_model": 1,
                  "stale": 1, "with_prev": 1, "config_version": 5,
                  "edges": 10, "edges_current_rule": 7, "edges_stale_rule": 3, "rule_version": 3,
                  "isolated": 1, "isolated_pct": pytest.approx(25.0),
                  "max_degree": 4, "p95_degree": pytest.approx(3.7)}


def test_vector_stats_stale_is_none_without_config():
    rows = [{"name": "A", "summary": "s", "key_points": [], "content": None, "has_emb": True,
             "model": EMBED_MODEL, "sha": "x", "has_prev": False}]
    edge_cfg_row = [{"rule_version": 0, "edges": 0, "edges_current_rule": 0}]
    degree_rows = [{"degree": 0}]
    s = Sess([[], rows, edge_cfg_row, degree_rows])
    st = vector_stats(FakeDriver(s))
    assert st["stale"] is None and st["config_version"] is None
    assert st["isolated"] == 1 and st["rule_version"] == 0
