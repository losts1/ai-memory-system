"""Pure logic for the duplicate-Facts owner report (phase 6, task 1).

No I/O and no driver here — grouping, keeper selection, and markdown rendering
are all pure functions over plain dicts, exercised by tests/test_duplicates.py.
Task 2 layers Neo4j reads/writes on top of exactly these signatures.

Generated supersede commands take the form
``ai-memory supersede <keeper> <other> --apply``, with each name passed
through ``shlex.quote`` so the command is safe to copy-paste into a shell
regardless of spaces, quotes, or other shell-special characters in the name.
"""
from __future__ import annotations

import os
import re
import shlex
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone

from ai_memory.retrieval import (
    _TRAILING_SUFFIX, as_supersedes_multimap, strip_time_suffix, validate_index_name,
)
from ai_memory.search import load_supersedes
from ai_memory.wordindex import DUP_COS, canonical_pair

__all__ = [
    "DUP_COS",
    "build_report",
    "canonical_pair",
    "choose_keeper",
    "duplicate_report",
    "is_handled",
    "load_fact_meta",
    "load_supersedes_strict",
    "merge_groups",
    "name_suffix",
    "name_time_key",
    "near_copy_pairs",
    "plan_supersedes",
    "render_markdown",
    "suffix_groups",
    "supersede_fact",
]

_LIVE_EXCLUDE = ("superseded", "removed")

_TIME_RE = re.compile(r"(\d{1,2}:\d{2})")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_HASH_RE = re.compile(r"#(\d+)\s*$")


def name_time_key(name: str) -> str | None:
    """Sortable key for the trailing suffix on ``name``, if it names a date.

    A clock-only suffix (e.g. ``"(21:01 ET)"``) carries no day and must not
    decide a keeper, so it returns None. Only a suffix containing a date
    (``YYYY-MM-DD``) yields a key, optionally refined by a clock time also
    present in the suffix (zero-padded ``THH:MM``) and/or a ``#n`` counter
    (zero-padded ``#nn``) — all in a fixed order so every returned key sorts
    correctly as a plain string.
    """
    match = _TRAILING_SUFFIX.search(name or "")
    if not match:
        return None
    suffix = match.group(0)
    date_match = _DATE_RE.search(suffix)
    if not date_match:
        return None
    key = date_match.group(1)
    time_match = _TIME_RE.search(suffix)
    if time_match:
        hour, minute = time_match.group(1).split(":")
        key += f"T{int(hour):02d}:{minute}"
    hash_match = _HASH_RE.search(suffix)
    if hash_match:
        key += f"#{int(hash_match.group(1)):02d}"
    return key


def name_suffix(name: str) -> str:
    """Raw trailing time/date suffix text on ``name``, stripped and without
    the leading dash/space, or "" when ``name`` has no such suffix."""
    match = _TRAILING_SUFFIX.search(name or "")
    if not match:
        return ""
    suffix = match.group(0).strip()
    if suffix and suffix[0] in "—-":
        suffix = suffix[1:].strip()
    return suffix


def suffix_groups(names: Iterable[str]) -> dict[str, list[str]]:
    """Group names by their time-suffix-stripped base name (lowercased)."""
    buckets: dict[str, list[str]] = {}
    for name in names:
        base = strip_time_suffix(name).lower()
        buckets.setdefault(base, []).append(name)
    return {base: sorted(members) for base, members in buckets.items() if len(members) >= 2}


