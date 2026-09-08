from __future__ import annotations

import re
import shlex

import pytest

from ai_memory import duplicates as D


def test_name_time_key():
    # Clock-only suffixes carry no day and must not decide a keeper.
    assert D.name_time_key("Kill Switch State Machine (21:01 ET)") is None
    assert D.name_time_key("Shared — ntr: bitchat — 2026-08-30") == "2026-08-30"
    assert D.name_time_key("Shared — ntr: bitchat — 2026-08-30 #2") == "2026-08-30#02"
    assert D.name_time_key("X (2026-08-30 9:05 ET)") == "2026-08-30T09:05"
    assert D.name_time_key("Bloom Filters") is None
    # Zero-padded hour keeps same-date clock times sorting correctly.
    assert D.name_time_key("X (2026-08-30 9:05 ET)") < D.name_time_key("X (2026-08-30 10:00 ET)")


def test_name_suffix():
    assert D.name_suffix("Kill Switch State Machine (21:01 ET)") == "(21:01 ET)"
    assert D.name_suffix("Shared — ntr: bitchat — 2026-08-30") == "2026-08-30"
    assert D.name_suffix("Shared — ntr: bitchat — 2026-08-30 #2") == "2026-08-30 #2"
    assert D.name_suffix("Bloom Filters") == ""


def test_suffix_groups_and_pairs():
    g = D.suffix_groups(["Topic (19:01)", "topic — 2026-08-30", "Other", "Other (1)"])
    assert g == {"topic": ["Topic (19:01)", "topic — 2026-08-30"]}
    assert D.canonical_pair("b", "a") == ("a", "b")


def test_merge_groups_union_find():
    suffix = {"topic": ["Topic (19:01)", "Topic — 2026-08-30"]}
    pairs = [("Topic — 2026-08-30", "Topic v2", 0.97), ("X", "Y", 0.96)]
    groups = D.merge_groups(suffix, pairs)
    assert [g["members"] for g in groups] == [["Topic (19:01)", "Topic — 2026-08-30", "Topic v2"], ["X", "Y"]]
    assert groups[0]["signals"] == ["cosine", "suffix"] and groups[1]["signals"] == ["cosine"]
    assert groups[0]["cos"]["Topic — 2026-08-30|Topic v2"] == 0.97


def _m(name, status=None, assistant="Nova", space=None, created=None, updated=None):
    return {"name": name, "status": status, "assistant": assistant, "space": space, "created_at": created, "updated_at": updated}


def test_choose_keeper_prefers_name_time_then_updated_then_created():
    members = [_m("T — 2026-07-01", created="2026-08-01T00:00:00"), _m("T — 2026-08-01", created="2026-07-01T00:00:00")]
    assert D.choose_keeper(members) == ("T — 2026-08-01", "newest dated name suffix")
    members = [_m("A", updated="2026-08-02T00:00:00"), _m("B", updated="2026-08-03T00:00:00")]
    assert D.choose_keeper(members) == ("B", "newest updated_at")
    members = [_m("A", created="2026-08-02T00:00:00"), _m("B", created="2026-08-01T00:00:00")]
    assert D.choose_keeper(members) == ("A", "newest created_at")
    members = [_m("A", status="active"), _m("B", status="superseded")]
    assert D.choose_keeper(members) == ("A", "only live member")
    members = [_m("A"), _m("B")]
    assert D.choose_keeper(members) == ("B", "no timestamps; lexicographically last")


def test_choose_keeper_null_status_counts_as_live():
    """Private #2: `live` here must mean the same thing it means to is_handled,
    build_report and retrieval._is_active — status not in ("superseded", "removed").
    A NULL-status member is live, so it can be the only live member..."""
    members = [_m("A"), _m("B", status="superseded")]
    assert D.choose_keeper(members) == ("A", "only live member")
    # ...and two live members (one 'active', one NULL) are a real tie, not a
    # one-member shortcut — it falls through to the lexicographic fallback.
    members = [_m("A", status="active"), _m("B")]
    assert D.choose_keeper(members) == ("B", "no timestamps; lexicographically last")


def test_break_tie_prefers_live_over_superseded_including_null_status():
    """The same rule inside _break_tie: a NULL-status member beats a superseded one
    even though it is not literally status == 'active'."""
    assert D._break_tie([_m("Z", status="superseded"), _m("A")], "r") == ("A", "r")
    assert D._break_tie([_m("Z", status="removed"), _m("A", status="active")], "r") == ("A", "r")
    # all candidates dead -> no live subset to filter to; lexicographic last wins
    assert D._break_tie([_m("A", status="superseded"), _m("Z", status="superseded")], "r") == ("Z", "r")


