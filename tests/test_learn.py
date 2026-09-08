from pathlib import Path

from ai_memory.embed import EMBED_DIM, fact_embed_text, text_sha
from ai_memory.retrieval_config import RetrievalConfig
from ai_memory.wordindex import tokenize


def test_normalize_name_strips_punctuation():
    from ai_memory.learn import normalize_name
    result = normalize_name("Avellaneda-Stoikov (Model)")
    assert "avellaneda" in result
    assert "stoikov" in result
    assert "(" not in result


def test_normalize_name_lowercases():
    from ai_memory.learn import normalize_name
    assert normalize_name("SQL Optimization") == "sql optimization"


def test_extract_words_basic():
    from ai_memory.learn import extract_words
    words = extract_words("Avellaneda-Stoikov Model")
    assert "avellaneda" in words
    assert "stoikov" in words


def test_extract_words_short_important_tokens():
    from ai_memory.learn import extract_words
    words = extract_words("SQL Query Optimization")
    assert "sql" in words  # SHORT_WORDS bypass min_length


def test_extract_words_stop_words_removed():
    from ai_memory.learn import extract_words
    words = extract_words("Market Making Session")
    assert "market" not in words
    assert "making" not in words
    assert "session" not in words


def test_extract_words_returns_unique():
    from ai_memory.learn import extract_words
    words = extract_words("attention attention mechanism")
    assert len([w for w in words if w == "attention"]) == 1


def test_is_topic_saturated_below_threshold():
    from ai_memory.learn import is_topic_saturated
    existing = {"attention mechanism bert", "attention weights neural"}
    assert not is_topic_saturated("attention mechanism", existing, threshold=3)


def test_is_topic_saturated_at_threshold():
    from ai_memory.learn import is_topic_saturated
    existing = {
        "attention mechanism bert",
        "attention mechanism transformer",
        "attention mechanism head",
    }
    assert is_topic_saturated("attention mechanism", existing, threshold=3)


def test_is_topic_saturated_no_specific_words():
    from ai_memory.learn import is_topic_saturated
    # Only short words → can't determine saturation → False
    existing = {"ai systems", "ai models", "ai tools"}
    assert not is_topic_saturated("ai ml", existing, threshold=1)


def test_parse_learned_topics_empty():
    from ai_memory.learn import parse_learned_topics
    assert parse_learned_topics("no learned sections here", Path("test.md")) == []


def test_parse_learned_topics_basic():
    from ai_memory.learn import parse_learned_topics
    content = "## Learned: Gamma Parameter\n- key point one\n- key point two\nsome summary\n"
    results = parse_learned_topics(content, Path("2026-01-01.md"))
    assert len(results) == 1
    assert results[0]["name"] == "Gamma Parameter"
    assert results[0]["source_file"] == "2026-01-01.md"
    assert "key point one" in results[0]["key_points"]


def test_parse_learned_topics_strips_time_annotation():
    from ai_memory.learn import parse_learned_topics
    content = "## Learner Session: Async IO (3:00 PM EDT)\n- event loop\n"
    results = parse_learned_topics(content, Path("2026-01-01.md"))
    assert len(results) == 1
    assert results[0]["name"] == "Async IO"


def test_parse_learned_topics_strips_utc_time():
    from ai_memory.learn import parse_learned_topics
    content = "## Learned: Async IO (15:30 UTC)\n- event loop\n"
    results = parse_learned_topics(content, Path("2026-01-01.md"))
    assert len(results) == 1
    assert results[0]["name"] == "Async IO"


def test_parse_learned_topics_multiple():
    from ai_memory.learn import parse_learned_topics
    content = (
        "## Learned: Topic A\n- point A\n\n"
        "## Learned: Topic B\n- point B\n"
    )
    results = parse_learned_topics(content, Path("2026-01-01.md"))
    assert len(results) == 2


def test_learn_importable():
    from ai_memory.learn import (
        parse_learned_topics, extract_words, normalize_name,
        is_topic_saturated, sync_facts, rebuild_graph,
    )


def test_link_related_facts_and_cleanup_orphaned_words_retired():
    """The legacy shared-word edge writers are gone; wordindex.maintain_edges_for /
    rebuild_edges replace them (Task 3)."""
    import ai_memory.learn as L
    assert not hasattr(L, "link_related_facts")
    assert not hasattr(L, "cleanup_orphaned_words")
    assert not hasattr(L, "_post_sync_tx")


