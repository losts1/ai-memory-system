import pytest

from ai_memory.retrieval import (
    RRF_K, VECTOR_ONLY_FLOOR, fallback_pool, fuse_rrf, pool_size,
    same_topic, strip_time_suffix, apply_vector_only_floor, rank_adjust,
    RETURN_FIELDS, build_fallback_cypher, build_filters, build_fulltext_cypher, build_search_cypher,
    validate_index_name,
)


def test_constants():
    assert RRF_K == 60
    assert VECTOR_ONLY_FLOOR == 0.80


def test_pool_size_widens_small_k_and_scales():
    assert pool_size(1) == 16
    assert pool_size(5) == 20
    assert pool_size(10) == 40


def test_fallback_pool_caps_at_2000():
    assert fallback_pool(5) == 250
    assert fallback_pool(100) == 2000


def test_fuse_rrf_overlap_ranks_first_and_tags_via():
    ft = [{"name": "A", "teaser": "ft-a", "key_points": []}, {"name": "B", "teaser": "b", "key_points": []}]
    vec = [{"name": "C", "teaser": "c", "key_points": []}, {"name": "A", "teaser": "vec-a-longer", "key_points": ["k"]}]
    out = fuse_rrf([("ft", ft), ("vec", vec)])
    assert [h["name"] for h in out] == ["A", "C", "B"]   # A: 1/61+1/62; C: 1/61; B: 1/62 sorted by score desc
    assert out[0]["via"] == "ft+vec"
    assert out[0]["teaser"] == "vec-a-longer"
    assert out[0]["key_points"] == ["k"]
    assert out[0]["score"] == pytest.approx(1 / 61 + 1 / 62, abs=1e-6)


def test_fuse_rrf_ties_break_by_name_only():
    ft = [{"name": "Zed"}, {"name": "Alpha"}]
    vec = [{"name": "Alpha"}, {"name": "Zed"}]
    out = fuse_rrf([("ft", ft), ("vec", vec)])
    assert [h["name"] for h in out] == ["Alpha", "Zed"]


def test_fuse_rrf_skips_nameless_and_handles_empty():
    assert fuse_rrf([]) == []
    assert fuse_rrf([("ft", [{"teaser": "no name"}])]) == []


@pytest.mark.parametrize("name,base", [
    ("Kelly Criterion & Position Sizing (15:30 EDT)", "Kelly Criterion & Position Sizing"),
    ("Hawkes Processes for Order Flow (2026-03-10)", "Hawkes Processes for Order Flow"),
    ("Shared — ntr: bitchat — 2026-08-30", "Shared — ntr: bitchat"),
    ("Shared — Foo — 2026-08-30 #2", "Shared — Foo"),
    ("Grok — memory is sacred 2026-08-29", "Grok — memory is sacred 2026-08-29"),  # no suffix shape -> unchanged
    ("Plain Name", "Plain Name"),
    ("Postmortem (2026-03-10 root cause)", "Postmortem (2026-03-10 root cause)"),  # date not at the end -> unchanged
    ("GARCH Volatility Modeling (Learner Cron, 6:00 PM EDT)", "GARCH Volatility Modeling"),
    ("Signal Decay (00:30 UTC / 8:30 PM EDT)", "Signal Decay"),
    ("Skew Trap (02:01)", "Skew Trap"),
    ("Spread Budget Decomposition (23:01 ET)", "Spread Budget Decomposition"),
])
def test_strip_time_suffix_only_strips_trailing_suffixes(name, base):
    assert strip_time_suffix(name) == base


def test_same_topic_by_name_group_and_supersedes_chain():
    a = {"name": "Kelly Criterion & Position Sizing"}
    b = {"name": "Kelly Criterion & Position Sizing (15:30 EDT)"}
    c = {"name": "Unrelated"}
    assert same_topic(a, b)
    assert not same_topic(a, c)
    # SUPERSEDES: new -> old, chain of two
    sup = {"New v3": "New v2", "New v2": "Old v1"}
    assert same_topic({"name": "New v3"}, {"name": "Old v1"}, supersedes=sup)
    assert same_topic({"name": "Old v1"}, {"name": "New v3"}, supersedes=sup)
    assert not same_topic({"name": "New v3"}, {"name": "Unrelated"}, supersedes=sup)
    # Non-time-suffix parenthetical is not stripped
    assert not same_topic({"name": "Postmortem (2026-03-10 root cause)"}, {"name": "Postmortem"})


def test_same_topic_compares_stripped_names_case_insensitively():
    """The collapse rule must use the same case-folding as rank_adjust's
    exact-name boost, or the two rules disagree within one ranking pass."""
    assert same_topic({"name": "Kelly Criterion"}, {"name": "kelly criterion (15:30 EDT)"})
    assert same_topic({"name": "kelly criterion (15:30 EDT)"}, {"name": "Kelly Criterion"})
    assert not same_topic({"name": "Kelly Criterion"}, {"name": "kelly criterion II"})


def test_same_topic_follows_every_edge_of_a_multi_old_keeper():
    """One keeper superseding several olds: the multimap keeps all edges, so
    both olds are on the keeper's chain."""
    sup = {"K": {"A", "B"}}
    assert same_topic({"name": "K"}, {"name": "A"}, supersedes=sup)
    assert same_topic({"name": "K"}, {"name": "B"}, supersedes=sup)
    assert same_topic({"name": "A"}, {"name": "B"}, supersedes=sup)
    assert not same_topic({"name": "K"}, {"name": "C"}, supersedes=sup)