def merge_groups(
    suffix: Mapping[str, list[str]], pairs: Iterable[tuple[str, str, float]]
) -> list[dict]:
    """Union-find over suffix groups and cosine pairs into merged duplicate groups."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    suffix_names: set[str] = set()
    for members in suffix.values():
        suffix_names.update(members)
        first = members[0]
        for other in members[1:]:
            union(first, other)

    cosine_names: set[str] = set()
    cos_by_pair: dict[tuple[str, str], float] = {}
    for a, b, cos in pairs:
        cosine_names.add(a)
        cosine_names.add(b)
        union(a, b)
        cos_by_pair[(a, b)] = cos

    roots: dict[str, set[str]] = {}
    for name in parent:
        roots.setdefault(find(name), set()).add(name)

    out = []
    for members in roots.values():
        if len(members) < 2:
            continue
        signals = set()
        if members & suffix_names:
            signals.add("suffix")
        if members & cosine_names:
            signals.add("cosine")
        cos = {
            f"{a}|{b}": c
            for (a, b), c in cos_by_pair.items()
            if a in members and b in members
        }
        out.append(
            {
                "members": sorted(members, key=lambda n: (strip_time_suffix(n), n)),
                "signals": sorted(signals),
                "cos": cos,
            }
        )
    out.sort(key=lambda g: g["members"][0])
    return out


def _break_tie(candidates: list[dict], reason: str) -> tuple[str | None, str | None]:
    if len(candidates) > 1:
        live = [m for m in candidates if m.get("status") not in _LIVE_EXCLUDE]
        if live:
            candidates = live
    if len(candidates) > 1:
        winner = max(m["name"] for m in candidates)
    else:
        winner = candidates[0]["name"]
    return winner, reason


def choose_keeper(members: list[dict]) -> tuple[str | None, str | None]:
    """Pick the surviving Fact of a duplicate group, or flag it for the owner.

    Order: newest dated name suffix (name_time_key — a clock-only suffix
    carries no day and is never a candidate here), else newest ``updated_at``,
    else newest ``created_at``, else the only live member, else the
    lexicographically last name.

    "Live" means the same thing here as it does to ``is_handled``,
    ``build_report`` and ``retrieval._is_active``: a status not in
    ``_LIVE_EXCLUDE``, so a NULL status (every library Fact) counts as live."""
    assistants = {m.get("assistant") for m in members}
    spaces = {m.get("space") for m in members}
    if len(assistants) > 1 or len(spaces) > 1:
        return None, "needs_owner_decision"

    timed = [(name_time_key(m["name"]), m) for m in members]
    timed = [(k, m) for k, m in timed if k is not None]
    if timed:
        best = max(k for k, _ in timed)
        return _break_tie([m for k, m in timed if k == best], "newest dated name suffix")

    updated = [m for m in members if m.get("updated_at")]
    if updated:
        best = max(str(m["updated_at"]) for m in updated)
        return _break_tie([m for m in updated if str(m["updated_at"]) == best], "newest updated_at")

    created = [m for m in members if m.get("created_at")]
    if created:
        best = max(str(m["created_at"]) for m in created)
        return _break_tie([m for m in created if str(m["created_at"]) == best], "newest created_at")

    live = [m for m in members if m.get("status") not in _LIVE_EXCLUDE]
    if len(live) == 1:
        return live[0]["name"], "only live member"

    return max(m["name"] for m in members), "no timestamps; lexicographically last"


def is_handled(members: list[dict], supersedes: Mapping[str, set[str]]) -> bool:
    """True when a duplicate group needs no owner action any more.

    ``supersedes`` is the ``{new: {old, ...}}`` multimap, so a keeper that
    supersedes several members of the group resolves all of them."""
    live = [m for m in members if m.get("status") not in _LIVE_EXCLUDE]
    if len(live) <= 1:
        return True
    names = {m["name"] for m in members}
    valid_old = {
        old
        for new, olds in as_supersedes_multimap(supersedes).items() if new in names
        for old in olds if old in names
    }
    return len(names - valid_old) <= 1


def build_report(
    groups: list[dict],
    meta: Mapping[str, dict],
    supersedes: Mapping[str, set[str]],
    *,
    include_handled: bool = False,
) -> dict:
    """Assemble the owner-facing duplicate report from merged groups + Fact metadata."""
    out_groups: list[dict] = []
    handled_count = 0
    needs_owner_count = 0
    facts_count = 0
    commands_count = 0

    for g in groups:
        names = sorted(g["members"])
        members_meta = [
            dict(meta[n]) if n in meta else {"name": n, "meta_missing": True} for n in names
        ]
        for m in members_meta:
            m["name_time_key"] = name_time_key(m["name"])
            m["name_suffix"] = name_suffix(m["name"])

        handled = is_handled(members_meta, supersedes)
        keeper, reason = choose_keeper(members_meta)
        if handled:
            handled_count += 1

        commands: list[str] = []
        if not handled and keeper is not None:
            for m in members_meta:
                if m["name"] != keeper and m.get("status") not in _LIVE_EXCLUDE:
                    commands.append(
                        f"ai-memory supersede {shlex.quote(keeper)} {shlex.quote(m['name'])} --apply"
                    )

        out_group = {
            "members": members_meta,
            "signals": g.get("signals", []),
            "cos": g.get("cos", {}),
            "handled": handled,
            "keeper": keeper,
            "reason": reason,
            "commands": commands,
        }

        if handled and not include_handled:
            continue

        if keeper is None:
            needs_owner_count += 1
        out_groups.append(out_group)
        facts_count += len(members_meta)
        commands_count += len(commands)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "n_facts": len(meta),
        "groups": out_groups,
        "summary": {
            "groups": len(out_groups),
            "facts": facts_count,
            "handled": handled_count,
            "needs_owner_decision": needs_owner_count,
            "suggested_commands": commands_count,
        },
    }


def _md_cell(value: object) -> str:
    """Escape ``|`` so a value can't split a markdown table row into extra cells."""
    return str(value).replace("|", "\\|")


