"""Edge layer (spec §7): tokenizer, TF-IDF/embedding pair score, picks, on-write maintenance, nightly rebuild.

Note: imports _chain, a private helper of ai_memory.retrieval; importing it inside the package is acceptable.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping

from ai_memory.retrieval import _chain, strip_time_suffix
from ai_memory.retrieval_config import RETRIEVAL_CONFIG_ID

STOP = frozenset([
    "the", "and", "for", "with", "from", "via", "per", "how", "but", "key", "what", "when", "why", "this", "that",
    "are", "was", "were", "not", "you", "your", "has", "have",
    "utc", "edt", "est", "pst", "pdt", "cst", "cdt", "gmt",
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
    "nova", "weft", "grok", "claude", "thread", "shared", "ntr"
])
SHORT = frozenset({"ai", "ml", "sql", "gpu", "nlp", "rl", "api", "cli", "qa", "etl"})
EDGE_K = 5
DUP_COS = 0.95
TOKEN_CAP = 24
_TOK = re.compile(r"[a-z0-9]+")


def _keep(w: str) -> bool:
    return w not in STOP and not w.isdigit() and (len(w) >= 3 or w in SHORT)


def tokenize(text: str, name: str = "", cap: int = TOKEN_CAP) -> list[str]:
    cnt = Counter(w for w in _TOK.findall((text or "").lower()) if _keep(w))
    name_toks = [w for w in dict.fromkeys(_TOK.findall((name or "").lower())) if w in cnt]
    rest = [w for w, _ in cnt.most_common() if w not in name_toks]
    return (name_toks + rest)[:cap]


def idf_map(df: Mapping[str, int], n: int) -> dict[str, float]:
    return {w: math.log(n / d) for w, d in df.items() if d > 0}


def _w(tok: str, idf: Mapping[str, float], default_idf: float) -> float:
    return idf.get(tok, default_idf)


def tfidf_norm(tokens: Iterable[str], idf: Mapping[str, float], default_idf: float) -> float:
    return math.sqrt(sum(_w(t, idf, default_idf) ** 2 for t in set(tokens)))


def tfidf_cosine(tokens_a, tokens_b, idf, norm_a: float, norm_b: float, default_idf: float) -> float:
    if not norm_a or not norm_b:
        return 0.0
    shared = set(tokens_a) & set(tokens_b)
    return sum(_w(t, idf, default_idf) ** 2 for t in shared) / (norm_a * norm_b)


def shared_keywords(tokens_a, tokens_b, idf, cap: int = 10) -> list[str]:
    shared = set(tokens_a) & set(tokens_b)
    return sorted(shared, key=lambda t: (-idf.get(t, 0.0), t))[:cap]


def zscore(v: float, mean: float, std: float) -> float:
    return (v - mean) / std if std else 0.0


def blend(t: float, c: float, base: Mapping[str, float]) -> float:
    return 0.5 * zscore(t, base["t_mean"], base["t_std"]) + 0.5 * zscore(c, base["c_mean"], base["c_std"])


def is_duplicate(name_a: str, name_b: str, cos: float,
                 supersedes: Mapping[str, set[str]] | None = None) -> bool:
    """``supersedes`` is the ``{new: {old, ...}}`` multimap from load_supersedes,
    so every twin of a keeper is recognised, not just the last edge loaded."""
    if strip_time_suffix(name_a) == strip_time_suffix(name_b):
        return True
    if cos >= DUP_COS:
        return True
    return bool(supersedes) and name_b in _chain(name_a, supersedes)


def pick(cands: Iterable[tuple[str, float, float, float]], floor: float, k: int = EDGE_K) -> list[tuple[str, float, float, float]]:
    ok = [c for c in cands if c[1] >= floor]
    ok.sort(key=lambda c: (-c[1], c[0]))
    return ok[:k]


def canonical_pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def edges_from_picks(picks: Mapping[str, list[tuple[str, float, float, float]]]) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for src, lst in picks.items():
        for other, b, t, c in lst:
            key = canonical_pair(src, other)
            e = out.setdefault(key, {"weight": b, "tfidf": t, "cos": c, "picked_by": []})
            if src not in e["picked_by"]:
                e["picked_by"].append(src)
    for e in out.values():
        e["picked_by"].sort()
        e["via"] = "both" if len(e["picked_by"]) == 2 else e["picked_by"][0]
    return out


# --- Neo4j I/O (spec §7.4-7.5): edge config, word index, edges, nightly rebuild ---------


def load_edge_config(session) -> dict | None:
    """The z-score baselines + edge floor published alongside RetrievalConfig, or None
    when the edge layer hasn't been built yet (rule_version missing/null)."""
    rec = session.run(
        "MATCH (c:RetrievalConfig {id: $id}) "
        "RETURN c.rule_version AS rule_version, c.edge_floor AS edge_floor, "
        "c.t_mean AS t_mean, c.t_std AS t_std, c.c_mean AS c_mean, c.c_std AS c_std, "
        "c.n_facts AS n_facts",
        id=RETRIEVAL_CONFIG_ID,
    ).single()
    if rec is None or rec["rule_version"] is None:
        return None
    return {
        "rule_version": int(rec["rule_version"]),
        "edge_floor": float(rec["edge_floor"]),
        "t_mean": float(rec["t_mean"]),
        "t_std": float(rec["t_std"]),
        "c_mean": float(rec["c_mean"]),
        "c_std": float(rec["c_std"]),
        "n_facts": int(rec["n_facts"]),
    }


