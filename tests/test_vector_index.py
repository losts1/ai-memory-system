from __future__ import annotations

import pytest
from neo4j.exceptions import ClientError, Neo4jError

from ai_memory import vector_index as VI


def _ce(code: str, message: str) -> ClientError:
    """A ClientError whose `.code` really reflects `code` (see tests/test_search_path.py)."""
    return Neo4jError._hydrate_neo4j(code=code, message=message)


class Res:
    def __init__(self, rows): self.rows = rows
    def single(self): return self.rows[0] if self.rows else None
    def __iter__(self): return iter(self.rows)
    def consume(self): return None


class Sess:
    """Scripted fake: `script` maps a substring of the Cypher to a list of row-lists (popped in order)."""
    def __init__(self, script=None):
        self.calls = []; self.script = {k: list(v) for k, v in (script or {}).items()}
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def run(self, q, params=None, **kw):
        p = dict(params or {}); p.update(kw); self.calls.append((q, p))
        for key, rows in self.script.items():
            if key in q:
                return Res(rows.pop(0) if rows else [])
        return Res([])


class Drv:
    def __init__(self, s): self.s = s
    def session(self): return self.s


PROPS = ("assistant", "space", "status", "provenance_trust")
VEC = [0.1] * 768


def test_create_ddl_shape():
    ddl = VI.build_create_index_ddl("factEmbeddingIndex", PROPS)
    assert ddl.startswith("CYPHER 25\n")
    assert ddl[len("CYPHER 25\n"):] == (
                   "CREATE VECTOR INDEX `factEmbeddingIndex` FOR (f:Fact) ON (f.embedding) "
                   "WITH [f.assistant, f.space, f.status, f.provenance_trust] "
                   "OPTIONS {indexConfig: {`vector.dimensions`: 768, `vector.similarity_function`: 'cosine'}}")
    with pytest.raises(ValueError):
        VI.build_create_index_ddl("bad name", PROPS)
    with pytest.raises(ValueError):
        VI.build_create_index_ddl("ok", ("assistant", "bad-prop"))
    with pytest.raises(ValueError):
        VI.build_create_index_ddl("ok", ())
    with pytest.raises(ValueError, match="invalid property name"):
        VI.build_create_index_ddl("ok", ("bad-prop",))


def test_create_ddl_plain_has_no_cypher25_prefix_or_with_clause():
    ddl = VI.build_create_index_ddl_plain("factEmbeddingIndex")
    assert ddl == (
        "CREATE VECTOR INDEX `factEmbeddingIndex` FOR (f:Fact) ON (f.embedding) "
        "OPTIONS {indexConfig: {`vector.dimensions`: 768, `vector.similarity_function`: 'cosine'}}")
    with pytest.raises(ValueError):
        VI.build_create_index_ddl_plain("bad name")


def test_drop_ddl_and_probe_shape():
    assert VI.build_drop_ddl("x_v2") == "DROP INDEX `x_v2` IF EXISTS"
    q = VI.build_probe_cypher("idx", "f.assistant = $v")
    assert q.startswith("CYPHER 25\nMATCH (f:Fact)\n")
    assert "SEARCH f IN (VECTOR INDEX `idx` FOR $vec WHERE f.assistant = $v LIMIT $pool) SCORE AS s" in q
    assert "WHERE" not in VI.build_probe_cypher("idx", "")


def test_index_info_and_wait_online():
    s = Sess({"SHOW INDEXES": [
        [{"state": "POPULATING", "populationPercent": 40.0, "properties": ["embedding"]}],
        [{"state": "ONLINE", "populationPercent": 100.0, "properties": ["embedding", "assistant"]}],
    ]})
    t = [0.0]
    def clock(): return t[0]
    def sleep(x): t[0] += x
    waited = VI.wait_online(s, "idx", timeout_s=10, poll_s=0.5, clock=clock, sleep=sleep)
    assert waited == pytest.approx(0.5) and len([c for c in s.calls if "SHOW INDEXES" in c[0]]) == 2


