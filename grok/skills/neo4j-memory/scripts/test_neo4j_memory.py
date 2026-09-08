#!/usr/bin/env python3
"""Unit tests for neo4j_memory helpers. No live Neo4j/Ollama required."""
from __future__ import annotations

import hashlib
import inspect
import io
import json
import math
import sys
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from neo4j.exceptions import DriverError

import neo4j_memory as nm

_EMBED_CFG = {
    "embed_model": "nomic-embed-text",
    "embed_dim": 768,
    "ollama": "http://127.0.0.1:11434",
    "keep_alive": "30m",
    "embed_timeout": 3.0,
}


class _Sess:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, *a, **kw):
        return []


class _Drv:
    def session(self):
        return _Sess()


class ParseEmbed(unittest.TestCase):
    def test_api_embed(self):
        vec = nm.parse_embed_body({"embeddings": [[0.1, 0.2, 0.3]]})
        self.assertEqual(vec, [0.1, 0.2, 0.3])

    def test_api_embeddings_legacy(self):
        vec = nm.parse_embed_body({"embedding": [1, 2]})
        self.assertEqual(vec, [1.0, 2.0])

    def test_empty_rejected(self):
        self.assertIsNone(nm.parse_embed_body({}))
        self.assertIsNone(nm.parse_embed_body({"embeddings": []}))
        self.assertIsNone(nm.parse_embed_body({"embedding": []}))
        self.assertIsNone(nm.parse_embed_body({"embeddings": [["x"]]}))


class FactText(unittest.TestCase):
    def test_canonical_order_and_dash_points(self):
        self.assertEqual(nm._fact_text("Name", "Sum", "Body", ["p1", "p2"]), "Name Sum - p1 - p2 Body")

    def test_cap_2000(self):
        self.assertEqual(len(nm._fact_text("n", "x" * 5000)), nm.EMBED_CHARS)
        self.assertEqual(nm.FACT_EMBED_CHARS, nm.EMBED_CHARS)

    def test_boilerplate_runs_removed(self):
        grams = {"topic selection gap filling", "selection gap filling memory"}
        self.assertEqual(nm._fact_text("Fact", "Topic Selection Gap Filling memory. VPIN matters.", None, None, grams),
                         "Fact VPIN matters.")

    def test_text_sha_versioned(self):
        self.assertEqual(nm.text_sha("t", 1), hashlib.sha256(b"1\nt").hexdigest()[:16])
        self.assertNotEqual(nm.text_sha("t", 1), nm.text_sha("t", 2))


class RetrievalConfigReader(unittest.TestCase):
    class _S:
        def __init__(self, rows, raise_=None): self.rows, self.raise_ = rows, raise_
        def run(self, q, **kw):
            if self.raise_: raise self.raise_
            rows = self.rows
            class R:
                def single(self_inner): return rows[0] if rows else None
            return R()

    def test_reads_version_and_grams(self):
        cfg = nm._load_retrieval_config(self._S([{"version": 3, "boilerplate": ["a b c d"]}]))
        self.assertEqual(cfg, {"version": 3, "boilerplate": frozenset({"a b c d"})})

    def test_missing_or_failed_is_none(self):
        self.assertIsNone(nm._load_retrieval_config(self._S([])))
        self.assertIsNone(nm._load_retrieval_config(self._S([{"version": None, "boilerplate": []}])))
        self.assertIsNone(nm._load_retrieval_config(self._S([], raise_=DriverError("down"))))


class _CasSess:
    """Records every session.run() call; routes the read query to `row`, everything
    else (the CAS write) to a fixed `{"embedded": n}` result."""

    def __init__(self, row, embedded_count=1):
        self.row = row
        self.embedded_count = embedded_count
        self.calls = []

    def run(self, query, **kw):
        self.calls.append((query, kw))
        is_read = "f.key_points AS key_points, f.content AS content" in query
        result = self.row if is_read else {"embedded": self.embedded_count}

        class R:
            def single(self_inner):
                return result
        return R()


_EMBED_FACT_CFG = {"embed_model": "nomic-embed-text", "embed_dim": 768}
_EMBED_FACT_RC = {"version": 3, "boilerplate": frozenset()}
_EMBED_FACT_ROW = {"name": "Foo", "summary": "Sum", "key_points": ["p1"], "content": None}


class EmbedFactCas(unittest.TestCase):
    def test_embedded_writes_cas_params_and_provenance(self):
        vec = [0.1] * 768
        sess = _CasSess(_EMBED_FACT_ROW, embedded_count=1)
        with mock.patch.object(nm, "ollama_embed", lambda text, cfg=None, timeout=None: vec):
            status = nm._embed_fact_cas(sess, "Foo", _EMBED_FACT_CFG, _EMBED_FACT_RC, timeout=5.0)
        self.assertEqual(status, "embedded")
        self.assertEqual(len(sess.calls), 2)
        write_kw = sess.calls[1][1]
        self.assertEqual(write_kw["cas_summary"], "Sum")
        self.assertEqual(write_kw["cas_key_points"], ["p1"])
        self.assertEqual(write_kw["cas_content"], "")
        text = nm.fact_embed_text("Foo", "Sum", ["p1"], None, _EMBED_FACT_RC["boilerplate"])
        self.assertEqual(write_kw["embedding_text_sha"], nm.text_sha(text, _EMBED_FACT_RC["version"]))
        self.assertEqual(write_kw["embedding_model"], _EMBED_FACT_CFG["embed_model"])
        self.assertEqual(write_kw["embedding_dim"], _EMBED_FACT_CFG["embed_dim"])
        self.assertEqual(write_kw["boilerplate_version"], _EMBED_FACT_RC["version"])

    def test_cas_skipped_when_write_matches_zero_rows(self):
        sess = _CasSess(_EMBED_FACT_ROW, embedded_count=0)
        with mock.patch.object(nm, "ollama_embed", lambda text, cfg=None, timeout=None: [0.1] * 768):
            status = nm._embed_fact_cas(sess, "Foo", _EMBED_FACT_CFG, _EMBED_FACT_RC, timeout=5.0)
        self.assertEqual(status, "cas_skipped")

    def test_no_config_short_circuits_with_zero_calls(self):
        sess = _CasSess(_EMBED_FACT_ROW)

        def boom(*a, **k):
            raise AssertionError("should not query when retrieval_cfg is None")
        sess.run = boom
        status = nm._embed_fact_cas(sess, "Foo", _EMBED_FACT_CFG, None, timeout=5.0)
        self.assertEqual(status, "no_config")

    def test_ollama_down_after_read_is_embed_failed(self):
        sess = _CasSess(_EMBED_FACT_ROW)
        with mock.patch.object(nm, "ollama_embed", lambda *a, **k: None):
            status = nm._embed_fact_cas(sess, "Foo", _EMBED_FACT_CFG, _EMBED_FACT_RC, timeout=5.0)
        self.assertEqual(status, "embed_failed")
        self.assertEqual(len(sess.calls), 1)

    def test_missing_fact(self):
        sess = _CasSess(None)
        status = nm._embed_fact_cas(sess, "Ghost", _EMBED_FACT_CFG, _EMBED_FACT_RC, timeout=5.0)
        self.assertEqual(status, "missing")
        self.assertEqual(len(sess.calls), 1)

    def test_all_boilerplate_text_is_empty_text_not_embed_failed(self):
        """M3: a Fact whose prepared text strips to empty (name + body entirely corpus
        boilerplate) is its own status, distinct from an Ollama failure, so cmd_embed
        can count it as skipped rather than a failure."""
        row = {"name": "a b c d e", "summary": None, "key_points": None, "content": None}
        rc = {"version": 1, "boilerplate": frozenset({"a b c d", "b c d e"})}
        sess = _CasSess(row)

        def boom(*a, **k):
            raise AssertionError("should not call ollama_embed for empty text")
        with mock.patch.object(nm, "ollama_embed", boom):
            status = nm._embed_fact_cas(sess, "a b c d e", _EMBED_FACT_CFG, rc, timeout=5.0)
        self.assertEqual(status, "empty_text")
        self.assertEqual(len(sess.calls), 1)