def publish_edge_config(session, *, base: Mapping[str, float], edge_floor: float, n_facts: int,
                        pairs: int, seed: int) -> int:
    """MERGE the (:RetrievalConfig {id:'current'}) singleton, bumping rule_version. Never
    touches c.version/c.boilerplate (those belong to the embedding-side config)."""
    rec = session.run(
        "MERGE (c:RetrievalConfig {id: $id}) "
        "SET c.rule_version = coalesce(c.rule_version, 0) + 1, "
        "c.edge_floor = $edge_floor, c.t_mean = $t_mean, c.t_std = $t_std, "
        "c.c_mean = $c_mean, c.c_std = $c_std, c.baseline_pairs = $pairs, "
        "c.baseline_seed = $seed, c.n_facts = $n_facts, c.edges_updated_at = datetime() "
        "RETURN c.rule_version AS rule_version",
        id=RETRIEVAL_CONFIG_ID, edge_floor=edge_floor,
        t_mean=base["t_mean"], t_std=base["t_std"], c_mean=base["c_mean"], c_std=base["c_std"],
        pairs=pairs, seed=seed, n_facts=n_facts,
    ).single()
    return int(rec["rule_version"])


def write_fact_tokens(session, name: str, tokens: list[str], norm: float) -> None:
    """Replace one Fact's HAS_WORD edges and tfidf_norm. Later tasks call this on write;
    the nightly rebuild instead batches the whole corpus through one UNWIND per 500 Facts."""
    session.run(
        "MATCH (f:Fact {name: $name}) "
        "OPTIONAL MATCH (f)-[old:HAS_WORD]->() "
        "DELETE old "
        "WITH f "
        "SET f.tfidf_norm = $norm "
        "WITH f "
        "UNWIND $tokens AS t "
        "MERGE (w:Word {text: t}) "
        "MERGE (f)-[:HAS_WORD]->(w)",
        name=name, tokens=list(tokens), norm=norm,
    )


def _write_all_fact_tokens(session, items: list[dict], *, batch: int = 500) -> None:
    """items: [{"name", "tokens", "norm"}, ...] — one UNWIND per `batch` Facts."""
    stmt = (
        "UNWIND $rows AS r "
        "MATCH (f:Fact {name: r.name}) "
        "OPTIONAL MATCH (f)-[old:HAS_WORD]->() "
        "DELETE old "
        "WITH DISTINCT f, r "
        "SET f.tfidf_norm = r.norm "
        "WITH f, r "
        "UNWIND r.tokens AS t "
        "MERGE (w:Word {text: t}) "
        "MERGE (f)-[:HAS_WORD]->(w)"
    )
    for i in range(0, len(items), batch):
        session.run(stmt, rows=items[i:i + batch])


def write_idf(session, n_facts: int) -> int:
    rec = session.run(
        "MATCH (w:Word)<-[:HAS_WORD]-(f:Fact) "
        "WITH w, count(DISTINCT f) AS df "
        "SET w.df = df, w.idf = log(toFloat($n) / df) "
        "RETURN count(w) AS words",
        n=n_facts,
    ).single()
    return int(rec["words"]) if rec else 0


