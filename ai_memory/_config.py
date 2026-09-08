"""Shared Neo4j connection and workspace config for the ai_memory library."""
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv
from neo4j import GraphDatabase
from neo4j.exceptions import (
    AuthError,
    ConfigurationError,
    ServiceUnavailable,
)

from ai_memory.exceptions import Neo4jConnectionError
from ai_memory.vector_index import DEFAULT_FILTER_PROPS as EXPECTED_VECTOR_FILTER_PROPS


# Driver tuning (issue #N1 / #N19 — bounded pool + lifetime; was unbounded).
# Override per-process via env vars if needed.
_DEFAULT_POOL_SIZE = int(os.getenv("NEO4J_MAX_POOL_SIZE", "50"))
_DEFAULT_CONNECTION_LIFETIME = int(os.getenv("NEO4J_MAX_CONNECTION_LIFETIME", "3600"))
_DEFAULT_QUERY_TIMEOUT = float(os.getenv("NEO4J_QUERY_TIMEOUT_S", "30"))


def get_workspace(workspace=None) -> Path:
    """Resolve workspace: explicit arg > AI_MEMORY_DIR env > ~/.ai-memory."""
    if workspace is not None:
        return Path(workspace)
    return Path(os.getenv("AI_MEMORY_DIR", str(Path.home() / ".ai-memory")))


def get_query_timeout() -> float:
    """Return the configured per-query timeout in seconds."""
    return _DEFAULT_QUERY_TIMEOUT


def _driver_kwargs() -> dict:
    """Driver kwargs, including the server-notification mute.

    ``notifications_min_severity`` was added in neo4j-python 5.6. It cannot be
    detected from ``GraphDatabase.driver``'s signature, which is
    ``(uri, *, auth, **config)``, so it is always included here and
    ``get_driver`` retries without it if the driver rejects it.

    Default ``OFF``: the per-query "property does not exist" pings are severity
    WARNING, so a WARNING floor does not mute them. Override with
    ``NEO4J_NOTIFICATIONS_MIN_SEVERITY`` (OFF, WARNING, INFORMATION).
    """
    return dict(
        max_connection_pool_size=_DEFAULT_POOL_SIZE,
        max_connection_lifetime=_DEFAULT_CONNECTION_LIFETIME,
        connection_acquisition_timeout=30,
        notifications_min_severity=os.getenv(
            "NEO4J_NOTIFICATIONS_MIN_SEVERITY", "OFF"
        ),
    )


def get_driver(workspace=None):
    """
    Create a Neo4j driver, loading credentials from <workspace>/.env.neo4j.

    Raises:
      ValueError              — NEO4J_PASSWORD not set.
      Neo4jConnectionError    — Neo4j unreachable, auth failed, or TLS handshake failed.

    Caller is responsible for closing the driver. Consider using
    `MemoryClient` as a context manager so the driver is reused
    across multiple operations and cleanly closed.
    """
    ws = get_workspace(workspace)
    load_dotenv(ws / ".env.neo4j")

    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USERNAME", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")

    if not password:
        raise ValueError(
            f"NEO4J_PASSWORD not set. "
            f"Add it to {ws / '.env.neo4j'} or export it as an environment variable."
        )

    try:
        kwargs = _driver_kwargs()
        try:
            driver = GraphDatabase.driver(uri, auth=(user, password), **kwargs)
        except ConfigurationError:
            # neo4j-python < 5.6 rejects the notification kwarg; retry without.
            # A ConfigurationError for any other reason (bad URI scheme) recurs
            # here and falls through to the outer handler.
            kwargs.pop("notifications_min_severity", None)
            driver = GraphDatabase.driver(uri, auth=(user, password), **kwargs)
        driver.verify_connectivity()
        return driver
    except AuthError as e:
        raise Neo4jConnectionError(
            f"Authentication failed for user {user!r} at {uri}. "
            f"Check NEO4J_USERNAME / NEO4J_PASSWORD in {ws / '.env.neo4j'}."
        ) from e
    except (ServiceUnavailable, ConfigurationError, OSError) as e:
        raise Neo4jConnectionError(
            f"Cannot reach Neo4j at {uri}: {e.__class__.__name__}: {e}. "
            f"Verify NEO4J_URI in {ws / '.env.neo4j'} and that the server is running."
        ) from e


# ---------------------------------------------------------------------------
# Schema validation (issue #N5)
# ---------------------------------------------------------------------------

EXPECTED_CONSTRAINTS: Dict[str, tuple] = {
    "fact_id_unique":      ("Fact",      "id"),
    "fact_name_unique":    ("Fact",      "name"),
    "session_id_unique":   ("Session",   "id"),
    "assistant_id_unique": ("Assistant", "id"),
    "source_name_unique":  ("Source",    "name"),
}
# Range indexes — must match neo4j_seed.py and scripts/verify_schema.py
EXPECTED_INDEXES = {
    "fact_assistant_idx":    ("Fact",    "assistant"),
    "fact_source_idx":       ("Fact",    "source"),
    "fact_source_file_idx":  ("Fact",    "source_file"),
    "fact_created_at_idx":   ("Fact",    "created_at"),
    "session_date_idx":      ("Session", "date"),
    "session_assistant_idx": ("Session", "assistant"),
}
# Fulltext index — tracked separately from range indexes
EXPECTED_FULLTEXT = os.getenv("NEO4J_FULLTEXT_INDEX", "fact_content")
EXPECTED_FULLTEXT_PROPS = {"name", "content", "summary"}
EXPECTED_FULLTEXT_KP = os.getenv("NEO4J_FULLTEXT_KP_INDEX", "fact_key_points")
EXPECTED_FULLTEXT_KP_PROPS = {"key_points"}


