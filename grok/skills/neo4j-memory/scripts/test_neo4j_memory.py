#!/usr/bin/env python3
"""Unit tests for neo4j_memory helpers. No live Neo4j/Ollama required."""
from __future__ import annotations

import io
import json
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
    def test_joins_and_truncates(self):
        t = nm._fact_text("Name", "Sum", "Body", ["p1", "p2"])
        self.assertEqual(t, "Name Sum Body p1 p2")
        long = "x" * 5000
        self.assertEqual(len(nm._fact_text("n", long)), nm.FACT_EMBED_CHARS)


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
            "topic": None,
        }

    def test_vector_only_drops_below_floor(self):
        vec = [
            self._hit("keep", 0.88),
            self._hit("noise", 0.75),
        ]
        hits, backend = nm._finish_hybrid([], vec, True, 5)
        self.assertEqual(backend, "hybrid")
        self.assertEqual([h["name"] for h in hits], ["keep"])
        self.assertEqual(hits[0]["score"], 0.88)

    def test_vector_only_all_noise_is_empty(self):
        vec = [self._hit("a", 0.76), self._hit("b", 0.74)]
        hits, backend = nm._finish_hybrid([], vec, True, 5)
        self.assertEqual(backend, "hybrid")
        self.assertEqual(hits, [])

    def test_both_legs_still_rrf_unfiltered(self):
        ft = [self._hit("A", 9.0, "ft"), self._hit("B", 8.0, "ft")]
        vec = [self._hit("noise", 0.75), self._hit("A", 0.88)]
        hits, backend = nm._finish_hybrid(ft, vec, True, 3)
        self.assertEqual(backend, "hybrid")
        names = [h["name"] for h in hits]
        self.assertIn("A", names)
        self.assertIn("noise", names)


class Lucene(unittest.TestCase):
    def test_escapes_specials(self):
        self.assertIn("\\+", nm._escape_lucene("a+b"))


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
                key_points=[], status=None, score=3.0,
            )]
        if idx == "fact_key_points":
            return [_FtRec(
                name="PointsHit", text="short", assistant="Grok",
                key_points=["file=/tmp/report.md"], status="active", score=9.0,
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

    def test_demote_superseded(self):
        hits = [
            {"name": "old", "status": "superseded", "score": 9},
            {"name": "new", "status": "active", "score": 8},
            {"name": "nova", "status": None, "score": 7},
        ]
        out = nm._demote_inactive(hits)
        self.assertEqual([h["name"] for h in out], ["new", "nova", "old"])

    def test_collapse_superseded_sibling(self):
        hits = [
            {"name": "new", "status": "active", "topic": "ntr-kalman", "score": 8},
            {"name": "nova", "status": None, "topic": None, "score": 7},
            {"name": "old", "status": "superseded", "topic": "ntr-kalman", "score": 9},
        ]
        out = nm._collapse_superseded_siblings(hits)
        self.assertEqual([h["name"] for h in out], ["new", "nova"])

    def test_keep_superseded_without_active_sibling(self):
        hits = [
            {"name": "old", "status": "superseded", "topic": "ntr-kalman", "score": 9},
            {"name": "nova", "status": None, "topic": None, "score": 7},
        ]
        out = nm._collapse_superseded_siblings(hits)
        self.assertEqual([h["name"] for h in out], ["old", "nova"])


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
        self.assertEqual(hits[0]["via"], "ft")  # RRF: equal rank-0, name A < B

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


if __name__ == "__main__":
    unittest.main()