def test_learn_module_in_package():
    import ai_memory.learn
    assert hasattr(ai_memory.learn, "parse_learned_topics")
    assert hasattr(ai_memory.learn, "sync_facts")
    assert hasattr(ai_memory.learn, "rebuild_graph")


def test_memclient_has_learn():
    from ai_memory import MemoryClient
    assert hasattr(MemoryClient, "learn")


# ---------------------------------------------------------------------------
# parse_frontmatter_topic (Phase 5 — YAML-frontmatter corpora)
# ---------------------------------------------------------------------------

def test_parse_frontmatter_topic_no_frontmatter_returns_empty():
    from ai_memory.learn import parse_frontmatter_topic
    assert parse_frontmatter_topic("just a body, no frontmatter", Path("x.md")) == []


def test_parse_frontmatter_topic_missing_name_returns_empty():
    from ai_memory.learn import parse_frontmatter_topic
    content = "---\ndescription: only a description\n---\nbody\n"
    assert parse_frontmatter_topic(content, Path("x.md")) == []


def test_parse_frontmatter_topic_uses_description_as_summary():
    from ai_memory.learn import parse_frontmatter_topic
    content = (
        "---\n"
        "name: Bot restarts\n"
        "description: Always stagger Kraken bot restarts by 30s\n"
        "type: feedback\n"
        "---\n\n"
        "Body paragraph that is longer than the description.\n"
    )
    results = parse_frontmatter_topic(content, Path("feedback_bot_restarts.md"))
    assert len(results) == 1
    t = results[0]
    assert t["name"] == "Bot restarts"
    assert t["summary"] == "Always stagger Kraken bot restarts by 30s"
    assert t["source_file"] == "feedback_bot_restarts.md"


def test_parse_frontmatter_topic_falls_back_to_first_paragraph():
    from ai_memory.learn import parse_frontmatter_topic
    content = (
        "---\n"
        "name: Topic\n"
        "---\n\n"
        "First paragraph that becomes the summary.\n\n"
        "Second paragraph that should not.\n"
    )
    [t] = parse_frontmatter_topic(content, Path("x.md"))
    assert t["summary"].startswith("First paragraph")
    assert "Second paragraph" not in t["summary"]


def test_parse_frontmatter_topic_extracts_bullets_as_key_points():
    from ai_memory.learn import parse_frontmatter_topic
    content = (
        "---\nname: T\ndescription: D\n---\n\n"
        "- first bullet\n"
        "- second bullet\n"
        "* asterisk bullet\n"
        "1. numbered bullet\n"
        "Prose line that is not a bullet.\n"
    )
    [t] = parse_frontmatter_topic(content, Path("x.md"))
    assert "first bullet" in t["key_points"]
    assert "second bullet" in t["key_points"]
    assert "asterisk bullet" in t["key_points"]
    assert "numbered bullet" in t["key_points"]
    assert all("Prose line" not in kp for kp in t["key_points"])


def test_parse_frontmatter_topic_caps_key_points_at_10():
    from ai_memory.learn import parse_frontmatter_topic
    bullets = "\n".join(f"- bullet {i}" for i in range(20))
    content = f"---\nname: T\n---\n\n{bullets}\n"
    [t] = parse_frontmatter_topic(content, Path("x.md"))
    assert len(t["key_points"]) == 10


def test_parse_frontmatter_topic_strips_comment_lines():
    from ai_memory.learn import parse_frontmatter_topic
    content = (
        "---\n"
        "# this is a comment\n"
        "name: T\n"
        "description: D\n"
        "---\nbody\n"
    )
    [t] = parse_frontmatter_topic(content, Path("x.md"))
    assert t["name"] == "T"


def test_parse_frontmatter_topic_tolerates_bom():
    """Editors that prepend a UTF-8 BOM must not break frontmatter detection."""
    from ai_memory.learn import parse_frontmatter_topic
    content = "﻿---\nname: BOM-Topic\ndescription: D\n---\nbody\n"
    [t] = parse_frontmatter_topic(content, Path("x.md"))
    assert t["name"] == "BOM-Topic"