def render_markdown(report: dict) -> str:
    """Render a ``build_report`` result as a markdown document."""
    s = report["summary"]
    lines = [
        "# Duplicate Facts report",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "| Groups | Facts | Handled | Needs owner decision | Suggested commands |",
        "|---|---|---|---|---|",
        f"| {s['groups']} | {s['facts']} | {s['handled']} | {s['needs_owner_decision']} | {s['suggested_commands']} |",
        "",
    ]

    for i, g in enumerate(report["groups"], start=1):
        lines.append(f"## Group {i} — signals: {', '.join(g['signals'])}")
        lines.append("")
        lines.append("| Name | Assistant | Space | Status | Created | Updated | Suffix |")
        lines.append("|---|---|---|---|---|---|---|")
        for m in g["members"]:
            lines.append(
                "| {name} | {assistant} | {space} | {status} | {created} | {updated} | {suffix} |".format(
                    name=_md_cell(m.get("name", "")),
                    assistant=_md_cell(m.get("assistant") or ""),
                    space=_md_cell(m.get("space") or ""),
                    status=_md_cell(m.get("status") or ""),
                    created=_md_cell(m.get("created_at") or ""),
                    updated=_md_cell(m.get("updated_at") or ""),
                    suffix=_md_cell(m.get("name_suffix") or name_suffix(m.get("name", ""))),
                )
            )
        lines.append("")

        if g["handled"]:
            lines.append("Handled")
        elif g["keeper"] is None:
            lines.append("Needs owner decision")
        else:
            lines.append(f"Keeper: {g['keeper']} ({g['reason']})")
        lines.append("")

        if g["commands"]:
            lines.append("```")
            lines.extend(g["commands"])
            lines.append("```")
            lines.append("")

    return "\n".join(lines)


# --- Neo4j I/O: fact meta, near-copy discovery, report assembly, guarded supersede ---

_LOAD_FACT_META = (
    "MATCH (f:Fact) RETURN f.name AS name, f.status AS status, f.assistant AS assistant, "
    "f.space AS space, toString(f.created_at) AS created_at, toString(f.updated_at) AS updated_at, "
    "f.embedding IS NOT NULL AS has_embedding"
)

_SUPERSEDE_STMT = (
    "MATCH (neu:Fact {name:$neu}), (old:Fact {name:$old}) WHERE neu <> old "
    "SET old.status = 'superseded', old.superseded_at = $now, old.updated_at = $now, "
    "neu.status = coalesce(neu.status, 'active') "
    "MERGE (neu)-[r:SUPERSEDES]->(old) SET r.at = $now, r.by = $by "
    "RETURN old.name AS old"
)


def load_fact_meta(session) -> dict[str, dict]:
    """Every Fact's metadata, keyed by name (so build_report's meta_missing fallback
    never triggers for live data)."""
    return {r["name"]: dict(r) for r in session.run(_LOAD_FACT_META)}


def load_supersedes_strict(session) -> dict[str, set[str]]:
    """Like ai_memory.search.load_supersedes, but does not swallow exceptions.

    Returns the same ``{new: {old, ...}}`` multimap: a keeper that supersedes
    several olds must keep every edge, or the guards below pass open on the
    dropped ones.

    load_supersedes is best-effort by contract (fine for ranking/collapse, where a
    missing map just falls back to the name-suffix rule). supersede_fact's write
    guard needs the opposite: a failed load must raise, not silently return {} and
    let the already-superseded/cycle checks pass open ahead of an irreversible write.
    """
    out: dict[str, set[str]] = {}
    for r in session.run("MATCH (n:Fact)-[:SUPERSEDES]->(o:Fact) RETURN n.name AS n, o.name AS o"):
        out.setdefault(r["n"], set()).add(r["o"])
    return out


def _search_stmt(index: str) -> str:
    name = validate_index_name(index)
    return (
        "CYPHER 25\n"
        "MATCH (g:Fact)\n"
        f"SEARCH g IN (VECTOR INDEX `{name}` FOR $vec LIMIT $pool) SCORE AS s\n"
        "WITH g, s WHERE g.name <> $name\n"
        "RETURN g.name AS name, 2 * vector.similarity.cosine($vec, g.embedding) - 1 AS cos"
    )


