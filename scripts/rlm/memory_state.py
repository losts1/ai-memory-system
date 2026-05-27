#!/usr/bin/env python3
"""
Memory State Manager - Per-session memory state tracking in Neo4j.

Manages transient MemoryState, MemoryQuery, and MemoryFact nodes to track
which facts have been loaded into LLM context during a session.

================================================================================
PHASE 4 — ADVANCED RLM TOOLING (EXPERIMENTAL)

This is a core piece of the lazy Recursive Language Model architecture.
Instead of loading hundreds of facts on every turn, the agent can lazily
load only what is relevant and track what has already been "seen" in the
current session.

See UPGRADE_PLAN.md for Phase 4 context.
================================================================================

Usage:
    python3 memory_state.py --init --session "agent:main:main"
    python3 memory_state.py --record-query --session "agent:main:main" --query "gamma" --results Fact1,Fact2 --scores 0.95,0.89
    python3 memory_state.py --mark-loaded --session "agent:main:main" --facts Fact1,Fact2
    python3 memory_state.py --pending --session "agent:main:main"
    python3 memory_state.py --summary --session "agent:main:main"
    python3 memory_state.py --load-fact --session "agent:main:main" --fact "Merton-Portfolio"
    python3 memory_state.py --load-next --session "agent:main:main" --count 3
    python3 memory_state.py --cleanup --max-age-hours 24
    python3 memory_state.py --list-sessions
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

# Standard public package workspace handling
_WORKSPACE = Path(os.getenv("AI_MEMORY_DIR", str(Path.home() / ".ai-memory")))
load_dotenv(_WORKSPACE / ".env.neo4j")

try:
    from neo4j import GraphDatabase
except ImportError as e:
    print(json.dumps({"success": False, "error": f"Neo4j driver not available: {e}"}))
    sys.exit(1)

WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", str(_WORKSPACE)))
ENV_FILE = WORKSPACE / ".env.neo4j"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_list(raw: str) -> List[str]:
    """Parse comma-separated or bracket-enclosed list from CLI."""
    s = raw.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    return [item.strip() for item in s.split(",") if item.strip()]


class MemoryStateManager:
    """Manages per-session memory state in Neo4j transient nodes."""

    def __init__(self):
        self.driver = get_driver()

    def close(self):
        if self.driver:
            self.driver.close()


def get_driver():
    """Create a Neo4j driver using the standard public package pattern."""
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USERNAME", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")
    if not password:
        raise ValueError("NEO4J_PASSWORD not set in .env.neo4j")
    return GraphDatabase.driver(uri, auth=(user, password))

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def init_session(self, session_id: str) -> Dict[str, Any]:
        """Create or refresh a MemoryState node for a session (idempotent)."""
        now = _now()
        with self.driver.session() as s:
            result = s.run(
                """
                MERGE (ms:MemoryState {session_id: $session_id})
                ON CREATE SET ms.created_at = $now,
                              ms.updated_at = $now,
                              ms.query_count = 0
                ON MATCH  SET ms.updated_at = $now
                RETURN ms.session_id AS session_id,
                       ms.created_at AS created_at,
                       ms.query_count AS query_count
                """,
                session_id=session_id,
                now=now,
            )
            rec = result.single()
            return {
                "session_id": rec["session_id"],
                "created_at": str(rec["created_at"]),
                "query_count": rec["query_count"],
            }

    # ------------------------------------------------------------------
    # Query recording
    # ------------------------------------------------------------------

    def record_query(
        self,
        session_id: str,
        query: str,
        results: List[Dict[str, Any]],
        state: str = "pending",
    ) -> Dict[str, Any]:
        """
        Record a search query and its results into the session state.

        Args:
            session_id: Session identifier.
            query: The search query text.
            results: List of dicts with 'name' and 'score' keys.
            state: Initial state for returned facts ('loaded' or 'pending').
        """
        now = _now()
        query_id = str(uuid.uuid4())
        result_count = len(results)
        max_score = max((r.get("score", 0.0) for r in results), default=0.0)

        with self.driver.session() as s:
            # Ensure session exists
            s.run(
                """
                MERGE (ms:MemoryState {session_id: $session_id})
                ON CREATE SET ms.created_at = $now,
                              ms.updated_at = $now,
                              ms.query_count = 0
                """,
                session_id=session_id,
                now=now,
            )

            # Create the MemoryQuery node
            s.run(
                """
                MATCH (ms:MemoryState {session_id: $session_id})
                CREATE (q:MemoryQuery {
                    id: $query_id,
                    query_text: $query_text,
                    timestamp: $now,
                    result_count: $result_count,
                    max_score: $max_score
                })
                CREATE (ms)-[:HAS_QUERY]->(q)
                SET ms.query_count = ms.query_count + 1,
                    ms.updated_at = $now
                """,
                session_id=session_id,
                query_id=query_id,
                query_text=query,
                now=now,
                result_count=result_count,
                max_score=float(max_score),
            )

            # Create/update MemoryFact nodes for each result
            for r in results:
                fact_name = r.get("name", "")
                if not fact_name:
                    continue
                score = float(r.get("score", 0.0))
                loaded_at = now if state == "loaded" else None

                s.run(
                    """
                    MATCH (ms:MemoryState {session_id: $session_id})
                    MATCH (q:MemoryQuery {id: $query_id})
                    MERGE (mf:MemoryFact {fact_name: $fact_name, session_id: $session_id})
                    ON CREATE SET mf.state = $state,
                                  mf.score = $score,
                                  mf.loaded_at = $loaded_at
                    ON MATCH  SET mf.score = CASE WHEN $score > mf.score THEN $score ELSE mf.score END
                    MERGE (ms)-[:HAS_FACT]->(mf)
                    MERGE (q)-[:RETURNED]->(mf)
                    """,
                    session_id=session_id,
                    query_id=query_id,
                    fact_name=fact_name,
                    state=state,
                    score=score,
                    loaded_at=loaded_at,
                )

        return {
            "query_id": query_id,
            "session_id": session_id,
            "query": query,
            "result_count": result_count,
            "state": state,
        }

    # ------------------------------------------------------------------
    # State updates
    # ------------------------------------------------------------------

    def mark_loaded(self, session_id: str, fact_names: List[str]) -> int:
        """Mark facts as loaded (context was provided to LLM). Returns count updated."""
        now = _now()
        with self.driver.session() as s:
            result = s.run(
                """
                MATCH (ms:MemoryState {session_id: $session_id})-[:HAS_FACT]->(mf:MemoryFact)
                WHERE mf.fact_name IN $fact_names
                SET mf.state = 'loaded',
                    mf.loaded_at = $now,
                    ms.updated_at = $now
                RETURN count(mf) AS updated
                """,
                session_id=session_id,
                fact_names=fact_names,
                now=now,
            )
            rec = result.single()
            return rec["updated"] if rec else 0

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_pending(self, session_id: str) -> List[Dict[str, Any]]:
        """Get facts in 'pending' state (known but not yet loaded into context)."""
        with self.driver.session() as s:
            result = s.run(
                """
                MATCH (ms:MemoryState {session_id: $session_id})-[:HAS_FACT]->(mf:MemoryFact)
                WHERE mf.state = 'pending'
                RETURN mf.fact_name AS fact_name, mf.score AS score
                ORDER BY mf.score DESC
                """,
                session_id=session_id,
            )
            return [{"fact_name": r["fact_name"], "score": r["score"]} for r in result]

    def get_summary(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get full state summary for a session."""
        with self.driver.session() as s:
            state_result = s.run(
                """
                MATCH (ms:MemoryState {session_id: $session_id})
                RETURN ms.session_id AS session_id,
                       ms.created_at AS created_at,
                       ms.updated_at AS updated_at,
                       ms.query_count AS query_count
                """,
                session_id=session_id,
            )
            state_rec = state_result.single()
            if not state_rec:
                return None

            facts_result = s.run(
                """
                MATCH (ms:MemoryState {session_id: $session_id})-[:HAS_FACT]->(mf:MemoryFact)
                RETURN mf.fact_name AS fact_name,
                       mf.state AS state,
                       mf.score AS score,
                       mf.loaded_at AS loaded_at
                ORDER BY mf.score DESC
                """,
                session_id=session_id,
            )
            facts = [
                {
                    "fact_name": r["fact_name"],
                    "state": r["state"],
                    "score": r["score"],
                    "loaded_at": str(r["loaded_at"]) if r["loaded_at"] else None,
                }
                for r in facts_result
            ]

            queries_result = s.run(
                """
                MATCH (ms:MemoryState {session_id: $session_id})-[:HAS_QUERY]->(q:MemoryQuery)
                RETURN q.id AS id,
                       q.query_text AS query_text,
                       q.timestamp AS timestamp,
                       q.result_count AS result_count,
                       q.max_score AS max_score
                ORDER BY q.timestamp DESC
                LIMIT 10
                """,
                session_id=session_id,
            )
            queries = [
                {
                    "id": r["id"],
                    "query_text": r["query_text"],
                    "timestamp": str(r["timestamp"]),
                    "result_count": r["result_count"],
                    "max_score": r["max_score"],
                }
                for r in queries_result
            ]

            loaded = sum(1 for f in facts if f["state"] == "loaded")
            pending = sum(1 for f in facts if f["state"] == "pending")

            return {
                "session_id": state_rec["session_id"],
                "created_at": str(state_rec["created_at"]),
                "updated_at": str(state_rec["updated_at"]),
                "query_count": state_rec["query_count"],
                "facts_total": len(facts),
                "facts_loaded": loaded,
                "facts_pending": pending,
                "recent_queries": queries,
                "facts": facts,
            }

    # ------------------------------------------------------------------
    # Fact loading
    # ------------------------------------------------------------------

    def load_fact(self, session_id: Optional[str], fact_name: str) -> Optional[Dict[str, Any]]:
        """
        Return full content for a Fact, optionally marking it loaded in session state.

        Args:
            session_id: If provided, marks the fact as loaded in state.
            fact_name: Name of the Fact node to load.

        Returns:
            Dict with name, summary, key_points, or None if not found.
        """
        with self.driver.session() as s:
            result = s.run(
                """
                MATCH (f:Fact {name: $name})
                RETURN f.name AS name, f.summary AS summary, f.key_points AS key_points
                """,
                name=fact_name,
            )
            rec = result.single()
            if not rec:
                return None

            fact = {
                "name": rec["name"],
                "summary": rec["summary"],
                "key_points": rec["key_points"] or [],
            }

        if session_id:
            self.mark_loaded(session_id, [fact_name])

        return fact

    def load_next(self, session_id: str, count: int = 3) -> List[Dict[str, Any]]:
        """
        Load the next N pending facts (highest score first), marking them as loaded.

        Returns:
            List of fact dicts with name, summary, key_points.
        """
        pending = self.get_pending(session_id)[:count]
        if not pending:
            return []

        fact_names = [p["fact_name"] for p in pending]
        facts = []
        for name in fact_names:
            fact = self.load_fact(session_id, name)
            if fact:
                facts.append(fact)

        return facts

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def cleanup(self, max_age_hours: int = 24) -> int:
        """Remove MemoryState nodes (and related nodes) older than max_age_hours. Returns count deleted."""
        cutoff = _now() - timedelta(hours=max_age_hours)
        with self.driver.session() as s:
            result = s.run(
                """
                MATCH (ms:MemoryState)
                WHERE ms.updated_at < $cutoff
                OPTIONAL MATCH (ms)-[:HAS_QUERY]->(q:MemoryQuery)
                OPTIONAL MATCH (ms)-[:HAS_FACT]->(mf:MemoryFact)
                WITH ms, collect(DISTINCT q) AS queries, collect(DISTINCT mf) AS facts,
                     count(DISTINCT ms) AS session_count
                FOREACH (q IN queries | DETACH DELETE q)
                FOREACH (mf IN facts | DETACH DELETE mf)
                DETACH DELETE ms
                RETURN session_count
                """,
                cutoff=cutoff,
            )
            rec = result.single()
            return rec["session_count"] if rec else 0

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all active MemoryState sessions."""
        with self.driver.session() as s:
            result = s.run(
                """
                MATCH (ms:MemoryState)
                OPTIONAL MATCH (ms)-[:HAS_FACT]->(mf:MemoryFact)
                WITH ms,
                     count(CASE WHEN mf.state = 'loaded'  THEN 1 END) AS loaded_count,
                     count(CASE WHEN mf.state = 'pending' THEN 1 END) AS pending_count
                RETURN ms.session_id  AS session_id,
                       ms.created_at  AS created_at,
                       ms.updated_at  AS updated_at,
                       ms.query_count AS query_count,
                       loaded_count,
                       pending_count
                ORDER BY ms.updated_at DESC
                """
            )
            return [
                {
                    "session_id": r["session_id"],
                    "created_at": str(r["created_at"]),
                    "updated_at": str(r["updated_at"]),
                    "query_count": r["query_count"],
                    "facts_loaded": r["loaded_count"],
                    "facts_pending": r["pending_count"],
                }
                for r in result
            ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Memory state management for Neo4j-backed session tracking (Phase 4 RLM tool)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    p = subparsers.add_parser("init", help="Initialize or refresh a session")
    p.add_argument("--session", required=True, help="Session ID (e.g. 'weft:main')")

    # record-query
    p = subparsers.add_parser("record-query", help="Record a search query and results")
    p.add_argument("--session", required=True)
    p.add_argument("--query", required=True)
    p.add_argument("--results", required=True, help="Comma-separated fact names")
    p.add_argument("--scores", help="Comma-separated scores (optional)")
    p.add_argument("--state", default="pending", choices=["pending", "loaded"])

    # mark-loaded
    p = subparsers.add_parser("mark-loaded", help="Mark facts as loaded into context")
    p.add_argument("--session", required=True)
    p.add_argument("--facts", required=True, help="Comma-separated fact names")

    # pending
    p = subparsers.add_parser("pending", help="Show pending facts for a session")
    p.add_argument("--session", required=True)

    # summary
    p = subparsers.add_parser("summary", help="Show full state summary for a session")
    p.add_argument("--session", required=True)

    # load-fact
    p = subparsers.add_parser("load-fact", help="Load one specific fact")
    p.add_argument("--session", required=False)
    p.add_argument("--fact", required=True)

    # load-next
    p = subparsers.add_parser("load-next", help="Load the next N pending facts")
    p.add_argument("--session", required=True)
    p.add_argument("--count", type=int, default=3)

    # cleanup
    p = subparsers.add_parser("cleanup", help="Remove old sessions")
    p.add_argument("--max-age-hours", type=int, default=24)

    # list-sessions
    subparsers.add_parser("list-sessions", help="List all known sessions")

    args = parser.parse_args()

    try:
        manager = MemoryStateManager()
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}))
        sys.exit(1)

    try:
        if args.command == "init":
            result = manager.init_session(args.session)
            print(json.dumps({"success": True, **result}))

        elif args.command == "record-query":
            names = _parse_list(args.results)
            scores = _parse_list(args.scores) if args.scores else []
            scores = [float(s) for s in scores] + [0.0] * (len(names) - len(scores))
            scores = scores[: len(names)]
            results = [{"name": n, "score": sc} for n, sc in zip(names, scores)]
            result = manager.record_query(args.session, args.query, results, state=args.state)
            print(json.dumps({"success": True, **result}))

        elif args.command == "mark-loaded":
            fact_names = _parse_list(args.facts)
            updated = manager.mark_loaded(args.session, fact_names)
            print(json.dumps({"success": True, "updated": updated}))

        elif args.command == "pending":
            pending = manager.get_pending(args.session)
            print(json.dumps({"success": True, "pending": pending, "count": len(pending)}))

        elif args.command == "summary":
            summary = manager.get_summary(args.session)
            if summary is None:
                print(json.dumps({"success": False, "error": f"Session not found: {args.session}"}))
                sys.exit(1)
            print(json.dumps({"success": True, **summary}))

        elif args.command == "load-fact":
            fact = manager.load_fact(args.session, args.fact)
            if fact is None:
                print(json.dumps({"success": False, "error": f"Fact not found: {args.fact}"}))
                sys.exit(1)
            print(json.dumps({"success": True, **fact}))

        elif args.command == "load-next":
            facts = manager.load_next(args.session, count=args.count)
            print(json.dumps({"success": True, "facts": facts, "count": len(facts)}))

        elif args.command == "cleanup":
            deleted = manager.cleanup(max_age_hours=args.max_age_hours)
            print(json.dumps({"success": True, "deleted_sessions": deleted}))

        elif args.command == "list-sessions":
            sessions = manager.list_sessions()
            print(json.dumps({"success": True, "sessions": sessions, "count": len(sessions)}))

    finally:
        manager.close()


if __name__ == "__main__":
    main()
