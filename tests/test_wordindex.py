from __future__ import annotations

import json
import math

import pytest

from ai_memory import wordindex as W


def test_tokenize_rules():
    text = "Order-Flow Imbalance (OFI) — 30] [learner 2026-08-30 the market making signal signal signal AI"
    toks = W.tokenize(text, name="Order Flow Imbalance")
    assert toks[:3] == ["order", "flow", "imbalance"]          # name tokens first
    assert "ofi" in toks and "signal" in toks and "ai" in toks   # short allow-list kept
    assert "30" not in toks and "2026" not in toks and "the" not in toks and "learner" in toks
    assert len(set(toks)) == len(toks)


def test_tokenize_cap_and_frequency_order():
    body = " ".join(f"w{i}" for i in range(40)) + " zzz zzz zzz"
    toks = W.tokenize(body, name="", cap=24)
    assert len(toks) == 24 and toks[0] == "zzz"                 # most frequent first among non-name tokens


def test_tokenize_stopwords_include_tz_months_minds():
    assert W.tokenize("EDT UTC nova weft grok jan feb Nothing", name="") == ["nothing"]


def test_idf_and_tfidf():
    idf = W.idf_map({"a": 1, "b": 2, "c": 4}, 4)
    assert idf["a"] == pytest.approx(math.log(4)) and idf["c"] == 0.0
    na = W.tfidf_norm(["a", "b"], idf, default_idf=math.log(4))
    nb = W.tfidf_norm(["b", "x"], idf, default_idf=math.log(4))      # x unknown -> default idf
    assert na == pytest.approx(math.sqrt(idf["a"] ** 2 + idf["b"] ** 2))
    cos = W.tfidf_cosine(["a", "b"], ["b", "x"], idf, na, nb, default_idf=math.log(4))
    assert cos == pytest.approx(idf["b"] ** 2 / (na * nb))
    assert W.tfidf_cosine(["a"], ["x"], idf, na, nb, default_idf=1.0) == 0.0
    assert W.tfidf_cosine([], [], idf, 0.0, 0.0, default_idf=1.0) == 0.0


def test_shared_keywords_sorted_by_idf():
    idf = {"rare": 3.0, "mid": 1.0, "common": 0.1}
    assert W.shared_keywords(["common", "rare", "mid", "x"], ["mid", "common", "rare"], idf) == ["rare", "mid", "common"]


def test_zscore_blend_and_duplicates():
    base = {"t_mean": 0.0, "t_std": 0.1, "c_mean": 0.6, "c_std": 0.1}
    assert W.blend(0.1, 0.7, base) == pytest.approx(1.0)
    assert W.is_duplicate("Shared — x — 2026-08-30", "Shared — x — 2026-08-30 #2", 0.5)
    assert W.is_duplicate("Old", "New", 0.5, {"New": "Old"})
    assert W.is_duplicate("A", "B", 0.96) and not W.is_duplicate("A", "B", 0.94)


def test_is_duplicate_recognises_every_twin_of_one_keeper():
    """K supersedes both A and B: each is a SUPERSEDES twin of K and must not
    spend a RELATED_TO slot, even below DUP_COS and with unrelated names."""
    sup = {"K": {"A", "B"}}
    assert W.is_duplicate("K", "A", 0.0, sup)
    assert W.is_duplicate("K", "B", 0.0, sup)
    assert not W.is_duplicate("K", "C", 0.0, sup)


def test_pick_top_k_above_floor_ties_by_name():
    cands = [("b", 2.0, 0.1, 0.9), ("a", 2.0, 0.1, 0.9), ("c", 1.0, 0.0, 0.5), ("d", 0.5, 0.0, 0.4)]
    assert [c[0] for c in W.pick(cands, floor=1.0, k=5)] == ["a", "b", "c"]
    assert [c[0] for c in W.pick(cands, floor=1.0, k=2)] == ["a", "b"]
    assert W.pick(cands, floor=3.0) == []


def test_edges_from_picks_merges_both_directions():
    picks = {"A": [("B", 2.0, 0.1, 0.9)], "B": [("A", 2.0, 0.1, 0.9), ("C", 1.5, 0.0, 0.8)], "C": []}
    edges = W.edges_from_picks(picks)
    assert set(edges) == {("A", "B"), ("B", "C")}
    assert edges[("A", "B")]["picked_by"] == ["A", "B"] and edges[("A", "B")]["via"] == "both"
    assert edges[("B", "C")]["picked_by"] == ["B"] and edges[("B", "C")]["via"] == "B"
    assert edges[("A", "B")]["weight"] == 2.0 and edges[("B", "C")]["cos"] == 0.8


