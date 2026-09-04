"""Tests for ai_memory.eval.judge — the LLM relevance judge used by the retrieval eval.

No network: the judge's HTTP call is injected as a plain callable.
"""
import json

import pytest

from ai_memory.eval.judge import (
    CALIBRATION_THRESHOLD,
    JUDGE_SYSTEM_PROMPT,
    JudgeCache,
    build_judge_messages,
    calibration_agreement,
    judge_query,
    mrr,
    ndcg_at_k,
    parse_judgments,
    recall_at_k,
)

CANDS = [
    {"name": "Alpha", "summary": "About alpha.", "key_points": ["a1", "a2", "a3", "a4"],
     "assistant": "Nova", "status": None, "score": 0.91, "via": "vec"},
    {"name": "Beta", "summary": "About beta.", "key_points": [],
     "assistant": "Grok", "status": "superseded", "score": 0.4, "via": "ft"},
    {"name": "Gamma", "summary": "", "key_points": ["g1"],
     "assistant": None, "status": "active", "score": 0.2, "via": "ft+vec"},
]


# ── system prompt ────────────────────────────────────────────────────────────

def test_system_prompt_states_three_grades_and_json_only():
    for g in ("2", "1", "0"):
        assert f'"grade": {g}' in JUDGE_SYSTEM_PROMPT or f"grade {g}" in JUDGE_SYSTEM_PROMPT
    assert "JSON" in JUDGE_SYSTEM_PROMPT
    assert "superseded" in JUDGE_SYSTEM_PROMPT.lower()


# ── prompt builder ───────────────────────────────────────────────────────────

def test_messages_carry_system_and_user_roles():
    msgs = build_judge_messages("q", CANDS)
    assert [m["role"] for m in msgs] == ["system", "user"]
    assert msgs[0]["content"] == JUDGE_SYSTEM_PROMPT


def test_user_message_includes_every_candidate_name_and_the_query():
    user = build_judge_messages("what is alpha", CANDS)[1]["content"]
    assert "what is alpha" in user
    for c in CANDS:
        assert c["name"] in user


def test_user_message_hides_scores_and_ranker_identity():
    user = build_judge_messages("q", CANDS)[1]["content"]
    assert "0.91" not in user
    assert "via" not in user
    assert "vec" not in user.split("Gamma")[0]  # no "ft+vec" tags anywhere


def test_user_message_caps_key_points_at_three():
    user = build_judge_messages("q", CANDS)[1]["content"]
    assert "a3" in user
    assert "a4" not in user


def test_candidate_order_is_shuffled_deterministically_by_seed():
    a = build_judge_messages("q", CANDS, seed=1)[1]["content"]
    b = build_judge_messages("q", CANDS, seed=1)[1]["content"]
    assert a == b
    # Three candidates have six orderings; across ten seeds more than one must appear.
    def order(seed):
        u = build_judge_messages("q", CANDS, seed=seed)[1]["content"]
        return tuple(sorted(("Alpha", "Beta", "Gamma"), key=u.index))
    assert len({order(s) for s in range(10)}) > 1


# ── parser ───────────────────────────────────────────────────────────────────

def test_parse_clean_json_array():
    text = json.dumps([
        {"name": "Alpha", "grade": 2, "why": "direct"},
        {"name": "Beta", "grade": 0, "why": "superseded"},
    ])
    out = parse_judgments(text, {"Alpha", "Beta", "Gamma"})
    assert out == {"Alpha": {"grade": 2, "why": "direct"},
                   "Beta": {"grade": 0, "why": "superseded"}}


def test_parse_tolerates_fences_and_prose():
    text = "Sure, here you go:\n```json\n[{\"name\": \"Alpha\", \"grade\": 1, \"why\": \"x\"}]\n```\nDone."
    out = parse_judgments(text, {"Alpha"})
    assert out == {"Alpha": {"grade": 1, "why": "x"}}


def test_parse_ignores_unknown_names():
    text = json.dumps([{"name": "Zeta", "grade": 2, "why": "?"},
                       {"name": "Alpha", "grade": 0, "why": "no"}])
    out = parse_judgments(text, {"Alpha"})
    assert list(out) == ["Alpha"]


@pytest.mark.parametrize("bad", [
    "no json here",
    '{"name": "Alpha", "grade": 2}',                  # object, not array
    '[{"name": "Alpha", "grade": 3, "why": "x"}]',     # grade out of range
    '[{"name": "Alpha", "grade": "2", "why": "x"}]',   # grade not int
    '[{"name": "Alpha", "why": "x"}]',                 # grade missing
])
def test_parse_returns_none_on_malformed(bad):
    assert parse_judgments(bad, {"Alpha"}) is None


def test_parse_truncates_why_to_120_chars():
    text = json.dumps([{"name": "Alpha", "grade": 2, "why": "w" * 500}])
    out = parse_judgments(text, {"Alpha"})
    assert len(out["Alpha"]["why"]) == 120


# ── calibration ──────────────────────────────────────────────────────────────

def test_calibration_agreement_on_grade2_vs_not():
    judge = {"a": 2, "b": 1, "c": 0, "d": 2, "e": 2}
    human = {"a": 2, "b": 2, "c": 0, "d": 2, "e": 1}
    # agree on a (2/2), c (not/not), d (2/2); disagree on b (1 vs 2) and e (2 vs 1)
    assert calibration_agreement(judge, human) == pytest.approx(3 / 5)


def test_calibration_only_counts_keys_present_in_both():
    assert calibration_agreement({"a": 2, "z": 0}, {"a": 2, "y": 1}) == 1.0


