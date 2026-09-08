"""The (:RetrievalConfig {id: "current"}) singleton (spec §4): boilerplate grams + version.
Phase 5 adds the z-score baselines and edge floor to the same node."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

RETRIEVAL_CONFIG_ID = "current"


@dataclass(frozen=True)
class RetrievalConfig:
    version: int
    boilerplate: frozenset
    updated_at: str | None = None
    rule_version: int | None = None
    edge_floor: float | None = None
    t_mean: float | None = None
    t_std: float | None = None
    c_mean: float | None = None
    c_std: float | None = None
    baseline_pairs: int | None = None
    baseline_seed: int | None = None
    n_facts: int | None = None


def load_retrieval_config(session) -> RetrievalConfig | None:
    rec = session.run(
        "MATCH (c:RetrievalConfig {id: $id}) "
        "RETURN c.version AS version, c.boilerplate AS boilerplate, c.updated_at AS updated_at, "
        "c.rule_version AS rule_version, c.edge_floor AS edge_floor, "
        "c.t_mean AS t_mean, c.t_std AS t_std, c.c_mean AS c_mean, c.c_std AS c_std, "
        "c.baseline_pairs AS baseline_pairs, c.baseline_seed AS baseline_seed, c.n_facts AS n_facts",
        id=RETRIEVAL_CONFIG_ID,
    ).single()
    if rec is None or rec["version"] is None:
        return None
    rule_version = rec.get("rule_version")
    edge_floor = rec.get("edge_floor")
    t_mean = rec.get("t_mean")
    t_std = rec.get("t_std")
    c_mean = rec.get("c_mean")
    c_std = rec.get("c_std")
    baseline_pairs = rec.get("baseline_pairs")
    baseline_seed = rec.get("baseline_seed")
    n_facts = rec.get("n_facts")
    return RetrievalConfig(
        version=int(rec["version"]),
        boilerplate=frozenset(rec["boilerplate"] or []),
        updated_at=rec["updated_at"],
        rule_version=int(rule_version) if rule_version is not None else None,
        edge_floor=float(edge_floor) if edge_floor is not None else None,
        t_mean=float(t_mean) if t_mean is not None else None,
        t_std=float(t_std) if t_std is not None else None,
        c_mean=float(c_mean) if c_mean is not None else None,
        c_std=float(c_std) if c_std is not None else None,
        baseline_pairs=int(baseline_pairs) if baseline_pairs is not None else None,
        baseline_seed=int(baseline_seed) if baseline_seed is not None else None,
        n_facts=int(n_facts) if n_facts is not None else None,
    )


def publish_retrieval_config(session, grams: Iterable[str], *, now: str | None = None) -> RetrievalConfig:
    grams_sorted = sorted(set(grams))
    now = now or datetime.now(timezone.utc).isoformat()
    rec = session.run(
        "MERGE (c:RetrievalConfig {id: $id}) "
        "ON CREATE SET c.version = 0 "
        "SET c.version = c.version + 1, c.boilerplate = $grams, c.updated_at = $now "
        "RETURN c.version AS version, c.updated_at AS updated_at",
        id=RETRIEVAL_CONFIG_ID, grams=grams_sorted, now=now,
    ).single()
    return RetrievalConfig(version=int(rec["version"]), boilerplate=frozenset(grams_sorted), updated_at=rec["updated_at"])