# --- Task 2: Neo4j I/O + nightly rebuild ------------------------------------


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


class FakeDriver:
    def __init__(self, session):
        self._s = session
    def session(self):
        return self._s
    def close(self):
        pass


class Sess(FakeSession):
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def test_publish_edge_config_statement_and_return():
    s = FakeSession([[{"rule_version": 4}]])
    base = {"t_mean": 0.1, "t_std": 0.2, "c_mean": 0.3, "c_std": 0.4}
    rv = W.publish_edge_config(s, base=base, edge_floor=1.23, n_facts=50, pairs=40000, seed=7)
    assert rv == 4
    cypher, params = s.calls[0]
    assert "MERGE (c:RetrievalConfig {id: $id})" in cypher
    assert "c.rule_version = coalesce(c.rule_version, 0) + 1" in cypher
    for prop in ("edge_floor", "t_mean", "t_std", "c_mean", "c_std", "baseline_pairs", "baseline_seed", "n_facts"):
        assert f"c.{prop} = $" in cypher
    assert params["edge_floor"] == 1.23 and params["n_facts"] == 50
    assert params["pairs"] == 40000 and params["seed"] == 7
    assert params["t_mean"] == 0.1 and params["c_std"] == 0.4


def test_load_edge_config_none_when_missing_or_null():
    assert W.load_edge_config(FakeSession([[]])) is None
    assert W.load_edge_config(FakeSession([[{"rule_version": None}]])) is None


def test_load_edge_config_parses_row():
    row = {"rule_version": 3, "edge_floor": 1.1, "t_mean": 0.1, "t_std": 0.2,
           "c_mean": 0.3, "c_std": 0.4, "n_facts": 10}
    cfg = W.load_edge_config(FakeSession([[row]]))
    assert cfg == {"rule_version": 3, "edge_floor": 1.1, "t_mean": 0.1, "t_std": 0.2,
                   "c_mean": 0.3, "c_std": 0.4, "n_facts": 10}


def test_write_edges_batches_and_statement_shape():
    edges = {
        ("A", "B"): {"weight": 2.0, "tfidf": 0.1, "cos": 0.9, "picked_by": ["A", "B"], "via": "both", "shared_keywords": ["x"]},
        ("B", "C"): {"weight": 1.5, "tfidf": 0.0, "cos": 0.8, "picked_by": ["B"], "via": "B", "shared_keywords": []},
    }
    s = FakeSession([[{"n": 1}], [{"n": 1}]])
    written = W.write_edges(s, edges, rule_version=3, batch=1)
    assert written == 2
    assert len(s.calls) == 2
    cypher, params = s.calls[0]
    assert "UNWIND $rows AS r" in cypher
    assert "MATCH (a:Fact {name: r.a}), (b:Fact {name: r.b})" in cypher
    assert "MERGE (a)-[e:RELATED_TO]->(b)" in cypher
    for prop in ("weight", "tfidf", "cos", "shared_keywords", "picked_by", "via", "rule_version"):
        assert prop in cypher
    assert "REMOVE e.shared_count, e.source" in cypher
    assert params["rv"] == 3
    assert len(params["rows"]) == 1


def test_cutover_deletes_stale_and_null_rule_edges():
    s = FakeSession([[{"n": 5}]])
    deleted = W.cutover(s, rule_version=3)
    assert deleted == 5
    cypher, params = s.calls[0]
    assert "WHERE e.rule_version IS NULL OR e.rule_version <> $rv" in cypher
    assert params["rv"] == 3


def test_write_idf_statement_and_count():
    s = FakeSession([[{"words": 42}]])
    n = W.write_idf(s, n_facts=10)
    assert n == 42
    cypher, params = s.calls[0]
    assert "log(toFloat($n) / df)" in cypher
    assert params["n"] == 10


def test_write_fact_tokens_statement_shape():
    s = FakeSession([[]])
    W.write_fact_tokens(s, "N", ["a", "b"], 1.5)
    cypher, params = s.calls[0]
    assert "MATCH (f:Fact {name: $name})" in cypher
    assert "DELETE old" in cypher
    assert "SET f.tfidf_norm = $norm" in cypher
    assert "UNWIND $tokens AS t" in cypher
    assert "MERGE (w:Word {text: t})" in cypher
    assert "MERGE (f)-[:HAS_WORD]->(w)" in cypher
    assert params == {"name": "N", "tokens": ["a", "b"], "norm": 1.5}