def write_edges(session, edges: Mapping[tuple[str, str], dict], rule_version: int, *, batch: int = 500) -> int:
    stmt = (
        "UNWIND $rows AS r "
        "MATCH (a:Fact {name: r.a}), (b:Fact {name: r.b}) "
        "MERGE (a)-[e:RELATED_TO]->(b) "
        "SET e.weight = r.weight, e.tfidf = r.tfidf, e.cos = r.cos, "
        "e.shared_keywords = r.shared, e.picked_by = r.picked_by, e.via = r.via, "
        "e.rule_version = $rv "
        "REMOVE e.shared_count, e.source "
        "RETURN count(e) AS n"
    )
    items = list(edges.items())
    written = 0
    for i in range(0, len(items), batch):
        rows = [
            {"a": pair[0], "b": pair[1], "weight": e["weight"], "tfidf": e["tfidf"], "cos": e["cos"],
             "shared": e.get("shared_keywords", []), "picked_by": e["picked_by"], "via": e["via"]}
            for pair, e in items[i:i + batch]
        ]
        rec = session.run(stmt, rows=rows, rv=rule_version).single()
        written += int(rec["n"]) if rec else 0
    return written


def cutover(session, rule_version: int) -> int:
    rec = session.run(
        "MATCH ()-[e:RELATED_TO]->() WHERE e.rule_version IS NULL OR e.rule_version <> $rv "
        "DELETE e "
        "RETURN count(e) AS n",
        rv=rule_version,
    ).single()
    return int(rec["n"]) if rec else 0


def cleanup_orphan_words(session) -> int:
    rec = session.run(
        "MATCH (w:Word) WHERE NOT (w)<-[:HAS_WORD]-(:Fact) "
        "DELETE w "
        "RETURN count(w) AS n"
    ).single()
    return int(rec["n"]) if rec else 0


def _percentile(values: list, p: float) -> float:
    """Linear-interpolation percentile (numpy's default method), no numpy required."""
    if not values:
        return 0.0
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    idx = (p / 100.0) * (len(s) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(s) - 1)
    return float(s[lo] + (s[hi] - s[lo]) * (idx - lo))


def edge_stats(session) -> dict:
    rec = session.run(
        "OPTIONAL MATCH (c:RetrievalConfig {id: $id}) "
        "WITH coalesce(c.rule_version, 0) AS rv "
        "OPTIONAL MATCH ()-[e:RELATED_TO]->() "
        "WITH rv, count(e) AS edges, sum(CASE WHEN e.rule_version = rv THEN 1 ELSE 0 END) AS edges_current_rule "
        "RETURN rv AS rule_version, edges, edges_current_rule",
        id=RETRIEVAL_CONFIG_ID,
    ).single()
    rule_version = int(rec["rule_version"]) if rec and rec["rule_version"] is not None else 0
    edges = int(rec["edges"]) if rec and rec["edges"] is not None else 0
    edges_current_rule = int(rec["edges_current_rule"]) if rec and rec["edges_current_rule"] is not None else 0
    edges_stale_rule = edges - edges_current_rule

    degrees = [int(r["degree"]) for r in session.run(
        "MATCH (f:Fact) RETURN COUNT { (f)-[:RELATED_TO]-() } AS degree"
    )]
    n_facts = len(degrees)
    isolated = sum(1 for d in degrees if d == 0)
    isolated_pct = (isolated / n_facts * 100.0) if n_facts else 0.0
    max_degree = max(degrees) if degrees else 0
    p95_degree = _percentile(degrees, 95)
    return {
        "edges": edges, "edges_current_rule": edges_current_rule, "edges_stale_rule": edges_stale_rule,
        "rule_version": rule_version, "isolated": isolated, "isolated_pct": isolated_pct,
        "max_degree": max_degree, "p95_degree": p95_degree, "n_facts": n_facts,
    }


