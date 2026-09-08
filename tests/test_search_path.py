# tests/test_search_path.py
"""search_vector: SEARCH path, latched fallback, timeouts, degrade rules (spec §3)."""
import logging

import pytest
from neo4j import Query
from neo4j.exceptions import ClientError, Neo4jError

import ai_memory.search as S


def _ce(code: str, message: str) -> ClientError:
    """A ClientError whose `.code` really reflects `code` (issue C2): the
    dict-constructor fake (`ClientError({"code": ..., "message": ...})`)
    reports `.code == "Neo.DatabaseError.General.UnknownError"` in this
    driver version — `.code` is a read-only property hydrated from the
    server response, not from the dict passed to __init__."""
    return Neo4jError._hydrate_neo4j(code=code, message=message)


class FakeResult(list):
    pass


class FakeSession:
    def __init__(self, script):
        # script: list of callables(query_text, params) -> rows or raising
        self.script = list(script)
        self.calls = []

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def run(self, query, params=None, **kw):
        text = query.text if isinstance(query, Query) else query
        self.calls.append((query, dict(params or {}, **kw)))
        step = self.script.pop(0)
        return FakeResult(step(text, params or kw))


class FakeDriver:
    def __init__(self, script):
        self.session_obj = FakeSession(script)
        self.closed = False

    def session(self): return self.session_obj
    def close(self): self.closed = True


def _rec(name, s=0.9):
    return {"name": name, "text": f"about {name}", "key_points": ["p"], "assistant": "Grok",
            "status": None, "space": None, "s": s}


def _22nd3():
    return _ce("Neo.ClientError.Statement.PropertyNotFound",
               "22ND3: The property `assistant` is not an additional property for vector search with filters on the vector index `x`.")


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    S.reset_fallback()
    monkeypatch.setattr(S, "_embed", lambda q: [0.0] * 768)   # no Ollama
    yield
    S.reset_fallback()


def test_search_path_uses_cypher25_query_object_with_timeout():
    drv = FakeDriver([lambda t, p: [_rec("A"), _rec("B", 0.8)]])
    hits = S.search_vector("q", driver=drv, max_results=5, assistant="Grok")
    q, params = drv.session_obj.calls[0]
    assert isinstance(q, Query) and q.text.lstrip().startswith("CYPHER 25")
    assert q.timeout == S.get_query_timeout()
    assert "WHERE f.assistant = $assistant" in q.text
    assert params["pool"] == 20 and params["assistant"] == "Grok"
    assert [h["name"] for h in hits] == ["A", "B"]
    assert hits[0]["via"] == "vec" and hits[0]["vec_score"] == 0.9 and hits[0]["score"] == 0.9


def test_pool_param_widens_returned_list_and_max_results_caps_otherwise():
    rows = [_rec(f"n{i}", 1 - i / 100) for i in range(30)]
    drv = FakeDriver([lambda t, p: rows])
    assert len(S.search_vector("q", driver=drv, max_results=5)) == 5
    drv = FakeDriver([lambda t, p: rows])
    assert len(S.search_vector("q", driver=drv, max_results=5, pool=20)) == 20


def test_22nd3_latches_fallback_and_reruns_with_queryNodes(caplog):
    def boom(t, p):
        raise _22nd3()
    drv = FakeDriver([boom, lambda t, p: [_rec("A")]])
    with caplog.at_level(logging.WARNING, logger="ai_memory.search"):
        hits = S.search_vector("q", driver=drv, max_results=5, assistant="Grok")
    assert [h["name"] for h in hits] == ["A"]
    second, params = drv.session_obj.calls[1]
    assert "db.index.vector.queryNodes($index, $pool2, $vec)" in second.text
    assert params["pool2"] == 250 and params["pool"] == 20
    assert S._fallback_state["active"] is True
    assert sum("neo4j_migrate_vector_filters" in r.message for r in caplog.records) == 1
    # next call goes straight to fallback, no second warning
    drv2 = FakeDriver([lambda t, p: [_rec("B")]])
    with caplog.at_level(logging.WARNING, logger="ai_memory.search"):
        S.search_vector("q", driver=drv2, max_results=5)
    assert "queryNodes" in drv2.session_obj.calls[0][0].text
    assert sum("neo4j_migrate_vector_filters" in r.message for r in caplog.records) == 1


def test_search_syntax_error_on_search_statement_latches_fallback(caplog):
    """C2.1: a SyntaxError with no '22ND3'/'additional property' text, raised
    by the SEARCH statement, still latches — keyed on the statement, not the
    error message."""
    def boom(t, p):
        raise _ce("Neo.ClientError.Statement.SyntaxError", "Unsupported language version '25'")
    drv = FakeDriver([boom, lambda t, p: [_rec("A")]])
    with caplog.at_level(logging.WARNING, logger="ai_memory.search"):
        hits = S.search_vector("q", driver=drv, max_results=5)
    assert [h["name"] for h in hits] == ["A"]
    assert S._fallback_state["active"] is True
    assert len(caplog.records) == 1


