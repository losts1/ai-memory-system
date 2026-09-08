from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ai_memory.embed import EMBED_DIM, fact_embed_text, text_sha
from ai_memory.retrieval_config import RetrievalConfig
from ai_memory.wordindex import tokenize


class _Res:
    def __init__(self, rows): self.rows = rows
    def single(self): return self.rows[0] if self.rows else None


class _Sess:
    def __init__(self, rows): self.calls = []; self.rows = list(rows)
    def run(self, q, **params):
        self.calls.append((q, params)); return _Res(self.rows.pop(0) if self.rows else [])


FACT = {"name": "N", "content": "body " * 3, "source": "sess.md"}


def test_sync_writer_embeds_canonical_text_in_one_statement():
    import neo4j_sync as S
    cfg = RetrievalConfig(version=2, boilerplate=frozenset())
    s = _Sess([[{"summary": "Sum", "key_points": ["k"]}], [{"name": "N", "embedded": 1}]])
    status = S.write_fact_with_embedding(s, FACT, relative_path="sess.md", assistant="Nova", cfg=cfg, embed_fn=lambda t: [0.2] * EMBED_DIM)
    assert status == "embedded"
    read_q, _ = s.calls[0]
    assert "RETURN f.summary AS summary, f.key_points AS key_points" in read_q
    q, p = s.calls[1]
    assert "MERGE (f:Fact {name: $name})" in q and "SET f.content = $content" in q and "CALL {" in q
    assert "coalesce(f.summary, '') = $cas_summary" in q and "coalesce(f.key_points, []) = $cas_key_points" in q
    assert "$cas_content" not in q                                  # content is this writer's own field
    assert p["cas_summary"] == "Sum" and p["cas_key_points"] == ["k"] and p["assistant"] == "Nova"
    text = fact_embed_text("N", "Sum", ["k"], FACT["content"][:2000], frozenset())
    assert p["embedding_text_sha"] == text_sha(text, 2)


def test_sync_writer_reports_cas_skipped_when_concurrently_edited():
    import neo4j_sync as S
    cfg = RetrievalConfig(version=2, boilerplate=frozenset())
    s = _Sess([[{"summary": "Sum", "key_points": ["k"]}], [{"name": "N", "embedded": 0}]])
    status = S.write_fact_with_embedding(s, FACT, relative_path="sess.md", assistant=None, cfg=cfg, embed_fn=lambda t: [0.2] * EMBED_DIM)
    assert status == "cas_skipped"


def test_sync_writer_text_only_when_no_vector():
    import neo4j_sync as S
    s = _Sess([[{"summary": None, "key_points": None}], [{"name": "N"}]])
    status = S.write_fact_with_embedding(s, FACT, relative_path="sess.md", assistant=None, cfg=RetrievalConfig(1, frozenset()), embed_fn=lambda t: None)
    assert status == "text_only" and "CALL {" not in s.calls[1][0]
    s = _Sess([[{"summary": None, "key_points": None}], [{"name": "N"}]])
    status = S.write_fact_with_embedding(s, FACT, relative_path="sess.md", assistant=None, cfg=None, embed_fn=lambda t: [0.2] * EMBED_DIM)
    assert status == "text_only" and len(s.calls) == 2 and "CALL {" not in s.calls[1][0]


def test_sync_writer_writes_word_tokens_from_canonical_text_including_content():
    """The words block must run for the same Fact and thread `WITH f` into the embed
    block; a content string with tokens the name/summary don't contribute proves
    content isn't silently dropped from the canonical text."""
    import neo4j_sync as S
    fact = {"name": "N", "content": "Zebra kumquat lattice", "source": "sess.md"}
    cfg = RetrievalConfig(version=2, boilerplate=frozenset())
    s = _Sess([[{"summary": "Sum", "key_points": ["k"]}], [{"name": "N", "embedded": 1}]])
    status = S.write_fact_with_embedding(s, fact, relative_path="sess.md", assistant=None, cfg=cfg, embed_fn=lambda t: [0.2] * EMBED_DIM)
    assert status == "embedded"
    write_q, write_p = s.calls[1]
    assert "DELETE old" in write_q and "HAS_WORD" in write_q and "FOREACH" in write_q
    assert "CALL {" in write_q
    assert "RETURN f.name AS name, embedded" in write_q
    expected_text = fact_embed_text("N", "Sum", ["k"], fact["content"], frozenset())
    expected_tokens = tokenize(expected_text, "N")
    assert write_p["words"] == expected_tokens
    for tok in ("zebra", "kumquat", "lattice"):
        assert tok in write_p["words"]


