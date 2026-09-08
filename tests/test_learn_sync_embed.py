from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts" / "rlm"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from ai_memory.embed import EMBED_DIM


class _Res:
    def __init__(self, rows): self.rows = rows
    def single(self): return self.rows[0] if self.rows else None
    def __iter__(self): return iter(self.rows)


class _Sess:
    def __init__(self, rows): self.calls = []; self.rows = list(rows)
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def run(self, q, **params):
        self.calls.append((q, params)); return _Res(self.rows.pop(0) if self.rows else [])


class _Drv:
    def __init__(self, s): self.s = s
    def session(self): return self.s


def test_update_neo4j_vector_uses_embed_fact_per_topic():
    import neo4j_learn_sync as LS
    s = _Sess([[{"version": 2, "boilerplate": [], "updated_at": None}],
               [{"name": "A", "summary": "s", "key_points": ["k"], "content": None}], [{"embedded": 1}],
               [{"name": "B", "summary": "s", "key_points": [], "content": None}], [{"embedded": 0}]])
    n = LS.update_neo4j_vector([{"name": "A", "key_points": ["k"]}, {"name": "B", "key_points": []}], _Drv(s), embed_fn=lambda t: [0.1] * EMBED_DIM)
    assert n == 1
    assert "RetrievalConfig" in s.calls[0][0]
    assert "CALL {" in s.calls[2][0] and "$cas_summary" in s.calls[2][0] and "$cas_content" in s.calls[2][0]
    assert not hasattr(LS, "_get_embedding_with_cache")


def test_update_neo4j_vector_returns_zero_without_config():
    import neo4j_learn_sync as LS
    s = _Sess([[]])
    assert LS.update_neo4j_vector([{"name": "A"}], _Drv(s), embed_fn=lambda t: [0.1] * EMBED_DIM) == 0


def test_sync_text_only_calls_sync_facts_with_embed_fn_none(monkeypatch):
    """Controller ruling: neo4j_learn_sync must not embed each topic twice — sync_facts
    (which now embeds by default) is called with embed_fn=None, leaving update_neo4j_vector
    as the script's single embedding pass."""
    import neo4j_learn_sync as LS
    calls = []

    def fake_sync_facts(topics, *, assistant=None, embed_fn=None):
        calls.append({"topics": topics, "assistant": assistant, "embed_fn": embed_fn})
        return len(topics)

    monkeypatch.setattr(LS, "sync_facts", fake_sync_facts)
    n = LS._sync_text_only([{"name": "A"}], "Nova")
    assert n == 1
    assert len(calls) == 1
    assert calls[0]["embed_fn"] is None
    assert calls[0]["assistant"] == "Nova"