def test_property_not_found_code_branch_latches_22nd3(caplog):
    """C2.2: the `.code` branch of _is_22nd3 — message has 'additional
    property' but neither '22ND3' nor the literal 'not an additional
    property' phrase, so only the code+message-fragment branch can match."""
    def boom(t, p):
        raise _ce("Neo.ClientError.Statement.PropertyNotFound",
                  "`assistant` is an additional property outside the vector index schema.")
    drv = FakeDriver([boom, lambda t, p: [_rec("A")]])
    with caplog.at_level(logging.WARNING, logger="ai_memory.search"):
        hits = S.search_vector("q", driver=drv, max_results=5)
    assert [h["name"] for h in hits] == ["A"]
    assert S._fallback_state["active"] is True


def test_syntax_error_from_non_search_statement_does_not_latch():
    """C2.3: a SyntaxError from the fulltext leg (no SEARCH clause involved)
    propagates as before and never touches the vector-search latch."""
    def boom(t, p):
        raise _ce("Neo.ClientError.Statement.SyntaxError", "unexpected token near WHERE")
    drv = FakeDriver([boom])
    with pytest.raises(S.Neo4jQueryError):
        S.search_graph("q", driver=drv)
    assert S._fallback_state["active"] is False


def test_is_search_syntax_error_keyed_on_statement_text():
    """C2.4 mutation guard: the same error is a match only when the
    statement it was raised for actually contains SEARCH."""
    err = _ce("Neo.ClientError.Statement.SyntaxError", "Unsupported language version '25'")
    assert S._is_search_syntax_error(err, "CYPHER 25\nMATCH (f:Fact)\nSEARCH f IN (VECTOR INDEX `fact_embeddings` FOR $vec LIMIT $pool) SCORE AS s") is True
    assert S._is_search_syntax_error(err, "CYPHER 25\nMATCH (f:Fact) RETURN f") is False


def test_search_syntax_error_then_missing_index_returns_empty_and_leaves_no_latch(caplog):
    """The latch must be set only once the queryNodes retry has succeeded: when
    the retry itself fails because the index is missing/populating, search_vector
    returns [] per its documented contract and nothing is latched."""
    def boom(t, p):
        raise _ce("Neo.ClientError.Statement.SyntaxError", "Unsupported language version '25'")

    def missing(t, p):
        raise _ce("Neo.ClientError.Procedure.ProcedureCallFailed",
                  "There is no such vector schema index: fact_embeddings")
    drv = FakeDriver([boom, missing])
    with caplog.at_level(logging.WARNING, logger="ai_memory.search"):
        assert S.search_vector("q", driver=drv) == []
    assert "queryNodes" in drv.session_obj.calls[1][0].text
    assert S._fallback_state["active"] is False


def test_22nd3_then_missing_index_returns_empty_and_leaves_no_latch():
    def boom(t, p):
        raise _22nd3()

    def missing(t, p):
        raise _ce("Neo.ClientError.Procedure.ProcedureCallFailed",
                  "Index is still populating")
    drv = FakeDriver([boom, missing])
    assert S.search_vector("q", driver=drv) == []
    assert S._fallback_state["active"] is False


def test_builder_syntax_error_on_search_statement_does_not_latch():
    """A malformed SEARCH statement is a bug in build_search_cypher — it must
    surface, not hide behind the over-fetch fallback (which is how the earlier
    $index parameterization bug masked itself)."""
    def boom(t, p):
        raise _ce("Neo.ClientError.Statement.SyntaxError", "Invalid input 'FOO'")
    drv = FakeDriver([boom])
    with pytest.raises(S.Neo4jQueryError):
        S.search_vector("q", driver=drv)
    assert S._fallback_state["active"] is False
    assert len(drv.session_obj.calls) == 1          # no fallback retry issued


def test_load_supersedes_keeps_every_edge_of_one_keeper():
    """A keeper with several olds has one row per old; the loaded multimap must
    keep them all (a {new: old} dict kept only the last)."""
    drv = FakeDriver([lambda t, p: [{"n": "K", "o": "A"}, {"n": "K", "o": "B"}]])
    assert S.load_supersedes(drv.session()) == {"K": {"A", "B"}}


def test_load_supersedes_is_best_effort_on_failure():
    def boom(t, p):
        raise RuntimeError("down")
    assert S.load_supersedes(FakeDriver([boom]).session()) == {}