def test_sync_writer_writes_word_tokens_with_empty_boilerplate_when_no_cfg_or_embed_fn():
    """With embed_fn=None (or cfg=None) the words block is still present and tokens
    come from the prepared text with empty boilerplate."""
    import neo4j_sync as S
    fact = {"name": "N", "content": "Zebra kumquat lattice", "source": "sess.md"}
    s = _Sess([[{"summary": None, "key_points": None}], [{"name": "N"}]])
    status = S.write_fact_with_embedding(s, fact, relative_path="sess.md", assistant=None, cfg=None, embed_fn=None)
    assert status == "text_only"
    write_q, write_p = s.calls[1]
    assert "FOREACH" in write_q and "CALL {" not in write_q
    expected_text = fact_embed_text("N", None, None, fact["content"], ())
    assert write_p["words"] == tokenize(expected_text, "N")
    for tok in ("zebra", "kumquat", "lattice"):
        assert tok in write_p["words"]


def test_sync_writer_threads_cfg_boilerplate_not_hardcoded_empty():
    """M-5 (final-review.md): the other new token tests use an empty boilerplate, so an
    implementation that always passed () instead of cfg.boilerplate would still pass
    them. Pin that cfg.boilerplate is actually threaded through by using real
    boilerplate grams that change the resulting tokens."""
    import neo4j_sync as S
    content = "zzzboiler zzztest zzzalpha zzzbeta real distinguishing words here today extra"
    boilerplate = frozenset(["zzzboiler zzztest zzzalpha zzzbeta", "zzztest zzzalpha zzzbeta real"])
    fact = {"name": "N", "content": content, "source": "sess.md"}
    cfg = RetrievalConfig(version=3, boilerplate=boilerplate)
    s = _Sess([[{"summary": None, "key_points": None}], [{"name": "N", "embedded": 1}]])
    status = S.write_fact_with_embedding(s, fact, relative_path="sess.md", assistant=None, cfg=cfg, embed_fn=lambda t: [0.2] * EMBED_DIM)
    assert status == "embedded"
    write_p = s.calls[1][1]
    expected_with_boilerplate = tokenize(fact_embed_text("N", None, None, content, boilerplate), "N")
    expected_without_boilerplate = tokenize(fact_embed_text("N", None, None, content, ()), "N")
    assert expected_with_boilerplate != expected_without_boilerplate
    assert write_p["words"] == expected_with_boilerplate
    assert "zzzboiler" not in write_p["words"]


def test_sync_writer_dedupes_rows_after_deleting_old_words():
    """A Fact with N pre-existing HAS_WORD edges must not multiply the row past the
    DELETE — the WITH after `DELETE old` must be `WITH DISTINCT f`, or the FOREACH
    body and the CAS embed subquery each run N times (I-1 in final-review.md)."""
    import neo4j_sync as S
    fact = {"name": "N", "content": "Zebra kumquat lattice", "source": "sess.md"}
    cfg = RetrievalConfig(version=2, boilerplate=frozenset())
    s = _Sess([[{"summary": "Sum", "key_points": ["k"]}], [{"name": "N", "embedded": 1}]])
    S.write_fact_with_embedding(s, fact, relative_path="sess.md", assistant=None, cfg=cfg, embed_fn=lambda t: [0.2] * EMBED_DIM)
    write_q, _ = s.calls[1]
    assert re.search(r"DELETE old\s*\n\s*WITH DISTINCT f\b", write_q), write_q


# ---------------------------------------------------------------------------
# Private #2 — the session-sync writer must not clobber another mind's Fact
# ---------------------------------------------------------------------------

def test_sync_writer_refuses_another_minds_fact(capsys):
    import neo4j_sync as S
    cfg = RetrievalConfig(version=2, boilerplate=frozenset())
    s = _Sess([[{"summary": "Sum", "key_points": ["k"], "owner": "Nova"}]])
    status = S.write_fact_with_embedding(s, FACT, relative_path="sess.md", assistant="Weft", cfg=cfg, embed_fn=lambda t: [0.2] * EMBED_DIM)
    assert status == "owner_conflict"
    assert len(s.calls) == 1                                       # the read only; no MERGE
    assert "f.assistant AS owner" in s.calls[0][0]
    err = capsys.readouterr().err
    assert "Refusing to overwrite Fact 'N'" in err and "'Nova'" in err and "'Weft'" in err


