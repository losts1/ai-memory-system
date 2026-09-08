"""Judged edge sample for the phase-5 edge-layer gate.

RELATED_TO edges built by the new rule carry ``rule_version``; legacy edges
have none. This module samples a reproducible slice of edges, grades the
target side against the source with the judge's "edge" rubric, and reports
a gate: the new edges must not be judged worse than the legacy ones.

No network dependency: the Neo4j session and the judge's HTTP ``call`` are
both injected, so everything here is testable offline (see judge.py).
"""
from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path
from typing import Callable

from ai_memory.eval.harness import openai_chat_call
from ai_memory.eval.judge import JudgeCache, judge_fact_text, judge_query

_EDGE_CYPHER = (
    "MATCH (a:Fact)-[r:RELATED_TO]->(b:Fact) WHERE {filter} "
    "RETURN a.name AS a_name, a.summary AS a_summary, a.key_points AS a_key_points, "
    "a.assistant AS a_assistant, a.status AS a_status, "
    "b.name AS b_name, b.summary AS b_summary, b.key_points AS b_key_points, "
    "b.assistant AS b_assistant, b.status AS b_status, "
    "r.rule_version AS rule_version, r.weight AS weight"
)


def _normalize_key_points(kp) -> list:
    if kp is None:
        return []
    if isinstance(kp, str):
        return [kp]
    return list(kp)


def _fact_from_row(row, prefix: str) -> dict:
    return {
        "name": row[f"{prefix}_name"],
        "summary": row[f"{prefix}_summary"],
        "key_points": _normalize_key_points(row[f"{prefix}_key_points"]),
        "assistant": row[f"{prefix}_assistant"],
        "status": row[f"{prefix}_status"],
    }


def sample_edges(session, n: int, seed: int, *, rule_version: int | None = None, legacy: bool = False) -> list:
    """Reproducible sample of RELATED_TO edges matching the chosen filter.

    legacy=True -> rule_version IS NULL; rule_version=N -> = N; neither -> all.
    Rows are sorted by (a_name, b_name) before sampling so the sample is
    stable regardless of Neo4j's row order.
    """
    if legacy:
        filt, params = "r.rule_version IS NULL", {}
    elif rule_version is not None:
        filt, params = "r.rule_version = $rv", {"rv": rule_version}
    else:
        filt, params = "true", {}
    cypher = _EDGE_CYPHER.format(filter=filt)
    rows = list(session.run(cypher, params))
    rows.sort(key=lambda r: (r["a_name"], r["b_name"]))
    picked = random.Random(seed).sample(rows, min(n, len(rows)))
    return [
        {
            "a": _fact_from_row(r, "a"),
            "b": _fact_from_row(r, "b"),
            "rule_version": r["rule_version"],
            "weight": r["weight"],
        }
        for r in picked
    ]


def judge_edges(sample: list, call: Callable, *, model: str, cache: JudgeCache | None = None, seed: int = 0) -> dict:
    """Grade each edge's target against its source with the "edge" rubric.

    An unjudged edge (model failed twice, or the reply omitted the target)
    counts as missing, never as grade 0.
    """
    judged = 0
    related = 0
    direct = 0
    grades = []
    for edge in sample:
        a, b = edge["a"], edge["b"]
        result = judge_query(judge_fact_text(a), [b], call, model=model, cache=cache, seed=seed, rubric="edge")
        if not result or b["name"] not in result:
            continue
        j = result[b["name"]]
        grade = j["grade"]
        judged += 1
        if grade >= 1:
            related += 1
        if grade == 2:
            direct += 1
        grades.append({"a": a["name"], "b": b["name"], "grade": grade, "why": j["why"]})
    if cache is not None:
        cache.save()
    n = len(sample)
    return {
        "n": n,
        "judged": judged,
        "unjudged": n - judged,
        "related_share": (related / judged) if judged else None,
        "direct_share": (direct / judged) if judged else None,
        "grades": grades,
    }


def edge_gate(before: dict, after: dict) -> bool:
    """Phase-5 gate: the new edges must be fully judged and not worse."""
    return (
        after["unjudged"] == 0
        and after["judged"] > 0
        and before["judged"] > 0
        and after["related_share"] >= before["related_share"]
        and after["direct_share"] >= before["direct_share"]
    )


def _open_driver(workspace):
    from ai_memory._config import get_driver
    return get_driver(workspace)


def _filter_description(legacy: bool, rule_version: int | None) -> str:
    if legacy:
        return "legacy (rule_version IS NULL)"
    if rule_version is not None:
        return f"rule_version = {rule_version}"
    return "all edges"


def _fmt_share(x) -> str:
    return f"{x:.2f}" if x is not None else "n/a"


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(
        prog="ai-memory eval-edges",
        description="Judged RELATED_TO edge sample against the edge rubric (phase-5 gate)",
    )
    ap.add_argument("--sample", type=int, default=30)
    ap.add_argument("--seed", type=int, default=7)
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--legacy", action="store_true", help="sample only legacy edges (rule_version IS NULL)")
    group.add_argument("--rule-version", type=int, default=None, help="sample only edges with this rule_version")
    ap.add_argument("--judge-url", default=os.getenv("AI_MEMORY_JUDGE_URL", "http://localhost:8080/v1/chat/completions"))
    ap.add_argument("--judge-model", default=os.getenv("AI_MEMORY_JUDGE_MODEL", "qwen3.8-27b-q6k"))
    ap.add_argument("--cache", default=str(Path.home() / ".ai-memory" / "judge_cache.json"))
    ap.add_argument("--json", dest="json_out", default=None, help="write results JSON here")
    ap.add_argument("--gate-against", default=None, help="previous --json output; print gate: PASS|FAIL")
    ap.add_argument("--workspace", default=None)
    a = ap.parse_args(argv)

    driver = _open_driver(a.workspace)
    try:
        with driver.session() as session:
            sample = sample_edges(session, a.sample, a.seed, rule_version=a.rule_version, legacy=a.legacy)
        cache = JudgeCache(a.cache)
        call = openai_chat_call(a.judge_url, a.judge_model)
        result = judge_edges(sample, call, model=a.judge_model, cache=cache, seed=a.seed)

        print(f"filter: {_filter_description(a.legacy, a.rule_version)}")
        print(f"n={result['n']} judged={result['judged']} unjudged={result['unjudged']} "
              f"related_share={_fmt_share(result['related_share'])} direct_share={_fmt_share(result['direct_share'])}")
        for g in result["grades"][:5]:
            print(f"  {g['a']} -> {g['b']}: grade={g['grade']}  {g['why']}")

        if a.json_out:
            Path(a.json_out).write_text(json.dumps(result, indent=2), encoding="utf-8")

        if result["judged"] == 0:
            print("error: nothing was judged", file=sys.stderr)
            return 1

        if a.gate_against:
            before = json.loads(Path(a.gate_against).read_text(encoding="utf-8"))
            passed = edge_gate(before, result)
            print(f"gate: {'PASS' if passed else 'FAIL'}")
            return 0 if passed else 1
        return 0
    finally:
        close = getattr(driver, "close", None)
        if close:
            close()


if __name__ == "__main__":
    sys.exit(main())