def test_wait_online_times_out():
    s = Sess({"SHOW INDEXES": [[{"state": "POPULATING", "populationPercent": 1.0, "properties": []}]] * 100})
    t = [0.0]
    with pytest.raises(TimeoutError):
        VI.wait_online(s, "idx", timeout_s=2, poll_s=1, clock=lambda: t[0], sleep=lambda x: t.__setitem__(0, t[0] + x))


def test_membership_check_compares_distinct_names_to_embedded_count():
    s = Sess({
        "count(f)": [[{"c": 3}]],
        "SEARCH f IN": [[{"name": "A", "s": 0.9}, {"name": "B", "s": 0.8}, {"name": "A", "s": 0.7}]],
        "NOT f.name IN $names": [[{"name": "C"}]],
    })
    r = VI.membership_check(s, "idx", VEC)
    assert r == {"embedded": 3, "returned": 2, "ok": False, "missing_sample": ["C"]}
    _q, p = next(c for c in s.calls if "SEARCH f IN" in c[0])
    assert p["pool"] == 3 and p["vec"] == VEC


def test_membership_check_omits_missing_sample_when_it_matches():
    s = Sess({"count(f)": [[{"c": 2}]], "SEARCH f IN": [[{"name": "A", "s": 0.9}, {"name": "B", "s": 0.8}]]})
    r = VI.membership_check(s, "idx", VEC)
    assert r == {"embedded": 2, "returned": 2, "ok": True}
    assert not any("NOT f.name IN $names" in c[0] for c in s.calls)


def test_property_probes_use_most_common_value_and_skip_absent_props():
    s = Sess({
        "f.assistant IS NOT NULL": [[{"v": "Nova", "c": 1261}]],
        "f.space IS NOT NULL": [[]],
        "WHERE f.assistant = $v": [[{"name": "X", "s": 0.9}]],
    })
    r = VI.property_probes(s, "idx", VEC, ("assistant", "space"))
    assert r["assistant"] == {"present": 1261, "value": "Nova", "rows": 1, "ok": True}
    assert r["space"] == {"present": 0, "value": None, "rows": None, "ok": True}


def test_property_probes_catches_client_error_per_property_instead_of_raising():
    class Boom(Sess):
        def run(self, q, params=None, **kw):
            if "WHERE f.assistant = $v" in q:
                raise _ce("Neo.ClientError.Statement.SyntaxError", "not among the accepted predicates")
            return super().run(q, params, **kw)
    s = Boom({"f.assistant IS NOT NULL": [[{"v": ["Nova", "Weft"], "c": 5}]]})
    r = VI.property_probes(s, "idx", VEC, ("assistant",))
    assert r["assistant"]["ok"] is False
    assert r["assistant"]["rows"] is None
    assert r["assistant"]["present"] == 5
    assert "not among the accepted predicates" in r["assistant"]["error"]


def test_preflight_creates_waits_probes_and_always_drops_v2():
    s = Sess({
        # first entry: the base index's current state (no "assistant" yet, so already_migrated
        # is False and preflight proceeds); the rest: the temp index during/after creation.
        "SHOW INDEXES": [[{"state": "ONLINE", "populationPercent": 100.0, "properties": ["embedding"]}]]
                         + [[{"state": "ONLINE", "populationPercent": 100.0, "properties": ["embedding", "assistant"]}]] * 5,
        "count(f)": [[{"c": 2}]],
        "SEARCH f IN": [[{"name": "A", "s": 1.0}, {"name": "B", "s": 0.9}], [{"name": "A", "s": 1.0}]],
        "IS NOT NULL": [[{"v": "Nova", "c": 2}]],
    })
    rep = VI.preflight(Drv(s), "factEmbeddingIndex", ("assistant",), embed_fn=lambda t: VEC, log=lambda *a: None)
    stmts = [c[0] for c in s.calls]
    assert any(st.startswith("CYPHER 25\nCREATE VECTOR INDEX `factEmbeddingIndex_v2`") for st in stmts)
    assert stmts[-1] == "DROP INDEX `factEmbeddingIndex_v2` IF EXISTS"
    assert rep["membership"]["ok"] and rep["probes"]["assistant"]["ok"] and rep["temp_index"] == "factEmbeddingIndex_v2"
    assert "population_s" in rep and rep["show_properties"] == ["embedding", "assistant"]