def test_cleanup_orphan_words():
    s = FakeSession([[{"n": 2}]])
    assert W.cleanup_orphan_words(s) == 2
    cypher, _ = s.calls[0]
    assert "Word" in cypher and "HAS_WORD" in cypher


def test_edge_stats_from_scripted_rows():
    s = FakeSession([
        [{"rule_version": 3, "edges": 10, "edges_current_rule": 7}],
        [{"degree": 0}, {"degree": 2}, {"degree": 2}, {"degree": 4}],
    ])
    st = W.edge_stats(s)
    assert st["edges"] == 10 and st["edges_current_rule"] == 7 and st["edges_stale_rule"] == 3
    assert st["rule_version"] == 3
    assert st["n_facts"] == 4 and st["isolated"] == 1 and st["isolated_pct"] == pytest.approx(25.0)
    assert st["max_degree"] == 4
    assert st["p95_degree"] == pytest.approx(3.7)


def test_edge_stats_degree_query_uses_count_not_size():
    # Neo4j 5+ removed size() on a pattern expression (Neo.ClientError.Statement.SyntaxError);
    # the repo convention (ai_memory/graph.py:91) is COUNT { ... }.
    s = FakeSession([
        [{"rule_version": 0, "edges": 0, "edges_current_rule": 0}],
        [],
    ])
    W.edge_stats(s)
    degree_cypher = s.calls[1][0]
    assert "COUNT {" in degree_cypher
    assert "size((" not in degree_cypher


def test_rebuild_edges_without_numpy_raises(monkeypatch):
    import builtins
    real = builtins.__import__
    def fake(name, *a, **k):
        if name == "numpy":
            raise ImportError("no numpy")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    with pytest.raises(RuntimeError, match=r"ai-memory-system\[edges\]"):
        W.rebuild_edges(object())


def test_rebuild_edges_raises_for_zero_facts():
    pytest.importorskip("numpy")
    s = Sess([_CFG_ROW, [], []])
    with pytest.raises(RuntimeError, match="at least 2 embedded Facts"):
        W.rebuild_edges(FakeDriver(s), seed=0, pairs=2000, dry_run=True)


def test_rebuild_edges_raises_for_one_fact():
    pytest.importorskip("numpy")
    rows = [{"name": "A", "summary": "s", "key_points": [], "content": "c",
             "embedding": [1.0, 0.0], "status": "active"}]
    s = Sess([_CFG_ROW, rows, []])
    with pytest.raises(RuntimeError, match="at least 2 embedded Facts"):
        W.rebuild_edges(FakeDriver(s), seed=0, pairs=2000, dry_run=True)


def test_rebuild_edges_raises_when_fewer_than_two_facts_embedded():
    pytest.importorskip("numpy")
    rows = [
        {"name": "A", "summary": "s", "key_points": [], "content": "c", "embedding": None, "status": "active"},
        {"name": "B", "summary": "s", "key_points": [], "content": "c", "embedding": None, "status": "active"},
    ]
    s = Sess([_CFG_ROW, rows, []])
    with pytest.raises(RuntimeError, match="at least 2 embedded Facts"):
        W.rebuild_edges(FakeDriver(s), seed=0, pairs=2000, dry_run=True)


def test_rebuild_edges_raises_when_baseline_mask_is_empty():
    # 6 Facts, only indices 2 and 4 embedded. With seed=0/pairs=3, np.random.default_rng(0)
    # draws ii=[5,3,3], jj=[1,1,0] — none of those sampled pairs touch {2,4}, so the
    # has[ii] & has[jj] mask is empty even though >= 2 Facts are individually embedded.
    pytest.importorskip("numpy")
    rows = [
        {"name": f"F{i}", "summary": "s", "key_points": [], "content": "c",
         "embedding": ([1.0, 0.0, 0.0] if i in (2, 4) else None), "status": "active"}
        for i in range(6)
    ]
    s = Sess([_CFG_ROW, rows, []])
    with pytest.raises(RuntimeError, match="at least 2 embedded Facts"):
        W.rebuild_edges(FakeDriver(s), seed=0, pairs=3, dry_run=True)