def maintain_edges_for(session, name: str, edge_cfg: dict, *, k: int = EDGE_K, log=None) -> dict:
    """On-write edge maintenance for one Fact (spec §7.4), no numpy required.

    X (the just-written Fact `name`) picks its own top-`k` neighbours above the corpus
    floor; any neighbour g whose own picks are weaker than X's blend score re-picks X,
    un-picking g's weakest pick when that would push it past `k`. A g that already has
    X among its own picks just gets its weight refreshed (no eviction, not counted as a
    re-pick). Finally, any edge X previously picked but no longer does has X revoked
    from `picked_by` (deleted if that empties it). Returns {"picked", "repicked",
    "deleted", "revoked", "skipped"} — see module docstring / task brief for the exact
    contract. Issues no writes when `skipped` is set.
    """
    empty = {"picked": 0, "repicked": 0, "deleted": 0, "revoked": 0}
    if not edge_cfg:
        return {**empty, "skipped": "no_config"}

    rec = session.run(
        "MATCH (f:Fact {name: $n}) "
        "RETURN f.embedding IS NOT NULL AS has_emb, [(f)-[:HAS_WORD]->(w) | w.text] AS toks, "
        "f.tfidf_norm AS norm",
        n=name,
    ).single()
    if rec is None:
        return {**empty, "skipped": "missing"}
    if not rec["has_emb"]:
        return {**empty, "skipped": "no_embedding"}
    toks_x = list(rec["toks"] or [])

    default_idf = math.log(max(edge_cfg["n_facts"], 2))

    idf1 = {
        r["text"]: r["idf"]
        for r in session.run(
            "MATCH (w:Word) WHERE w.text IN $toks RETURN w.text AS text, w.idf AS idf", toks=toks_x,
        )
        if r["idf"] is not None
    }
    norm_x = tfidf_norm(toks_x, idf1, default_idf)
    write_fact_tokens(session, name, toks_x, norm_x)

    # vector.similarity.cosine returns the server's *normalised* similarity (1+cos)/2
    # (verified on the live Neo4j 2026.04: orthogonal -> 0.5, opposite -> 0.0), not raw
    # cosine — de-normalise it here so `cos` lands on the same axis as rebuild_edges'
    # raw-cosine baselines (c_mean/c_std) and DUP_COS. Note: the server computes this
    # in float32 while rebuild_edges' numpy path is float64 — measured max deviation
    # 3.6e-07 (<=2.5e-06 in `blend`), far below any pick margin but not bit-identical.
    rows = [dict(r) for r in session.run(
        "MATCH (f:Fact {name: $n}) MATCH (g:Fact) WHERE g <> f AND g.embedding IS NOT NULL "
        "RETURN g.name AS name, 2 * vector.similarity.cosine(f.embedding, g.embedding) - 1 AS cos, "
        "[(g)-[:HAS_WORD]->(w) | w.text] AS toks, g.tfidf_norm AS norm",
        n=name,
    )]
    toks_by_name = {r["name"]: list(r["toks"] or []) for r in rows}

    union_toks = set(toks_x)
    for toks in toks_by_name.values():
        union_toks.update(toks)
    idf2 = {
        r["text"]: r["idf"]
        for r in session.run(
            "MATCH (w:Word) WHERE w.text IN $toks RETURN w.text AS text, w.idf AS idf",
            toks=sorted(union_toks),
        )
        if r["idf"] is not None
    }

    try:
        from ai_memory.search import load_supersedes
        supersedes = load_supersedes(session)
    except Exception:  # noqa: BLE001 — best-effort, mirrors load_supersedes' own contract
        supersedes = {}

    cands: list[tuple[str, float, float, float]] = []
    for r in rows:
        g_name = r["name"]
        cos = float(r["cos"]) if r["cos"] is not None else 0.0
        if is_duplicate(name, g_name, cos, supersedes):
            continue
        toks_g = toks_by_name[g_name]
        norm_g = r["norm"]
        norm_g = float(norm_g) if norm_g is not None else tfidf_norm(toks_g, idf2, default_idf)
        t = tfidf_cosine(toks_x, toks_g, idf2, norm_x, norm_g, default_idf)
        b = blend(t, cos, edge_cfg)
        cands.append((g_name, b, t, cos))

    x_picks = pick(cands, edge_cfg["edge_floor"], k)
    ok = [c for c in cands if c[1] >= edge_cfg["edge_floor"]]

    # Which of `ok` already have X among their own picks? Those get a weight-only
    # update below (no eviction, not counted as a re-pick) — batched in one query so
    # the per-g loop below never has to look at X's own edge to decide this.
    already_picks_x: set[str] = set()
    if ok:
        for r in session.run(
            "UNWIND $names AS g "
            "MATCH (x:Fact {name: $n})-[e:RELATED_TO]-(o:Fact {name: g}) "
            "WHERE g IN coalesce(e.picked_by, []) "
            "RETURN o.name AS g",
            n=name, names=[c[0] for c in ok],
        ):
            already_picks_x.add(r["g"])

    repicked: list[tuple[str, float, float, float]] = []
    weight_updates: list[tuple[str, float, float, float]] = []
    unpicks: list[tuple[str, str]] = []
    for g_name, b, t, c in ok:
        if g_name in already_picks_x:
            weight_updates.append((g_name, b, t, c))
            continue
        current = [
            (r["other"], r["weight"])
            for r in session.run(
                "MATCH (g:Fact {name: $g})-[e:RELATED_TO]-(o:Fact) "
                "WHERE $g IN coalesce(e.picked_by, []) AND o.name <> $n "
                "RETURN o.name AS other, e.weight AS weight ORDER BY e.weight ASC",
                g=g_name, n=name,
            )
        ]
        if len(current) < k:
            repicked.append((g_name, b, t, c))
        elif b > current[0][1]:
            repicked.append((g_name, b, t, c))
            unpicks.append((g_name, current[0][0]))

    edges: dict[tuple[str, str], dict] = {}
    for g_name, b, t, c in x_picks:
        pair = canonical_pair(name, g_name)
        e = edges.setdefault(pair, {
            "weight": b, "tfidf": t, "cos": c, "picked_by": set(),
            "shared_keywords": shared_keywords(toks_x, toks_by_name[g_name], idf2),
        })
        e["picked_by"].add(name)
    for g_name, b, t, c in repicked:
        pair = canonical_pair(name, g_name)
        e = edges.setdefault(pair, {
            "weight": b, "tfidf": t, "cos": c, "picked_by": set(),
            "shared_keywords": shared_keywords(toks_x, toks_by_name[g_name], idf2),
        })
        e["picked_by"].add(g_name)
    for g_name, b, t, c in weight_updates:
        pair = canonical_pair(name, g_name)
        e = edges.setdefault(pair, {
            "weight": b, "tfidf": t, "cos": c, "picked_by": set(),
            "shared_keywords": shared_keywords(toks_x, toks_by_name[g_name], idf2),
        })
        e["weight"], e["tfidf"], e["cos"] = b, t, c
        e["picked_by"].add(g_name)

    if edges:
        pairs_param = [{"a": a, "b": b} for a, b in edges]
        for r in session.run(
            "UNWIND $pairs AS p "
            "MATCH (a:Fact {name: p.a})-[e:RELATED_TO]-(b:Fact {name: p.b}) "
            "RETURN p.a AS a, p.b AS b, coalesce(e.picked_by, []) AS picked_by",
            pairs=pairs_param,
        ):
            pair = (r["a"], r["b"])
            if pair in edges and r["picked_by"]:
                edges[pair]["picked_by"].update(r["picked_by"])
        for e in edges.values():
            e["picked_by"] = sorted(e["picked_by"])
            e["via"] = "both" if len(e["picked_by"]) == 2 else e["picked_by"][0]
        write_edges(session, edges, edge_cfg["rule_version"])

    deleted = 0
    for g_name, weakest in unpicks:
        r = session.run(
            "MATCH (g:Fact {name: $g})-[e:RELATED_TO]-(o:Fact {name: $weakest}) "
            "SET e.picked_by = [p IN coalesce(e.picked_by, []) WHERE p <> $g] "
            "WITH e, size(e.picked_by) AS remaining "
            "SET e.via = CASE remaining WHEN 2 THEN 'both' WHEN 1 THEN e.picked_by[0] ELSE null END "
            "WITH e, remaining WHERE remaining = 0 "
            "DELETE e "
            "RETURN remaining",
            g=g_name, weakest=weakest,
        ).single()
        if r is not None:
            deleted += 1

    # Revoke X from any edge it previously picked but no longer does (spec §7.4: the
    # written Fact's picks are recomputed each time, not just added to). `collect()`
    # drops nulls, so `empties` is exactly the now-orphaned edges regardless of how
    # many (zero included) matched — no UNWIND-over-possibly-empty-list pitfall.
    keep = [g_name for g_name, *_ in x_picks]
    rrec = session.run(
        "MATCH (x:Fact {name: $n})-[e:RELATED_TO]-(o:Fact) "
        "WHERE $n IN coalesce(e.picked_by, []) AND NOT o.name IN $keep "
        "SET e.picked_by = [p IN coalesce(e.picked_by, []) WHERE p <> $n] "
        "WITH e, size(e.picked_by) AS remaining "
        "SET e.via = CASE remaining WHEN 2 THEN 'both' WHEN 1 THEN e.picked_by[0] ELSE null END "
        "WITH collect(e) AS revoked_edges, collect(CASE WHEN remaining = 0 THEN e END) AS empties "
        "FOREACH (d IN empties | DELETE d) "
        "RETURN size(revoked_edges) AS revoked, size(empties) AS deleted",
        n=name, keep=keep,
    ).single()
    revoked = int(rrec["revoked"]) if rrec else 0
    deleted += int(rrec["deleted"]) if rrec else 0

    result = {"picked": len(x_picks), "repicked": len(repicked), "deleted": deleted,
              "revoked": revoked, "skipped": None}
    if log is not None:
        log(f"maintain_edges_for({name}): {result}")
    return result