def test_preflight_drops_v2_even_when_a_probe_raises():
    class Boom(Sess):
        def run(self, q, params=None, **kw):
            if "SEARCH f IN" in q:
                self.calls.append((q, {})); raise RuntimeError("probe failed")
            return super().run(q, params, **kw)
    s = Boom({"SHOW INDEXES": [[{"state": "ONLINE", "populationPercent": 100.0, "properties": ["embedding"]}]] * 3, "count(f)": [[{"c": 1}]]})
    with pytest.raises(RuntimeError):
        VI.preflight(Drv(s), "idx", ("assistant",), embed_fn=lambda t: VEC, log=lambda *a: None)
    assert s.calls[-1][0] == "DROP INDEX `idx_v2` IF EXISTS"


def test_preflight_reports_already_migrated_and_skips_v2_creation():
    s = Sess({"SHOW INDEXES": [[{"state": "ONLINE", "populationPercent": 100.0,
                                  "properties": ["embedding", "assistant", "space", "status", "provenance_trust"]}]]})
    calls = []
    rep = VI.preflight(Drv(s), "idx", PROPS, embed_fn=lambda t: calls.append(t) or VEC, log=lambda *a: None)
    assert rep == {"ok": True, "already_migrated": True, "index": "idx", "props": list(PROPS),
                   "show_properties": ["embedding", "assistant", "space", "status", "provenance_trust"]}
    assert calls == []   # embedding is never called when already migrated
    assert not any("CREATE VECTOR INDEX" in c[0] for c in s.calls)


def test_migrate_dry_run_runs_nothing():
    s = Sess()
    rep = VI.migrate(Drv(s), "idx", PROPS, embed_fn=lambda t: VEC, dry_run=True, log=lambda *a: None)
    assert s.calls == [] and rep["ok"] is True and rep["dry_run"] is True
    assert rep["statements"][0] == "DROP INDEX `idx` IF EXISTS" and rep["statements"][1].startswith("CYPHER 25\nCREATE VECTOR INDEX `idx`")


def test_migrate_drops_creates_waits_and_gates():
    s = Sess({
        # first entry: the live index before migration (no filter props, so not already_migrated)
        "SHOW INDEXES": [[{"state": "ONLINE", "populationPercent": 100.0, "properties": ["embedding"]}]]
                        + [[{"state": "ONLINE", "populationPercent": 100.0, "properties": ["embedding", "assistant", "space", "status", "provenance_trust"]}]] * 5,
        "count(f)": [[{"c": 2}]],
        "SEARCH f IN": [[{"name": "A", "s": 1.0}, {"name": "B", "s": 0.9}]] + [[{"name": "A", "s": 1.0}]] * 4,
        "IS NOT NULL": [[{"v": "Nova", "c": 2}], [{"v": "shared", "c": 1}], [{"v": "active", "c": 1}], [{"v": "trusted", "c": 1}]],
    })
    rep = VI.migrate(Drv(s), "idx", PROPS, embed_fn=lambda t: VEC, log=lambda *a: None)
    stmts = [c[0] for c in s.calls if "SHOW INDEXES" not in c[0]]   # the already-migrated pre-check reads first
    assert stmts[0] == "CYPHER 25\nRETURN 1 AS ok"
    assert stmts[1] == "DROP INDEX `idx` IF EXISTS" and stmts[2].startswith("CYPHER 25\nCREATE VECTOR INDEX `idx` ")
    assert rep["ok"] is True and rep["membership"]["ok"] and all(p["ok"] for p in rep["probes"].values())


