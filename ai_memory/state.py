"""
Per-session memory state tracking (Phase 4 RLM lazy loading).

Manages transient MemoryState, MemoryQuery, and MemoryFact nodes in Neo4j
to track which Facts have been loaded into LLM context during a session.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from ai_memory._config import get_driver


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MemoryStateManager:
    """
    Per-session memory state in Neo4j (transient nodes).

    Enables RLM lazy loading: instead of loading hundreds of Facts on every
    turn, the agent loads only what is relevant and tracks what is already
    in context.

    Bind a session_id at construction to avoid repeating it on every call:

        mgr = MemoryStateManager(session_id="weft:main")
        mgr.init_session()
        mgr.record_query("gamma", [{"name": "Gamma-Parameter", "score": 0.9}])
        pending = mgr.get_pending()
        mgr.close()

    Explicit per-call session_id still overrides the bound default.
    """

    def __init__(self, workspace=None, session_id: Optional[str] = None):
        self.driver = None
        self.driver = get_driver(workspace)
        self.session_id = session_id

    def close(self):
        if self.driver:
            self.driver.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _resolve_sid(self, session_id: Optional[str]) -> str:
        sid = session_id if session_id is not None else self.session_id
        if sid is None:
            raise ValueError(
                "session_id required: bind via MemoryStateManager(session_id=...) "
                "or MemoryClient.state(session_id=...), or pass per call."
            )
        return sid

    def _ensure_state_in(self, s, session_id: str, now) -> None:
        """MERGE MemoryState within an existing session. Idempotent. Bumps updated_at."""
        s.run(
            """
            MERGE (ms:MemoryState {session_id: $session_id})
            ON CREATE SET ms.created_at = $now,
                          ms.updated_at = $now,
                          ms.query_count = 0
            ON MATCH  SET ms.updated_at = $now
            """,
            session_id=session_id,
            now=now,
        )

    def _create_memory_query(
        self, s, session_id: str, query_id: str, query: str, now, result_count: int, max_score: float
    ) -> None:
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
            session_id=session_id, query_id=query_id, query_text=query,
            now=now, result_count=result_count, max_score=float(max_score),
        )

    def _link_fact_to_query(
        self, s, session_id: str, query_id: str, fact_name: str, score: float, state: str, loaded_at
    ) -> None:
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
            session_id=session_id, query_id=query_id, fact_name=fact_name,
            state=state, score=score, loaded_at=loaded_at,
        )

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def init_session(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Create or refresh a MemoryState node for a session (idempotent)."""
        sid = self._resolve_sid(session_id)
        now = _now()
        with self.driver.session() as s:
            self._ensure_state_in(s, sid, now)
            result = s.run(
                """
                MATCH (ms:MemoryState {session_id: $session_id})
                RETURN ms.session_id AS session_id,
                       ms.created_at AS created_at,
                       ms.query_count AS query_count
                """,
                session_id=sid,
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
        session_id: Optional[str],
        query: str,
        results: List[Dict[str, Any]],
        state: str = "pending",
    ) -> Dict[str, Any]:
        """Record a search query and its results into the session state."""
        sid = self._resolve_sid(session_id)
        now = _now()
        query_id = str(uuid.uuid4())
        result_count = len(results)
        max_score = max((r.get("score", 0.0) for r in results), default=0.0)

        with self.driver.session() as s:
            self._ensure_state_in(s, sid, now)
            self._create_memory_query(s, sid, query_id, query, now, result_count, max_score)
            for r in results:
                fact_name = r.get("name", "")
                if not fact_name:
                    continue
                score = float(r.get("score", 0.0))
                loaded_at = now if state == "loaded" else None
                self._link_fact_to_query(s, sid, query_id, fact_name, score, state, loaded_at)

        return {
            "query_id": query_id,
            "session_id": sid,
            "query": query,
            "result_count": result_count,
            "state": state,
        }

    # ------------------------------------------------------------------
    # State updates
    # ------------------------------------------------------------------

    def mark_loaded(self, session_id: Optional[str], fact_names: List[str]) -> int:
        """Mark facts as loaded into context. Returns count updated."""
        sid = self._resolve_sid(session_id)
        now = _now()
        with self.driver.session() as s:
            self._ensure_state_in(s, sid, now)
            result = s.run(
                """
                MATCH (ms:MemoryState {session_id: $session_id})-[:HAS_FACT]->(mf:MemoryFact)
                WHERE mf.fact_name IN $fact_names
                SET mf.state = 'loaded', mf.loaded_at = $now, ms.updated_at = $now
                RETURN count(mf) AS updated
                """,
                session_id=sid, fact_names=fact_names, now=now,
            )
            rec = result.single()
            return rec["updated"] if rec else 0

    # ------------------------------------------------------------------
    # Queries (read-only — do not create state, do not bump updated_at)
    # ------------------------------------------------------------------

    def get_pending(self, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get facts in 'pending' state (known but not yet in context)."""
        sid = self._resolve_sid(session_id)
        with self.driver.session() as s:
            result = s.run(
                """
                MATCH (ms:MemoryState {session_id: $session_id})-[:HAS_FACT]->(mf:MemoryFact)
                WHERE mf.state = 'pending'
                RETURN mf.fact_name AS fact_name, mf.score AS score
                ORDER BY mf.score DESC
                """,
                session_id=sid,
            )
            return [{"fact_name": r["fact_name"], "score": r["score"]} for r in result]

    def get_summary(self, session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Get full state summary for a session, or None if it doesn't exist."""
        sid = self._resolve_sid(session_id)
        state_info = self._get_session_state(sid)
        if not state_info:
            return None
        facts = self._get_facts_for_session(sid)
        queries = self._get_recent_queries(sid, limit=10)
        loaded = sum(1 for f in facts if f["state"] == "loaded")
        pending = sum(1 for f in facts if f["state"] == "pending")
        return {
            "session_id": state_info["session_id"],
            "created_at": state_info["created_at"],
            "updated_at": state_info["updated_at"],
            "query_count": state_info["query_count"],
            "facts_total": len(facts),
            "facts_loaded": loaded,
            "facts_pending": pending,
            "recent_queries": queries,
            "facts": facts,
        }

    def _get_session_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self.driver.session() as s:
            result = s.run(
                """
                MATCH (ms:MemoryState {session_id: $session_id})
                RETURN ms.session_id AS session_id,
                       ms.created_at AS created_at,
                       ms.updated_at AS updated_at,
                       ms.query_count AS query_count
                """,
                session_id=session_id,
            )
            rec = result.single()
            if not rec:
                return None
            return {
                "session_id": rec["session_id"],
                "created_at": str(rec["created_at"]),
                "updated_at": str(rec["updated_at"]),
                "query_count": rec["query_count"],
            }

    def _get_facts_for_session(self, session_id: str) -> List[Dict[str, Any]]:
        with self.driver.session() as s:
            result = s.run(
                """
                MATCH (ms:MemoryState {session_id: $session_id})-[:HAS_FACT]->(mf:MemoryFact)
                RETURN mf.fact_name AS fact_name, mf.state AS state,
                       mf.score AS score, mf.loaded_at AS loaded_at
                ORDER BY mf.score DESC
                """,
                session_id=session_id,
            )
            return [
                {
                    "fact_name": r["fact_name"],
                    "state": r["state"],
                    "score": r["score"],
                    "loaded_at": str(r["loaded_at"]) if r["loaded_at"] else None,
                }
                for r in result
            ]

    def _get_recent_queries(self, session_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        with self.driver.session() as s:
            result = s.run(
                """
                MATCH (ms:MemoryState {session_id: $session_id})-[:HAS_QUERY]->(q:MemoryQuery)
                RETURN q.id AS id, q.query_text AS query_text, q.timestamp AS timestamp,
                       q.result_count AS result_count, q.max_score AS max_score
                ORDER BY q.timestamp DESC LIMIT $limit
                """,
                session_id=session_id, limit=limit,
            )
            return [
                {
                    "id": r["id"],
                    "query_text": r["query_text"],
                    "timestamp": str(r["timestamp"]),
                    "result_count": r["result_count"],
                    "max_score": r["max_score"],
                }
                for r in result
            ]

    # ------------------------------------------------------------------
    # Fact loading
    # ------------------------------------------------------------------

    def load_fact(
        self, session_id: Optional[str], fact_name: str
    ) -> Optional[Dict[str, Any]]:
        """Return full Fact content, optionally tracking it as loaded in session state.

        Tracking happens when an explicit session_id is passed, or when this
        manager was constructed with a bound session_id. To explicitly skip
        tracking on a bound manager, pass session_id="".
        """
        with self.driver.session() as s:
            result = s.run(
                "MATCH (f:Fact {name: $name}) "
                "RETURN f.name AS name, f.summary AS summary, f.key_points AS key_points",
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

        # Resolve sid: explicit arg wins, then bound default. Empty string opts out.
        sid = session_id if session_id is not None else self.session_id
        if sid:
            now = _now()
            with self.driver.session() as s:
                self._ensure_state_in(s, sid, now)
                # MERGE the MemoryFact so direct load_fact() (without prior
                # record_query) is also tracked. ON MATCH preserves the score
                # set by an earlier record_query call.
                s.run(
                    """
                    MATCH (ms:MemoryState {session_id: $session_id})
                    MERGE (mf:MemoryFact {fact_name: $fact_name, session_id: $session_id})
                    ON CREATE SET mf.state = 'loaded', mf.score = 0.0, mf.loaded_at = $now
                    ON MATCH  SET mf.state = 'loaded', mf.loaded_at = $now
                    MERGE (ms)-[:HAS_FACT]->(mf)
                    """,
                    session_id=sid, fact_name=fact_name, now=now,
                )
        return fact

    def load_next(
        self, session_id: Optional[str] = None, count: int = 3
    ) -> List[Dict[str, Any]]:
        """Load the next N pending facts (highest score first), marking them loaded."""
        sid = self._resolve_sid(session_id)
        pending = self.get_pending(sid)[:count]
        if not pending:
            return []
        facts = []
        for p in pending:
            fact = self.load_fact(sid, p["fact_name"])
            if fact:
                facts.append(fact)
        return facts

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def cleanup(self, max_age_hours: int = 24) -> int:
        """Remove MemoryState nodes older than max_age_hours. Returns count deleted."""
        cutoff = _now() - timedelta(hours=max_age_hours)
        with self.driver.session() as s:
            result = s.run(
                """
                MATCH (ms:MemoryState)
                WHERE ms.updated_at < $cutoff
                OPTIONAL MATCH (ms)-[:HAS_QUERY]->(q:MemoryQuery)
                OPTIONAL MATCH (ms)-[:HAS_FACT]->(mf:MemoryFact)
                WITH collect(DISTINCT ms) AS sessions,
                     collect(DISTINCT q)  AS queries,
                     collect(DISTINCT mf) AS facts
                FOREACH (q IN queries | DETACH DELETE q)
                FOREACH (mf IN facts | DETACH DELETE mf)
                FOREACH (ms IN sessions | DETACH DELETE ms)
                RETURN size(sessions) AS session_count
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
                RETURN ms.session_id AS session_id,
                       ms.created_at AS created_at,
                       ms.updated_at AS updated_at,
                       ms.query_count AS query_count,
                       loaded_count, pending_count
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
