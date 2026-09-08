"""Spec §6 contract: the library (ai_memory) and the grok client (grok/skills/neo4j-memory) agree."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from unittest import mock

import pytest

from ai_memory import embed as LE
from ai_memory import retrieval as LR
from ai_memory import wordindex as LW

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "grok" / "skills" / "neo4j-memory" / "scripts" / "neo4j_memory.py"
FIX = ROOT / "tests" / "fixtures" / "embed_text_cases.json"


@pytest.fixture(scope="module")
def nm():
    spec = importlib.util.spec_from_file_location("grok_neo4j_memory", CLIENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_prepared_text_byte_equal_on_fixtures(nm):
    for c in json.loads(FIX.read_text(encoding="utf-8")):
        bp = frozenset(c["boilerplate"])
        lib = LE.fact_embed_text(c["name"], c["summary"], c["key_points"], c["content"], bp)
        cli = nm.fact_embed_text(c["name"], c["summary"], c["key_points"], c["content"], bp)
        assert lib == cli == c["expected"], c["id"]


@pytest.mark.parametrize("text,grams", [
    ("keep0 a, b c d e keep1", {"a b c d", "b c d e"}),
    ("start w x y z zz!", {"w x y z", "x y z zz"}),
    ("a b c d e keep", {"a b c d", "b c d e"}),
    ("Ünïcode — a b c d e — tail", {"a b c d", "b c d e"}),
    ("the probability of informed trading matters", {"probability of informed trading"}),
])
def test_strip_boilerplate_parity(nm, text, grams):
    assert LE.strip_boilerplate(text, grams) == nm.strip_boilerplate(text, grams)


def test_text_sha_parity(nm):
    assert LE.text_sha("N S - k", 7) == nm.text_sha("N S - k", 7)


_LUCENE_CASES = [
    "x AND y",
    "NOT this",
    "a OR b OR c",
    "andy",                       # substring, not a bare operator — untouched
    "NOTABLE and Ordinary",
    "reserve AND (guard) OR NOT",
    "a+b:c/d",
    "  padded AND spaced  ",
]


@pytest.mark.parametrize("q", _LUCENE_CASES)
def test_escape_lucene_parity(nm, q):
    """Both clients must escape specials AND lower-case the bare boolean
    keywords, or the same user query parses differently in each."""
    from ai_memory.search import _escape_lucene as lib_escape
    assert lib_escape(q) == nm._escape_lucene(q), q


@pytest.mark.parametrize("a,b,expected", [
    ("Kelly Criterion", "kelly criterion (15:30 EDT)", True),
    ("kelly criterion (15:30 EDT)", "Kelly Criterion", True),
    ("Kelly Criterion", "Kelly Criterion (15:30 EDT)", True),
    ("Kelly Criterion", "kelly criterion II", False),
    ("Postmortem (2026-03-10 root cause)", "Postmortem", False),
])
def test_same_topic_case_parity(nm, a, b, expected):
    assert LR.same_topic({"name": a}, {"name": b}) is expected
    assert nm.same_topic({"name": a}, {"name": b}) is expected


@pytest.mark.parametrize("sup", [{"K": {"A", "B"}}, {"K": "A"}])
def test_supersedes_multimap_parity(nm, sup):
    """Both clients read the {new: {old, ...}} multimap (and still accept the
    legacy {new: old} shape) identically."""
    for other in ("A", "B", "C"):
        assert LR.same_topic({"name": "K"}, {"name": other}, sup) == \
            nm.same_topic({"name": "K"}, {"name": other}, sup), (sup, other)
        assert LW.is_duplicate("K", other, 0.0, sup) == \
            nm.is_duplicate("K", other, 0.0, sup), (sup, other)


def test_shared_constants_identical(nm):
    assert nm.RRF_K == LR.RRF_K
    assert nm.VECTOR_ONLY_FLOOR == LR.VECTOR_ONLY_FLOOR


def _search_clause(cypher: str) -> str:
    return "\n".join(line for line in cypher.splitlines() if not line.startswith(("RETURN", "ORDER BY")))


def test_vector_leg_cypher_identical_modulo_param_names(nm):
    for where in ("", "f.assistant = $assistant", "f.assistant = $assistant AND f.space = $space"):
        lib = _search_clause(LR.build_search_cypher(where, "factEmbeddingIndex"))
        cli = _search_clause(nm.build_vector_search_cypher("factEmbeddingIndex", where))
        cli = cli.replace("$embedding", "$vec").replace("$k)", "$pool)")
        assert lib == cli, where
    assert LR.build_filters("Grok", "shared", "trusted") == nm.build_filters("Grok", "shared", "trusted")


def _h(name, score, status=None, via="ft"):
    return {"name": name, "teaser": "", "key_points": [], "assistant": None, "status": status,
            "space": None, "score": score, "via": via, "vec_score": score if via == "vec" else None}


CASES = {
    "plain": (["A", "B", "C"], ["C", "A", "D"], "", {}),
    "superseded-suffix": (["Shared — x — 2026-08-30", "Shared — x — 2026-08-30 #2"], [], "", {}),
    "supersedes-chain": (["Old", "New"], ["Old"], "", {"New": "Old"}),
    "exact-name": (["Other", "Weft — identity"], ["Other"], "weft — identity", {}),
    "exact-tie": (["B", "A"], ["A", "B"], "", {}),
    # k-sensitive: "E" ranks 1st in vec only; "A" ranks 6th in ft and 9th in vec.
    # At RRF_K=60 (default) A's two mediocre ranks beat E's single top rank; at
    # k=5 the reverse holds (proven in test_k_sensitivity_case_is_discriminating).
    # A mutated RRF_K constant flips the ranked order for this case alone.
    "k-sensitive": (
        ["F0", "F1", "F2", "F3", "F4", "A"],
        ["E", "V1", "V2", "V3", "V4", "V5", "V6", "V7", "A"],
        "", {},
    ),
}
INACTIVE = {"Shared — x — 2026-08-30", "Old"}


@pytest.mark.parametrize("case", list(CASES))
def test_fused_and_ranked_order_identical(nm, case):
    ft_names, vec_names, query, supersedes = CASES[case]
    ft = [_h(n, 10.0 - i, "superseded" if n in INACTIVE else None) for i, n in enumerate(ft_names)]
    vec = [_h(n, 0.95 - i * 0.01, "superseded" if n in INACTIVE else None, "vec") for i, n in enumerate(vec_names)]
    legs = [(o, l) for o, l in (("ft", ft), ("vec", vec)) if l]
    lib = [(h["name"], round(h["score"], 6)) for h in LR.rank_adjust(LR.fuse_rrf(legs), query, supersedes)[:5]]
    cli = [(h["name"], round(h["score"], 6)) for h in nm.rank_pipeline(ft, vec, bool(vec), query, supersedes, 5)[0]]
    assert lib == cli, case


def test_k_sensitivity_case_is_discriminating(nm):
    """Prove the "k-sensitive" case actually discriminates a wrong RRF_K: the
    relative order of A vs E must differ between k=60 (default) and k=5."""
    ft_names, vec_names, _, _ = CASES["k-sensitive"]
    ft = [_h(n, 10.0 - i) for i, n in enumerate(ft_names)]
    vec = [_h(n, 0.95 - i * 0.01, via="vec") for i, n in enumerate(vec_names)]
    legs = [(o, l) for o, l in (("ft", ft), ("vec", vec)) if l]
    order_default = [h["name"] for h in LR.fuse_rrf(legs)]
    order_k5 = [h["name"] for h in LR.fuse_rrf(legs, k=5)]
    assert order_default.index("A") < order_default.index("E")
    assert order_k5.index("A") > order_k5.index("E")


def test_vector_only_floor_identical(nm):
    vec = [_h("Strong", 0.91, via="vec"), _h("Weak", 0.5, via="vec")]
    lib = [h["name"] for h in LR.rank_adjust(LR.fuse_rrf([("vec", LR.apply_vector_only_floor(vec, []))]), "", {})]
    cli = [h["name"] for h in nm.rank_pipeline([], vec, True, "", {}, 5)[0]]
    assert lib == cli == ["Strong"]


SPEC_KEYS = {"name", "teaser", "key_points", "assistant", "status", "space", "score", "via"}


def test_hit_dict_keys(nm):
    class R(dict):
        def keys(self): return dict.keys(self)
    rec = R(name="N", text="t", assistant="Grok", key_points=["k"], status=None, space="shared", topic=None, score=0.5)
    cli_hit = nm._hit_from_record(rec)
    assert SPEC_KEYS <= set(cli_hit)
    lib_row = {"name": "N", "text": "t", "key_points": ["k"], "assistant": "Grok", "status": None, "space": "shared", "s": 0.5}
    from ai_memory.search import _rows_to_hits
    assert SPEC_KEYS <= set(_rows_to_hits([lib_row], "vec")[0])


def test_cas_subquery_identical(nm):
    for fields in (["content"], ["summary", "key_points"], ["summary", "key_points", "content"]):
        assert LE.build_embed_subquery(fields) == nm.build_embed_subquery(fields)
        assert LE.build_embed_subquery(fields, keep_prev=True) == nm.build_embed_subquery(fields, keep_prev=True)
    assert LE.embed_params([0.1], "sha", 3, cas={"summary": None, "key_points": "abc", "content": ""}) == \
        nm.embed_params([0.1], "sha", 3, cas={"summary": None, "key_points": "abc", "content": ""})


def test_cas_statement_has_all_three_fields(nm):
    """Pin the actual CAS *Cypher* (not just the params dict) to all three text
    fields, so a narrowed build_embed_subquery(["content"]) call would fail this
    test even though embed_params still receives summary/key_points/content."""
    row = {"name": "Foo", "summary": "Sum", "key_points": ["p1"], "content": "Body"}
    calls = []

    class S:
        def run(self, query, **kw):
            calls.append((query, kw))
            if "RETURN f.name AS name" in query:
                class R:
                    def single(self_inner):
                        return row
                return R()

            class R2:
                def single(self_inner):
                    return {"embedded": 1}
            return R2()

    cfg_dict = {"embed_model": "nomic-embed-text", "embed_dim": 768}
    retrieval_cfg = {"version": 2, "boilerplate": frozenset()}
    with mock.patch.object(nm, "ollama_embed", lambda *a, **k: [0.1] * 768):
        status = nm._embed_fact_cas(S(), "Foo", cfg_dict, retrieval_cfg, timeout=5.0)
    assert status == "embedded"
    write_query, write_kw = calls[1]
    expected_cypher = (
        "MATCH (f:Fact {name: $name})\n"
        + LE.build_embed_subquery(["summary", "key_points", "content"])
        + "\nRETURN embedded"
    )
    assert write_query == expected_cypher
    assert write_kw["cas_summary"] == "Sum"
    assert write_kw["cas_key_points"] == ["p1"]
    assert write_kw["cas_content"] == "Body"
    text = LE.fact_embed_text("Foo", "Sum", ["p1"], "Body", frozenset())
    assert write_kw["embedding_text_sha"] == LE.text_sha(text, 2)


# --- Edge layer (spec §7): tokenizer/scoring/Cypher parity with ai_memory/wordindex.py ---

_FIXTURE_CASES = json.loads(FIX.read_text(encoding="utf-8"))

# The same adversarial strip_boilerplate strings used above (test_strip_boilerplate_parity),
# reused here to exercise tokenize on non-trivial/Unicode input.
_ADVERSARIAL_TEXTS = [
    "keep0 a, b c d e keep1",
    "start w x y z zz!",
    "a b c d e keep",
    "Ünïcode — a b c d e — tail",
    "the probability of informed trading matters",
]


@pytest.mark.parametrize("case", _FIXTURE_CASES, ids=[c["id"] for c in _FIXTURE_CASES])
def test_tokenize_parity_on_fixture_expected(nm, case):
    text = case["expected"]
    assert LW.tokenize(text, case["name"]) == nm.tokenize(text, case["name"]), case["id"]


@pytest.mark.parametrize("text", _ADVERSARIAL_TEXTS)
def test_tokenize_parity_on_adversarial_strings(nm, text):
    assert LW.tokenize(text) == nm.tokenize(text), text


def test_edge_layer_constants_identical(nm):
    assert nm.STOP == LW.STOP
    assert nm.SHORT == LW.SHORT
    assert nm.EDGE_K == LW.EDGE_K
    assert nm.DUP_COS == LW.DUP_COS
    assert nm.TOKEN_CAP == LW.TOKEN_CAP


def test_wordindex_scoring_parity(nm):
    idf = {"a": 2.0, "b": 1.0, "c": 0.5}
    default_idf = 0.75
    tokens_a, tokens_b = ["a", "b"], ["b", "c"]
    na_lib = LW.tfidf_norm(tokens_a, idf, default_idf)
    na_cli = nm.tfidf_norm(tokens_a, idf, default_idf)
    assert na_lib == na_cli
    nb_lib = LW.tfidf_norm(tokens_b, idf, default_idf)
    nb_cli = nm.tfidf_norm(tokens_b, idf, default_idf)
    assert nb_lib == nb_cli
    assert LW.tfidf_cosine(tokens_a, tokens_b, idf, na_lib, nb_lib, default_idf) == \
        nm.tfidf_cosine(tokens_a, tokens_b, idf, na_cli, nb_cli, default_idf)

    base = {"t_mean": 0.0, "t_std": 0.1, "c_mean": 0.6, "c_std": 0.1}
    assert LW.blend(0.1, 0.7, base) == nm.blend(0.1, 0.7, base)

    assert LW.is_duplicate("Shared — x — 2026-08-30", "Shared — x — 2026-08-30 #2", 0.5) == \
        nm.is_duplicate("Shared — x — 2026-08-30", "Shared — x — 2026-08-30 #2", 0.5) is True
    assert LW.is_duplicate("Old", "New", 0.5, {"New": "Old"}) == \
        nm.is_duplicate("Old", "New", 0.5, {"New": "Old"}) is True
    assert LW.is_duplicate("A", "B", 0.96) == nm.is_duplicate("A", "B", 0.96) is True
    assert LW.is_duplicate("A", "B", 0.94) == nm.is_duplicate("A", "B", 0.94) is False

    # k-boundary: exactly k candidates clear the floor (k=4 == len(cands)); a tie by
    # weight ("a" vs "b") must break by name.
    cands = [("b", 2.0, 0.1, 0.9), ("a", 2.0, 0.1, 0.9), ("c", 1.0, 0.0, 0.5), ("d", 0.5, 0.0, 0.4)]
    for k in (1, 2, 3, 4, 5):
        assert [c[0] for c in LW.pick(cands, floor=0.5, k=k)] == \
            [c[0] for c in nm.pick(cands, floor=0.5, k=k)], k
    assert [c[0] for c in nm.pick(cands, floor=0.5, k=4)] == ["a", "b", "c", "d"]

    picks = {"A": [("B", 2.0, 0.1, 0.9)], "B": [("A", 2.0, 0.1, 0.9), ("C", 1.5, 0.0, 0.8)], "C": []}
    assert LW.edges_from_picks(picks) == nm.edges_from_picks(picks)


def test_edge_config_statement_identical(nm):
    """load_edge_config/_load_edge_config: same Cypher (only the reader differs)."""
    class S:
        def __init__(self):
            self.q = None

        def run(self, q, **kw):
            self.q = q
            return mock.Mock(single=lambda: None)

    s_lib, s_cli = S(), S()
    assert LW.load_edge_config(s_lib) is None
    assert nm._load_edge_config(s_cli) is None
    assert s_lib.q == s_cli.q


class _MECResult:
    def __init__(self, rows):
        self._rows = rows

    def single(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _MECSession:
    def __init__(self, rows_by_call):
        self.calls = []
        self.rows_by_call = list(rows_by_call)

    def run(self, cypher, **kw):
        self.calls.append((str(cypher), kw))
        return _MECResult(self.rows_by_call.pop(0) if self.rows_by_call else [])


_MEC_CFG = {"rule_version": 7, "edge_floor": 0.2, "t_mean": 0.0, "t_std": 1.0,
            "c_mean": 0.5, "c_std": 0.1, "n_facts": 50}

# Identical scenario to ai_memory/tests/test_wordindex.py::test_maintain_edges_for_picks_repicks_and_unpicks_weakest
_MEC_ROWS = [
    [{"has_emb": True, "toks": [], "norm": None}],
    [],
    [],
    [
        {"name": "A", "cos": 0.78, "toks": [], "norm": None},
        {"name": "B", "cos": 0.60, "toks": [], "norm": None},
        {"name": "C", "cos": 0.56, "toks": [], "norm": None},
        {"name": "D", "cos": 0.51, "toks": [], "norm": None},
    ],
    [],
    [],
    [],
    [
        {"other": "old", "weight": 0.2}, {"other": "p2", "weight": 0.3},
        {"other": "p3", "weight": 0.5}, {"other": "p4", "weight": 0.7},
        {"other": "p5", "weight": 0.9},
    ],
    [{"other": "q1", "weight": 0.1}, {"other": "q2", "weight": 0.4}],
    [],
    [],
    [{"n": 3}],
    [{"remaining": 0}],
    [{"revoked": 0, "deleted": 0}],
]


def test_maintain_edges_for_statements_identical(nm):
    """Run the library's maintain_edges_for and the client's _maintain_edges_for
    against the identical scripted scenario, then diff the row-query, current-picks,
    un-pick, and revocation statement text byte-for-byte."""
    s_lib, s_cli = _MECSession(_MEC_ROWS), _MECSession(_MEC_ROWS)
    r_lib = LW.maintain_edges_for(s_lib, "Target", _MEC_CFG)
    r_cli = nm._maintain_edges_for(s_cli, "Target", _MEC_CFG)
    assert r_lib == r_cli == {"picked": 3, "repicked": 3, "deleted": 1, "revoked": 0, "skipped": None}
    assert len(s_lib.calls) == len(s_cli.calls) == 14
    # index 3 = row query, 6 = already_picks_x precheck (bonus), 7 = current-picks,
    # 12 = un-pick, 13 = revocation.
    for i in (3, 6, 7, 12, 13):
        assert s_lib.calls[i][0] == s_cli.calls[i][0], i
