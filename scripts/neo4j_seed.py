#!/usr/bin/env python3
"""
Neo4j Schema Initialization for AI Memory System

Creates the knowledge graph schema:
- Fact nodes (learned topics)
- Session nodes (raw session logs)
- Relationships (LEARNED_IN)
- Vector index for semantic search (requires Neo4j 5.x+)
- Full-text index for keyword search

Usage:
    python3 neo4j_seed.py
"""

import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Workspace directory: set AI_MEMORY_DIR env var to override default (~/.ai-memory)
_WORKSPACE = Path(os.getenv("AI_MEMORY_DIR", str(Path.home() / ".ai-memory")))
load_dotenv(_WORKSPACE / ".env.neo4j")

from neo4j import GraphDatabase
from neo4j.exceptions import ClientError

from ai_memory._config import EXPECTED_VECTOR_FILTER_PROPS
from ai_memory.vector_index import build_create_index_ddl, build_create_index_ddl_plain

# Must match NEO4J_VECTOR_INDEX in hybrid_memory_search.py (default: fact_embeddings)
VECTOR_INDEX = os.getenv("NEO4J_VECTOR_INDEX", "fact_embeddings")


def seed_vector_index_ddl(with_filters: bool = True) -> str:
    """CREATE VECTOR INDEX ... IF NOT EXISTS ... for VECTOR_INDEX.

    with_filters=True (default): Cypher 25 DDL WITH [filter props] — requires a server
    that accepts the `CYPHER 25` prefix. with_filters=False: plain Cypher 5 DDL with no
    WITH clause, for Cypher-5-only servers (in-index filtering unavailable there; the
    library uses the over-fetch fallback)."""
    ddl = (build_create_index_ddl(VECTOR_INDEX, EXPECTED_VECTOR_FILTER_PROPS) if with_filters
           else build_create_index_ddl_plain(VECTOR_INDEX))
    return ddl.replace(
        f"CREATE VECTOR INDEX `{VECTOR_INDEX}` ",
        f"CREATE VECTOR INDEX `{VECTOR_INDEX}` IF NOT EXISTS ",
        1,
    )


def create_vector_index(session) -> str:
    """Run the filtered (Cypher 25) vector-index DDL; on a Cypher-5-only server fall
    back to the plain DDL (no filter properties). Returns "filtered" or "plain"."""
    try:
        session.run(seed_vector_index_ddl(with_filters=True))
        return "filtered"
    except ClientError as e:
        msg = str(e)
        code = getattr(e, "code", "") or ""
        if "Invalid input 'WITH'" in msg or code.endswith("SyntaxError"):
            print(
                "  Note: this server does not accept Cypher 25 filter properties; creating "
                "the vector index without them (in-index filtering unavailable; the library "
                "uses the over-fetch fallback)"
            )
            session.run(seed_vector_index_ddl(with_filters=False))
            return "plain"
        raise


