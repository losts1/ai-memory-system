from ai_memory.provenance import Provenance


def test_provenance_defaults():
    p = Provenance(source="user")
    assert p.source == "user"
    assert p.trust == "unknown"
    assert p.risk_score is None
    assert p.signals == []


def test_to_dict_excludes_none_and_empty_list():
    p = Provenance(source="user")
    d = p.to_dict()
    assert "risk_score" not in d
    assert "signals" not in d
    assert d["source"] == "user"
    assert d["trust"] == "unknown"


def test_to_dict_includes_populated_fields():
    p = Provenance(
        source="web_fetch",
        trust="suspicious",
        risk_score=47,
        risk_band="medium",
        signals=["exfiltration", "embedded_command"],
        original_source="https://example.com",
        assistant="Weft",
    )
    d = p.to_dict()
    assert d["risk_score"] == 47
    assert d["signals"] == ["exfiltration", "embedded_command"]
    assert d["original_source"] == "https://example.com"


def test_from_dict_roundtrip():
    p = Provenance(
        source="bash",
        trust="untrusted",
        risk_score=30,
        risk_band="low",
        signals=["embedded_command"],
        assistant="Nova",
    )
    d = p.to_dict()
    p2 = Provenance.from_dict(d)
    assert p2.source == "bash"
    assert p2.trust == "untrusted"
    assert p2.risk_score == 30
    assert p2.signals == ["embedded_command"]
    assert p2.assistant == "Nova"


def test_from_dict_ignores_unknown_keys():
    p = Provenance.from_dict({
        "source": "api",
        "trust": "trusted",
        "future_field": "some_value",
    })
    assert p.source == "api"
    assert p.trust == "trusted"


def test_from_dict_missing_source_raises():
    try:
        Provenance.from_dict({"trust": "trusted"})
        assert False, "expected TypeError"
    except TypeError:
        pass


def test_written_at_auto_populated():
    p = Provenance(source="memory")
    assert p.written_at
    assert "T" in p.written_at