class CmdEmbedSelection(unittest.TestCase):
    def test_selects_missing_or_foreign_vectors(self):
        src = inspect.getsource(nm.cmd_embed)
        self.assertIn("f.embedding IS NULL OR f.embedding_text_sha IS NULL", src)


class MergeRrf(unittest.TestCase):
    def test_overlap_ranks_higher(self):
        ft = [
            {"name": "A", "teaser": "ft-a", "assistant": "Nova", "key_points": [], "score": 9},
            {"name": "B", "teaser": "ft-b", "assistant": "Nova", "key_points": [], "score": 8},
        ]
        vec = [
            {"name": "C", "teaser": "vec-c", "assistant": "Weft", "key_points": [], "score": 0.9},
            {"name": "A", "teaser": "vec-a-longer-teaser", "assistant": "Nova", "key_points": ["k"], "score": 0.8},
        ]
        out = nm.merge_rrf([("ft", ft), ("vec", vec)], limit=3)
        self.assertEqual(out[0]["name"], "A")
        self.assertEqual(out[0]["via"], "ft+vec")
        self.assertIn("vec-a-longer-teaser", out[0]["teaser"])
        names = [h["name"] for h in out]
        self.assertEqual(set(names), {"A", "B", "C"})

    def test_empty(self):
        self.assertEqual(nm.merge_rrf([], 5), [])
        self.assertEqual(nm.merge_rrf([("ft", [])], 5), [])

    def test_limit(self):
        ft = [{"name": f"n{i}", "teaser": "", "assistant": "Grok", "key_points": [], "score": 1} for i in range(10)]
        out = nm.merge_rrf([("ft", ft)], limit=2)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["via"], "ft")

    def test_no_limit_fuses_whole_pool(self):
        ft = [{"name": f"n{i}", "teaser": "", "assistant": "Grok", "key_points": [], "score": 1} for i in range(10)]
        out = nm.merge_rrf([("ft", ft)])
        self.assertEqual(len(out), 10)

    def test_short_teaser_keeps_key_points(self):
        ft = [{
            "name": "A", "teaser": "long teaser from fulltext search hit",
            "assistant": "Nova", "key_points": [], "score": 9,
        }]
        vec = [{
            "name": "A", "teaser": "short",
            "assistant": "Nova", "key_points": ["useful point"], "score": 0.8,
        }]
        out = nm.merge_rrf([("ft", ft), ("vec", vec)], limit=1)
        self.assertEqual(out[0]["teaser"], "long teaser from fulltext search hit")
        self.assertEqual(out[0]["key_points"], ["useful point"])
        self.assertEqual(out[0]["via"], "ft+vec")


class Parser(unittest.TestCase):
    def test_search_modes(self):
        p = nm.build_parser()
        args = p.parse_args(["search", "foo", "--mode", "vector", "--max", "8"])
        self.assertEqual(args.mode, "vector")
        self.assertEqual(args.max, 8)
        args = p.parse_args(["search", "foo"])
        self.assertEqual(args.mode, "hybrid")

    def test_search_set_filters(self):
        p = nm.build_parser()
        args = p.parse_args(["search", "q", "--assistant", "Weft", "--space", "shared"])
        self.assertEqual(args.assistant, "Weft")
        self.assertEqual(args.space, "shared")
        self.assertIsNone(args.trust)
        args = p.parse_args(["search", "q"])
        self.assertIsNone(args.assistant)
        self.assertIsNone(args.space)
        self.assertIsNone(args.trust)

    def test_embed_flags(self):
        p = nm.build_parser()
        args = p.parse_args(["embed", "--dry-run", "--limit", "4"])
        self.assertTrue(args.dry_run)
        self.assertEqual(args.limit, 4)

    def test_write_no_embed(self):
        p = nm.build_parser()
        args = p.parse_args(["write", "--name", "n", "--summary", "s", "--no-embed"])
        self.assertTrue(args.no_embed)

    def test_shared_write_flags(self):
        p = nm.build_parser()
        args = p.parse_args([
            "write", "--space", "shared", "--topic", "foo",
            "--name", "Foo", "--summary", "s", "--supersede",
        ])
        self.assertEqual(args.space, "shared")
        self.assertTrue(args.supersede)
        args = p.parse_args([
            "write", "--space", "shared", "--topic", "foo",
            "--append", "--point", "p",
        ])
        self.assertTrue(args.append)
        args = p.parse_args([
            "remove", "--name", "Shared — Foo — 2026-08-30",
            "--reason", "stale",
        ])
        self.assertEqual(args.reason, "stale")
        args = p.parse_args(["history", "--topic", "foo"])
        self.assertEqual(args.topic, "foo")

    def test_force_assistant_flag(self):
        p = nm.build_parser()
        args = p.parse_args(["write", "--name", "n", "--summary", "s", "--assistant", "Nova"])
        self.assertEqual(nm._require_grok(args), 3)
        args = p.parse_args([
            "write", "--name", "n", "--summary", "s",
            "--assistant", "Nova", "--force-assistant",
        ])
        self.assertIsNone(nm._require_grok(args))
        args = p.parse_args(["organize", "--assistant", "Weft"])
        self.assertEqual(nm._require_grok(args), 3)

    def test_untagged_owner_blocks_without_force(self):
        self.assertEqual(
            nm._owner_blocks_write(None, "Grok", False),
            "untagged (pass --force-assistant)",
        )
        self.assertEqual(
            nm._owner_blocks_write("", "Grok", False),
            "untagged (pass --force-assistant)",
        )
        self.assertIsNone(nm._owner_blocks_write(None, "Grok", True))
        self.assertEqual(
            nm._owner_blocks_write("Nova", "Grok", False),
            "owned by 'Nova'",
        )
        self.assertEqual(
            nm._owner_blocks_write("Nova", "Grok", True),
            "owned by 'Nova'",
        )
        self.assertIsNone(nm._owner_blocks_write("Grok", "Grok", False))
        self.assertIsNone(nm._owner_blocks_write("Nova", "Nova", True))
        self.assertEqual(
            nm._owner_blocks_write("  ", "Grok", False),
            "untagged (pass --force-assistant)",
        )


class FinishHybridFloor(unittest.TestCase):
    def _hit(self, name: str, score: float, via: str = "vec") -> dict:
        return {
            "name": name, "teaser": name, "assistant": "Nova",
            "key_points": [], "score": score, "via": via, "status": None,
            "topic": None, "vec_score": score if via == "vec" else None,
        }

    def test_vector_only_drops_below_floor(self):
        vec = [
            self._hit("keep", 0.88),
            self._hit("noise", 0.75),
        ]
        hits, backend = nm.rank_pipeline([], vec, True, "", {}, 5)
        self.assertEqual(backend, "hybrid")
        self.assertEqual([h["name"] for h in hits], ["keep"])

    def test_vector_only_all_noise_is_empty(self):
        vec = [self._hit("a", 0.76), self._hit("b", 0.74)]
        hits, backend = nm.rank_pipeline([], vec, True, "", {}, 5)
        self.assertEqual(backend, "hybrid")
        self.assertEqual(hits, [])

    def test_both_legs_still_rrf_unfiltered(self):
        ft = [self._hit("A", 9.0, "ft"), self._hit("B", 8.0, "ft")]
        vec = [self._hit("noise", 0.75), self._hit("A", 0.88)]
        hits, backend = nm.rank_pipeline(ft, vec, True, "", {}, 3)
        self.assertEqual(backend, "hybrid")
        names = [h["name"] for h in hits]
        self.assertIn("A", names)
        self.assertIn("noise", names)