def test_migrate_gate_fails_on_membership_mismatch_and_keeps_index():
    s = Sess({
        "SHOW INDEXES": [[{"state": "ONLINE", "populationPercent": 100.0, "properties": ["embedding"]}]] * 3,
        "count(f)": [[{"c": 3}]],
        "SEARCH f IN": [[{"name": "A", "s": 1.0}]] * 5,
        "IS NOT NULL": [[]] * 4,
    })
    rep = VI.migrate(Drv(s), "idx", PROPS, embed_fn=lambda t: VEC, log=lambda *a: None)
    assert rep["ok"] is False and rep["membership"] == {"embedded": 3, "returned": 1, "ok": False, "missing_sample": []}
    assert not any(st.startswith("DROP INDEX `idx`") for st in [c[0] for c in s.calls][3:])   # no drop after the create


def test_migrate_fails_closed_without_embedding():
    s = Sess()
    with pytest.raises(RuntimeError):
        VI.migrate(Drv(s), "idx", PROPS, embed_fn=lambda t: None, log=lambda *a: None)
    assert all("SHOW INDEXES" in q for q, _ in s.calls)   # only the read-only pre-check ran; no DDL


def test_migrate_aborts_before_any_change_when_cypher25_capability_rejected():
    class Boom(Sess):
        def run(self, q, params=None, **kw):
            if "RETURN 1 AS ok" in q:
                self.calls.append((q, {}))
                raise _ce("Neo.ClientError.Statement.SyntaxError", "Unsupported language version '25'")
            return super().run(q, params, **kw)
    s = Boom()
    with pytest.raises(RuntimeError, match="server rejected Cypher 25; migration aborted before any change"):
        VI.migrate(Drv(s), "idx", PROPS, embed_fn=lambda t: VEC, log=lambda *a: None)
    assert not any(q.startswith("DROP INDEX") or "CREATE VECTOR INDEX" in q for q, _ in s.calls)


def test_migrate_recovers_plain_index_and_reports_when_create_fails_after_drop():
    class Boom(Sess):
        def run(self, q, params=None, **kw):
            if "CREATE VECTOR INDEX `idx`" in q and "WITH [" in q:
                self.calls.append((q, {}))
                raise _ce("Neo.ClientError.Statement.SyntaxError", "boom")
            return super().run(q, params, **kw)
    s = Boom()
    with pytest.raises(RuntimeError) as ei:
        VI.migrate(Drv(s), "idx", PROPS, embed_fn=lambda t: VEC, log=lambda *a: None)
    msg = str(ei.value)
    assert "dropped" in msg
    assert "CREATE VECTOR INDEX `idx`" in msg and "WITH [" in msg   # the exact filtered CREATE to re-run
    stmts = [q for q, _ in s.calls if "SHOW INDEXES" not in q]
    assert stmts[0] == "CYPHER 25\nRETURN 1 AS ok"
    assert stmts[1] == "DROP INDEX `idx` IF EXISTS"
    assert any("CREATE VECTOR INDEX `idx` FOR (f:Fact) ON (f.embedding) OPTIONS" in q and "WITH [" not in q
               for q in stmts)   # plain index recreated as recovery


def test_migrate_recovery_reports_not_recovered_when_plain_create_also_fails():
    class Boom(Sess):
        def run(self, q, params=None, **kw):
            if "CREATE VECTOR INDEX `idx`" in q:
                self.calls.append((q, {}))
                raise _ce("Neo.ClientError.Statement.SyntaxError", "boom")
            return super().run(q, params, **kw)
    s = Boom()
    with pytest.raises(RuntimeError, match="recovered: no"):
        VI.migrate(Drv(s), "idx", PROPS, embed_fn=lambda t: VEC, log=lambda *a: None)


def test_migrate_noops_when_already_migrated():
    """review #5: migrate() dropped a healthy filtered index; mirror preflight's short-circuit."""
    s = Sess({"SHOW INDEXES": [[{"state": "ONLINE", "populationPercent": 100.0,
                                  "properties": ["embedding", *PROPS]}]]})

    def no_embed(_t):
        raise AssertionError("must not embed when nothing will change")

    rep = VI.migrate(Drv(s), "idx", PROPS, embed_fn=no_embed, log=lambda *a: None)
    assert rep["ok"] is True and rep["already_migrated"] is True and rep["index"] == "idx"
    assert not any("DROP INDEX" in c[0] or "CREATE VECTOR INDEX" in c[0] for c in s.calls)
