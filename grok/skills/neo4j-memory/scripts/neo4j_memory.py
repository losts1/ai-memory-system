#!/usr/bin/env python3
"""Grok ↔ Neo4j memory CLI.

Reads credentials from ~/.grok/.env.neo4j (or $GROK_HOME/.env.neo4j).
Writes Facts tagged assistant=Grok. Refuses to overwrite another mind's Fact.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import dotenv_values
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

GROK_HOME = Path(os.environ.get("GROK_HOME", Path.home() / ".grok"))
ENV_FILE = GROK_HOME / ".env.neo4j"
HITS_FILE = GROK_HOME / "neo4j-hits.md"
SESSION_FILE = GROK_HOME / "neo4j-session.md"
INBOX_FILE = GROK_HOME / "neo4j-inbox.jsonl"
ASSISTANT = "Grok"
DEFAULT_MAX = 5
TEASER = 220

_LUCENE_SPECIAL = re.compile(r'[\+\-\&\|\!\(\)\{\}\[\]\^\"\~\*\?\:\/\\]')
_WORD = re.compile(r"[a-z0-9]{3,}")
_STOP = {
    "the", "and", "for", "with", "from", "that", "this", "are", "was", "were",
    "you", "your", "have", "has", "not", "but", "can", "how", "what", "when",
    "why", "who", "into", "about", "just", "like", "then", "than", "them",
}


def _cfg() -> dict:
    if not ENV_FILE.exists():
        raise SystemExit(f"missing {ENV_FILE}")
    raw = dotenv_values(ENV_FILE)
    uri = raw.get("NEO4J_URI") or "bolt://localhost:7687"
    user = raw.get("NEO4J_USERNAME") or raw.get("NEO4J_USER") or "neo4j"
    password = raw.get("NEO4J_PASSWORD")
    if not password:
        raise SystemExit(f"NEO4J_PASSWORD unset in {ENV_FILE}")
    return {
        "uri": uri,
        "user": user,
        "password": password,
        "vector": raw.get("NEO4J_VECTOR_INDEX") or "factEmbeddingIndex",
        "fulltext": raw.get("NEO4J_FULLTEXT_INDEX") or "fact_content",
    }


def _driver():
    c = _cfg()
    return GraphDatabase.driver(c["uri"], auth=(c["user"], c["password"])), c


def _escape_lucene(q: str) -> str:
    return _LUCENE_SPECIAL.sub(lambda m: "\\" + m.group(), q.strip())


def _teaser(text, n=TEASER) -> str:
    if not text:
        return ""
    s = " ".join(str(text).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _words(text: str) -> list[str]:
    seen = []
    for w in _WORD.findall(text.lower()):
        if w in _STOP or w in seen:
            continue
        seen.append(w)
    return seen[:12]


def search_fulltext(session, query: str, index: str, limit: int) -> list[dict]:
    lucene = _escape_lucene(query)
    if not lucene:
        return []
    rows = session.run(
        """
        CALL db.index.fulltext.queryNodes($index, $q)
        YIELD node, score
        RETURN node.name AS name,
               coalesce(node.summary, node.content) AS text,
               node.assistant AS assistant,
               node.key_points AS key_points,
               score
        ORDER BY score DESC
        LIMIT $limit
        """,
        index=index,
        q=lucene,
        limit=limit,
    )
    out = []
    for r in rows:
        out.append({
            "name": r["name"],
            "assistant": r["assistant"],
            "score": round(float(r["score"] or 0), 3),
            "teaser": _teaser(r["text"]),
            "key_points": (r["key_points"] or [])[:3],
        })
    return out


def cmd_search(args: argparse.Namespace) -> int:
    drv, cfg = _driver()
    try:
        with drv.session() as s:
            hits = search_fulltext(s, args.query, cfg["fulltext"], args.max)
    except Neo4jError as e:
        print(f"search failed: {e}", file=sys.stderr)
        return 1
    finally:
        drv.close()
    if not hits:
        print("No hits.")
        return 0
    for h in hits:
        mind = h["assistant"] or "?"
        print(f"{h['score']:>6}  [{mind}] {h['name']}")
        if h["teaser"]:
            print(f"         {h['teaser']}")
        for kp in h["key_points"]:
            print(f"         - {_teaser(kp, 160)}")
    return 0


def cmd_stats(_args: argparse.Namespace) -> int:
    drv, _ = _driver()
    try:
        with drv.session() as s:
            facts = s.run("MATCH (f:Fact) RETURN count(f) AS c").single()["c"]
            grok = s.run(
                "MATCH (f:Fact {assistant: $a}) RETURN count(f) AS c",
                a=ASSISTANT,
            ).single()["c"]
            minds = s.run(
                "MATCH (f:Fact) RETURN coalesce(f.assistant,'(none)') AS a, count(*) AS c "
                "ORDER BY c DESC"
            ).data()
    except Neo4jError as e:
        print(f"stats failed: {e}", file=sys.stderr)
        return 1
    finally:
        drv.close()
    print(f"facts {facts}  grok {grok}")
    for r in minds:
        print(f"  {r['c']:>5}  {r['a']}")
    return 0


def cmd_write(args: argparse.Namespace) -> int:
    name = args.name.strip()
    summary = (args.summary or "").strip()
    points = [p.strip() for p in (args.point or []) if p.strip()]
    assistant = (args.assistant or ASSISTANT).strip()
    source = (args.source or "grok").strip()
    session_id = (args.session or "").strip() or None
    if not name or not summary:
        print("write needs --name and --summary", file=sys.stderr)
        return 2
    drv, _ = _driver()
    now = _now()
    try:
        with drv.session() as s:
            existing = s.run(
                "MATCH (f:Fact {name: $name}) RETURN f.assistant AS a",
                name=name,
            ).single()
            if existing and existing["a"] not in (None, assistant):
                print(
                    f"refused: Fact {name!r} owned by {existing['a']!r}",
                    file=sys.stderr,
                )
                return 3
            s.run(
                """
                MERGE (a:Assistant {id: $assistant})
                ON CREATE SET a.name = $assistant, a.created_at = datetime()
                MERGE (f:Fact {name: $name})
                ON CREATE SET
                    f.summary = $summary,
                    f.key_points = $points,
                    f.source_file = $source,
                    f.assistant = $assistant,
                    f.created_at = $now,
                    f.updated_at = $now
                ON MATCH SET
                    f.summary = $summary,
                    f.key_points = $points,
                    f.source_file = $source,
                    f.updated_at = $now,
                    f.assistant = coalesce(f.assistant, $assistant)
                WITH f, a
                MERGE (f)-[:CREATED_BY]->(a)
                """,
                name=name,
                summary=summary,
                points=points,
                assistant=assistant,
                source=source,
                now=now,
            )
            if session_id:
                s.run(
                    """
                    MATCH (f:Fact {name: $name})
                    MERGE (sess:Session {id: $session_id})
                    MERGE (f)-[:LEARNED_IN]->(sess)
                    """,
                    name=name,
                    session_id=session_id,
                )
            words = _words(name + " " + summary + " " + " ".join(points))
            if words:
                s.run(
                    """
                    MATCH (f:Fact {name: $name})
                    OPTIONAL MATCH (f)-[old:HAS_WORD]->(:Word)
                    DELETE old
                    WITH DISTINCT f
                    UNWIND $words AS word
                    MERGE (w:Word {text: word})
                    MERGE (f)-[:HAS_WORD]->(w)
                    """,
                    name=name,
                    words=words,
                )
    except Neo4jError as e:
        print(f"write failed: {e}", file=sys.stderr)
        return 1
    finally:
        drv.close()
    print(f"wrote [{assistant}] {name}")
    return 0


def cmd_organize(args: argparse.Namespace) -> int:
    """RELATED_TO among one mind's facts that share >=2 words. Does not touch other minds' edges."""
    assistant = (args.assistant or ASSISTANT).strip()
    drv, _ = _driver()
    try:
        with drv.session() as s:
            rec = s.run(
                """
                MATCH (f1:Fact {assistant: $a})-[:HAS_WORD]->(w:Word)<-[:HAS_WORD]-(f2:Fact {assistant: $a})
                WHERE elementId(f1) < elementId(f2)
                WITH f1, f2, collect(DISTINCT w.text) AS shared
                WHERE size(shared) >= 2
                MERGE (f1)-[r:RELATED_TO]->(f2)
                SET r.shared_keywords = shared,
                    r.shared_count = size(shared),
                    r.source = 'grok-organize'
                RETURN count(*) AS linked
                """,
                a=assistant,
            ).single()
            n = rec["linked"] if rec else 0
    except Neo4jError as e:
        print(f"organize failed: {e}", file=sys.stderr)
        return 1
    finally:
        drv.close()
    print(f"linked {n} {assistant} fact pairs")
    return 0


def _prompt_from_hook(data: dict) -> str:
    for key in ("prompt", "text", "userPrompt", "message", "lastAssistantMessage"):
        v = data.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    content = data.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, dict):
        t = content.get("text")
        if isinstance(t, str) and t.strip():
            return t.strip()
    return ""


def _write_hits(query: str, hits: list[dict]) -> None:
    lines = [
        f"# Neo4j hits  ({_now()})",
        f"query: {query[:200]}",
        "",
    ]
    if not hits:
        lines.append("(none)")
    for h in hits:
        mind = h["assistant"] or "?"
        lines.append(f"- **{h['name']}** [{mind}] score={h['score']}")
        if h["teaser"]:
            lines.append(f"  {h['teaser']}")
    HITS_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_hook_prompt(_args: argparse.Namespace) -> int:
    """UserPromptSubmit: fulltext-search the prompt, write neo4j-hits.md. Always exit 0."""
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    prompt = _prompt_from_hook(data)
    if len(prompt) < 8:
        return 0
    query = " ".join(prompt.split()[:24])
    try:
        drv, cfg = _driver()
        try:
            with drv.session() as s:
                hits = search_fulltext(s, query, cfg["fulltext"], DEFAULT_MAX)
        finally:
            drv.close()
        _write_hits(query, hits)
    except Exception:
        return 0
    return 0


def cmd_hook_session(_args: argparse.Namespace) -> int:
    """SessionStart: write compact graph stats. Always exit 0."""
    try:
        drv, cfg = _driver()
        try:
            with drv.session() as s:
                facts = s.run("MATCH (f:Fact) RETURN count(f) AS c").single()["c"]
                grok = s.run(
                    "MATCH (f:Fact {assistant: $a}) RETURN count(f) AS c",
                    a=ASSISTANT,
                ).single()["c"]
                recent = s.run(
                    """
                    MATCH (f:Fact)
                    WHERE f.updated_at IS NOT NULL
                    RETURN f.name AS name, f.assistant AS a
                    ORDER BY f.updated_at DESC LIMIT 5
                    """
                ).data()
        finally:
            drv.close()
        lines = [
            f"# Neo4j session  ({_now()})",
            f"uri: {cfg['uri']}",
            f"facts: {facts}  grok: {grok}",
            "search: python3 ~/.grok/skills/neo4j-memory/scripts/neo4j_memory.py search QUERY",
            "",
        ]
        if recent:
            lines.append("recent:")
            for r in recent:
                lines.append(f"- [{r['a'] or '?'}] {r['name']}")
        SESSION_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        return 0
    return 0


def cmd_hook_stop(_args: argparse.Namespace) -> int:
    """Stop observe: inbox the turn. Never blocks. Always exit 0."""
    try:
        data = json.load(sys.stdin)
    except Exception:
        return 0
    if data.get("reason") and data.get("reason") != "end_turn":
        return 0
    if data.get("subagentType"):
        return 0
    rec = {
        "ts": _now(),
        "sessionId": data.get("sessionId"),
        "cwd": data.get("cwd") or data.get("workspaceRoot"),
        "promptId": data.get("promptId"),
        "last": _teaser(data.get("lastAssistantMessage") or "", 400),
    }
    try:
        with INBOX_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        return 0
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="neo4j_memory")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="Fulltext search Facts")
    s.add_argument("query")
    s.add_argument("--max", type=int, default=DEFAULT_MAX)
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("stats", help="Graph counts")
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser("write", help="MERGE a Fact tagged to an assistant")
    s.add_argument("--name", required=True)
    s.add_argument("--summary", required=True)
    s.add_argument("--point", action="append")
    s.add_argument("--assistant", default=ASSISTANT)
    s.add_argument("--source", default="grok")
    s.add_argument("--session", default=None, help="Session.id for LEARNED_IN")
    s.set_defaults(func=cmd_write)

    s = sub.add_parser("organize", help="RELATED_TO among one mind's facts sharing 2+ words")
    s.add_argument("--assistant", default=ASSISTANT)
    s.set_defaults(func=cmd_organize)

    s = sub.add_parser("hook-prompt", help="UserPromptSubmit handler")
    s.set_defaults(func=cmd_hook_prompt)

    s = sub.add_parser("hook-session", help="SessionStart handler")
    s.set_defaults(func=cmd_hook_session)

    s = sub.add_parser("hook-stop", help="Stop observe handler")
    s.set_defaults(func=cmd_hook_stop)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
