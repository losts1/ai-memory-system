"""Retrieval evaluation harness (spec §8).

Runs named rankers over a golden file, pools their top-N per query for one
judging pass, and reports exact-hit rate, nDCG@k, Recall@k and MRR per ranker.
The golden file lives OUTSIDE the repo (private content); pass --golden or set
AI_MEMORY_GOLDEN. A golden query the judge cannot grade fails the gate.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Callable, Dict, List, Optional

from ai_memory.eval.judge import JudgeCache, judge_query, mrr, ndcg_at_k, passes_ship_gate, recall_at_k

Ranker = Callable[[str, dict, int], List[dict]]
GOLDEN_ENV = "AI_MEMORY_GOLDEN"


def load_golden(path) -> List[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("golden file must be a JSON list")
    out = []
    for i, g in enumerate(data):
        if not isinstance(g, dict) or not isinstance(g.get("query"), str) or not isinstance(g.get("expect"), list):
            raise ValueError(f"golden entry at index {i} must have 'query' (str) and 'expect' (list)")
        out.append({"query": g["query"], "filters": dict(g.get("filters") or {}), "expect": list(g["expect"])})
    return out


def filter_golden(golden: list[dict], subset: str) -> list[dict]:
    if subset == "all":
        return golden
    if subset == "scoped":
        return [g for g in golden if g.get("filters")]
    if subset == "unscoped":
        return [g for g in golden if not g.get("filters")]
    raise ValueError(f"unknown subset: {subset!r}")


def exact_hit_rate(hits: List[dict], expect: List[str], k: int) -> float:
    if not expect:
        return 1.0
    top = {h.get("name") for h in hits[:k]}
    return len(top & set(expect)) / len(expect)


def openai_chat_call(url: str, model: str, timeout: float = 300.0) -> Callable[[list], str]:
    def call(messages):
        body = json.dumps({"model": model, "messages": messages, "stream": False, "think": False, "temperature": 0}).encode()
        req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)["choices"][0]["message"]["content"]
    return call


def _as_candidate(h: dict) -> dict:
    """Search hits carry ``teaser``, not ``summary``; render the text the
    judge sees from whichever is present."""
    return dict(h, summary=h.get("summary") or h.get("teaser") or "")


def make_rankers(driver, workspace) -> Dict[str, Ranker]:
    """legacy = today's post-filter path; hybrid_fallback = contract over the
    over-fetch path; hybrid_search = contract over SEARCH — it raises
    RuntimeError if the vector index falls back to over-fetch mid-call
    (fails loudly before phase 3 on an un-migrated index — that is the
    point of having it)."""
    from ai_memory import search as S
    from ai_memory.retrieval import RETURN_FIELDS, build_filters

    def legacy(query, filters, k):
        # Reproduce the pre-redesign behaviour: top-k from the index, then filter, then LIMIT k.
        from neo4j import Query
        emb = S._embed(query)
        if emb is None:
            return []
        where, params = build_filters(filters.get("assistant"), filters.get("space"), filters.get("trust"))
        cypher = ("CALL db.index.vector.queryNodes($index, $k, $vec) YIELD node AS f, score AS s\n"
                  + (f"WHERE {where}\n" if where else "") + f"RETURN {RETURN_FIELDS}\nORDER BY s DESC\nLIMIT $k")
        with driver.session() as session:
            rows = list(session.run(Query(cypher, timeout=S.get_query_timeout()),
                                    dict(params, index=os.getenv("NEO4J_VECTOR_INDEX", "fact_embeddings"), k=k, vec=emb)))
        return S._rows_to_hits(rows, "vec")

    def hybrid_fallback(query, filters, k):
        S.reset_fallback()
        with S._fallback_lock:
            S._fallback_state.update(active=True, index=os.getenv("NEO4J_VECTOR_INDEX", "fact_embeddings"), until=float("inf"), warned=True)
        try:
            return S.search_hybrid(query, workspace=workspace, k=k, driver=driver, **_kw(filters))
        finally:
            S.reset_fallback()

    def hybrid_search(query, filters, k):
        S.reset_fallback()
        hits = S.search_hybrid(query, workspace=workspace, k=k, driver=driver, **_kw(filters))
        if S._fallback_state.get("active"):
            raise RuntimeError(
                "hybrid_search: vector index fell back to over-fetch; migrate the index "
                "(phase 3) or drop this ranker from --rankers")
        return hits

    return {"legacy": legacy, "hybrid_fallback": hybrid_fallback, "hybrid_search": hybrid_search}


def _kw(filters: dict) -> dict:
    return {"assistant": filters.get("assistant"), "space": filters.get("space"), "trust": filters.get("trust")}


def evaluate(golden: List[dict], rankers: Dict[str, Ranker], judge_call, model: str,
             cache: Optional[JudgeCache], k: int = 5, pool_n: int = 10) -> dict:
    per: Dict[str, dict] = {n: {"exact5": [], "ndcg5": [], "recall5": [], "mrr": [], "unjudged": [],
                                "judged_queries": 0, "candidates": 0} for n in rankers}
    pool_total = 0
    for g in golden:
        runs = {n: r(g["query"], g["filters"], pool_n) for n, r in rankers.items()}
        pool: Dict[str, dict] = {}
        for hits in runs.values():
            for h in hits[:pool_n]:
                pool.setdefault(h["name"], _as_candidate(h))
        pool_total += len(pool)
        # An empty pool (no ranker retrieved anything) is unjudged, not "judged with
        # nDCG 0" — spec §8 says an unjudged golden fails closed (review #3).
        grades = judge_query(g["query"], list(pool.values()), judge_call, model=model, cache=cache) if pool else None
        for n, hits in runs.items():
            per[n]["candidates"] += len(hits)
            if grades is None:
                per[n]["unjudged"].append(g["query"])
                continue
            # exact5 is appended only for judged rows so every column shares the
            # judged_queries denominator (review #3).
            per[n]["exact5"].append(exact_hit_rate(hits, g["expect"], k))
            gmap = {name: j["grade"] for name, j in grades.items()}
            ranked = [h["name"] for h in hits]
            per[n]["ndcg5"].append(ndcg_at_k(ranked, gmap, k))
            per[n]["recall5"].append(recall_at_k(ranked, gmap, k))
            per[n]["mrr"].append(mrr(ranked, gmap))
            per[n]["judged_queries"] += 1
    def avg(xs):
        return (sum(xs) / len(xs)) if xs else 0.0

    out = {"per_ranker": {}, "pool_size": pool_total}
    for n, m in per.items():
        out["per_ranker"][n] = {"exact5": avg(m["exact5"]), "ndcg5": avg(m["ndcg5"]), "recall5": avg(m["recall5"]),
                                "mrr": avg(m["mrr"]), "unjudged": m["unjudged"], "judged_queries": m["judged_queries"],
                                "candidates": m["candidates"]}
    return out


def gate(before: dict, after: dict, ranker: str) -> bool:
    b, a = before["per_ranker"][ranker], after["per_ranker"][ranker]
    if a["unjudged"] or b["unjudged"]:
        return False
    if a["judged_queries"] == 0 or b["judged_queries"] == 0:
        return False
    if a["candidates"] == 0 or b["candidates"] == 0:
        return False
    return passes_ship_gate(
        before={"golden": {"ndcg5": b["exact5"], "recall5": b["exact5"]}, "judged": {"ndcg5": b["ndcg5"], "recall5": b["recall5"]}},
        after={"golden": {"ndcg5": a["exact5"], "recall5": a["exact5"]}, "judged": {"ndcg5": a["ndcg5"], "recall5": a["recall5"]}},
    )


def format_table(results: dict) -> str:
    lines = [f"{'ranker':<18} {'exact5':>7} {'ndcg5':>7} {'recall5':>8} {'mrr':>6} {'judged':>7} {'unjudged':>9} {'candidates':>10}"]
    for n, m in results["per_ranker"].items():
        lines.append(f"{n:<18} {m['exact5']:>7.2f} {m['ndcg5']:>7.2f} {m['recall5']:>8.2f} {m['mrr']:>6.2f} "
                     f"{m['judged_queries']:>7} {len(m['unjudged']):>9} {m.get('candidates', 0):>10}")
    lines.append(f"pooled candidates: {results.get('pool_size', 0)}")
    if "subset" in results and "queries" in results:
        lines.append(f"subset: {results['subset']}  queries: {results['queries']}")
    return "\n".join(lines)


def pool_candidates(queries: List[dict], rankers: Dict[str, Ranker], n: int = 10) -> List[dict]:
    out = []
    for g in queries:
        seen: Dict[str, dict] = {}
        for rname, r in rankers.items():
            for h in r(g["query"], dict(g.get("filters") or {}), n)[:n]:
                if h["name"] not in seen:
                    # M6: exactly {name, teaser, assistant, status, seen_in} —
                    # no score/via/summary leaking into the labelling skeleton.
                    seen[h["name"]] = {
                        "name": h["name"],
                        "teaser": (h.get("teaser") or h.get("summary") or "")[:200],
                        "assistant": h.get("assistant"),
                        "status": h.get("status"),
                        "seen_in": [],
                    }
                seen[h["name"]]["seen_in"].append(rname)
        out.append({"query": g["query"], "filters": dict(g.get("filters") or {}), "expect": [], "candidates": list(seen.values())})
    return out


def _open_driver(workspace):
    from ai_memory._config import get_driver
    return get_driver(workspace)


def _select_rankers(rankers, spec):
    names = [n.strip() for n in (spec or "").split(",") if n.strip()]
    chosen = {n: rankers[n] for n in names if n in rankers}
    if not chosen:
        print(f"warning: no ranker matched --rankers {spec!r}; using all: {', '.join(rankers)}", file=sys.stderr)
        return dict(rankers)
    unknown = [n for n in names if n not in rankers]
    if unknown:
        print(f"warning: unknown ranker(s) ignored: {', '.join(unknown)}", file=sys.stderr)
    return chosen


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="ai-memory eval", description="Retrieval evaluation against a golden set (spec §8)")
    ap.add_argument("--golden", default=os.getenv(GOLDEN_ENV), help=f"golden JSON path (default ${GOLDEN_ENV})")
    ap.add_argument("--rankers", default="legacy,hybrid_fallback,hybrid_search")
    ap.add_argument("--judge-url", default=os.getenv("AI_MEMORY_JUDGE_URL", "http://localhost:8080/v1/chat/completions"))
    ap.add_argument("--judge-model", default=os.getenv("AI_MEMORY_JUDGE_MODEL", "qwen3.8-27b-q6k"))
    ap.add_argument("--cache", default=str(Path.home() / ".ai-memory" / "judge_cache.json"))
    ap.add_argument("--label", action="store_true", help="print a labelling skeleton (pooled candidates) instead of evaluating")
    ap.add_argument("--json", dest="json_out", default=None, help="write results JSON here")
    ap.add_argument("--gate-against", default=None,
                    help="previous --json output; apply the ship gate (spec §8) and print gate: PASS|FAIL, exit 1 on FAIL")
    ap.add_argument("--gate-ranker", default="hybrid_search",
                    help="ranker compared by --gate-against (default hybrid_search; use hybrid_fallback before phase 3)")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--pool", type=int, default=10)
    ap.add_argument("--workspace", default=None)
    ap.add_argument("--subset", default="all", choices=["all", "scoped", "unscoped"],
                     help="restrict the golden set: scoped = non-empty filters, unscoped = empty filters")
    a = ap.parse_args(argv)
    if not a.golden:
        print(f"error: no golden file; pass --golden or set {GOLDEN_ENV}", file=sys.stderr)
        return 2
    try:
        golden = load_golden(a.golden)
        golden = filter_golden(golden, a.subset)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"error: golden file: {e}", file=sys.stderr)
        return 2
    driver = _open_driver(a.workspace)
    try:
        rankers = make_rankers(driver, a.workspace)
        chosen = _select_rankers(rankers, a.rankers)
        if a.label:
            print(json.dumps(pool_candidates(golden, chosen, a.pool), indent=2, ensure_ascii=False))
            return 0
        cache = JudgeCache(a.cache)
        res = evaluate(golden, chosen, openai_chat_call(a.judge_url, a.judge_model), model=a.judge_model, cache=cache, k=a.k, pool_n=a.pool)
        res["subset"] = a.subset
        res["queries"] = len(golden)
        print(format_table(res))
        if a.json_out:
            Path(a.json_out).write_text(json.dumps(res, indent=2), encoding="utf-8")
        if a.gate_against:
            before = json.loads(Path(a.gate_against).read_text(encoding="utf-8"))
            if a.gate_ranker not in before.get("per_ranker", {}) or a.gate_ranker not in res["per_ranker"]:
                print(f"error: --gate-ranker {a.gate_ranker!r} is not in both runs", file=sys.stderr)
                return 2
            passed = gate(before, res, a.gate_ranker)
            print(f"gate: {'PASS' if passed else 'FAIL'} ({a.gate_ranker})")
            return 0 if passed else 1
        return 0
    finally:
        close = getattr(driver, "close", None)
        if close:
            close()


if __name__ == "__main__":
    sys.exit(main())
