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