def near_copy_pairs(
    session, *, index: str, threshold: float = DUP_COS, k: int = 3
) -> list[tuple[str, str, float]]:
    """Near-copy Fact pairs by exact cosine, found via one in-index SEARCH per
    embedded Fact. Pairs are canonicalised (a < b) and de-duplicated."""
    stmt = _search_stmt(index)
    pool = k + 1
    names = [
        r["name"]
        for r in session.run("MATCH (f:Fact) WHERE f.embedding IS NOT NULL RETURN f.name AS name ORDER BY f.name")
    ]
    pairs: dict[tuple[str, str], float] = {}
    for name in names:
        rec = session.run("MATCH (f:Fact {name:$name}) RETURN f.embedding AS e", name=name).single()
        vec = rec["e"] if rec else None
        if vec is None:
            continue
        for row in session.run(stmt, name=name, vec=vec, pool=pool):
            cos = row["cos"]
            if cos is None or cos < threshold:
                continue
            pair = canonical_pair(name, row["name"])
            if pair[0] == pair[1]:
                continue
            prev = pairs.get(pair)
            if prev is None or cos > prev:
                pairs[pair] = cos
    return sorted((a, b, cos) for (a, b), cos in pairs.items())


def _default_index() -> str:
    return os.getenv("NEO4J_VECTOR_INDEX", "fact_embeddings")


def duplicate_report(
    driver,
    *,
    threshold: float = DUP_COS,
    k: int = 3,
    include_handled: bool = False,
    index: str | None = None,
) -> dict:
    """Owner-facing duplicate report: suffix groups + near-copy pairs, merged and
    rendered against live Fact metadata and SUPERSEDES edges. Never writes."""
    if index is None:
        index = _default_index()
    with driver.session() as session:
        meta = load_fact_meta(session)
        supersedes = load_supersedes(session)
        pairs = near_copy_pairs(session, index=index, threshold=threshold, k=k)
        groups = merge_groups(suffix_groups(meta.keys()), pairs)
        report = build_report(groups, meta, supersedes, include_handled=include_handled)
    report["params"] = {"threshold": threshold, "k": k, "index": index}
    report["summary"]["near_copy_pairs"] = len(pairs)
    report["summary"]["supersedes_loaded"] = sum(len(olds) for olds in supersedes.values())
    return report


def _ancestors(start: str, parents: Mapping[str, list[str]]) -> set[str]:
    """Names reachable from ``start`` by repeatedly following ``parents``
    (the Facts that already supersede a given name)."""
    seen: set[str] = set()
    todo = list(parents.get(start, []))
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen.add(n)
        todo.extend(parents.get(n, []))
    return seen


def plan_supersedes(
    session, pairs: Iterable[tuple[str, str]], supersedes: Mapping[str, set[str]]
) -> list[dict]:
    """Validate a batch of proposed (new, old) supersede pairs against live Fact
    existence and the current SUPERSEDES graph, without writing anything.

    ``supersedes`` is the ``{new: {old, ...}}`` multimap; every edge is considered
    by both the already-superseded check and the cycle check."""
    pairs = list(pairs)
    supersedes = as_supersedes_multimap(supersedes)
    names = sorted({n for pair in pairs for n in pair})
    existing: set[str] = set()
    if names:
        existing = {
            r["name"] for r in session.run("MATCH (f:Fact) WHERE f.name IN $names RETURN f.name AS name", names=names)
        }

    parents: dict[str, list[str]] = {}
    for new, olds in supersedes.items():
        for old in olds:
            parents.setdefault(old, []).append(new)

    out = []
    for new, old in pairs:
        reason = None
        if new not in existing:
            reason = "unknown new"
        elif old not in existing:
            reason = "unknown old"
        elif new == old:
            reason = "same fact"
        else:
            other = next((k for k, olds in supersedes.items() if old in olds and k != new), None)
            if other is not None:
                reason = f"old already superseded by {other}"
            elif old in _ancestors(new, parents):
                reason = "would create a cycle"
        out.append({"new": new, "old": old, "ok": reason is None, "reason": reason})
    return out


def supersede_fact(
    session, new_name: str, old_name: str, *, by: str = "ai-memory", now: str | None = None
) -> dict:
    """Guarded supersede: refuses (ValueError) before writing anything when
    plan_supersedes flags the pair; otherwise marks ``old`` superseded by ``new``.

    Uses load_supersedes_strict (not the best-effort ai_memory.search.load_supersedes)
    so a failed SUPERSEDES-map load raises instead of silently disabling the
    already-superseded/cycle guards ahead of a write."""
    supersedes = load_supersedes_strict(session)
    plan = plan_supersedes(session, [(new_name, old_name)], supersedes)[0]
    if not plan["ok"]:
        raise ValueError(plan["reason"])
    if now is None:
        now = datetime.now(timezone.utc).isoformat()
    session.run(_SUPERSEDE_STMT, neu=new_name, old=old_name, now=now, by=by)
    return {"new": new_name, "old": old_name, "at": now}