def validate_schema(driver, *, vector_index: Optional[str] = None) -> Dict[str, Any]:
    """Compare the live schema against ai-memory's expectations.

    Returns a dict ``{"ok": [...], "drift": [...], "missing": [...], "vector_indexes": [...],
    "retrieval_config": "version N" | "missing",
    "vector_filter_props": "ok" | "missing: [..]" | "index not found"}`` describing
    constraints/indexes that match (by name+shape), exist with the right shape but a
    different name (drift — common after IF NOT EXISTS re-creates), or are missing
    entirely. Never raises; intended for diagnostics, not gating.

    If ``vector_index`` is supplied, also verifies that index exists; otherwise
    reports the names of any vector indexes found so the caller can pick one.
    ``vector_filter_props`` checks the same ``vector_index`` (or, when None, the
    env ``NEO4J_VECTOR_INDEX`` default ``fact_embeddings``) for
    ``EXPECTED_VECTOR_FILTER_PROPS`` coverage.
    """
    out: Dict[str, List[str]] = {"ok": [], "drift": [], "missing": [], "vector_indexes": []}
    with driver.session() as s:
        present_c = {}
        for r in s.run("SHOW CONSTRAINTS YIELD name, labelsOrTypes, properties"):
            if r["labelsOrTypes"] and r["properties"]:
                present_c[r["name"]] = (r["labelsOrTypes"][0], r["properties"][0])
        present_i = {}
        for r in s.run("SHOW INDEXES YIELD name, type WHERE type <> 'LOOKUP' RETURN name, type"):
            present_i[r["name"]] = r["type"]
        # Vector indexes (for vector_index sanity)
        for r in s.run("SHOW INDEXES YIELD name, type WHERE type = 'VECTOR' RETURN name"):
            out["vector_indexes"].append(r["name"])
        # Fulltext index — check existence AND property coverage
        ft_found = False
        for r in s.run(
            "SHOW INDEXES YIELD name, type, properties "
            "WHERE type = 'FULLTEXT' AND name = $name RETURN properties",
            name=EXPECTED_FULLTEXT,
        ):
            ft_found = True
            live_props = set(r["properties"] or [])
            missing_props = EXPECTED_FULLTEXT_PROPS - live_props
            if missing_props:
                out["drift"].append(
                    f"fulltext index {EXPECTED_FULLTEXT!r} missing properties: {sorted(missing_props)}"
                )
            else:
                out["ok"].append(f"fulltext index {EXPECTED_FULLTEXT!r}")
        if not ft_found:
            out["missing"].append(f"fulltext index {EXPECTED_FULLTEXT!r}")

        # Fulltext index for key_points — check existence AND property coverage
        ft_kp_found = False
        for r in s.run(
            "SHOW INDEXES YIELD name, type, properties "
            "WHERE type = 'FULLTEXT' AND name = $name RETURN properties",
            name=EXPECTED_FULLTEXT_KP,
        ):
            ft_kp_found = True
            live_props = set(r["properties"] or [])
            missing_props = EXPECTED_FULLTEXT_KP_PROPS - live_props
            if missing_props:
                out["drift"].append(
                    f"fulltext index {EXPECTED_FULLTEXT_KP!r} missing properties: {sorted(missing_props)}"
                )
            else:
                out["ok"].append(f"fulltext index {EXPECTED_FULLTEXT_KP!r}")
        if not ft_kp_found:
            out["missing"].append(f"fulltext index {EXPECTED_FULLTEXT_KP!r}")

        cfg = list(s.run("MATCH (c:RetrievalConfig {id: 'current'}) RETURN c.version AS version"))
        out["retrieval_config"] = f"version {cfg[0]['version']}" if cfg else "missing"

        vi_name = vector_index or os.getenv("NEO4J_VECTOR_INDEX", "fact_embeddings")
        vi_rows = list(s.run(
            "SHOW INDEXES YIELD name, type, properties WHERE type = 'VECTOR' AND name = $name "
            "RETURN properties",
            name=vi_name,
        ))
        if not vi_rows:
            out["vector_filter_props"] = "index not found"
        else:
            missing = sorted(set(EXPECTED_VECTOR_FILTER_PROPS) - set(vi_rows[0]["properties"] or []))
            out["vector_filter_props"] = "ok" if not missing else f"missing: {missing}"

    for cname, shape in EXPECTED_CONSTRAINTS.items():
        if cname in present_c:
            out["ok"].append(f"constraint {cname} ({shape})")
        else:
            # Same-shape, different-name?
            for pname, pshape in present_c.items():
                if pshape == shape and pname not in EXPECTED_CONSTRAINTS:
                    out["drift"].append(f"constraint shape {shape} present as {pname!r} (expected {cname!r})")
                    break
            else:
                out["missing"].append(f"constraint {cname} {shape}")

    for iname in EXPECTED_INDEXES:
        if iname in present_i:
            out["ok"].append(f"index {iname}")
        else:
            out["missing"].append(f"index {iname}")

    if vector_index is not None:
        if vector_index in out["vector_indexes"]:
            out["ok"].append(f"vector index {vector_index!r}")
        else:
            out["missing"].append(
                f"vector index {vector_index!r} "
                f"(available: {sorted(out['vector_indexes']) or 'none'})"
            )

    return out