def test_extract_bullets_skips_code_fences():
    """Bullets inside ``` ... ``` are not extracted as key_points."""
    from ai_memory.learn import _extract_bullets
    body = (
        "- real bullet 1\n"
        "```\n"
        "- not a bullet (inside code fence)\n"
        "```\n"
        "- real bullet 2\n"
    )
    bullets = _extract_bullets(body)
    assert "real bullet 1" in bullets
    assert "real bullet 2" in bullets
    assert all("not a bullet" not in b for b in bullets)


def test_extract_bullets_rejects_version_strings():
    """`1.2.3 foo` (version, no whitespace after first period) is not a bullet."""
    from ai_memory.learn import _extract_bullets
    body = (
        "1. real numbered item\n"
        "1.2.3 should not be eaten\n"
    )
    bullets = _extract_bullets(body)
    assert "real numbered item" in bullets
    assert all("should not be eaten" not in b for b in bullets)


def test_make_teaser_prefers_description_when_provided():
    from ai_memory.metadata import make_teaser
    long_summary = ("alpha beta gamma " * 30).strip()  # > 150 chars, has spaces
    short_description = "curated one-liner"
    # default path truncates and ends with "..."
    default_teaser = make_teaser(long_summary)
    assert default_teaser.endswith("...")
    assert len(default_teaser) <= 153
    # description path wins regardless of summary length
    assert make_teaser(long_summary, description=short_description) == short_description
    assert make_teaser("", description=None) == ""
    assert make_teaser("normal summary") == "normal summary"
    # whitespace-stripped description
    assert make_teaser("ignored", description="  spaced  ") == "spaced"
    # whitespace-only description must fall through to summary, not return ""
    assert make_teaser("summary wins", description="   ") == "summary wins"


def test_sync_fact_tx_lets_transient_error_reach_driver_retry():
    from neo4j.exceptions import TransientError
    import pytest
    from ai_memory.learn import _sync_fact_tx

    class Tx:
        def run(self, *a, **k):
            raise TransientError("deadlock")

    topic = {"name": "n", "summary": "s", "key_points": [], "source_file": "f", "created_at": "2026-01-01T00:00:00Z"}
    with pytest.raises(TransientError):
        _sync_fact_tx(Tx(), topic, None)


# ---------------------------------------------------------------------------
# Task 5 — write() and learn() embed the canonical text in the MERGE statement
#
# The embedding is prepared by _prepare_embed OUTSIDE any write transaction
# (spec §4; a stalled Ollama must not hold a Fact lock). _sync_fact_tx takes
# the already-prepared params dict (or None) and never reads or embeds itself.
# ---------------------------------------------------------------------------

class _Res:
    def __init__(self, rows): self.rows = rows
    def single(self): return self.rows[0] if self.rows else None


class _Tx:
    def __init__(self, rows_by_call): self.calls = []; self.rows = list(rows_by_call)
    def run(self, q, **params):
        self.calls.append((q, params))
        return _Res(self.rows.pop(0) if self.rows else [])


TOPIC = {"name": "T", "summary": "S", "key_points": ["k1"], "source_file": "api", "created_at": "2026-09-04T00:00:00+00:00"}
CFG = RetrievalConfig(version=3, boilerplate=frozenset())


def test_prepare_embed_reads_content_once_and_builds_params():
    from ai_memory.learn import _prepare_embed
    session = _Tx([[{"content": "existing body"}]])
    embed, tokens = _prepare_embed(session, TOPIC, CFG, lambda t: [0.1] * EMBED_DIM)
    assert len(session.calls) == 1
    read_q, read_params = session.calls[0]
    assert read_q.strip().startswith("OPTIONAL MATCH (f:Fact {name: $name})")
    assert read_params["name"] == "T"
    assert embed["cas_content"] == "existing body"
    expected_text = fact_embed_text("T", "S", ["k1"], "existing body", frozenset())
    assert embed["embedding_text_sha"] == text_sha(expected_text, 3) and embed["boilerplate_version"] == 3
    assert tokens == tokenize(expected_text, "T")