def test_choose_keeper_clock_only_suffix_does_not_decide():
    # Controller ruling (I-1 regression): a clock-only suffix carries no day and
    # must not decide the keeper — falls through to created_at.
    members = [
        _m("Kill Switch State Machine (21:01 ET)", created="2026-06-28T00:00:00"),
        _m("Kill Switch State Machine (06:00 ET)", created="2026-07-18T00:00:00"),
    ]
    assert D.choose_keeper(members) == ("Kill Switch State Machine (06:00 ET)", "newest created_at")


def test_choose_keeper_dated_key_beats_undated_member():
    # A member with no name-time key at all (no suffix) never competes with a
    # dated one, even if it has a newer created_at/updated_at.
    members = [
        _m("Topic — 2026-01-01", created="2026-01-01T00:00:00"),
        _m("Topic", created="2026-09-01T00:00:00", updated="2026-09-01T00:00:00"),
    ]
    assert D.choose_keeper(members) == ("Topic — 2026-01-01", "newest dated name suffix")


def test_choose_keeper_same_date_clock_times_sort_correctly():
    members = [
        _m("X (2026-08-30 9:05 ET)"),
        _m("X (2026-08-30 10:00 ET)"),
    ]
    assert D.choose_keeper(members) == ("X (2026-08-30 10:00 ET)", "newest dated name suffix")


def test_choose_keeper_needs_owner_decision_across_minds_or_spaces():
    assert D.choose_keeper([_m("A", assistant="Nova"), _m("B", assistant="Grok")]) == (None, "needs_owner_decision")
    assert D.choose_keeper([_m("A", space="shared"), _m("B", space=None)]) == (None, "needs_owner_decision")


def test_is_handled():
    assert D.is_handled([_m("A", status="active"), _m("B", status="superseded")], {})
    assert D.is_handled([_m("A"), _m("B")], {"A": "B"})
    assert not D.is_handled([_m("A"), _m("B")], {})
    assert not D.is_handled([_m("A"), _m("B"), _m("C", status="superseded")], {})


def test_build_report_and_markdown():
    groups = [{"members": ["A — 2026-01-01", "A — 2026-02-01"], "signals": ["suffix"], "cos": {}},
              {"members": ["X", "Y"], "signals": ["cosine"], "cos": {"X|Y": 0.96}}]
    meta = {"A — 2026-01-01": _m("A — 2026-01-01"), "A — 2026-02-01": _m("A — 2026-02-01"), "X": _m("X", status="active"), "Y": _m("Y", status="superseded")}
    rep = D.build_report(groups, meta, {"X": "Y"}, include_handled=False)
    assert rep["summary"] == {"groups": 1, "facts": 2, "handled": 1, "needs_owner_decision": 0, "suggested_commands": 1}
    g = rep["groups"][0]
    assert g["keeper"] == "A — 2026-02-01"
    assert g["reason"] == "newest dated name suffix"
    assert len(g["commands"]) == 1
    assert shlex.split(g["commands"][0]) == ["ai-memory", "supersede", "A — 2026-02-01", "A — 2026-01-01", "--apply"]
    # name_time_key/name_suffix are carried into the JSON member dict.
    keeper_member = next(m for m in g["members"] if m["name"] == "A — 2026-02-01")
    assert keeper_member["name_time_key"] == "2026-02-01"
    assert keeper_member["name_suffix"] == "2026-02-01"
    rep2 = D.build_report(groups, meta, {"X": "Y"}, include_handled=True)
    assert rep2["summary"]["groups"] == 2 and rep2["groups"][1]["handled"] is True
    md = D.render_markdown(rep)
    assert "A — 2026-02-01" in md and "ai-memory supersede" in md and md.startswith("# Duplicate Facts report")
    assert "| Suffix |" in md

    # missing-meta fallback is flagged so callers can surface it
    rep3 = D.build_report(
        [{"members": ["A — 2026-01-01", "Z"], "signals": ["suffix"], "cos": {}}], meta, {}, include_handled=True
    )
    missing = next(m for m in rep3["groups"][0]["members"] if m["name"] == "Z")
    assert missing["meta_missing"] is True
    assert missing["name_time_key"] is None
    assert missing["name_suffix"] == ""
    present = next(m for m in rep3["groups"][0]["members"] if m["name"] == "A — 2026-01-01")
    assert "meta_missing" not in present