class Lucene(unittest.TestCase):
    def test_escapes_specials(self):
        self.assertIn("\\+", nm._escape_lucene("a+b"))

    def test_lowercases_boolean_operators(self):
        """Bare AND/OR/NOT must not parse as Lucene operators (spec §3); a word
        that merely contains them ("andy") is untouched."""
        self.assertEqual(nm._escape_lucene("x AND y"), "x and y")
        self.assertEqual(nm._escape_lucene("NOT this"), "not this")
        self.assertEqual(nm._escape_lucene("a OR b OR c"), "a or b or c")
        self.assertEqual(nm._escape_lucene("andy"), "andy")


class VectorSearchCypher(unittest.TestCase):
    def test_shape_and_filters(self):
        where, params = nm.build_filters("Weft", None, None)
        self.assertEqual((where, params), ("f.assistant = $assistant", {"assistant": "Weft"}))
        c = nm.build_vector_search_cypher("factEmbeddingIndex", where)
        self.assertTrue(c.startswith("CYPHER 25\nMATCH (f:Fact)\n"))
        self.assertIn("SEARCH f IN (VECTOR INDEX `factEmbeddingIndex` FOR $embedding WHERE f.assistant = $assistant LIMIT $k) SCORE AS s", c)
        self.assertIn("f.space AS space", c)
        self.assertNotIn("$index", c)
        c0 = nm.build_vector_search_cypher("idx", "")
        self.assertIn("FOR $embedding LIMIT $k) SCORE AS s", c0)
        with self.assertRaises(ValueError):
            nm.build_vector_search_cypher("bad name", "")

    def test_filters_never_status_and_and_joined(self):
        where, params = nm.build_filters("Grok", "shared", "trusted")
        self.assertEqual(where, "f.assistant = $assistant AND f.space = $space AND f.provenance_trust = $trust")
        self.assertEqual(params, {"assistant": "Grok", "space": "shared", "trust": "trusted"})
        self.assertNotIn("status", where)


class SearchVectorRunsSearchStatement(unittest.TestCase):
    def test_statement_and_params(self):
        seen = {}
        class S:
            def run(self, q, **kw):
                seen["q"], seen["kw"] = (q.text if hasattr(q, "text") else q), kw
                return []
        hits = nm.search_vector(S(), [0.1] * 3, "idx", 7, assistant="Weft")
        self.assertEqual(hits, [])
        self.assertIn("SEARCH f IN (VECTOR INDEX `idx` FOR $embedding WHERE f.assistant = $assistant LIMIT $k)", seen["q"])
        self.assertEqual(seen["kw"]["k"], 7)
        self.assertEqual(seen["kw"]["assistant"], "Weft")
        self.assertNotIn("index", seen["kw"])


class _FtRec(dict):
    def keys(self):
        return dict.keys(self)


class _FtSess:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, *args, **kw):
        idx = kw.get("index")
        if idx == "fact_content":
            return [_FtRec(
                name="SummaryHit", text="in summary", assistant="Grok",
                key_points=[], status=None, space=None, score=3.0,
            )]
        if idx == "fact_key_points":
            return [_FtRec(
                name="PointsHit", text="short", assistant="Grok",
                key_points=["file=/tmp/report.md"], status="active", space=None, score=9.0,
            )]
        raise AssertionError(f"unexpected index {idx}")


class _FtDrv:
    def session(self):
        return _FtSess()


class FulltextKpFuse(unittest.TestCase):
    def test_fuses_key_points_index(self):
        hits = nm._fulltext_leg(
            _FtDrv(), "report.md", "fact_content", 5,
            extra_indexes=["fact_key_points"],
        )
        names = {h["name"] for h in hits}
        self.assertEqual(names, {"SummaryHit", "PointsHit"})

    def test_missing_extra_index_keeps_content_hits(self):
        class Boom(_FtSess):
            def run(self, q, **kw):
                if kw.get("index") == "fact_key_points":
                    raise nm._BOLT_FAIL[0]("no such index")
                return super().run(q, **kw)

        class Drv:
            def session(self):
                return Boom()

        # ProcedureCallFailed is Neo4jError; use DriverError from tests
        class Boom2(_FtSess):
            def run(self, *args, **kw):
                if kw.get("index") == "fact_key_points":
                    raise DriverError("index missing")
                return super().run(*args, **kw)

        class Drv2:
            def session(self):
                return Boom2()

        hits = nm._fulltext_leg(
            Drv2(), "q", "fact_content", 5,
            extra_indexes=["fact_key_points"],
        )
        self.assertEqual([h["name"] for h in hits], ["SummaryHit"])

    def test_key_points_only_hit_is_tagged_kp_not_ft(self):
        """A hit found only in the key-points index must not claim it came from
        the content index — including on the single-list short-circuit."""
        class KpOnlySess(_FtSess):
            def run(self, *args, **kw):
                if kw.get("index") == "fact_key_points":
                    return super().run(*args, **kw)
                return []

        class Drv:
            def session(self):
                return KpOnlySess()

        hits = nm._fulltext_leg(
            Drv(), "report.md", "fact_content", 5,
            extra_indexes=["fact_key_points"],
        )
        self.assertEqual([h["name"] for h in hits], ["PointsHit"])
        self.assertEqual(hits[0]["via"], "kp")

    def test_both_indexes_keep_their_own_via(self):
        hits = nm._fulltext_leg(
            _FtDrv(), "report.md", "fact_content", 5,
            extra_indexes=["fact_key_points"],
        )
        by_name = {h["name"]: h["via"] for h in hits}
        self.assertEqual(by_name, {"SummaryHit": "ft", "PointsHit": "kp"})

    def test_demote_superseded(self):
        """rank_adjust (successor of _demote_inactive) keeps active hits first, order preserved."""
        hits = [
            {"name": "old", "status": "superseded", "score": 9},
            {"name": "new", "status": "active", "score": 8},
            {"name": "nova", "status": None, "score": 7},
        ]
        out = nm.rank_adjust(hits, "", {})
        self.assertEqual([h["name"] for h in out], ["new", "nova", "old"])

    def test_collapse_superseded_sibling(self):
        """rank_adjust (successor of _collapse_superseded_siblings) drops a superseded hit
        once an active same-topic sibling (per same_topic's name-based rule) is present."""
        hits = [
            {"name": "Shared — ntr-kalman — 2026-08-30 #2", "status": "active", "score": 8},
            {"name": "nova", "status": None, "score": 7},
            {"name": "Shared — ntr-kalman — 2026-08-30", "status": "superseded", "score": 9},
        ]
        out = nm.rank_adjust(hits, "", {})
        self.assertEqual(
            [h["name"] for h in out],
            ["Shared — ntr-kalman — 2026-08-30 #2", "nova"],
        )

    def test_keep_superseded_without_active_sibling(self):
        """No active sibling to collapse against, but active-before-inactive still sorts it last."""
        hits = [
            {"name": "Shared — ntr-kalman — 2026-08-30", "status": "superseded", "score": 9},
            {"name": "nova", "status": None, "score": 7},
        ]
        out = nm.rank_adjust(hits, "", {})
        self.assertEqual(
            [h["name"] for h in out],
            ["nova", "Shared — ntr-kalman — 2026-08-30"],
        )