def test_prepare_embed_returns_none_when_vector_is_none():
    from ai_memory.learn import _prepare_embed
    session = _Tx([[{"content": "existing body"}]])
    embed, tokens = _prepare_embed(session, TOPIC, CFG, lambda t: None)
    assert embed is None
    expected_text = fact_embed_text("T", "S", ["k1"], "existing body", frozenset())
    assert tokens == tokenize(expected_text, "T")


def test_prepare_embed_returns_none_without_cfg():
    """No cfg -> no vector, and no Neo4j read is issued (nothing to strip boilerplate
    against, no config version to validate a CAS write with); tokens still come back,
    computed with empty boilerplate."""
    from ai_memory.learn import _prepare_embed
    session1 = _Tx([])
    embed1, tokens1 = _prepare_embed(session1, TOPIC, None, lambda t: [0.1] * EMBED_DIM)
    assert embed1 is None and session1.calls == []
    expected_text = fact_embed_text("T", "S", ["k1"], None, ())
    assert tokens1 == tokenize(expected_text, "T")


def test_prepare_embed_without_embed_fn_still_tokenises_with_cfg_and_content():
    """F4: cfg present but embed_fn=None (the scripts/rlm/neo4j_learn_sync.py path, which
    embeds separately itself) -> no vector, but tokens must still follow cfg.boilerplate
    and the Fact's existing `content` — not silently degrade to name/summary/key_points
    only, as if no config were available at all."""
    from ai_memory.learn import _prepare_embed
    session2 = _Tx([[{"content": "existing body"}]])
    embed2, tokens2 = _prepare_embed(session2, TOPIC, CFG, None)
    assert embed2 is None
    assert len(session2.calls) == 1
    expected_text = fact_embed_text("T", "S", ["k1"], "existing body", CFG.boilerplate)
    assert tokens2 == tokenize(expected_text, "T")


def test_sync_fact_tx_uses_prepared_embed_with_no_read_call():
    from ai_memory.learn import _prepare_embed, _sync_fact_tx
    embed, tokens = _prepare_embed(_Tx([[{"content": "existing body"}]]), TOPIC, CFG, lambda t: [0.1] * EMBED_DIM)
    assert tokens                                   # non-empty, drawn from the full prepared text
    tx = _Tx([[{"owner": None}], [{"name": "T", "embedded": 1}], []])
    ok = _sync_fact_tx(tx, TOPIC, None, embed=embed, tokens=tokens)
    assert ok
    # ownership read (review #2) + main write + words UNWIND; no *content* read —
    # the embed params were prepared outside the transaction.
    assert len(tx.calls) == 3
    assert "RETURN f.assistant AS owner" in tx.calls[0][0]
    merge_q, params = tx.calls[1]
    assert "MERGE (f:Fact {name: $name})" in merge_q and "WITH DISTINCT f\nCALL {" in merge_q
    assert "coalesce(f.content, '') = $cas_content" in merge_q and "$cas_summary" not in merge_q
    assert params["cas_content"] == "existing body"
    assert params["embedding_text_sha"] == embed["embedding_text_sha"] and params["boilerplate_version"] == 3
    assert "RETURN f.name as name, embedded" in merge_q or "RETURN f.name AS name, embedded" in merge_q
    words_q, words_params = tx.calls[2]
    assert "UNWIND $words AS word" in words_q
    assert words_params["words"] == tokens


def test_sync_fact_tx_without_embed_writes_text_only():
    from ai_memory.learn import _sync_fact_tx
    tx = _Tx([[{"owner": None}], [{"name": "T"}]])
    assert _sync_fact_tx(tx, TOPIC, None, embed=None)
    # ownership read (review #2) + main write, no embed subquery
    assert "CALL {" not in tx.calls[1][0] and len(tx.calls) == 2


