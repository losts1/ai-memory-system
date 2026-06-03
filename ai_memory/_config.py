"""Shared Neo4j connection and workspace config for the ai_memory library."""
import os
from pathlib import Path

from dotenv import load_dotenv


def get_workspace(workspace=None) -> Path:
    """Resolve workspace: explicit arg > AI_MEMORY_DIR env > ~/.ai-memory."""
    if workspace is not None:
        return Path(workspace)
    return Path(os.getenv("AI_MEMORY_DIR", str(Path.home() / ".ai-memory")))


def get_driver(workspace=None):
    """
    Create a Neo4j driver, loading credentials from <workspace>/.env.neo4j.

    Raises ValueError if NEO4J_PASSWORD is not set.
    Caller is responsible for closing the driver.
    """
    ws = get_workspace(workspace)
    load_dotenv(ws / ".env.neo4j")

    from neo4j import GraphDatabase  # lazy import — neo4j is optional until used

    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USERNAME", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")

    if not password:
        raise ValueError(
            f"NEO4J_PASSWORD not set. "
            f"Add it to {ws / '.env.neo4j'} or export it as an environment variable."
        )

    driver = GraphDatabase.driver(uri, auth=(user, password))
    driver.verify_connectivity()
    return driver
