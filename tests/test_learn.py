from pathlib import Path


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
        link_related_facts, cleanup_orphaned_words,
    )


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