def test_sync_writer_refuses_untagged_writer_on_tagged_fact(capsys):
    import neo4j_sync as S
    s = _Sess([[{"summary": None, "key_points": None, "owner": "Nova"}]])
    status = S.write_fact_with_embedding(s, FACT, relative_path="sess.md", assistant=None, cfg=None, embed_fn=None)
    assert status == "owner_conflict" and len(s.calls) == 1
    assert "untagged" in capsys.readouterr().err


def test_sync_writer_updates_own_fact_and_claims_untagged_one():
    import neo4j_sync as S
    cfg = RetrievalConfig(version=2, boilerplate=frozenset())
    s = _Sess([[{"summary": "Sum", "key_points": ["k"], "owner": "Nova"}], [{"name": "N", "embedded": 1}]])
    assert S.write_fact_with_embedding(s, FACT, relative_path="sess.md", assistant="Nova", cfg=cfg, embed_fn=lambda t: [0.2] * EMBED_DIM) == "embedded"
    # untagged Fact: library memory a tagged writer may update and claim
    s = _Sess([[{"summary": "Sum", "key_points": ["k"], "owner": None}], [{"name": "N", "embedded": 1}]])
    assert S.write_fact_with_embedding(s, FACT, relative_path="sess.md", assistant="Nova", cfg=cfg, embed_fn=lambda t: [0.2] * EMBED_DIM) == "embedded"
    assert s.calls[1][1]["assistant"] == "Nova"
    # no existing Fact at all -> plain create
    s = _Sess([[], [{"name": "N"}]])
    assert S.write_fact_with_embedding(s, FACT, relative_path="sess.md", assistant="Nova", cfg=None, embed_fn=None) == "text_only"


def test_sync_file_tallies_owner_conflicts_and_skips_their_edge_maintenance(tmp_path, monkeypatch):
    import neo4j_sync as S

    filepath = tmp_path / "2026-01-01-test.md"
    filepath.write_text("## Learned: A\nbody a\n\n## Learned: B\nbody b\n")

    monkeypatch.setattr(S, "load_retrieval_config", lambda session: None)
    monkeypatch.setattr(
        S, "write_fact_with_embedding",
        lambda session, fact, **kw: "owner_conflict" if fact["name"] == "A" else "text_only",
    )
    maintained = []
    monkeypatch.setattr(S, "maintain_edges_after_write", lambda session, name: maintained.append(name))

    class _FakeSession:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def run(self, q, **kw): return _Res([])

    class _FakeDriver:
        def session(self): return _FakeSession()

    result = S.sync_file(_FakeDriver(), filepath, {"files": {}}, assistant="Weft")

    assert maintained == ["B"]                       # the refused Fact gets no edge work
    assert result == {
        "status": "synced", "file": "2026-01-01-test.md", "facts": 2, "dropped": 0,
        "embed_failures": 1, "cas_skipped": 0, "owner_conflicts": 1,
    }


def test_sync_file_runs_edge_maintenance_per_fact_and_survives_a_raising_maintenance(tmp_path, monkeypatch):
    import neo4j_sync as S

    content = "## Learned: A\nbody a\n\n## Learned: B\nbody b\n"
    filepath = tmp_path / "2026-01-01-test.md"
    filepath.write_text(content)

    monkeypatch.setattr(S, "load_retrieval_config", lambda session: None)
    monkeypatch.setattr(S, "write_fact_with_embedding", lambda session, fact, **kw: "text_only")

    maintained = []

    def _maint(session, name):
        maintained.append(name)
        if name == "B":
            raise RuntimeError("boom")

    monkeypatch.setattr(S, "maintain_edges_after_write", _maint)

    class _FakeSession:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def run(self, q, **kw): return _Res([])

    class _FakeDriver:
        def session(self): return _FakeSession()

    state = {"files": {}}
    result = S.sync_file(_FakeDriver(), filepath, state, assistant=None)

    assert maintained == ["A", "B"]
    assert result == {
        "status": "synced", "file": "2026-01-01-test.md", "facts": 2, "dropped": 0,
        "embed_failures": 2, "cas_skipped": 0, "owner_conflicts": 0,
    }