def test_fallback_state_mutations_are_lock_guarded():
    """The latch is process-wide and search_vector runs on worker threads."""
    import threading
    assert isinstance(S._fallback_lock, type(threading.Lock()))
    with S._fallback_lock:
        pass


def test_other_client_errors_do_not_latch():
    def boom(t, p):
        raise _ce("Neo.ClientError.Statement.ArgumentError", "bad arg")
    drv = FakeDriver([boom])
    with pytest.raises(S.Neo4jQueryError):
        S.search_vector("q", driver=drv)
    assert S._fallback_state["active"] is False


def test_index_not_found_returns_empty_with_warning_and_no_latch(caplog):
    def boom(t, p):
        raise _ce("Neo.ClientError.Procedure.ProcedureCallFailed",
                  "There is no such vector schema index: factEmbeddingIndex")
    drv = FakeDriver([boom])
    with caplog.at_level(logging.WARNING, logger="ai_memory.search"):
        assert S.search_vector("q", driver=drv) == []
    assert S._fallback_state["active"] is False
    assert any("not found" in r.message.lower() or "no such" in r.message.lower() for r in caplog.records)


def test_latch_expires_after_ttl_and_on_index_name_change(monkeypatch):
    S._fallback_state.update(active=True, until=1000.0, index="factEmbeddingIndex", warned=True)
    monkeypatch.setattr(S.time, "monotonic", lambda: 999.0)
    assert S._use_fallback("factEmbeddingIndex") is True
    monkeypatch.setattr(S.time, "monotonic", lambda: 1001.0)
    assert S._use_fallback("factEmbeddingIndex") is False          # TTL -> re-probe
    S._fallback_state.update(active=True, until=5000.0, index="factEmbeddingIndex")
    assert S._use_fallback("other_index") is False                 # name change -> re-probe


def test_status_is_never_filtered_in_index():
    drv = FakeDriver([lambda t, p: []])
    S.search_vector("q", driver=drv, assistant="Grok", space="shared", trust_filter="trusted")
    q, _ = drv.session_obj.calls[0]
    assert "status" not in q.text.split("SEARCH")[1].split("SCORE")[0]


def test_search_graph_queries_both_fulltext_indexes_and_fuses(caplog):
    script = [lambda t, p: [_rec("A", 3.0)] if p["index"] == "fact_content" else [_rec("B", 9.0), _rec("A", 1.0)]]
    drv = FakeDriver(script * 2)
    hits = S.search_graph("report.md", driver=drv, max_results=5, assistant="Grok")
    texts = [c[0].text for c in drv.session_obj.calls]
    assert all("db.index.fulltext.queryNodes($index, $q)" in t for t in texts)
    assert {c[1]["index"] for c in drv.session_obj.calls} == {"fact_content", "fact_key_points"}
    assert all("WHERE f.assistant = $assistant" in t for t in texts)
    assert [h["name"] for h in hits] == ["A", "B"]      # A appears in both legs
    assert hits[0]["via"] == "ft+kp"


def test_search_graph_caps_fused_hits_to_pool():
    """M3: search_graph must cap to `pool` after merging the two fulltext
    legs, mirroring search_vector's pool cap."""
    script = lambda t, p: [_rec(f"{p['index']}-{i}", 3.0 - i) for i in range(3)]
    drv = FakeDriver([script, script])
    hits = S.search_graph("q", driver=drv, max_results=5, pool=3)
    assert len(hits) == 3


def test_search_graph_degrades_when_key_points_index_missing(caplog):
    def kp_missing(t, p):
        if p["index"] == "fact_key_points":
            raise _ce("Neo.ClientError.Procedure.ProcedureCallFailed",
                      "There is no such fulltext schema index: fact_key_points")
        return [_rec("A", 3.0)]
    drv = FakeDriver([kp_missing, kp_missing])
    with caplog.at_level(logging.WARNING, logger="ai_memory.search"):
        hits = S.search_graph("q", driver=drv)
    assert [h["name"] for h in hits] == ["A"] and hits[0]["via"] == "ft"
    assert any("fact_key_points" in r.message for r in caplog.records)


def test_search_graph_escapes_lucene_and_lowercases_operators():
    drv = FakeDriver([lambda t, p: [], lambda t, p: []])
    S.search_graph("reserve AND (guard) OR NOT", driver=drv)
    q = drv.session_obj.calls[0][1]["q"]
    assert "\\(" in q and " and " in q and " or " in q and " not" in q