def test_render_markdown_escapes_pipe():
    groups = [{"members": ["B|C"], "signals": ["suffix"], "cos": {}}]
    meta = {"B|C": _m("B|C")}
    rep = D.build_report(groups, meta, {}, include_handled=True)
    md = D.render_markdown(rep)
    row = next(line for line in md.splitlines() if "B" in line and line.startswith("|"))
    # split on unescaped "|" only, the way a markdown table renderer would treat cell delimiters
    cells = re.split(r"(?<!\\)\|", row)
    assert len(cells) == 9  # 7 columns -> leading/trailing empty strings + 7 cells
    assert "B\\|C" in md


def test_build_report_commands_are_shell_safe():
    keeper_name = "A"
    other_name = 'B "weird" name'
    groups = [{"members": [keeper_name, other_name], "signals": ["suffix"], "cos": {}}]
    # Both members are live (NULL status is live), so the keeper is decided by created_at.
    meta = {
        keeper_name: _m(keeper_name, status="active", created="2026-02-01T00:00:00"),
        other_name: _m(other_name, created="2026-01-01T00:00:00"),
    }
    rep = D.build_report(groups, meta, {}, include_handled=False)
    cmd = rep["groups"][0]["commands"][0]
    assert shlex.split(cmd) == ["ai-memory", "supersede", keeper_name, other_name, "--apply"]


# --- Task 2: Neo4j I/O -------------------------------------------------------


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
        p = dict(params or {})
        p.update(kw)
        self.calls.append((cypher, p))
        return FakeResult(self.rows_by_call.pop(0) if self.rows_by_call else [])

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeDriver:
    def __init__(self, session):
        self._s = session

    def session(self):
        return self._s

    def close(self):
        pass


def test_load_fact_meta_maps_rows():
    rows = [
        {"name": "A", "status": "active", "assistant": "Nova", "space": None,
         "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-02T00:00:00", "has_embedding": True},
        {"name": "B", "status": None, "assistant": None, "space": "shared",
         "created_at": None, "updated_at": None, "has_embedding": False},
    ]
    s = FakeSession([rows])
    meta = D.load_fact_meta(s)
    cypher, params = s.calls[0]
    assert cypher == (
        "MATCH (f:Fact) RETURN f.name AS name, f.status AS status, f.assistant AS assistant, "
        "f.space AS space, toString(f.created_at) AS created_at, toString(f.updated_at) AS updated_at, "
        "f.embedding IS NOT NULL AS has_embedding"
    )
    assert params == {}
    assert set(meta.keys()) == {"A", "B"}
    assert meta["A"]["has_embedding"] is True
    assert meta["B"]["space"] == "shared"


def test_near_copy_pairs_fetch_and_search_per_embedded_fact():
    rows_by_call = [
        [{"name": "A"}, {"name": "B"}],                            # embedded-names listing
        [{"e": [0.1, 0.2]}],                                        # vec fetch for A
        [{"name": "B", "cos": 0.97}, {"name": "C", "cos": 0.5}],    # search for A
        [{"e": [0.1, 0.2]}],                                        # vec fetch for B
        [{"name": "A", "cos": 0.97}],                               # search for B (symmetric)
    ]
    s = FakeSession(rows_by_call)
    pairs = D.near_copy_pairs(s, index="fact_embeddings", threshold=0.95, k=3)
    assert pairs == [("A", "B", 0.97)]
    assert len(s.calls) == 5
    fetch_cypher, fetch_params = s.calls[1]
    assert fetch_cypher == "MATCH (f:Fact {name:$name}) RETURN f.embedding AS e"
    assert fetch_params == {"name": "A"}
    search_cypher, search_params = s.calls[2]
    assert search_cypher.startswith("CYPHER 25")
    assert "VECTOR INDEX `fact_embeddings`" in search_cypher
    assert "2 * vector.similarity.cosine($vec, g.embedding) - 1" in search_cypher
    assert search_params["pool"] == 4  # k + 1
    assert search_params["name"] == "A"


def test_near_copy_pairs_rejects_invalid_index():
    s = FakeSession([])
    with pytest.raises(ValueError):
        D.near_copy_pairs(s, index="bad-name!", threshold=0.95, k=3)
    assert s.calls == []