def create_schema(driver):
    """Create Neo4j schema for memory system."""

    with driver.session() as session:
        # 1. Constraints (unique IDs)
        print("Creating constraints...")

        constraints = [
            "CREATE CONSTRAINT fact_id_unique IF NOT EXISTS FOR (f:Fact) REQUIRE f.id IS UNIQUE",
            # Primary identity for Facts is f.name (v1.2). Both neo4j_sync.py and
            # ai_memory.learn MERGE on name, so this constraint guarantees a single
            # node per fact regardless of which sync path created it.
            "CREATE CONSTRAINT fact_name_unique IF NOT EXISTS FOR (f:Fact) REQUIRE f.name IS UNIQUE",
            "CREATE CONSTRAINT session_id_unique IF NOT EXISTS FOR (s:Session) REQUIRE s.id IS UNIQUE",
            # Phase 2 multi-tenancy: Assistant nodes must have unique stable IDs
            "CREATE CONSTRAINT assistant_id_unique IF NOT EXISTS FOR (a:Assistant) REQUIRE a.id IS UNIQUE",
            # Source nodes are created by ai_memory/learn._sync_fact_tx for provenance tracking
            "CREATE CONSTRAINT source_name_unique IF NOT EXISTS FOR (s:Source) REQUIRE s.name IS UNIQUE",
        ]

        for constraint in constraints:
            try:
                session.run(constraint)
            except Exception as e:
                if "already exists" not in str(e).lower():
                    print(f"  Warning: {e}")

        # 2. Range indexes for common lookups
        print("Creating indexes...")

        indexes = [
            "CREATE INDEX session_date_idx IF NOT EXISTS FOR (s:Session) ON (s.date)",
            "CREATE INDEX fact_source_idx IF NOT EXISTS FOR (f:Fact) ON (f.source)",
            # Phase 2 multi-tenancy: efficient filtering by assistant/mind
            "CREATE INDEX fact_assistant_idx IF NOT EXISTS FOR (f:Fact) ON (f.assistant)",
            "CREATE INDEX session_assistant_idx IF NOT EXISTS FOR (s:Session) ON (s.assistant)",
            # Temporal and source-file lookups (written by ai_memory/learn.py)
            "CREATE INDEX fact_created_at_idx IF NOT EXISTS FOR (f:Fact) ON (f.created_at)",
            "CREATE INDEX fact_source_file_idx IF NOT EXISTS FOR (f:Fact) ON (f.source_file)",
        ]

        for index in indexes:
            try:
                session.run(index)
            except Exception as e:
                if "already exists" not in str(e).lower():
                    print(f"  Warning: {e}")

        # 3. Vector index for semantic search (Neo4j 5.x DDL syntax)
        print("Creating vector index...")

        try:
            create_vector_index(session)
            # IF NOT EXISTS silently skips creation when another vector index already
            # exists on the same (label, property) — verify the index is actually there
            result = session.run(
                "SHOW INDEXES YIELD name, type WHERE type = 'VECTOR' RETURN name"
            )
            vector_names = [r["name"] for r in result]
            if VECTOR_INDEX in vector_names:
                print(f"  Vector index '{VECTOR_INDEX}' ready (768-dim cosine)")
                props_result = session.run(
                    "SHOW INDEXES YIELD name, type, properties "
                    "WHERE type = 'VECTOR' AND name = $name RETURN properties",
                    name=VECTOR_INDEX,
                )
                props_rec = props_result.single()
                live_props = set((props_rec["properties"] if props_rec else None) or [])
                if set(EXPECTED_VECTOR_FILTER_PROPS) - live_props:
                    print(
                        f"  Note: '{VECTOR_INDEX}' exists without filter properties — "
                        "run: python scripts/neo4j_migrate_vector_filters.py --migrate"
                    )
            else:
                print(f"  Note: '{VECTOR_INDEX}' was not created (IF NOT EXISTS skipped it)")
                if vector_names:
                    print(f"  Existing vector index(es): {vector_names}")
                    print(f"  Set NEO4J_VECTOR_INDEX={vector_names[0]} in .env.neo4j to use existing index")
                else:
                    print("  (Vector search requires Neo4j 5.x+)")
        except Exception as e:
            if "already exists" not in str(e).lower():
                print(f"  Note: {e}")
                print("  (Vector search requires Neo4j 5.x+)")

        # 4. Full-text index for keyword search
        # Covers name + content (neo4j_sync.py) + summary (ai_memory/learn.py)
        # MIGRATION: if fact_content already exists with [name, content] only,
        # run `DROP INDEX fact_content` in Neo4j Browser, then re-run this script.
        print("Creating full-text index...")

        try:
            session.run("""
                CREATE FULLTEXT INDEX fact_content IF NOT EXISTS
                FOR (n:Fact) ON EACH [n.name, n.content, n.summary]
            """)
            # Detect existing index with wrong property list (IF NOT EXISTS skips recreation)
            result = session.run(
                "SHOW INDEXES YIELD name, type, properties "
                "WHERE name = 'fact_content' AND type = 'FULLTEXT' RETURN properties"
            )
            rec = result.single()
            if rec is None:
                # IF NOT EXISTS skipped — something named 'fact_content' exists but
                # is not a FULLTEXT index (e.g. a stale RANGE index with the same name).
                print(
                    "  Warning: 'fact_content' exists but is NOT a FULLTEXT index.\n"
                    "  Run: DROP INDEX fact_content; then re-run neo4j_seed.py."
                )
            elif "summary" not in (rec["properties"] or []):
                print(
                    "  Warning: fact_content index exists but does NOT cover n.summary.\n"
                    "  Run: DROP INDEX fact_content; then re-run neo4j_seed.py to upgrade."
                )
            else:
                print("  Full-text index fact_content ready ([name, content, summary])")
        except Exception as e:
            if "already exists" not in str(e).lower():
                print(f"  Warning: {e}")

        # 5. Key-points full-text index for fact highlights
        print("Creating key-points full-text index...")
        try:
            session.run("""
                CREATE FULLTEXT INDEX fact_key_points IF NOT EXISTS
                FOR (n:Fact) ON EACH [n.key_points]
            """)
        except Exception as e:
            if "already exists" not in str(e).lower():
                print(f"  Warning: {e}")

        # 6. Retrieval config singleton (spec §4): version 1 with no boilerplate so writers can
        #    embed on a fresh install; `ai-memory embed --all` publishes later versions.
        print("Creating retrieval config...")
        session.run(
            "MERGE (c:RetrievalConfig {id: 'current'}) "
            "ON CREATE SET c.version = 1, c.boilerplate = [], c.updated_at = toString(datetime())"
        )
        print("  RetrievalConfig ready")

    print("\nSchema created successfully!")


def verify_schema(driver):
    """Quick count-only sanity check after create_schema().

    Prints total constraint and index counts — does NOT validate that the
    correct named items exist. For rigorous validation run:
        python3 scripts/verify_schema.py
    """

    with driver.session() as session:
        result = session.run("SHOW CONSTRAINTS")
        constraints = list(result)
        print(f"Constraints: {len(constraints)}")

        result = session.run("SHOW INDEXES")
        indexes = list(result)
        print(f"Indexes: {len(indexes)}")

        # Check vector index (Neo4j 5.x+)
        try:
            result = session.run(
                "SHOW INDEXES YIELD name, type WHERE type = 'VECTOR' RETURN name"
            )
            vector_indexes = list(result)
            if vector_indexes:
                print(f"Vector indexes: {[v['name'] for v in vector_indexes]}")
            else:
                print("  Note: no vector index found (requires Neo4j 5.x+)")
        except Exception as e:
            print(f"  Warning: could not verify vector index: {e}")
    print("  Run `python3 scripts/verify_schema.py` for full schema validation.")


def main():
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    username = os.getenv("NEO4J_USERNAME", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")

    if not password:
        print("Error: NEO4J_PASSWORD not set in .env.neo4j")
        sys.exit(1)

    print(f"Connecting to Neo4j at {uri}...")
    driver = None
    try:
        driver = GraphDatabase.driver(uri, auth=(username, password))

        # Test connection
        with driver.session() as session:
            result = session.run("RETURN 1")
            result.single()
        print("Connection successful!\n")

        create_schema(driver)
        print()
        verify_schema(driver)

    finally:
        if driver is not None:
            driver.close()


if __name__ == "__main__":
    main()