class HybridParallel(unittest.TestCase):
    def test_fulltext_and_vector_overlap(self):
        """Both legs must enter before either finishes, or Barrier times out."""
        barrier = threading.Barrier(2, timeout=1)

        def fake_ft(session, q, index, limit, bolt_timeout=None):
            barrier.wait()
            return [{
                "name": "A", "assistant": "Grok", "score": 2.0,
                "teaser": "a", "key_points": [], "via": "ft",
            }]

        def fake_embed(text, cfg=None, timeout=None):
            barrier.wait()
            return [0.0] * 768

        def fake_vec(session, embedding, index, limit, bolt_timeout=None):
            return [{
                "name": "B", "assistant": "Nova", "score": 0.9,
                "teaser": "b", "key_points": [], "via": "vec",
            }]

        cfg = {"fulltext": "fact_content", "vector": "factEmbeddingIndex"}
        with mock.patch.object(nm, "search_fulltext", fake_ft), \
             mock.patch.object(nm, "ollama_embed", fake_embed), \
             mock.patch.object(nm, "search_vector", fake_vec):
            hits, backend = nm.search_memories(_Drv(), cfg, "query", 5)
        self.assertEqual(backend, "hybrid")
        self.assertEqual({h["name"] for h in hits}, {"A", "B"})
        # "lex" is the neutral outer label for the whole lexical pool (matching
        # ai_memory/search.py::search_hybrid); RRF: equal rank-0, name A < B.
        self.assertEqual(hits[0]["via"], "lex")

    def test_vector_leg_down_still_returns_fulltext(self):
        def fake_ft(session, q, index, limit, bolt_timeout=None):
            return [{
                "name": "A", "assistant": "Grok", "score": 4.2,
                "teaser": "a", "key_points": [], "via": "ft",
            }]

        cfg = {"fulltext": "fact_content", "vector": "factEmbeddingIndex"}
        with mock.patch.object(nm, "search_fulltext", fake_ft), \
             mock.patch.object(nm, "ollama_embed", lambda *a, **k: None):
            hits, backend = nm.search_memories(_Drv(), cfg, "query", 5)
        self.assertEqual(backend, "fulltext")
        self.assertEqual(hits[0]["name"], "A")

    def test_vector_driver_error_keeps_fulltext(self):
        def fake_ft(session, q, index, limit, bolt_timeout=None):
            return [{
                "name": "A", "assistant": "Grok", "score": 4.2,
                "teaser": "a", "key_points": [], "via": "ft",
            }]

        def fake_vec(session, embedding, index, limit, bolt_timeout=None):
            raise DriverError("session expired")

        cfg = {"fulltext": "fact_content", "vector": "factEmbeddingIndex"}
        with mock.patch.object(nm, "search_fulltext", fake_ft), \
             mock.patch.object(nm, "ollama_embed", lambda *a, **k: [0.0] * 768), \
             mock.patch.object(nm, "search_vector", fake_vec):
            hits, backend = nm.search_memories(_Drv(), cfg, "query", 5)
        self.assertEqual(backend, "fulltext")
        self.assertEqual(hits[0]["name"], "A")

    def test_vector_mode_down_is_distinct_from_empty(self):
        cfg = {"fulltext": "fact_content", "vector": "factEmbeddingIndex"}
        with mock.patch.object(nm, "ollama_embed", lambda *a, **k: None):
            hits, backend = nm.search_memories(
                _Drv(), cfg, "query", 5, mode="vector",
            )
        self.assertEqual(hits, [])
        self.assertEqual(backend, "vector-down")
        notices = nm._search_stderr("vector", "vector-down", [])
        self.assertTrue(any("needs Ollama" in n for n in notices))
        empty_ok = nm._search_stderr("vector", "vector", [])
        self.assertEqual(empty_ok, ["No hits."])
        hybrid_empty = nm._search_stderr("hybrid", "fulltext", [])
        self.assertTrue(any("vector skipped" in n for n in hybrid_empty))
        self.assertTrue(any(n == "No hits." for n in hybrid_empty))

    def test_hung_vector_returns_fulltext_within_deadline(self):
        def fake_ft(session, q, index, limit, bolt_timeout=None):
            return [{
                "name": "A", "assistant": "Grok", "score": 4.2,
                "teaser": "a", "key_points": [], "via": "ft",
            }]

        def fake_embed(text, cfg=None, timeout=None):
            time.sleep(8)
            return [0.0] * 768

        cfg = {"fulltext": "fact_content", "vector": "factEmbeddingIndex"}
        t0 = time.perf_counter()
        with mock.patch.object(nm, "search_fulltext", fake_ft), \
             mock.patch.object(nm, "ollama_embed", fake_embed):
            hits, backend = nm.search_memories(
                _Drv(), cfg, "query", 5,
                deadline=0.25, embed_timeout=30, bolt_timeout=30,
            )
        self.assertLess(time.perf_counter() - t0, 1.5)
        self.assertEqual(backend, "fulltext")
        self.assertEqual(hits[0]["name"], "A")

    def test_hook_writes_hits_before_search_failure(self):
        hits_path = Path("/tmp/grok-lost/test-hook-hits.md")
        hits_path.parent.mkdir(parents=True, exist_ok=True)
        if hits_path.exists():
            hits_path.unlink()
        stdin = io.StringIO(json.dumps({
            "prompt": "how do we recall similar ideas without exact keywords",
        }))

        def boom(*a, **k):
            raise RuntimeError("search exploded")

        with mock.patch.object(sys, "stdin", stdin), \
             mock.patch.object(nm, "_driver", lambda **k: (_Drv(), {"vector": "v", "embed_model": "m"})), \
             mock.patch.object(nm, "search_memories", boom), \
             mock.patch.object(nm, "HITS_FILE", hits_path):
            rc = nm.cmd_hook_prompt(None)
        self.assertEqual(rc, 0)
        text = hits_path.read_text(encoding="utf-8")
        self.assertIn("backend: fulltext", text)
        self.assertIn("(none)", text)


class _SupersedesSlowSess:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, q, **kw):
        text = q.text if hasattr(q, "text") else q
        if "SUPERSEDES" in text:
            time.sleep(1.0)
            return []
        if "queryNodes" in text:
            return [_FtRec(
                name="A", text="a", assistant="Grok", key_points=[],
                status=None, space=None, score=2.0,
            )]
        if "VECTOR INDEX" in text:
            return [_FtRec(
                name="B", text="b", assistant="Grok", key_points=[],
                status=None, space=None, score=0.9,
            )]
        return []


class _SupersedesSlowDrv:
    def session(self):
        return _SupersedesSlowSess()


class SupersedesDeadline(unittest.TestCase):
    """Important #1: the SUPERSEDES lookup must live inside the deadline, dispatched
    as a third daemon future alongside fulltext/vector rather than an un-timed
    blocking round-trip before them."""

    def test_slow_supersedes_does_not_block_deadline(self):
        cfg = {"fulltext": "fact_content", "vector": "factEmbeddingIndex"}
        t0 = time.monotonic()
        with mock.patch.object(nm, "ollama_embed", lambda *a, **k: [0.0] * 768):
            hits, backend = nm.search_memories(
                _SupersedesSlowDrv(), cfg, "query", 5, deadline=0.2,
            )
        elapsed = time.monotonic() - t0
        self.assertLess(elapsed, 0.6)
        self.assertTrue(hits)
        self.assertEqual(backend, "hybrid")

    def test_supersedes_statement_wrapped_with_timeout(self):
        captured = {}

        class S:
            def run(self, q, **kw):
                captured["q"] = q
                return []

        nm._load_supersedes(S(), bolt_timeout=2.5)
        q = captured["q"]
        self.assertTrue(hasattr(q, "text"))
        self.assertEqual(q.text, "MATCH (n:Fact)-[:SUPERSEDES]->(o:Fact) RETURN n.name AS n, o.name AS o")

    def test_supersedes_non_bolt_exception_yields_empty(self):
        class S:
            def run(self, q, **kw):
                raise RuntimeError("weird")

        self.assertEqual(nm._load_supersedes(S(), bolt_timeout=1.0), {})