def test_rebuild_edges_raises_on_embedding_width_mismatch():
    pytest.importorskip("numpy")
    rows = [
        {"name": "A", "summary": "s", "key_points": [], "content": "c", "embedding": [1.0, 0.0], "status": "active"},
        {"name": "B", "summary": "s", "key_points": [], "content": "c", "embedding": [1.0, 0.0, 0.0], "status": "active"},
    ]
    s = Sess([_CFG_ROW, rows, []])
    with pytest.raises(RuntimeError) as ei:
        W.rebuild_edges(FakeDriver(s), seed=0, pairs=10, dry_run=True)
    msg = str(ei.value)
    assert "B" in msg and "3" in msg and "2" in msg


_DUP_ROWS = [
    {"name": "Dup A", "summary": "shared summary text", "key_points": [],
     "content": "shared body content", "embedding": [1.0, 0.0, 0.0], "status": "active"},
    {"name": "Dup B", "summary": "shared summary text", "key_points": [],
     "content": "shared body content", "embedding": [1.0, 0.0, 0.0], "status": "active"},
]


def test_rebuild_edges_raises_before_any_write_when_no_edges():
    pytest.importorskip("numpy")
    s = Sess([_CFG_ROW, _DUP_ROWS, []])
    with pytest.raises(RuntimeError, match="refusing to cut over"):
        W.rebuild_edges(FakeDriver(s), seed=0, pairs=2000, dry_run=False)
    assert len(s.calls) == 3               # only the 3 reads — no write statement issued