_CORPUS_ROWS = (
    "MATCH (f:Fact) RETURN f.name AS name, f.summary AS summary, f.key_points AS key_points, "
    "f.content AS content, f.embedding AS embedding, f.status AS status"
)


def rebuild_edges(driver, *, seed: int = 0, pairs: int = 40000, k: int = EDGE_K,
                  dry_run: bool = False, log=print) -> dict:
    """Nightly full rebuild of the word index + RELATED_TO edge layer (spec §7.5)."""
    try:
        import numpy as np
    except ImportError as e:
        raise RuntimeError(
            "numpy is required for the nightly edge rebuild: pip install 'ai-memory-system[edges]'"
        ) from e

    from ai_memory.embed import fact_embed_text
    from ai_memory.retrieval_config import load_retrieval_config
    from ai_memory.search import load_supersedes

    with driver.session() as s:
        cfg = load_retrieval_config(s)
        if cfg is None:
            raise RuntimeError("no RetrievalConfig node; run ai-memory embed --all (publish) first")
        rows = [dict(r) for r in s.run(_CORPUS_ROWS)]
        supersedes = load_supersedes(s)

        rule_version_next = (cfg.rule_version or 0) + 1
        names = [r["name"] for r in rows]
        name_idx = {nm: i for i, nm in enumerate(names)}
        n = len(names)
        embedded_count = sum(1 for r in rows if r.get("embedding"))
        if n < 2 or embedded_count < 2:
            raise RuntimeError(
                f"edge rebuild needs at least 2 embedded Facts (got N={n}, embedded={embedded_count})"
            )
        texts = [fact_embed_text(r["name"], r["summary"], r["key_points"], r["content"], cfg.boilerplate)
                 for r in rows]
        tokens_list = [tokenize(t, r["name"]) for t, r in zip(texts, rows)]

        df: Counter = Counter()
        for toks in tokens_list:
            df.update(set(toks))
        idf = idf_map(df, n)
        vocab = {w: i for i, w in enumerate(sorted(idf))}
        vsize = len(vocab)

        M = np.zeros((n, vsize), dtype=float)
        for i, toks in enumerate(tokens_list):
            for t in set(toks):
                if t in vocab:
                    M[i, vocab[t]] = idf[t]
        row_norm = np.linalg.norm(M, axis=1)
        Mn = M / np.maximum(row_norm, 1e-9)[:, None]
        T = Mn @ Mn.T
        np.fill_diagonal(T, 0)

        dim = 0
        for r in rows:
            if r.get("embedding"):
                dim = len(r["embedding"])
                break
        E = np.zeros((n, dim), dtype=float)
        has = np.zeros(n, dtype=bool)
        for i, r in enumerate(rows):
            emb = r.get("embedding")
            if emb:
                if len(emb) != dim:
                    raise RuntimeError(
                        f"edge rebuild: Fact {r['name']!r} has embedding length {len(emb)}, "
                        f"expected {dim} (from the first embedded Fact)"
                    )
                v = np.array(emb, dtype=float)
                vnorm = np.linalg.norm(v)
                if vnorm > 0:
                    E[i, :len(v)] = v / vnorm
                    has[i] = True
        C = E @ E.T
        C[~has, :] = 0
        C[:, ~has] = 0
        np.fill_diagonal(C, 0)

        rng = np.random.default_rng(seed)
        ii = rng.integers(0, n, pairs)
        jj = rng.integers(0, n, pairs)
        mask = (ii != jj) & has[ii] & has[jj]
        ii, jj = ii[mask], jj[mask]
        if ii.size == 0:
            raise RuntimeError(
                f"edge rebuild needs at least 2 embedded Facts (got N={n}, embedded={int(has.sum())})"
            )

        t_mean = float(T[ii, jj].mean())
        t_std = float(T[ii, jj].std() + 1e-9)
        c_mean = float(C[ii, jj].mean())
        c_std = float(C[ii, jj].std() + 1e-9)
        zT = (T - t_mean) / t_std
        zC = (C - c_mean) / c_std
        B = 0.5 * zT + 0.5 * zC
        np.fill_diagonal(B, -99)
        edge_floor = float(np.percentile(B[ii, jj], 99))

        order = np.argsort(-B, axis=1)
        picks: dict[str, list[tuple[str, float, float, float]]] = {}
        for i, name in enumerate(names):
            cands: list[tuple[str, float, float, float]] = []
            for j in order[i]:
                j = int(j)
                if j == i:
                    continue
                bij = float(B[i, j])
                if bij < edge_floor:
                    break
                if is_duplicate(name, names[j], float(C[i, j]), supersedes):
                    continue
                cands.append((names[j], bij, float(T[i, j]), float(C[i, j])))
                if len(cands) >= k:
                    break
            picks[name] = cands

        edges = edges_from_picks(picks)
        for (a, b), e in edges.items():
            e["shared_keywords"] = shared_keywords(tokens_list[name_idx[a]], tokens_list[name_idx[b]], idf)

        degree: Counter = Counter()
        for a, b in edges:
            degree[a] += 1
            degree[b] += 1
        degrees = [degree.get(nm, 0) for nm in names]
        isolated = sum(1 for d in degrees if d == 0)
        isolated_pct = (isolated / n * 100.0) if n else 0.0

        # edge_list is the JSON-serialisable form of `edges` (whose keys are (a, b) tuples):
        # a list of dicts sorted by (a, b), each carrying the pair's own name pair.
        edge_list = [
            {"a": a, "b": b, "weight": e["weight"], "tfidf": e["tfidf"], "cos": e["cos"],
             "picked_by": e["picked_by"], "via": e["via"], "shared_keywords": e["shared_keywords"]}
            for (a, b), e in sorted(edges.items())
        ]

        base = {"t_mean": t_mean, "t_std": t_std, "c_mean": c_mean, "c_std": c_std}
        report = {
            "n_facts": n, "vocab": vsize,
            "edge_floor": edge_floor, "base": base, "edges": len(edges), "edge_list": edge_list,
            "isolated": isolated, "isolated_pct": isolated_pct,
            "max_degree": max(degrees) if degrees else 0, "p95_degree": _percentile(degrees, 95),
            "dry_run": dry_run,
        }
        log(f"rebuild_edges: {n} facts, vocab={vsize}, edge_floor={edge_floor:.4f}, "
            f"edges={len(edges)}, isolated={isolated} ({isolated_pct:.1f}%)")
        if dry_run:
            # Informational prediction only — nothing has been published yet, so this is the
            # best guess of what publish_edge_config would return. Write mode reports the real,
            # returned rule_version instead (set below); the two keys are mutually exclusive.
            report["rule_version_next"] = rule_version_next
            return report

        if not edges:
            raise RuntimeError("edge rebuild produced no edges; refusing to cut over")

        _write_all_fact_tokens(s, [
            {"name": names[i], "tokens": tokens_list[i], "norm": float(row_norm[i])} for i in range(n)
        ])
        write_idf(s, n)
        rv = publish_edge_config(s, base=base, edge_floor=edge_floor, n_facts=n, pairs=pairs, seed=seed)
        edges_written = write_edges(s, edges, rv)
        edges_deleted = cutover(s, rv)
        words_orphaned = cleanup_orphan_words(s)

        report["rule_version"] = rv
        report["edges_written"] = edges_written
        report["edges_deleted"] = edges_deleted
        report["words_orphaned"] = words_orphaned
        return report