def _h(name, score, status=None):
    return {"name": name, "score": score, "status": status, "teaser": "", "key_points": [], "via": "ft"}


def test_rank_adjust_sinks_inactive_below_active():
    hits = [_h("old", 0.9, "superseded"), _h("new", 0.5), _h("gone", 0.4, "removed"), _h("other", 0.3)]
    out = rank_adjust(hits, query="nothing matches")
    assert [h["name"] for h in out] == ["new", "other", "old", "gone"]


def test_rank_adjust_collapses_inactive_sibling_of_active_hit():
    hits = [_h("Kelly Criterion (15:30 EDT)", 0.9, "superseded"), _h("Kelly Criterion", 0.5), _h("Other", 0.3)]
    out = rank_adjust(hits, query="x")
    assert [h["name"] for h in out] == ["Kelly Criterion", "Other"]


def test_rank_adjust_keeps_inactive_without_active_sibling():
    hits = [_h("Lonely (15:30 EDT)", 0.9, "superseded"), _h("Other", 0.3)]
    out = rank_adjust(hits, query="x")
    assert [h["name"] for h in out] == ["Other", "Lonely (15:30 EDT)"]


def test_rank_adjust_exact_name_boost_is_active_only():
    # An inactive hit is never boosted by the exact-name rule: with no active
    # exact match it stays sunk below the active hits.
    hits = [_h("B", 0.9), _h("Reserve Guard", 0.1, "superseded")]
    out = rank_adjust(hits, query="Reserve Guard")
    assert [h["name"] for h in out] == ["B", "Reserve Guard"]
    # An active exact match is boosted to the top; the inactive case-variant of
    # the same name is same_topic (case-insensitively) and collapses away.
    hits = [_h("B", 0.9), _h("reserve guard", 0.2), _h("Reserve Guard", 0.1, "superseded")]
    out = rank_adjust(hits, query="Reserve Guard")
    assert [h["name"] for h in out] == ["reserve guard", "B"]


def test_rank_adjust_uses_supersedes_chain_for_collapse():
    hits = [_h("Old v1", 0.9, "superseded"), _h("New v3", 0.5)]
    out = rank_adjust(hits, query="x", supersedes={"New v3": "New v2", "New v2": "Old v1"})
    assert [h["name"] for h in out] == ["New v3"]


def test_vector_only_floor_applies_only_when_lexical_empty():
    vec = [{"name": "a", "vec_score": 0.88}, {"name": "b", "vec_score": 0.75}]
    assert [h["name"] for h in apply_vector_only_floor(vec, [])] == ["a"]
    assert apply_vector_only_floor(vec, [{"name": "x"}]) == vec


def test_build_filters_only_set_inputs_and_joined_by_and():
    assert build_filters(None, None, None) == ("", {})
    where, params = build_filters("Grok", None, "trusted")
    assert where == "f.assistant = $assistant AND f.provenance_trust = $trust"
    assert params == {"assistant": "Grok", "trust": "trusted"}
    assert "status" not in build_filters("Grok", "shared", "trusted")[0]


def test_validate_index_name_accepts_identifiers_and_rejects_the_rest():
    assert validate_index_name("factEmbeddingIndex") == "factEmbeddingIndex"
    assert validate_index_name("fact_embeddings_v2") == "fact_embeddings_v2"
    for bad in ("", "9abc", "fact-embeddings", "a b", "x`y", "$index", "idx;DROP INDEX x"):
        with pytest.raises(ValueError):
            validate_index_name(bad)


def test_build_search_cypher_inlines_index_and_keeps_params():
    c = build_search_cypher("f.assistant = $assistant", "factEmbeddingIndex")
    assert c.startswith("CYPHER 25\n")
    assert "SEARCH f IN (VECTOR INDEX `factEmbeddingIndex` FOR $vec WHERE f.assistant = $assistant LIMIT $pool) SCORE AS s" in c
    assert "$index" not in c
    assert f"RETURN {RETURN_FIELDS}" in c
    assert c.rstrip().endswith("ORDER BY s DESC")
    c0 = build_search_cypher("", "factEmbeddingIndex")
    assert "FOR $vec LIMIT $pool) SCORE AS s" in c0 and "WHERE" not in c0


def test_build_search_cypher_rejects_bad_index_name():
    with pytest.raises(ValueError):
        build_search_cypher("", "bad name")


def test_fallback_cypher_filters_after_yield_and_returns_pool():
    c = build_fallback_cypher("f.space = $space")
    assert "CYPHER 25" not in c
    assert "CALL db.index.vector.queryNodes($index, $pool2, $vec)" in c
    assert c.index("YIELD") < c.index("WHERE f.space = $space") < c.index("RETURN")
    assert c.rstrip().endswith("LIMIT $pool")


def test_fulltext_cypher_filters_after_yield():
    c = build_fulltext_cypher("f.assistant = $assistant")
    assert "CALL db.index.fulltext.queryNodes($index, $q)" in c
    assert c.index("YIELD") < c.index("WHERE") < c.index("RETURN")
    assert build_fulltext_cypher("").count("WHERE") == 0