# Synthetic 6-Fact corpus, two clusters of 3. Each cluster: a "hub" fact with a pure
# axis embedding and two "leaf" facts offset by an equal-magnitude, mutually-orthogonal
# perturbation — this makes hub-leaf cosine (and the token-derived tfidf cosine, since
# all three cluster members share byte-identical token sets — the only per-item
# difference is a purely-numeric name suffix, which the tokenizer drops) exactly equal
# (bit-for-bit) for both hub-leaf pairs, so the z-blend floor comparison isn't at the
# mercy of floating-point rounding noise between nominally-tied candidates. Cross-cluster
# pairs are exactly orthogonal (cosine 0) and share no tokens. Below DUP_COS (0.95).
_A1 = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
_A2 = [1.0, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
_A3 = [1.0, 0.0, 0.5, 0.0, 0.0, 0.0, 0.0, 0.0]
_B1 = [0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
_B2 = [0.0, 0.0, 0.0, 0.0, 1.0, 0.5, 0.0, 0.0]
_B3 = [0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.5, 0.0]
_ALPHA = {"Widget Alpha 1", "Widget Alpha 2", "Widget Alpha 3"}
_BETA = {"Gadget Beta 1", "Gadget Beta 2", "Gadget Beta 3"}
_CLUSTER_ROWS = [
    {"name": "Widget Alpha 1", "summary": "alpha widget assembly torque", "key_points": [],
     "content": "fastening bracket clamp linkage", "embedding": _A1, "status": "active"},
    {"name": "Widget Alpha 2", "summary": "alpha widget assembly torque", "key_points": [],
     "content": "fastening bracket clamp linkage", "embedding": _A2, "status": "active"},
    {"name": "Widget Alpha 3", "summary": "alpha widget assembly torque", "key_points": [],
     "content": "fastening bracket clamp linkage", "embedding": _A3, "status": "active"},
    {"name": "Gadget Beta 1", "summary": "beta gadget calibration sensor", "key_points": [],
     "content": "bench routine testing harness", "embedding": _B1, "status": "active"},
    {"name": "Gadget Beta 2", "summary": "beta gadget calibration sensor", "key_points": [],
     "content": "bench routine testing harness", "embedding": _B2, "status": "active"},
    {"name": "Gadget Beta 3", "summary": "beta gadget calibration sensor", "key_points": [],
     "content": "bench routine testing harness", "embedding": _B3, "status": "active"},
]
_CFG_ROW = [{"version": 1, "boilerplate": [], "updated_at": None}]


def test_rebuild_edges_dry_run_connects_within_clusters_only():
    pytest.importorskip("numpy")
    s = Sess([_CFG_ROW, _CLUSTER_ROWS, []])
    report = W.rebuild_edges(FakeDriver(s), seed=0, pairs=2000, dry_run=True)
    assert report["dry_run"] is True
    assert report["n_facts"] == 6
    assert report["isolated"] == 0
    assert report["edges"] == len(report["edge_list"]) and report["edges"] > 0
    for edge in report["edge_list"]:
        a, b = edge["a"], edge["b"]
        assert (a in _ALPHA and b in _ALPHA) or (a in _BETA and b in _BETA)
        assert set(edge) == {"a", "b", "weight", "tfidf", "cos", "picked_by", "via", "shared_keywords"}
    assert report["edge_list"] == sorted(report["edge_list"], key=lambda e: (e["a"], e["b"]))
    assert report["rule_version_next"] == 1              # coalesce(None, 0) + 1, dry-run only
    assert "rule_version" not in report                   # nothing was actually published
    assert "base" in report and set(report["base"]) == {"t_mean", "t_std", "c_mean", "c_std"}
    assert len(s.calls) == 3                               # only the 3 reads — no writes issued
    json.dumps(report)                                     # must be JSON-serialisable


def test_rebuild_edges_full_writes_in_order():
    pytest.importorskip("numpy")
    s = Sess([
        _CFG_ROW, _CLUSTER_ROWS, [],
        [],                          # batched fact-token write (no RETURN)
        [{"words": 16}],             # write_idf
        [{"rule_version": 1}],       # publish_edge_config
        [{"n": 4}],                  # write_edges
        [{"n": 0}],                  # cutover
        [{"n": 0}],                  # cleanup_orphan_words
    ])
    report = W.rebuild_edges(FakeDriver(s), seed=0, pairs=2000, dry_run=False)
    assert report["dry_run"] is False
    assert report["rule_version"] == 1                     # the real value publish_edge_config returned
    assert "rule_version_next" not in report               # write mode reports the real version only
    assert report["edges_written"] == 4
    assert report["edges_deleted"] == 0
    assert report["words_orphaned"] == 0
    assert report["edges"] == len(report["edge_list"])
    json.dumps(report)                                     # must be JSON-serialisable
    calls = [c[0] for c in s.calls]
    assert len(calls) == 9
    assert "HAS_WORD" in calls[3] and "UNWIND $rows AS r" in calls[3]
    assert "log(toFloat($n) / df)" in calls[4]
    assert "coalesce(c.rule_version, 0) + 1" in calls[5]
    assert "MERGE (a)-[e:RELATED_TO]->(b)" in calls[6]
    assert "e.rule_version IS NULL OR e.rule_version <> $rv" in calls[7]
    assert "Word" in calls[8]


# --- Task 3: on-write maintenance ------------------------------------------


# All fixtures below give X and every candidate empty HAS_WORD tokens, so tfidf_cosine
# is always 0 (norm_x == 0.0 short-circuits it) and blend(t=0, cos, cfg) collapses to
# 5*(cos - 0.5) with this baseline — letting `cos` stay a realistic value (< DUP_COS)
# while still landing each candidate's weight exactly where the test needs it. `cos`
# fixture values here represent the RAW cosine already de-normalised by the row query's
# `2 * vector.similarity.cosine(...) - 1` — see test_maintain_edges_for_row_query_*.
_MEC_CFG = {"rule_version": 7, "edge_floor": 0.2, "t_mean": 0.0, "t_std": 1.0,
            "c_mean": 0.5, "c_std": 0.1, "n_facts": 50}


def test_maintain_edges_for_no_config_skips_with_no_queries():
    s = FakeSession()
    result = W.maintain_edges_for(s, "Target", None)
    assert result == {"picked": 0, "repicked": 0, "deleted": 0, "revoked": 0, "skipped": "no_config"}
    assert s.calls == []


def test_maintain_edges_for_missing_fact_skips():
    s = FakeSession([[]])
    result = W.maintain_edges_for(s, "Target", _MEC_CFG)
    assert result == {"picked": 0, "repicked": 0, "deleted": 0, "revoked": 0, "skipped": "missing"}
    assert len(s.calls) == 1


def test_maintain_edges_for_no_embedding_skips():
    s = FakeSession([[{"has_emb": False, "toks": [], "norm": None}]])
    result = W.maintain_edges_for(s, "Target", _MEC_CFG)
    assert result == {"picked": 0, "repicked": 0, "deleted": 0, "revoked": 0, "skipped": "no_embedding"}
    assert len(s.calls) == 1


def test_maintain_edges_for_row_query_denormalises_cosine():
    """F1: Neo4j's vector.similarity.cosine returns (1+cos)/2, not raw cosine (verified
    live on 2026.04: orthogonal -> 0.5, opposite -> 0.0). The row query must undo that
    scaling so `cos` matches the raw-cosine axis rebuild_edges' baselines describe."""
    s = FakeSession([
        [{"has_emb": True, "toks": [], "norm": None}],   # read X
        [],                                               # idf fetch #1
        [],                                               # write_fact_tokens
        [],                                               # row query -> no candidates
        [],                                               # idf fetch #2
        [],                                               # load_supersedes
        [{"revoked": 0, "deleted": 0}],                   # revocation (keep=[])
    ])
    W.maintain_edges_for(s, "Target", _MEC_CFG)
    row_q, row_p = s.calls[3]
    assert "2 * vector.similarity.cosine(f.embedding, g.embedding) - 1 AS cos" in row_q
    assert row_p == {"n": "Target"}


def test_maintain_edges_for_blend_uses_raw_cosine_without_further_conversion():
    """Unit case pinning F1's downstream consumption: a row whose `cos` already IS the
    de-normalised value the fixed query would produce for a server score of 0.5
    (orthogonal, raw cos 0.0) must flow into blend() unchanged -- not re-scaled a
    second time in Python."""
    cfg = {"rule_version": 1, "edge_floor": -5.0, "t_mean": 0.0, "t_std": 1.0,
           "c_mean": 0.0, "c_std": 1.0, "n_facts": 10}
    s = FakeSession([
        [{"has_emb": True, "toks": [], "norm": None}],     # read X
        [],                                                 # idf fetch #1
        [],                                                 # write_fact_tokens
        [{"name": "G", "cos": 0.0, "toks": [], "norm": None}],  # row query: raw cos already 0.0
        [],                                                 # idf fetch #2
        [],                                                 # load_supersedes
        [],                                                 # already_picks_x precheck (G not in it)
        [],                                                 # current picks for G (0 < k)
        [],                                                 # existing picked_by for (G, Target)
        [{"n": 1}],                                         # write_edges
        [{"revoked": 0, "deleted": 0}],                     # revocation (keep=["G"])
    ])
    result = W.maintain_edges_for(s, "Target", cfg)
    assert result["picked"] == 1
    write_edges_p = s.calls[9][1]
    row = write_edges_p["rows"][0]
    assert row["weight"] == pytest.approx(0.0)              # 0.5*zscore(0,0,1) + 0.5*zscore(0,0,1) == 0.0
    assert row["cos"] == pytest.approx(0.0)


def test_maintain_edges_for_picks_repicks_and_unpicks_weakest():
    """X has 3 neighbours above the floor (A, B, C) and picks all 3; D is below the
    floor and excluded entirely. A already has k=5 picks whose worst is weaker than
    b(X,A) -> A re-picks X and un-picks its weakest ('old'), which is deleted since
    nobody else had picked it. B has only 2 picks -> re-picks X, nothing deleted.
    None of A/B/C already had X among their own picks, so all three go through the
    full current-picks/eviction path rather than the weight-update fast path."""
    s = FakeSession([
        [{"has_emb": True, "toks": [], "norm": None}],            # read X
        [],                                                        # idf fetch #1 (toks_x empty)
        [],                                                        # write_fact_tokens (no RETURN)
        [                                                           # row query
            {"name": "A", "cos": 0.78, "toks": [], "norm": None},    # blend = 1.4
            {"name": "B", "cos": 0.60, "toks": [], "norm": None},    # blend = 0.5
            {"name": "C", "cos": 0.56, "toks": [], "norm": None},    # blend = 0.3
            {"name": "D", "cos": 0.51, "toks": [], "norm": None},    # blend = 0.05 (below floor)
        ],
        [],                                                        # idf fetch #2 (union, empty)
        [],                                                        # load_supersedes
        [],                                                        # already_picks_x precheck: none of A/B/C
        [                                                           # current picks for A (k=5, worst=old/0.2)
            {"other": "old", "weight": 0.2}, {"other": "p2", "weight": 0.3},
            {"other": "p3", "weight": 0.5}, {"other": "p4", "weight": 0.7},
            {"other": "p5", "weight": 0.9},
        ],
        [{"other": "q1", "weight": 0.1}, {"other": "q2", "weight": 0.4}],  # current picks for B (2 < k)
        [],                                                        # current picks for C (0 < k)
        [],                                                        # existing picked_by for the 3 pairs (none yet)
        [{"n": 3}],                                                # write_edges
        [{"remaining": 0}],                                        # unpick A-old (nobody else had picked "old")
        [{"revoked": 0, "deleted": 0}],                            # revocation (keep=[A,B,C])
    ])
    result = W.maintain_edges_for(s, "Target", _MEC_CFG)
    assert result == {"picked": 3, "repicked": 3, "deleted": 1, "revoked": 0, "skipped": None}
    assert len(s.calls) == 14

    read_x_q, read_x_p = s.calls[0]
    assert "f.embedding IS NOT NULL AS has_emb" in read_x_q and read_x_p == {"n": "Target"}

    wft_q, wft_p = s.calls[2]
    assert "MERGE (f)-[:HAS_WORD]->(w)" in wft_q
    assert wft_p == {"name": "Target", "tokens": [], "norm": 0.0}

    row_q, row_p = s.calls[3]
    assert "2 * vector.similarity.cosine(f.embedding, g.embedding) - 1 AS cos" in row_q
    assert row_p == {"n": "Target"}

    precheck_q, precheck_p = s.calls[6]
    assert "UNWIND $names AS g" in precheck_q and "coalesce(e.picked_by, [])" in precheck_q
    assert precheck_p == {"n": "Target", "names": ["A", "B", "C"]}

    picks_a_q, picks_a_p = s.calls[7]
    assert "$g IN coalesce(e.picked_by, [])" in picks_a_q and "o.name <> $n" in picks_a_q
    assert "ORDER BY e.weight ASC" in picks_a_q
    assert picks_a_p == {"g": "A", "n": "Target"}

    existing_q, existing_p = s.calls[10]
    assert "UNWIND $pairs AS p" in existing_q and "MERGE" not in existing_q
    assert "coalesce(e.picked_by, [])" in existing_q
    assert existing_p["pairs"] == [{"a": "A", "b": "Target"}, {"a": "B", "b": "Target"}, {"a": "C", "b": "Target"}]

    write_edges_q, write_edges_p = s.calls[11]
    assert "MERGE (a)-[e:RELATED_TO]->(b)" in write_edges_q
    rows_by_pair = {(r["a"], r["b"]): r for r in write_edges_p["rows"]}
    assert rows_by_pair[("A", "Target")]["weight"] == pytest.approx(1.4)
    assert rows_by_pair[("A", "Target")]["picked_by"] == ["A", "Target"]
    assert rows_by_pair[("A", "Target")]["via"] == "both"
    assert rows_by_pair[("B", "Target")]["weight"] == pytest.approx(0.5)
    assert rows_by_pair[("C", "Target")]["weight"] == pytest.approx(0.3)
    assert write_edges_p["rv"] == 7

    unpick_q, unpick_p = s.calls[12]
    assert "SET e.picked_by = [p IN coalesce(e.picked_by, []) WHERE p <> $g]" in unpick_q
    assert "DELETE e" in unpick_q
    assert unpick_p == {"g": "A", "weakest": "old"}

    revoke_q, revoke_p = s.calls[13]
    assert "NOT o.name IN $keep" in revoke_q and "coalesce(e.picked_by, [])" in revoke_q
    assert "FOREACH" in revoke_q and "DELETE" in revoke_q
    assert revoke_p["n"] == "Target" and sorted(revoke_p["keep"]) == ["A", "B", "C"]


def test_maintain_edges_for_duplicates_excluded_no_writes():
    """A name-suffix duplicate is excluded before any picking happens, so X picks
    nothing and no edge writes are issued at all; the revocation pass still runs
    (with an empty keep-list) since it happens unconditionally."""
    s = FakeSession([
        [{"has_emb": True, "toks": [], "norm": None}],             # read X
        [],                                                         # idf fetch #1
        [],                                                         # write_fact_tokens
        [{"name": "Shared — x — 2026-08-30 #2", "cos": 2.0, "toks": [], "norm": None}],  # row query
        [],                                                         # idf fetch #2
        [],                                                         # load_supersedes
        [{"revoked": 0, "deleted": 0}],                             # revocation (keep=[])
    ])
    result = W.maintain_edges_for(s, "Shared — x — 2026-08-30", _MEC_CFG)
    assert result == {"picked": 0, "repicked": 0, "deleted": 0, "revoked": 0, "skipped": None}
    assert len(s.calls) == 7                # no precheck, no current-picks, no existing-picked-by, no write_edges
    revoke_p = s.calls[6][1]
    assert revoke_p["keep"] == []


def test_maintain_edges_for_merges_existing_picked_by_from_other_side():
    """G already picked X in a prior run (picked_by=['G']) but is not in the
    already_picks_x precheck result for this test (isolating the merge-on-write path
    from the already_picks_x fast path covered separately); when X now also picks G,
    write_edges must be given the UNION, not just X's own side."""
    s = FakeSession([
        [{"has_emb": True, "toks": [], "norm": None}],             # read X
        [],                                                         # idf fetch #1
        [],                                                         # write_fact_tokens
        [{"name": "G", "cos": 0.60, "toks": [], "norm": None}],     # row query; blend = 0.5
        [],                                                         # idf fetch #2
        [],                                                         # load_supersedes
        [],                                                         # already_picks_x precheck: G not in it
        [                                                           # G's 5 current picks, all far stronger -> no re-pick
            {"other": "p0", "weight": 10.0}, {"other": "p1", "weight": 11.0},
            {"other": "p2", "weight": 12.0}, {"other": "p3", "weight": 13.0},
            {"other": "p4", "weight": 14.0},
        ],
        [{"a": "G", "b": "Target", "picked_by": ["G"]}],             # existing picked_by for (G, Target)
        [{"n": 1}],                                                 # write_edges
        [{"revoked": 0, "deleted": 0}],                             # revocation (keep=["G"])
    ])
    result = W.maintain_edges_for(s, "Target", _MEC_CFG)
    assert result == {"picked": 1, "repicked": 0, "deleted": 0, "revoked": 0, "skipped": None}
    assert len(s.calls) == 11
    _write_edges_q, write_edges_p = s.calls[9]
    row = write_edges_p["rows"][0]
    assert sorted(row["picked_by"]) == ["G", "Target"]
    assert row["via"] == "both"


def test_maintain_edges_for_already_picks_x_updates_weight_without_eviction():
    """F2 reproduction: G already picks X (Target) at weight 0.25 -- its weakest of
    five. The new blend b(X,G)=0.30 clears the floor. Because G already has X among
    its own picks, this must be a weight update only: no current-picks query for G, no
    eviction, not counted in `repicked`, and the edge survives with G still in
    `picked_by` (no DELETE issued, deleted stays 0)."""
    s = FakeSession([
        [{"has_emb": True, "toks": [], "norm": None}],              # read X
        [],                                                          # idf fetch #1
        [],                                                          # write_fact_tokens
        [{"name": "G", "cos": 0.56, "toks": [], "norm": None}],      # row query; blend = 0.30
        [],                                                          # idf fetch #2
        [],                                                          # load_supersedes
        [{"g": "G"}],                                                # already_picks_x precheck: G already picks X
        [{"a": "G", "b": "Target", "picked_by": ["G"]}],              # existing picked_by for (G, Target)
        [{"n": 1}],                                                  # write_edges
        [{"revoked": 0, "deleted": 0}],                              # revocation (keep=["G"])
    ])
    result = W.maintain_edges_for(s, "Target", _MEC_CFG)
    assert result == {"picked": 1, "repicked": 0, "deleted": 0, "revoked": 0, "skipped": None}
    assert len(s.calls) == 10                # no current-picks query for G at all
    write_edges_p = s.calls[8][1]
    row = write_edges_p["rows"][0]
    assert row["weight"] == pytest.approx(0.30)
    assert sorted(row["picked_by"]) == ["G", "Target"]
    calls_text = " ".join(str(c[0]) for c in s.calls)
    assert "ORDER BY e.weight ASC" not in calls_text     # the current-picks statement never ran
    assert "DELETE e" not in calls_text                  # no un-pick statement ran either


def test_maintain_edges_for_revokes_stale_picks_no_longer_chosen():
    """F3: X previously picked P (edge (P, X) has picked_by=['Target']) but this run
    finds no candidates at all, so X's new pick set is empty. The revocation statement
    must run with keep=[] and strip X from every edge it previously picked, deleting
    any that become orphaned."""
    s = FakeSession([
        [{"has_emb": True, "toks": [], "norm": None}],   # read X
        [],                                               # idf fetch #1
        [],                                               # write_fact_tokens
        [],                                               # row query -> no candidates this run
        [],                                               # idf fetch #2
        [],                                               # load_supersedes
        [{"revoked": 1, "deleted": 1}],                   # revocation: P's edge is stripped and deleted
    ])
    result = W.maintain_edges_for(s, "Target", _MEC_CFG)
    assert result == {"picked": 0, "repicked": 0, "deleted": 1, "revoked": 1, "skipped": None}
    assert len(s.calls) == 7
    revoke_q, revoke_p = s.calls[6]
    assert "WHERE $n IN coalesce(e.picked_by, []) AND NOT o.name IN $keep" in revoke_q
    assert "coalesce(e.picked_by, [])" in revoke_q
    assert "FOREACH (d IN empties | DELETE d)" in revoke_q
    assert revoke_p == {"n": "Target", "keep": []}