class SupersedesMultimap(unittest.TestCase):
    """One keeper superseding several olds keeps every edge; a {new: old} dict
    kept only the last row (ai_memory/search.py::load_supersedes parity)."""

    def test_load_supersedes_keeps_every_edge(self):
        class S:
            def run(self, q, **kw):
                return [{"n": "K", "o": "A"}, {"n": "K", "o": "B"}]

        self.assertEqual(nm._load_supersedes(S()), {"K": {"A", "B"}})

    def test_chain_and_is_duplicate_see_every_twin(self):
        sup = {"K": {"A", "B"}}
        self.assertTrue(nm.same_topic({"name": "K"}, {"name": "A"}, sup))
        self.assertTrue(nm.same_topic({"name": "K"}, {"name": "B"}, sup))
        self.assertTrue(nm.is_duplicate("K", "A", 0.0, sup))
        self.assertTrue(nm.is_duplicate("K", "B", 0.0, sup))
        self.assertFalse(nm.is_duplicate("K", "C", 0.0, sup))


class SameTopicCase(unittest.TestCase):
    def test_stripped_names_compare_case_insensitively(self):
        self.assertTrue(nm.same_topic({"name": "Kelly Criterion"},
                                      {"name": "kelly criterion (15:30 EDT)"}))
        self.assertFalse(nm.same_topic({"name": "Kelly Criterion"},
                                       {"name": "kelly criterion II"}))


class SingleLegPoolWidth(unittest.TestCase):
    """--mode fulltext/vector must retrieve at pool width and fuse/rank like
    hybrid; ai_memory/search.py::search_hybrid passes `pool` for every mode, and
    `limit` only slices at the end."""

    _CFG = {"fulltext": "fact_content", "vector": "factEmbeddingIndex"}

    def test_fulltext_mode_retrieves_pool_not_limit(self):
        seen = {}

        def fake_ft(driver, query, index, limit, *a, **kw):
            seen["limit"] = limit
            return []

        with mock.patch.object(nm, "_fulltext_leg", fake_ft), \
             mock.patch.object(nm, "_supersedes_leg", lambda *a, **k: {}):
            nm.search_memories(_Drv(), self._CFG, "q", 5, mode="fulltext")
        self.assertEqual(seen["limit"], nm._pool_size(5))
        self.assertNotEqual(seen["limit"], 5)

    def test_vector_mode_retrieves_pool_not_limit(self):
        seen = {}

        def fake_vec(driver, query, cfg, limit, *a, **kw):
            seen["limit"] = limit
            return [], True

        with mock.patch.object(nm, "_vector_leg", fake_vec), \
             mock.patch.object(nm, "_supersedes_leg", lambda *a, **k: {}):
            nm.search_memories(_Drv(), self._CFG, "q", 5, mode="vector")
        self.assertEqual(seen["limit"], nm._pool_size(5))
        self.assertNotEqual(seen["limit"], 5)


class VectorLegBadIndexName(unittest.TestCase):
    """M1: a ValueError from validate_index_name (bad --mode vector index name) must
    not traceback; _vector_leg swallows it like a Bolt failure."""

    def test_bad_index_name_does_not_traceback(self):
        cfg = {"fulltext": "fact_content", "vector": "bad name"}
        with mock.patch.object(nm, "ollama_embed", lambda *a, **k: [0.0] * 768):
            hits, backend = nm.search_memories(_Drv(), cfg, "q", 5, mode="vector")
        self.assertEqual(hits, [])
        self.assertEqual(backend, "vector-down")


class VectorScoreParity(unittest.TestCase):
    """M9: a vector hit's vec_score is the raw cosine from the SEARCH statement,
    unaffected by later RRF rescoring."""

    def test_vec_score_matches_raw_cosine(self):
        class S:
            def run(self, q, **kw):
                return [_FtRec(
                    name="A", text="a", assistant="Grok", key_points=[],
                    status=None, space=None, score=0.88,
                )]

        hits = nm.search_vector(S(), [0.1] * 3, "idx", 5)
        self.assertEqual(hits[0]["vec_score"], 0.88)


class RankPipeline(unittest.TestCase):
    def _h(self, name, score, status=None, via="ft"):
        return {"name": name, "teaser": "", "key_points": [], "assistant": None, "status": status,
                "space": None, "score": score, "via": via, "topic": None, "vec_score": score if via == "vec" else None}

    def test_superseded_sibling_dropped_when_active_present(self):
        ft = [self._h("Shared — x — 2026-08-30", 5.0, "superseded"), self._h("Shared — x — 2026-08-30 #2", 4.0)]
        hits, backend = nm.rank_pipeline(ft, [], False, "", {}, 5)
        self.assertEqual([h["name"] for h in hits], ["Shared — x — 2026-08-30 #2"])
        self.assertEqual(backend, "fulltext")

    def test_supersedes_chain_collapses(self):
        ft = [self._h("Old name", 5.0, "superseded"), self._h("New name", 4.0)]
        hits, _ = nm.rank_pipeline(ft, [], False, "", {"New name": "Old name"}, 5)
        self.assertEqual([h["name"] for h in hits], ["New name"])

    def test_exact_name_boost_active_only(self):
        ft = [self._h("Other", 5.0), self._h("Weft — identity", 1.0)]
        hits, _ = nm.rank_pipeline(ft, [], False, "weft — identity", {}, 5)
        self.assertEqual(hits[0]["name"], "Weft — identity")
        ft = [self._h("Other", 5.0), self._h("Gone", 1.0, "removed")]
        hits, _ = nm.rank_pipeline(ft, [], False, "gone", {}, 5)
        self.assertEqual(hits[0]["name"], "Other")

    def test_vector_only_floor_then_rank(self):
        vec = [self._h("Strong", 0.91, via="vec"), self._h("Weak", 0.5, via="vec")]
        hits, backend = nm.rank_pipeline([], vec, True, "", {}, 5)
        self.assertEqual([h["name"] for h in hits], ["Strong"])
        self.assertEqual(backend, "hybrid")

    def test_pool_fused_before_slice(self):
        ft = [self._h("A", 5.0, "superseded"), self._h("B", 4.0), self._h("C", 3.0)]
        hits, _ = nm.rank_pipeline(ft, [], False, "", {}, 2)
        self.assertEqual([h["name"] for h in hits], ["B", "C"])   # inactive A sinks; slice happens after ranking


class EmbedRetry(unittest.TestCase):
    def test_timeout_does_not_hit_legacy(self):
        calls = []

        def fake_http(url, payload, timeout):
            calls.append(url)
            raise TimeoutError("timed out")

        with mock.patch.object(nm, "_http_json", fake_http):
            out = nm.ollama_embed("hello", _EMBED_CFG, timeout=3)
        self.assertIsNone(out)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].endswith("/api/embed"))

    def test_urlerror_timeout_does_not_hit_legacy(self):
        calls = []

        def fake_http(url, payload, timeout):
            calls.append(url)
            raise urllib.error.URLError(TimeoutError("timed out"))

        with mock.patch.object(nm, "_http_json", fake_http):
            out = nm.ollama_embed("hello", _EMBED_CFG, timeout=3)
        self.assertIsNone(out)
        self.assertEqual(len(calls), 1)

    def test_http_404_retries_legacy(self):
        calls = []
        vec = [0.1] * 768

        def fake_http(url, payload, timeout):
            calls.append(url)
            if url.endswith("/api/embed"):
                raise urllib.error.HTTPError(url, 404, "Not Found", hdrs=None, fp=None)
            return {"embedding": vec}

        with mock.patch.object(nm, "_http_json", fake_http):
            out = nm.ollama_embed("hello", _EMBED_CFG, timeout=3)
        self.assertEqual(out, vec)
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[1].endswith("/api/embeddings"))

    def test_http_500_does_not_retry(self):
        calls = []

        def fake_http(url, payload, timeout):
            calls.append(url)
            raise urllib.error.HTTPError(url, 500, "Error", hdrs=None, fp=None)

        with mock.patch.object(nm, "_http_json", fake_http):
            out = nm.ollama_embed("hello", _EMBED_CFG, timeout=3)
        self.assertIsNone(out)
        self.assertEqual(len(calls), 1)