def test_near_copy_pairs_skips_fact_without_embedding():
    rows_by_call = [
        [{"name": "A"}],   # embedded-names listing
        [{"e": None}],      # vec fetch unexpectedly null
    ]
    s = FakeSession(rows_by_call)
    pairs = D.near_copy_pairs(s, index="fact_embeddings", threshold=0.95, k=3)
    assert pairs == []
    assert len(s.calls) == 2  # no SEARCH call issued


def test_plan_supersedes_ok():
    s = FakeSession([[{"name": "A"}, {"name": "B"}]])
    out = D.plan_supersedes(s, [("A", "B")], {})
    assert out == [{"new": "A", "old": "B", "ok": True, "reason": None}]
    cypher, params = s.calls[0]
    assert "IN $names" in cypher
    assert sorted(params["names"]) == ["A", "B"]


def test_plan_supersedes_unknown_new():
    s = FakeSession([[{"name": "B"}]])
    out = D.plan_supersedes(s, [("A", "B")], {})
    assert out == [{"new": "A", "old": "B", "ok": False, "reason": "unknown new"}]


def test_plan_supersedes_unknown_old():
    s = FakeSession([[{"name": "A"}]])
    out = D.plan_supersedes(s, [("A", "B")], {})
    assert out == [{"new": "A", "old": "B", "ok": False, "reason": "unknown old"}]


def test_plan_supersedes_same_fact():
    s = FakeSession([[{"name": "A"}]])
    out = D.plan_supersedes(s, [("A", "A")], {})
    assert out == [{"new": "A", "old": "A", "ok": False, "reason": "same fact"}]


def test_plan_supersedes_old_already_superseded_by_another():
    s = FakeSession([[{"name": "A"}, {"name": "B"}]])
    out = D.plan_supersedes(s, [("A", "B")], {"X": "B"})
    assert out == [{"new": "A", "old": "B", "ok": False, "reason": "old already superseded by X"}]


def test_plan_supersedes_direct_cycle():
    s = FakeSession([[{"name": "A"}, {"name": "B"}]])
    out = D.plan_supersedes(s, [("A", "B")], {"B": "A"})
    assert out == [{"new": "A", "old": "B", "ok": False, "reason": "would create a cycle"}]


def test_plan_supersedes_transitive_cycle():
    s = FakeSession([[{"name": "A"}, {"name": "C"}]])
    out = D.plan_supersedes(s, [("A", "C")], {"B": "A", "C": "B"})
    assert out == [{"new": "A", "old": "C", "ok": False, "reason": "would create a cycle"}]


def test_supersede_fact_refuses_without_write():
    s = FakeSession([
        [],               # load_supersedes
        [{"name": "B"}],  # existence check: A missing
    ])
    with pytest.raises(ValueError, match="unknown new"):
        D.supersede_fact(s, "A", "B")
    assert len(s.calls) == 2


def test_supersede_fact_runs_statement_on_ok():
    s = FakeSession([
        [],                              # load_supersedes
        [{"name": "A"}, {"name": "B"}],  # existence check
        [{"old": "B"}],                  # write result
    ])
    result = D.supersede_fact(s, "A", "B", by="tester", now="2026-09-05T00:00:00+00:00")
    assert result == {"new": "A", "old": "B", "at": "2026-09-05T00:00:00+00:00"}
    cypher, params = s.calls[-1]
    assert params == {"neu": "A", "old": "B", "now": "2026-09-05T00:00:00+00:00", "by": "tester"}
    assert "MERGE (neu)-[r:SUPERSEDES]->(old)" in cypher
    assert "SET old.status = 'superseded'" in cypher


def test_duplicate_report_merges_and_reports():
    meta_rows = [
        {"name": "Topic (19:01)", "status": "active", "assistant": "Nova", "space": None,
         "created_at": None, "updated_at": None, "has_embedding": False},
        {"name": "Topic (21:01)", "status": "active", "assistant": "Nova", "space": None,
         "created_at": None, "updated_at": None, "has_embedding": False},
    ]
    s = FakeSession([
        meta_rows,  # load_fact_meta
        [],         # load_supersedes
        [],         # near_copy_pairs embedded-names listing (none embedded)
    ])
    driver = FakeDriver(s)
    report = D.duplicate_report(driver, threshold=0.9, k=2, index="fact_embeddings")
    assert report["params"] == {"threshold": 0.9, "k": 2, "index": "fact_embeddings"}
    assert report["summary"]["near_copy_pairs"] == 0
    assert report["summary"]["supersedes_loaded"] == 0
    assert report["summary"]["groups"] == 1
    assert report["groups"][0]["members"][0]["name"] == "Topic (19:01)"
    assert report["groups"][0]["keeper"] == "Topic (21:01)"


