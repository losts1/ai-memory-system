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
    assert p2.written_at == p.written_at


def test_from_dict_ignores_unknown_keys():
    p = Provenance.from_dict({
        "source": "api",
        "trust": "trusted",
        "future_field": "some_value",
    })
    assert p.source == "api"
    assert p.trust == "trusted"


def test_from_dict_missing_source_raises():
    import pytest
    with pytest.raises(TypeError):
        Provenance.from_dict({"trust": "trusted"})


def test_written_at_auto_populated():
    from datetime import datetime
    p = Provenance(source="memory")
    dt = datetime.fromisoformat(p.written_at)
    assert dt.tzinfo is not None  # must be timezone-aware (UTC)


def test_to_dict_preserves_risk_score_zero():
    """risk_score=0 is a valid score and must not be filtered by to_dict()."""
    p = Provenance(source="api", risk_score=0)
    d = p.to_dict()
    assert "risk_score" in d
    assert d["risk_score"] == 0


def test_signals_string_coerced_to_list():
    """signals passed as a string must be coerced to a single-element list."""
    p = Provenance.from_dict({"source": "api", "signals": "exfiltration"})
    assert p.signals == ["exfiltration"]


def test_signals_empty_string_coerced_to_empty_list():
    """signals='' must coerce to []."""
    p = Provenance.from_dict({"source": "api", "signals": ""})
    assert p.signals == []


def test_signals_invalid_type_raises():
    """signals of unexpected type must raise TypeError."""
    import pytest
    with pytest.raises(TypeError, match="signals"):
        Provenance(source="api", signals=42)


def test_sync_fact_tx_clears_stale_provenance_on_update():
    """Re-writing a Fact with updated provenance must write None for newly-absent fields.

    Regression for: risk_score=47 → risk_score=None left stale 47 in Neo4j
    because to_dict() omits None fields, so the SET clause omitted risk_score.
    """
    from unittest.mock import MagicMock
    from ai_memory.learn import _sync_fact_tx

    all_calls_params = []

    def fake_run(cypher, **params):
        all_calls_params.append(params)
        mock_result = MagicMock()
        mock_result.single.return_value = {"name": "Test"}
        return mock_result

    tx = MagicMock()
    tx.run.side_effect = fake_run

    topic = {
        'name': 'Test Fact',
        'summary': 'test',
        'key_points': [],
        'source_file': 'test.md',
        'created_at': '2026-06-07T00:00:00Z',
        'provenance': Provenance(
            source='web_fetch',
            trust='trusted',
            risk_score=None,   # intentionally absent — must be written as None to Neo4j
        ),
    }
    _sync_fact_tx(tx, topic)

    first_call = all_calls_params[0]
    # risk_score was None → to_dict() omits it → but prov_risk_score must still be in params
    assert 'prov_risk_score' in first_call
    assert first_call['prov_risk_score'] is None  # must be written as NULL, not omitted