def test_calibration_empty_overlap_is_zero():
    assert calibration_agreement({"a": 2}, {"b": 2}) == 0.0


def test_calibration_threshold_is_point_eight():
    assert CALIBRATION_THRESHOLD == 0.8


# ── metrics ──────────────────────────────────────────────────────────────────

def test_ndcg_perfect_ranking_is_one():
    grades = {"a": 2, "b": 1, "c": 0}
    assert ndcg_at_k(["a", "b", "c"], grades, 3) == pytest.approx(1.0)


def test_ndcg_reversed_ranking_is_below_one():
    grades = {"a": 2, "b": 1, "c": 0}
    assert ndcg_at_k(["c", "b", "a"], grades, 3) < 1.0


def test_ndcg_no_relevant_items_is_zero():
    assert ndcg_at_k(["a", "b"], {"a": 0, "b": 0}, 2) == 0.0


def test_ndcg_unjudged_names_count_as_zero():
    grades = {"a": 2}
    assert ndcg_at_k(["x", "a"], grades, 2) < ndcg_at_k(["a", "x"], grades, 2)


def test_recall_at_k_counts_grade2_only():
    grades = {"a": 2, "b": 1, "c": 2, "d": 2}
    assert recall_at_k(["a", "b", "c"], grades, 3) == pytest.approx(2 / 3)


def test_recall_at_k_with_no_grade2_is_zero():
    assert recall_at_k(["a"], {"a": 1}, 1) == 0.0


def test_mrr_is_reciprocal_rank_of_first_grade2():
    assert mrr(["b", "a"], {"a": 2, "b": 1}) == pytest.approx(0.5)
    assert mrr(["b"], {"a": 2, "b": 1}) == 0.0


# ── cache + judge_query ──────────────────────────────────────────────────────

def test_cache_roundtrip_persists_to_disk(tmp_path):
    p = tmp_path / "judge_cache.json"
    c = JudgeCache(p)
    c.put("m", "q", "Alpha", "text", {"grade": 2, "why": "ok"})
    assert c.get("m", "q", "Alpha", "text") == {"grade": 2, "why": "ok"}
    c.save()
    assert JudgeCache(p).get("m", "q", "Alpha", "text") == {"grade": 2, "why": "ok"}


def test_cache_key_changes_with_fact_text_and_model(tmp_path):
    c = JudgeCache(tmp_path / "c.json")
    c.put("m", "q", "Alpha", "text-v1", {"grade": 2, "why": "ok"})
    assert c.get("m", "q", "Alpha", "text-v2") is None
    assert c.get("m2", "q", "Alpha", "text-v1") is None


def test_cache_key_changes_when_system_prompt_changes(tmp_path, monkeypatch):
    """A re-calibrated prompt must never be served grades from the old one."""
    import ai_memory.eval.judge as J
    c = JudgeCache(tmp_path / "c.json")
    c.put("m", "q", "Alpha", "text", {"grade": 2, "why": "ok"})
    assert c.get("m", "q", "Alpha", "text") is not None
    monkeypatch.setattr(J, "JUDGE_SYSTEM_PROMPT", J.JUDGE_SYSTEM_PROMPT + "\nstricter.")
    assert c.get("m", "q", "Alpha", "text") is None


def test_judge_query_calls_model_and_returns_grades():
    seen = []

    def call(messages):
        seen.append(messages)
        return json.dumps([{"name": c["name"], "grade": 1, "why": "r"} for c in CANDS])

    out = judge_query("q", CANDS, call, model="m")
    assert out == {c["name"]: {"grade": 1, "why": "r"} for c in CANDS}
    assert len(seen) == 1


def test_judge_query_retries_once_then_returns_none():
    calls = []

    def call(messages):
        calls.append(1)
        return "garbage"

    assert judge_query("q", CANDS, call, model="m") is None
    assert len(calls) == 2


def test_judge_query_uses_cache_and_only_judges_misses(tmp_path):
    cache = JudgeCache(tmp_path / "c.json")
    cache.put("m", "q", "Alpha", judge_fact_text(CANDS[0]), {"grade": 2, "why": "cached"})
    asked = []

    def call(messages):
        asked.append(messages[1]["content"])
        return json.dumps([{"name": "Beta", "grade": 0, "why": "b"},
                           {"name": "Gamma", "grade": 1, "why": "g"}])

    out = judge_query("q", CANDS, call, model="m", cache=cache)
    assert out["Alpha"] == {"grade": 2, "why": "cached"}
    assert out["Beta"]["grade"] == 0 and out["Gamma"]["grade"] == 1
    assert len(asked) == 1 and "Alpha" not in asked[0]


def test_judge_query_all_cached_makes_no_call(tmp_path):
    cache = JudgeCache(tmp_path / "c.json")
    for c in CANDS:
        cache.put("m", "q", c["name"], judge_fact_text(c), {"grade": 0, "why": ""})

    def call(messages):
        raise AssertionError("should not be called")

    out = judge_query("q", CANDS, call, model="m", cache=cache)
    assert set(out) == {c["name"] for c in CANDS}


from ai_memory.eval.judge import judge_fact_text  # noqa: E402  (used above)


def test_judge_fact_text_is_stable_and_excludes_scores():
    t = judge_fact_text(CANDS[0])
    assert "Alpha" in t and "About alpha." in t and "a3" in t
    assert "a4" not in t and "0.91" not in t
    assert t == judge_fact_text(dict(CANDS[0], score=0.1, via="ft"))