def test_duplicate_report_default_index_resolution(monkeypatch):
    monkeypatch.delenv("NEO4J_VECTOR_INDEX", raising=False)
    s = FakeSession([[], [], []])
    driver = FakeDriver(s)
    report = D.duplicate_report(driver)
    assert report["params"]["index"] == "fact_embeddings"


def test_duplicate_report_records_supersedes_loaded():
    meta_rows = [
        {"name": "A", "status": "superseded", "assistant": "Nova", "space": None,
         "created_at": None, "updated_at": None, "has_embedding": False},
        {"name": "B", "status": "active", "assistant": "Nova", "space": None,
         "created_at": None, "updated_at": None, "has_embedding": False},
    ]
    s = FakeSession([
        meta_rows,               # load_fact_meta
        [{"n": "B", "o": "A"}],  # load_supersedes (best-effort)
        [],                      # near_copy_pairs embedded-names listing
    ])
    driver = FakeDriver(s)
    report = D.duplicate_report(driver, index="fact_embeddings")
    assert report["summary"]["supersedes_loaded"] == 1


def test_load_supersedes_strict_maps_rows():
    s = FakeSession([[{"n": "A", "o": "B"}]])
    result = D.load_supersedes_strict(s)
    assert result == {"A": {"B"}}
    cypher, params = s.calls[0]
    assert cypher == "MATCH (n:Fact)-[:SUPERSEDES]->(o:Fact) RETURN n.name AS n, o.name AS o"
    assert params == {}


def test_load_supersedes_strict_keeps_every_edge_of_one_keeper():
    """A keeper superseding several olds has one row per old; a {new: old} dict
    would keep only the last."""
    s = FakeSession([[{"n": "K", "o": "A"}, {"n": "K", "o": "B"}]])
    assert D.load_supersedes_strict(s) == {"K": {"A", "B"}}


def test_is_handled_when_keeper_supersedes_two_olds():
    """K supersedes both A and B: the group is fully resolved and must not be
    reported to the owner as still needing action."""
    members = [
        {"name": "K", "status": "active"},
        {"name": "A", "status": "active"},
        {"name": "B", "status": "active"},
    ]
    assert D.is_handled(members, {"K": {"A", "B"}}) is True
    # only one of the two edges known (the old lossy {new: old} dict) -> A reads
    # as still unresolved and the owner is asked to act on a finished group
    assert D.is_handled(members, {"K": {"B"}}) is False


def test_plan_supersedes_sees_every_edge_of_a_multi_old_keeper():
    """K->A and K->B both loaded: proposing M->A is refused as already
    superseded, and A->K is refused as a cycle."""
    s = FakeSession([[{"name": "M"}, {"name": "A"}]])
    out = D.plan_supersedes(s, [("M", "A")], {"K": {"A", "B"}})
    assert out == [{"new": "M", "old": "A", "ok": False, "reason": "old already superseded by K"}]

    s = FakeSession([[{"name": "A"}, {"name": "K"}]])
    out = D.plan_supersedes(s, [("A", "K")], {"K": {"A", "B"}})
    assert out == [{"new": "A", "old": "K", "ok": False, "reason": "would create a cycle"}]


def test_load_supersedes_strict_does_not_swallow_errors():
    class BoomSession(FakeSession):
        def run(self, cypher, params=None, **kw):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        D.load_supersedes_strict(BoomSession([]))


def test_supersede_fact_propagates_strict_load_error_and_issues_no_write():
    class RaisesOnFirstCall(FakeSession):
        def run(self, cypher, params=None, **kw):
            p = dict(params or {})
            p.update(kw)
            self.calls.append((cypher, p))
            if len(self.calls) == 1:
                raise RuntimeError("boom")
            return FakeResult(self.rows_by_call.pop(0) if self.rows_by_call else [])

    s = RaisesOnFirstCall([])
    with pytest.raises(RuntimeError, match="boom"):
        D.supersede_fact(s, "A", "B")
    assert len(s.calls) == 1  # only the failed strict-load call; no existence check, no write