def test_search_hybrid_fuses_both_legs_and_applies_rank_rules():
    def script(t, p):
        if "SEARCH" in t:            # vector leg: an active hit and its superseded sibling
            return [_rec("Kelly Criterion", 0.9),
                    dict(_rec("Kelly Criterion (15:30 EDT)", 0.85), status="superseded")]
        if "fulltext" in t:          # two lexical calls (ft, kp)
            return [_rec("Other", 5.0)] if p["index"] == "fact_content" else []
        if "SUPERSEDES" in t:
            return []
        raise AssertionError(t)
    drv = FakeDriver([script] * 4)
    hits = S.search_hybrid("Kelly Criterion", driver=drv, k=5)
    names = [h["name"] for h in hits]
    assert names[0] == "Kelly Criterion"                    # exact-name boost among active
    assert "Kelly Criterion (15:30 EDT)" not in names       # collapsed: superseded sibling of an active hit
    assert "Other" in names
    assert set(hits[0]) >= {"name", "teaser", "key_points", "assistant", "status", "space", "score", "via"}


def test_search_hybrid_vector_only_floor_when_lexical_empty():
    def script(t, p):
        if "SEARCH" in t:
            return [dict(_rec("strong", 0.88)), dict(_rec("weak", 0.75))]
        return []
    drv = FakeDriver([script] * 4)
    assert [h["name"] for h in S.search_hybrid("q", driver=drv, k=5)] == ["strong"]


def test_search_hybrid_modes():
    # mode="vector": SEARCH call, then a supersedes lookup (same pipeline as hybrid).
    def vec_script(t, p):
        return [_rec("V", 0.9)] if "SEARCH" in t else []
    drv = FakeDriver([vec_script, vec_script])
    assert [h["name"] for h in S.search_hybrid("q", driver=drv, mode="vector")] == ["V"]

    # mode="fulltext": two fulltext calls (ft, kp), then a supersedes lookup.
    def lex_script(t, p):
        if "SUPERSEDES" in t:
            return []
        return [_rec("F", 2.0)] if p.get("index") == "fact_content" else []
    drv = FakeDriver([lex_script, lex_script, lex_script])
    assert [h["name"] for h in S.search_hybrid("q", driver=drv, mode="fulltext")] == ["F"]

    with pytest.raises(ValueError):
        S.search_hybrid("q", driver=FakeDriver([]), mode="nope")


def test_search_hybrid_vector_mode_floor_and_sink_below_active():
    """I2.1: mode='vector' runs through the same floor -> fuse -> rank_adjust
    pipeline as hybrid. A superseded hit on a different topic sinks below the
    active hits; a weak active hit below the 0.80 floor is dropped."""
    def script(t, p):
        if "SEARCH" in t:
            return [
                dict(_rec("Alpha", 0.90)),                                    # active, above floor
                dict(_rec("Beta", 0.99), status="superseded"),                # different topic, above floor
                dict(_rec("Gamma", 0.10)),                                    # active, below floor
            ]
        return []
    drv = FakeDriver([script, script])
    names = [h["name"] for h in S.search_hybrid("q", driver=drv, mode="vector", k=5)]
    assert names == ["Alpha", "Beta"]           # Gamma dropped by the floor; Beta sunk below Alpha


def test_search_hybrid_fulltext_and_hybrid_via_agree():
    """I2.2: a lexical-only hit gets the same `via` tag in mode='fulltext'
    as it does in mode='hybrid' — both route through the outer fuse_rrf."""
    def lex_script(t, p):
        if "SUPERSEDES" in t:
            return []
        return [_rec("X", 2.0)] if p.get("index") == "fact_content" else []

    def hybrid_script(t, p):
        if "SEARCH" in t:
            return []
        if "SUPERSEDES" in t:
            return []
        return [_rec("X", 2.0)] if p.get("index") == "fact_content" else []

    drv_fulltext = FakeDriver([lex_script, lex_script, lex_script])
    fulltext_hits = S.search_hybrid("q", driver=drv_fulltext, mode="fulltext", k=5)

    drv_hybrid = FakeDriver([hybrid_script] * 4)
    hybrid_hits = S.search_hybrid("q", driver=drv_hybrid, mode="hybrid", k=5)

    assert fulltext_hits[0]["via"] == hybrid_hits[0]["via"] == "lex"


def test_memory_client_search_delegates_to_search_hybrid(monkeypatch, tmp_path):
    from ai_memory import MemoryClient
    seen = {}
    monkeypatch.setattr(S, "search_hybrid", lambda q, **kw: seen.update(kw) or [{"name": "X", "score": 1, "via": "vec"}])
    import ai_memory
    monkeypatch.setattr(ai_memory, "search_hybrid", S.search_hybrid, raising=False)
    c = MemoryClient(workspace=tmp_path)
    monkeypatch.setattr(c, "driver", lambda: "drv")
    out = c.search("q", assistant="Grok", space="shared", graph=True, max_results=3)
    assert out[0]["name"] == "X"
    assert seen["assistant"] == "Grok" and seen["space"] == "shared" and seen["k"] == 3 and seen["mode"] == "hybrid"