class SharedName(unittest.TestCase):
    def test_dated_name(self):
        self.assertEqual(
            nm._shared_name("Nova non-trading index", "2026-08-30"),
            "Shared — Nova non-trading index — 2026-08-30",
        )

    def test_does_not_double_prefix(self):
        n = nm._shared_name("Shared — Foo — 2026-08-30", "2026-08-30")
        self.assertEqual(n, "Shared — Foo — 2026-08-30")

    def test_unique_suffix_when_taken(self):
        taken = {"Shared — Foo — 2026-08-30"}
        self.assertEqual(
            nm._unique_shared_name("Foo", "2026-08-30", taken),
            "Shared — Foo — 2026-08-30 #2",
        )

    def test_topic_slug(self):
        self.assertEqual(nm._topic_slug("Nova Non-Trading Index"), "nova-non-trading-index")
        self.assertEqual(nm._topic_slug("  Foo_Bar  "), "foo-bar")


class SharedWritePlan(unittest.TestCase):
    def test_create_when_empty(self):
        plan = nm._plan_shared_write(
            existing_by_name=None,
            active_for_topic=None,
            topic="nova-non-trading-index",
            title="Nova non-trading index",
            day="2026-08-30",
            append=False,
            supersede=False,
            writer="Grok",
        )
        self.assertEqual(plan["action"], "create")
        self.assertIsNone(plan["predecessor"])
        self.assertEqual(
            plan["name"],
            "Shared — Nova non-trading index — 2026-08-30",
        )

    def test_refuse_in_place_overwrite_of_active(self):
        plan = nm._plan_shared_write(
            existing_by_name=None,
            active_for_topic={
                "name": "Shared — Nova non-trading index — 2026-08-30",
                "assistant": "Grok",
                "status": "active",
            },
            topic="nova-non-trading-index",
            title="Nova non-trading index",
            day="2026-08-30",
            append=False,
            supersede=False,
            writer="Grok",
        )
        self.assertEqual(plan["action"], "refuse")
        self.assertIn("--supersede", plan["reason"])
        self.assertIn("--append", plan["reason"])

    def test_supersede_keeps_predecessor(self):
        plan = nm._plan_shared_write(
            existing_by_name=None,
            active_for_topic={
                "name": "Shared — Nova non-trading index — 2026-08-29",
                "assistant": "Weft",
                "status": "active",
            },
            topic="nova-non-trading-index",
            title="Nova non-trading index",
            day="2026-08-30",
            append=False,
            supersede=True,
            writer="Grok",
        )
        self.assertEqual(plan["action"], "supersede")
        self.assertEqual(
            plan["predecessor"],
            "Shared — Nova non-trading index — 2026-08-29",
        )
        self.assertEqual(
            plan["name"],
            "Shared — Nova non-trading index — 2026-08-30",
        )

    def test_append_on_active(self):
        plan = nm._plan_shared_write(
            existing_by_name=None,
            active_for_topic={
                "name": "Shared — Foo — 2026-08-30",
                "assistant": "Grok",
                "status": "active",
            },
            topic="foo",
            title="Foo",
            day="2026-08-30",
            append=True,
            supersede=False,
            writer="Weft",
        )
        self.assertEqual(plan["action"], "append")
        self.assertEqual(plan["name"], "Shared — Foo — 2026-08-30")

    def test_append_refused_without_active(self):
        plan = nm._plan_shared_write(
            existing_by_name=None,
            active_for_topic=None,
            topic="foo",
            title="Foo",
            day="2026-08-30",
            append=True,
            supersede=False,
            writer="Grok",
        )
        self.assertEqual(plan["action"], "refuse")

    def test_refuse_clobber_library_name(self):
        plan = nm._plan_shared_write(
            existing_by_name={
                "name": "Hawkes Processes Deep Dive",
                "assistant": "Nova",
                "space": None,
                "status": None,
            },
            active_for_topic=None,
            topic="hawkes",
            title="Hawkes Processes Deep Dive",
            day="2026-08-30",
            append=False,
            supersede=False,
            writer="Grok",
        )
        self.assertEqual(plan["action"], "refuse")
        self.assertIn("Nova", plan["reason"])

    def test_dated_point(self):
        self.assertEqual(
            nm._dated_point("2026-08-30", "added Hailo notes"),
            "[2026-08-30] added Hailo notes",
        )


class DriverMutesNotifications(unittest.TestCase):
    def test_driver_disables_server_notifications(self):
        seen = {}

        def fake_driver(uri, auth=None, **kw):
            seen.update(kw)
            return "drv"

        cfg = {"uri": "bolt://x:7687", "user": "u", "password": "p"}
        with mock.patch.object(nm, "_cfg", lambda: cfg), \
             mock.patch.object(nm.GraphDatabase, "driver", fake_driver):
            drv, _ = nm._driver(connect_timeout=1.5)
        self.assertEqual(drv, "drv")
        self.assertEqual(seen.get("notifications_min_severity"), "OFF")
        self.assertEqual(seen.get("connection_timeout"), 1.5)


class PlainWriteRefusesShared(unittest.TestCase):
    def test_plain_write_cannot_overwrite_shared_fact(self):
        ran = []

        class Sess(_Sess):
            def run(self, q, **kw):
                ran.append(q)
                if "RETURN f.assistant AS a" in q:
                    return mock.Mock(single=lambda: {"a": "Grok", "space": "shared"})
                raise AssertionError("write proceeded past the shared-space guard")

        class Drv:
            def session(self):
                return Sess()

            def close(self):
                pass

        args = nm.build_parser().parse_args([
            "write", "--name", "Shared — Foo — 2026-08-30",
            "--summary", "clobbered", "--no-embed",
        ])
        with mock.patch.object(nm, "_driver", lambda **k: (Drv(), {})):
            rc = nm.cmd_write(args)
        self.assertEqual(rc, 3)
        self.assertEqual(len(ran), 1)


class SharedRemovePlan(unittest.TestCase):
    def test_refuse_library(self):
        plan = nm._plan_shared_remove(
            existing={
                "name": "Weft — identity",
                "assistant": "Weft",
                "space": None,
                "status": None,
            },
            reason="cleanup",
            writer="Grok",
        )
        self.assertEqual(plan["action"], "refuse")

    def test_refuse_without_reason(self):
        plan = nm._plan_shared_remove(
            existing={
                "name": "Shared — Foo — 2026-08-30",
                "assistant": "Grok",
                "space": "shared",
                "status": "active",
            },
            reason="",
            writer="Grok",
        )
        self.assertEqual(plan["action"], "refuse")

    def test_tombstone_shared(self):
        plan = nm._plan_shared_remove(
            existing={
                "name": "Shared — Foo — 2026-08-30",
                "assistant": "Grok",
                "space": "shared",
                "status": "active",
            },
            reason="replaced by new survey",
            writer="Weft",
        )
        self.assertEqual(plan["action"], "remove")
        self.assertEqual(plan["status"], "removed")