def test_sync_facts_loads_config_once_and_passes_embed_fn(monkeypatch):
    """cfg and edge_cfg are each loaded exactly once per sync_facts() call (not per topic).
    No edge layer is built yet here (edge_cfg query returns no rows), so maintain_edges_for
    is never called."""
    import ai_memory.learn as L
    seen = {"cfg_loads": 0, "edge_cfg_loads": 0, "tx_embeds": []}
    class Sess:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def run(self, q, **kw):
            if "AS version" in q:
                seen["cfg_loads"] += 1
                return _Res([{"version": 3, "boilerplate": [], "updated_at": None}])
            if "AS rule_version" in q:
                seen["edge_cfg_loads"] += 1
                return _Res([])                     # no edge layer built yet -> None
            return _Res([])
        def execute_write(self, fn, *a, **kw):
            if fn is L._sync_fact_tx:
                seen["tx_embeds"].append(kw.get("embed"))
                return True
            return None
    class Drv:
        def session(self): return Sess()
        def close(self): pass
    monkeypatch.setattr(L, "get_driver", lambda ws=None: Drv())
    n = L.sync_facts([TOPIC, dict(TOPIC, name="U")], embed_fn=lambda t: None)
    assert n == 2 and seen["cfg_loads"] == 1 and seen["edge_cfg_loads"] == 1
    assert seen["tx_embeds"] == [None, None]        # embed_fn returns None -> no vector prepared


def test_sync_facts_runs_edge_maintenance_per_topic_when_edge_cfg_available(monkeypatch):
    """sync_facts calls maintain_edges_for once per successfully-synced topic when
    load_edge_config returns a config, and skips it entirely when it returns None."""
    import ai_memory.learn as L
    seen = {"maintained": []}
    class Sess:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def run(self, q, **kw):
            if "AS rule_version" in q:
                return _Res([{"rule_version": 3, "edge_floor": 1.0, "t_mean": 0.0, "t_std": 1.0,
                              "c_mean": 0.0, "c_std": 1.0, "n_facts": 10}])
            return _Res([])
        def execute_write(self, fn, *a, **kw):
            return True if fn is L._sync_fact_tx else None
    class Drv:
        def session(self): return Sess()
        def close(self): pass
    monkeypatch.setattr(L, "get_driver", lambda ws=None: Drv())
    monkeypatch.setattr(L, "maintain_edges_for", lambda session, name, edge_cfg, **kw: seen["maintained"].append(name))
    n = L.sync_facts([TOPIC, dict(TOPIC, name="U")], embed_fn=None)
    assert n == 2
    assert seen["maintained"] == ["T", "U"]


def test_sync_facts_tokenises_with_config_boilerplate_and_content_even_when_embed_fn_none(monkeypatch):
    """F4: with embed_fn=None (the scripts/rlm/neo4j_learn_sync.py production path — it
    embeds separately itself), tokens must still follow the RetrievalConfig (real
    boilerplate stripping, real existing `content`) rather than silently falling back
    to name/summary/key_points-only text with no boilerplate stripping."""
    import ai_memory.learn as L
    content = "zzzboiler zzztest zzzalpha zzzbeta real distinguishing words here today extra"
    boilerplate = ["zzzboiler zzztest zzzalpha zzzbeta", "zzztest zzzalpha zzzbeta real"]
    captured = {"tokens": None}
    class Sess:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def run(self, q, **kw):
            if "AS version" in q:
                return _Res([{"version": 3, "boilerplate": boilerplate, "updated_at": None}])
            if "AS rule_version" in q:
                return _Res([])
            if "OPTIONAL MATCH (f:Fact {name: $name})" in q:
                return _Res([{"content": content}])
            return _Res([])
        def execute_write(self, fn, *a, **kw):
            if fn is L._sync_fact_tx:
                captured["tokens"] = kw.get("tokens")
                return True
            return None
    class Drv:
        def session(self): return Sess()
        def close(self): pass
    monkeypatch.setattr(L, "get_driver", lambda ws=None: Drv())
    n = L.sync_facts([TOPIC], embed_fn=None)
    assert n == 1
    from ai_memory.embed import fact_embed_text
    expected_text = fact_embed_text(TOPIC["name"], TOPIC["summary"], TOPIC["key_points"], content, frozenset(boilerplate))
    assert captured["tokens"] == tokenize(expected_text, TOPIC["name"])
    assert captured["tokens"] == ["distinguishing", "words", "here", "today", "extra"]


# ---------------------------------------------------------------------------
# Private #2 — a library writer must not clobber another mind's Fact
#
# Rule (mirrors the grok client's _owner_blocks_write, with one relaxation):
# a tagged Fact may only be updated by its own mind; an untagged Fact is the
# library's own inherited memory and any writer may update (and claim) it.
# ---------------------------------------------------------------------------

