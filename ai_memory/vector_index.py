"""Vector index with filter properties (spec §4 "Index rebuild"): DDL/probe builders, wait-online,
pre-flight on a temporary `<index>_v2`, and the gated drop-and-recreate migration.

Measured on Neo4j 2026.04 (pre-flight 2026-09-05): the index name cannot be a parameter inside the
SEARCH clause (inlined, validated identifier); LIMIT may be a parameter; nodes lacking a declared
filter property are still indexed; population of 1,502 Facts takes ~1 s.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Sequence

from neo4j.exceptions import ClientError

from ai_memory.embed import embed_text
from ai_memory.retrieval import validate_index_name

DEFAULT_FILTER_PROPS = ("assistant", "space", "status", "provenance_trust")
EMBED_DIMS = 768
SIMILARITY = "cosine"
PREFLIGHT_QUERY = "kraken maker reserve balance guard"
CAPABILITY_PROBE_CYPHER = "CYPHER 25\nRETURN 1 AS ok"


def _first_line(e: Exception) -> str:
    s = str(e)
    return s.splitlines()[0] if s else s


def _prop(p: str) -> str:
    try:
        return validate_index_name(p)          # same identifier rule
    except ValueError:
        raise ValueError(f"invalid property name {p!r}: expected [A-Za-z_][A-Za-z0-9_]*") from None


def build_create_index_ddl(name: str, props: Sequence[str], *, dims: int = EMBED_DIMS, similarity: str = SIMILARITY) -> str:
    """Measured on Neo4j 2026.04: the `WITH [...]` filter-property list is Cypher 25 syntax —
    under the Cypher 5 default the server rejects it ("Invalid input 'WITH': expected 'OPTIONS'"),
    so the statement is prefixed with a literal `CYPHER 25` line (as build_probe_cypher already is)."""
    n = validate_index_name(name)
    if not props:
        raise ValueError("build_create_index_ddl requires at least one filter property")
    with_list = ", ".join(f"f.{_prop(p)}" for p in props)
    return (f"CYPHER 25\nCREATE VECTOR INDEX `{n}` FOR (f:Fact) ON (f.embedding) WITH [{with_list}] "
            f"OPTIONS {{indexConfig: {{`vector.dimensions`: {int(dims)}, `vector.similarity_function`: '{similarity}'}}}}")


def build_create_index_ddl_plain(name: str, *, dims: int = EMBED_DIMS, similarity: str = SIMILARITY) -> str:
    """Cypher 5 DDL (no `CYPHER 25` prefix, no `WITH [...]` filter-property list) for
    servers that reject Cypher 25 — an unfiltered vector index, same as before phase 3."""
    n = validate_index_name(name)
    return (f"CREATE VECTOR INDEX `{n}` FOR (f:Fact) ON (f.embedding) "
            f"OPTIONS {{indexConfig: {{`vector.dimensions`: {int(dims)}, `vector.similarity_function`: '{similarity}'}}}}")


def build_drop_ddl(name: str) -> str:
    return f"DROP INDEX `{validate_index_name(name)}` IF EXISTS"


def build_probe_cypher(name: str, where: str) -> str:
    n = validate_index_name(name)
    inner = f"WHERE {where} " if where else ""
    return ("CYPHER 25\nMATCH (f:Fact)\n"
            f"SEARCH f IN (VECTOR INDEX `{n}` FOR $vec {inner}LIMIT $pool) SCORE AS s\n"
            "RETURN f.name AS name, s")


def index_info(session, name: str) -> dict | None:
    rows = list(session.run(
        "SHOW INDEXES YIELD name, state, populationPercent, properties WHERE name = $n "
        "RETURN state, populationPercent, properties", n=name))
    if not rows:
        return None
    r = rows[0]
    return {"state": r["state"], "populationPercent": float(r["populationPercent"] or 0.0), "properties": list(r["properties"] or [])}


def wait_online(session, name: str, *, timeout_s: float = 600.0, poll_s: float = 0.5,
                clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> float:
    t0 = clock()
    while True:
        info = index_info(session, name)
        if info and info["state"] == "ONLINE" and info["populationPercent"] >= 100.0:
            return clock() - t0
        if clock() - t0 >= timeout_s:
            raise TimeoutError(f"index {name!r} not ONLINE/100% after {timeout_s}s: {info}")
        sleep(poll_s)


def embedded_count(session) -> int:
    return int(session.run("MATCH (f:Fact) WHERE f.embedding IS NOT NULL RETURN count(f) AS c").single()["c"])


def membership_check(session, name: str, vec: list) -> dict:
    n = embedded_count(session)
    rows = list(session.run(build_probe_cypher(name, ""), vec=vec, pool=max(n, 1)))
    returned_names = {r["name"] for r in rows}
    returned = len(returned_names)
    out = {"embedded": n, "returned": returned, "ok": returned == n}
    if returned != n:
        missing = list(session.run(
            "MATCH (f:Fact) WHERE f.embedding IS NOT NULL AND NOT f.name IN $names "
            "RETURN f.name AS name LIMIT 20", names=list(returned_names)))
        out["missing_sample"] = [r["name"] for r in missing]
    return out


def property_probes(session, name: str, vec: list, props: Sequence[str]) -> dict:
    out = {}
    for p in props:
        _prop(p)
        top = session.run(f"MATCH (f:Fact) WHERE f.{p} IS NOT NULL RETURN f.{p} AS v, count(*) AS c ORDER BY c DESC LIMIT 1").single()
        if top is None:
            out[p] = {"present": 0, "value": None, "rows": None, "ok": True}
            continue
        try:
            rows = list(session.run(build_probe_cypher(name, f"f.{p} = $v"), vec=vec, pool=5, v=top["v"]))
            out[p] = {"present": int(top["c"]), "value": top["v"], "rows": len(rows), "ok": len(rows) >= 1}
        except ClientError as e:
            out[p] = {"present": int(top["c"]), "value": top["v"], "rows": None, "ok": False, "error": _first_line(e)}
    return out


def _embed_or_fail(embed_fn, query):
    vec = embed_fn(query)
    if not vec:
        raise RuntimeError("embedding unavailable (Ollama down?); refusing to run index probes without a query vector")
    return vec


def preflight(driver, index: str, props: Sequence[str], embed_fn=embed_text, *, query: str = PREFLIGHT_QUERY, log=print) -> dict:
    """Create `<index>_v2` with the filter properties, wait, measure, probe, and ALWAYS drop it.

    If `index` already carries all of `props` (a previous --migrate already ran), returns
    an `already_migrated` report without touching anything or requiring an embedding."""
    base = validate_index_name(index)
    temp = f"{base}_v2"
    with driver.session() as s:
        info = index_info(s, base)
        if info and set(props) <= set(info.get("properties") or []):
            log(f"{base} already carries the filter properties; nothing to pre-flight")
            return {"ok": True, "already_migrated": True, "index": base, "props": list(props),
                    "show_properties": info.get("properties", [])}
        vec = _embed_or_fail(embed_fn, query)
        rep = {"index": base, "temp_index": temp, "props": list(props)}
        try:
            s.run(build_create_index_ddl(temp, props)).consume()
            rep["population_s"] = wait_online(s, temp)
            temp_info = index_info(s, temp) or {}
            rep["show_properties"] = temp_info.get("properties", [])
            rep["membership"] = membership_check(s, temp, vec)
            rep["probes"] = property_probes(s, temp, vec, props)
            log(f"preflight {temp}: population {rep['population_s']:.1f}s, membership {rep['membership']}, "
                f"probes ok={all(p['ok'] for p in rep['probes'].values())}")
        finally:
            s.run(build_drop_ddl(temp)).consume()
    rep["ok"] = bool(rep.get("membership", {}).get("ok")) and all(p["ok"] for p in rep.get("probes", {}).values())
    return rep


def _check_cypher25_capability(session) -> None:
    """Probe Cypher 25 support before the DROP; raise RuntimeError so migrate aborts
    before any change if the server would reject the filtered CREATE."""
    try:
        session.run(CAPABILITY_PROBE_CYPHER).consume()
    except ClientError as e:
        raise RuntimeError(
            f"server rejected Cypher 25; migration aborted before any change: {_first_line(e)}") from e


def migrate(driver, index: str, props: Sequence[str], embed_fn=embed_text, *, dry_run: bool = False,
            query: str = PREFLIGHT_QUERY, log=print) -> dict:
    """Drop and recreate `index` with the filter properties, wait until ONLINE/100%, then gate (§4 step 3).
    On gate failure the new index is left in place and rep['ok'] is False.

    Before the DROP, probes Cypher 25 support and aborts (no DROP/CREATE) if it is rejected.
    If the filtered CREATE fails after a successful DROP, attempts to recreate a plain
    (unfiltered) index and raises RuntimeError describing the outcome and the exact
    filtered CREATE statement to re-run."""
    base = validate_index_name(index)
    statements = [build_drop_ddl(base), build_create_index_ddl(base, props)]
    rep = {"index": base, "props": list(props), "dry_run": dry_run, "statements": statements}
    if dry_run:
        for st in statements:
            log(st)
        rep["ok"] = True
        return rep
    with driver.session() as s:
        # Same short-circuit as preflight(): a live index that already carries every
        # requested filter property is never dropped (review #5).
        info = index_info(s, base)
        if info and set(props) <= set(info.get("properties") or []):
            log(f"{base} already carries the filter properties; nothing to migrate")
            return {"ok": True, "already_migrated": True, "index": base, "props": list(props),
                    "dry_run": False, "show_properties": info.get("properties", [])}
    vec = _embed_or_fail(embed_fn, query)
    with driver.session() as s:
        _check_cypher25_capability(s)
        log(statements[0])
        s.run(statements[0]).consume()
        try:
            log(statements[1])
            s.run(statements[1]).consume()
        except Exception as e:
            recovered = False
            try:
                s.run(build_create_index_ddl_plain(base)).consume()
                recovered = True
            except Exception:  # noqa: BLE001 — recovery attempt; any failure just means recovered=False
                recovered = False
            raise RuntimeError(
                f"index {base!r} was dropped and the filtered CREATE failed "
                f"(recovered: {'yes' if recovered else 'no'}); "
                f"re-run this statement to restore the filters: {statements[1]}") from e
        rep["population_s"] = wait_online(s, base)
        info = index_info(s, base) or {}
        rep["show_properties"] = info.get("properties", [])
        rep["membership"] = membership_check(s, base, vec)
        rep["probes"] = property_probes(s, base, vec, props)
    rep["ok"] = rep["membership"]["ok"] and all(p["ok"] for p in rep["probes"].values())
    log(f"migrate {base}: population {rep['population_s']:.1f}s, membership {rep['membership']}, gate ok={rep['ok']}")
    return rep