class WriteTokensStatementShape(unittest.TestCase):
    def test_statement_shape_and_norm_param(self):
        calls = []

        class S:
            def run(self, q, **kw):
                calls.append((q, kw))
                if "RETURN c.rule_version AS rule_version" in q:
                    return mock.Mock(single=lambda: None)
                if "RETURN count(f) AS n" in q:
                    return mock.Mock(single=lambda: {"n": 4})
                if "MATCH (w:Word) WHERE w.text IN $toks" in q:
                    return [{"text": "foo", "idf": 2.0}]
                return mock.Mock(single=lambda: None)

        nm._write_tokens(S(), "N", "foo the and for a to")
        write_q, write_kw = calls[-1]
        self.assertIn("OPTIONAL MATCH (f)-[old:HAS_WORD]->()", write_q)
        self.assertIn("DELETE old", write_q)
        self.assertIn("SET f.tfidf_norm = $norm", write_q)
        self.assertIn("UNWIND $tokens AS t", write_q)
        self.assertIn("MERGE (w:Word {text: t})", write_q)
        self.assertIn("MERGE (f)-[:HAS_WORD]->(w)", write_q)
        self.assertEqual(write_kw["name"], "N")
        self.assertEqual(write_kw["tokens"], ["foo"])
        self.assertAlmostEqual(write_kw["norm"], 2.0)

    def test_uses_edge_config_n_facts_when_published_and_skips_count_query(self):
        calls = []
        edge_cfg = {"rule_version": 1, "edge_floor": 0.0, "t_mean": 0.0, "t_std": 1.0,
                    "c_mean": 0.0, "c_std": 1.0, "n_facts": 4}

        class S:
            def run(self, q, **kw):
                calls.append((q, kw))
                if "MATCH (w:Word) WHERE w.text IN $toks" in q:
                    return []
                return mock.Mock(single=lambda: None)

        with mock.patch.object(nm, "_load_edge_config", lambda s: edge_cfg):
            nm._write_tokens(S(), "N", "foo the and for a to")
        _write_q, write_kw = calls[-1]
        self.assertAlmostEqual(write_kw["norm"], math.log(4))  # default_idf, no idf hit
        self.assertFalse(any("RETURN count(f) AS n" in q for q, _ in calls))


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
        self.calls.append((cypher, kw))
        return _MECResult(self.rows_by_call.pop(0) if self.rows_by_call else [])


_MEC_CFG = {"rule_version": 7, "edge_floor": 0.2, "t_mean": 0.0, "t_std": 1.0,
            "c_mean": 0.5, "c_std": 0.1, "n_facts": 50}


class MaintainEdgesForScripted(unittest.TestCase):
    """Mirrors ai_memory/tests/test_wordindex.py's Task 3 comprehensive scenario: X
    (Target) has 4 neighbours above the floor. A already has k=5 picks whose weakest is
    beaten -> A re-picks X and evicts its old weakest ('old'), which is deleted since
    nobody else had picked it. B has only 2 picks -> re-picks X, nothing evicted. C has
    0 picks -> re-picks X. G already has X among its own picks -> weight-only update,
    no eviction, not counted as a re-pick (F2's fix)."""

    def test_picks_repicks_evicts_and_skips_eviction_for_existing_picker(self):
        s = _MECSession([
            [{"has_emb": True, "toks": [], "norm": None}],             # 1 read X
            [],                                                         # 2 idf fetch #1
            [],                                                         # 3 write_fact_tokens
            [                                                           # 4 row query
                {"name": "A", "cos": 0.78, "toks": [], "norm": None},     # blend = 1.4
                {"name": "B", "cos": 0.60, "toks": [], "norm": None},     # blend = 0.5
                {"name": "C", "cos": 0.56, "toks": [], "norm": None},     # blend = 0.3
                {"name": "G", "cos": 0.56, "toks": [], "norm": None},     # blend = 0.3, already picks X
                {"name": "D", "cos": 0.51, "toks": [], "norm": None},     # blend = 0.05, below floor
            ],
            [],                                                         # 5 idf fetch #2
            [],                                                         # 6 _load_supersedes
            [{"g": "G"}],                                               # 7 already_picks_x precheck
            [                                                           # 8 current picks for A (worst=old/0.2)
                {"other": "old", "weight": 0.2}, {"other": "p2", "weight": 0.3},
                {"other": "p3", "weight": 0.5}, {"other": "p4", "weight": 0.7},
                {"other": "p5", "weight": 0.9},
            ],
            [{"other": "q1", "weight": 0.1}, {"other": "q2", "weight": 0.4}],   # 9 current picks B (2<5)
            [],                                                         # 10 current picks C (0<5)
            [{"a": "G", "b": "Target", "picked_by": ["G"]}],            # 11 existing picked_by
            [{"n": 4}],                                                 # 12 write_edges
            [{"remaining": 0}],                                         # 13 unpick A-old
            [{"revoked": 0, "deleted": 0}],                             # 14 revocation
        ])
        result = nm._maintain_edges_for(s, "Target", _MEC_CFG)
        self.assertEqual(result, {"picked": 4, "repicked": 3, "deleted": 1, "revoked": 0, "skipped": None})
        self.assertEqual(len(s.calls), 14)

        read_x_q, read_x_p = s.calls[0]
        self.assertIn("f.embedding IS NOT NULL AS has_emb", read_x_q)
        self.assertEqual(read_x_p, {"n": "Target"})

        row_q, row_p = s.calls[3]
        self.assertIn("2 * vector.similarity.cosine(f.embedding, g.embedding) - 1 AS cos", row_q)
        self.assertEqual(row_p, {"n": "Target"})

        precheck_q, precheck_p = s.calls[6]
        self.assertIn("UNWIND $names AS g", precheck_q)
        self.assertIn("coalesce(e.picked_by, [])", precheck_q)
        self.assertEqual(precheck_p, {"n": "Target", "names": ["A", "B", "C", "G"]})

        picks_a_q, picks_a_p = s.calls[7]
        self.assertIn("$g IN coalesce(e.picked_by, [])", picks_a_q)
        self.assertIn("o.name <> $n", picks_a_q)
        self.assertIn("ORDER BY e.weight ASC", picks_a_q)
        self.assertEqual(picks_a_p, {"g": "A", "n": "Target"})

        # G (already picking X) never goes through the current-picks/eviction path.
        eviction_calls_text = " ".join(str(c[0]) for c in s.calls[7:10])
        self.assertEqual(eviction_calls_text.count("ORDER BY e.weight ASC"), 3)

        write_edges_q, write_edges_p = s.calls[11]
        self.assertIn("MERGE (a)-[e:RELATED_TO]->(b)", write_edges_q)
        rows_by_pair = {(r["a"], r["b"]): r for r in write_edges_p["rows"]}
        self.assertAlmostEqual(rows_by_pair[("A", "Target")]["weight"], 1.4)
        self.assertEqual(sorted(rows_by_pair[("A", "Target")]["picked_by"]), ["A", "Target"])
        self.assertEqual(rows_by_pair[("A", "Target")]["via"], "both")
        self.assertAlmostEqual(rows_by_pair[("G", "Target")]["weight"], 0.3)
        self.assertEqual(sorted(rows_by_pair[("G", "Target")]["picked_by"]), ["G", "Target"])
        self.assertEqual(write_edges_p["rv"], 7)

        unpick_q, unpick_p = s.calls[12]
        self.assertIn("SET e.picked_by = [p IN coalesce(e.picked_by, []) WHERE p <> $g]", unpick_q)
        self.assertIn("DELETE e", unpick_q)
        self.assertEqual(unpick_p, {"g": "A", "weakest": "old"})

        revoke_q, revoke_p = s.calls[13]
        self.assertIn("NOT o.name IN $keep", revoke_q)
        self.assertIn("coalesce(e.picked_by, [])", revoke_q)
        self.assertIn("FOREACH", revoke_q)
        self.assertEqual(revoke_p["n"], "Target")
        self.assertEqual(sorted(revoke_p["keep"]), ["A", "B", "C", "G"])

    def test_no_config_skips_with_no_queries(self):
        s = _MECSession([])
        result = nm._maintain_edges_for(s, "Target", None)
        self.assertEqual(result, {"picked": 0, "repicked": 0, "deleted": 0, "revoked": 0, "skipped": "no_config"})
        self.assertEqual(s.calls, [])