def test_sync_fact_tx_refuses_to_overwrite_another_minds_fact(capsys):
    from ai_memory.learn import _sync_fact_tx
    tx = _Tx([[{"owner": "Nova"}]])
    assert _sync_fact_tx(tx, TOPIC, "Claude", embed=None) is False
    assert len(tx.calls) == 1                            # the ownership read only
    assert "OPTIONAL MATCH (f:Fact {name: $name})" in tx.calls[0][0]
    assert "MERGE" not in tx.calls[0][0]                 # nothing was written
    err = capsys.readouterr().err
    assert "Refusing to overwrite Fact 'T'" in err and "'Nova'" in err and "'Claude'" in err


def test_sync_fact_tx_refuses_untagged_writer_on_tagged_fact(capsys):
    """A writer that passes no assistant is not 'everyone' — it is still a different
    writer from Nova, so the update is refused."""
    from ai_memory.learn import _sync_fact_tx
    tx = _Tx([[{"owner": "Nova"}]])
    assert _sync_fact_tx(tx, TOPIC, None, embed=None) is False
    assert len(tx.calls) == 1
    assert "untagged" in capsys.readouterr().err


def test_sync_fact_tx_updates_its_own_minds_fact():
    from ai_memory.learn import _sync_fact_tx
    tx = _Tx([[{"owner": "Claude"}], [{"name": "T"}]])
    assert _sync_fact_tx(tx, TOPIC, "Claude", embed=None) is True
    assert "MERGE (f:Fact {name: $name})" in tx.calls[1][0]


def test_sync_fact_tx_claims_untagged_fact():
    """The deliberate relaxation vs the grok client: untagged (NULL or blank) is
    library memory a tagged writer may update and claim."""
    from ai_memory.learn import _sync_fact_tx
    for owner in (None, "", "   "):
        tx = _Tx([[{"owner": owner}], [{"name": "T"}]])
        assert _sync_fact_tx(tx, TOPIC, "Claude", embed=None) is True
        assert "f.assistant = $assistant" in tx.calls[1][0]
        assert tx.calls[1][1]["assistant"] == "Claude"


def test_sync_fact_tx_creates_when_no_existing_fact():
    from ai_memory.learn import _sync_fact_tx
    tx = _Tx([[], [{"name": "T"}]])                      # ownership read returns no row
    assert _sync_fact_tx(tx, TOPIC, "Claude", embed=None) is True
    assert "MERGE (f:Fact {name: $name})" in tx.calls[1][0]


def test_sync_facts_does_not_count_or_maintain_edges_for_a_refused_fact(monkeypatch):
    """A refused Fact is not counted as synced and gets no edge maintenance."""
    import ai_memory.learn as L
    owners = {"T": "Nova", "U": None}
    seen = {"maintained": []}
    class Sess:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def run(self, q, **kw):
            if "AS rule_version" in q:
                return _Res([{"rule_version": 3, "edge_floor": 1.0, "t_mean": 0.0, "t_std": 1.0,
                              "c_mean": 0.0, "c_std": 1.0, "n_facts": 10}])
            return _Res([])
        def execute_write(self, fn, topic, assistant, **kw):
            tx = _Tx([[{"owner": owners[topic["name"]]}], [{"name": topic["name"]}]])
            return fn(tx, topic, assistant, **kw)
    class Drv:
        def session(self): return Sess()
        def close(self): pass
    monkeypatch.setattr(L, "get_driver", lambda ws=None: Drv())
    monkeypatch.setattr(L, "maintain_edges_for", lambda session, name, edge_cfg, **kw: seen["maintained"].append(name))
    n = L.sync_facts([TOPIC, dict(TOPIC, name="U")], assistant="Claude", embed_fn=None)
    assert n == 1
    assert seen["maintained"] == ["U"]


def test_maintain_edges_after_write_is_public_with_legacy_alias():
    """scripts/neo4j_sync.py imports maintain_edges_after_write directly (Task 1
    follow-on); _maintain_edges stays as a private alias for existing call sites."""
    import ai_memory.learn as L
    assert hasattr(L, "maintain_edges_after_write")
    assert L._maintain_edges is L.maintain_edges_after_write
