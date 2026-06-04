"""Exception hierarchy for the ai_memory library.

The Neo4j backends used to swallow infrastructure errors (connection refused,
auth failures, missing indexes) and return ``[]``. Callers couldn't tell
"zero hits" from "Neo4j is unreachable". v1.3.2 distinguishes them:

  * Query ran, no hits          → ``[]``  (unchanged)
  * Infrastructure failure      → raise ``Neo4jConnectionError`` / ``Neo4jIndexNotFoundError`` /
                                  generic ``Neo4jQueryError``

Programmer errors (bad Cypher) propagate as their original neo4j-driver
exceptions — they shouldn't be caught by application code.
"""


class AIMemoryError(Exception):
    """Base class for all ai_memory exceptions."""


class Neo4jError(AIMemoryError):
    """Base class for Neo4j-related issues that callers might want to handle."""


class Neo4jConnectionError(Neo4jError):
    """The driver could not reach Neo4j (refused, DNS, auth, TLS, timeout)."""


class Neo4jIndexNotFoundError(Neo4jError):
    """A required vector / fulltext index doesn't exist under the configured name.

    Attached attribute ``index_name`` holds the configured name; ``available``
    holds the list of indexes Neo4j reports as present (for the actionable hint).
    """

    def __init__(self, index_name, kind="vector", available=None):
        self.index_name = index_name
        self.kind = kind
        self.available = available or []
        hint = (
            f" Available {kind} indexes: {sorted(self.available)}."
            if self.available else
            f" No {kind} indexes appear to exist — has scripts/neo4j_seed.py run?"
        )
        super().__init__(
            f"Neo4j {kind} index {index_name!r} not found.{hint} "
            f"Set NEO4J_VECTOR_INDEX in .env.neo4j (or the workspace) to override."
        )


class Neo4jQueryError(Neo4jError):
    """An expected Cypher query failed for a reason other than the above."""