class CmdOrganize(unittest.TestCase):
    def test_exits_1_without_edge_config(self):
        class Sess(_Sess):
            pass

        class Drv:
            def session(self):
                return Sess()

            def close(self):
                pass

        args = nm.build_parser().parse_args(["organize"])
        buf = io.StringIO()
        with mock.patch.object(nm, "_driver", lambda **k: (Drv(), {})), \
             mock.patch.object(nm, "_load_edge_config", lambda s: None), \
             mock.patch("sys.stderr", buf):
            rc = nm.cmd_organize(args)
        self.assertEqual(rc, 1)
        self.assertIn("organize needs a published edge rule", buf.getvalue())

    def test_iterates_and_reports(self):
        class Sess(_Sess):
            def run(self, q, **kw):
                return [{"name": "F1"}, {"name": "F2"}]

        class Drv:
            def session(self):
                return Sess()

            def close(self):
                pass

        edge_cfg = {"rule_version": 1, "edge_floor": 0.0, "t_mean": 0.0, "t_std": 1.0,
                    "c_mean": 0.0, "c_std": 1.0, "n_facts": 2}
        seen = []

        def fake_maintain(session, name, cfg, **kw):
            seen.append(name)
            return {"picked": 1, "repicked": 0, "deleted": 0, "revoked": 1, "skipped": None}

        args = nm.build_parser().parse_args(["organize"])
        buf = io.StringIO()
        with mock.patch.object(nm, "_driver", lambda **k: (Drv(), {})), \
             mock.patch.object(nm, "_load_edge_config", lambda s: edge_cfg), \
             mock.patch.object(nm, "_maintain_edges_for", fake_maintain), \
             mock.patch("sys.stdout", buf):
            rc = nm.cmd_organize(args)
        self.assertEqual(rc, 0)
        self.assertEqual(seen, ["F1", "F2"])
        out = buf.getvalue()
        self.assertIn("maintained 2 facts", out)
        self.assertIn("picked 2", out)
        self.assertIn("repicked 0", out)
        self.assertIn("revoked 2", out)
        self.assertIn("deleted 0", out)


class WriteSurvivesMaintenanceFailure(unittest.TestCase):
    def test_write_succeeds_when_maintenance_raises(self):
        class Sess(_Sess):
            def run(self, q, **kw):
                if "RETURN f.assistant AS a, f.space AS space" in q:
                    return mock.Mock(single=lambda: None)
                if "RETURN c.version AS version" in q:
                    return mock.Mock(single=lambda: None)
                if "MATCH (w:Word) WHERE w.text IN $toks" in q:
                    return []
                if "RETURN count(f) AS n" in q:
                    return mock.Mock(single=lambda: {"n": 1})
                return mock.Mock(single=lambda: None)

        class Drv:
            def session(self):
                return Sess()

            def close(self):
                pass

        edge_cfg = {"rule_version": 1, "edge_floor": 0.0, "t_mean": 0.0, "t_std": 1.0,
                    "c_mean": 0.0, "c_std": 1.0, "n_facts": 1}

        def boom(*a, **k):
            raise RuntimeError("boom")

        args = nm.build_parser().parse_args([
            "write", "--name", "N", "--summary", "S", "--no-embed",
        ])
        with mock.patch.object(nm, "_driver", lambda **k: (Drv(), {})), \
             mock.patch.object(nm, "_load_edge_config", lambda s: edge_cfg), \
             mock.patch.object(nm, "_maintain_edges_for", boom):
            rc = nm.cmd_write(args)
        self.assertEqual(rc, 0)


class WriteTokensIncludeContent(unittest.TestCase):
    """I-1: cmd_write / cmd_write_shared must tokenize the same canonical text the
    embedding uses — including the Fact's `content` — not a content-free stand-in."""

    _CONTENT = "Some longer content body distinguishingwordxyz."

    def _find_tokens_call(self, calls):
        return next(kw for q, kw in calls if "UNWIND $tokens AS t" in q)

    def test_cmd_write_includes_content_in_tokens(self):
        calls = []
        content = self._CONTENT

        class Sess(_Sess):
            def run(self, q, **kw):
                calls.append((q, kw))
                if "RETURN f.assistant AS a, f.space AS space" in q:
                    return mock.Mock(single=lambda: None)
                if "RETURN c.version AS version" in q:
                    return mock.Mock(single=lambda: {"version": 1, "boilerplate": []})
                if "f.key_points AS key_points, f.content AS content" in q:
                    return mock.Mock(single=lambda: {
                        "name": "N", "summary": "S", "key_points": ["p1"], "content": content,
                    })
                if "RETURN c.rule_version AS rule_version" in q:
                    return mock.Mock(single=lambda: None)
                if "RETURN count(f) AS n" in q:
                    return mock.Mock(single=lambda: {"n": 3})
                if "MATCH (w:Word) WHERE w.text IN $toks" in q:
                    return []
                return mock.Mock(single=lambda: None)

        class Drv:
            def session(self):
                return Sess()

            def close(self):
                pass

        args = nm.build_parser().parse_args([
            "write", "--name", "N", "--summary", "S", "--point", "p1", "--no-embed",
        ])
        with mock.patch.object(nm, "_driver", lambda **k: (Drv(), {})):
            rc = nm.cmd_write(args)
        self.assertEqual(rc, 0)

        write_kw = self._find_tokens_call(calls)
        expected_text = nm.fact_embed_text("N", "S", ["p1"], self._CONTENT, frozenset())
        expected_tokens = nm.tokenize(expected_text, "N")
        # Sanity: content must actually change the token set, or this test cannot fail
        # the way I-1 needs it to.
        self.assertNotEqual(
            expected_tokens, nm.tokenize(nm.fact_embed_text("N", "S", ["p1"], None, frozenset()), "N"),
        )
        self.assertEqual(write_kw["tokens"], expected_tokens)

    def test_cmd_write_shared_includes_content_in_tokens(self):
        calls = []
        content = self._CONTENT

        class Sess(_Sess):
            def run(self, q, **kw):
                calls.append((q, kw))
                if "RETURN f.name AS name, f.assistant AS assistant" in q:
                    return mock.Mock(single=lambda: None)
                if "WHERE f.name STARTS WITH $p" in q:
                    return []
                if "RETURN c.version AS version" in q:
                    return mock.Mock(single=lambda: {"version": 1, "boilerplate": []})
                if "f.key_points AS key_points, f.content AS content" in q:
                    return mock.Mock(single=lambda: {
                        "name": "Shared X", "summary": "S", "key_points": ["p1"], "content": content,
                    })
                if "RETURN c.rule_version AS rule_version" in q:
                    return mock.Mock(single=lambda: None)
                if "RETURN count(f) AS n" in q:
                    return mock.Mock(single=lambda: {"n": 3})
                if "MATCH (w:Word) WHERE w.text IN $toks" in q:
                    return []
                return mock.Mock(single=lambda: None)

        class Drv:
            def session(self):
                return Sess()

            def close(self):
                pass

        args = nm.build_parser().parse_args([
            "write", "--space", "shared", "--name", "Foo", "--summary", "S",
            "--point", "p1", "--no-embed",
        ])
        with mock.patch.object(nm, "_driver", lambda **k: (Drv(), {})):
            rc = nm.cmd_write_shared(args)
        self.assertEqual(rc, 0)

        write_kw = self._find_tokens_call(calls)
        expected_text = nm.fact_embed_text("Shared X", "S", ["p1"], self._CONTENT, frozenset())
        expected_tokens = nm.tokenize(expected_text, "Shared X")
        self.assertNotEqual(
            expected_tokens,
            nm.tokenize(nm.fact_embed_text("Shared X", "S", ["p1"], None, frozenset()), "Shared X"),
        )
        self.assertEqual(write_kw["tokens"], expected_tokens)


if __name__ == "__main__":
    unittest.main()
