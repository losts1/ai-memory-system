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

# Load environment
load_dotenv(Path.home() / ".openclaw" / "workspace" / ".env.neo4j")

from neo4j import GraphDatabase

# Must match NEO4J_VECTOR_INDEX in hybrid_memory_search.py (default: fact_embeddings)
VECTOR_INDEX = os.getenv("NEO4J_VECTOR_INDEX", "fact_embeddings")


def create_schema(driver):
    """Create Neo4j schema for memory system."""

    with driver.session() as session:
        # 1. Constraints (unique IDs)
        print("Creating constraints...")

        constraints = [
            "CREATE CONSTRAINT fact_id_unique IF NOT EXISTS FOR (f:Fact) REQUIRE f.id IS UNIQUE",
            "CREATE CONSTRAINT session_id_unique IF NOT EXISTS FOR (s:Session) REQUIRE s.id IS UNIQUE",
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
            session.run(
                f"CREATE VECTOR INDEX {VECTOR_INDEX} IF NOT EXISTS "
                "FOR (n:Fact) ON (n.embedding) "
                "OPTIONS {indexConfig: {`vector.dimensions`: 768, `vector.similarity_function`: 'cosine'}}"
            )
            # IF NOT EXISTS silently skips creation when another vector index already
            # exists on the same (label, property) — verify the index is actually there
            result = session.run(
                "SHOW INDEXES YIELD name, type WHERE type = 'VECTOR' RETURN name"
            )
            vector_names = [r["name"] for r in result]
            if VECTOR_INDEX in vector_names:
                print(f"  Vector index '{VECTOR_INDEX}' ready (768-dim cosine)")
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
        # Covers Fact.name and Fact.content — the properties actually written by neo4j_sync.py
        print("Creating full-text index...")

        try:
            session.run("""
                CREATE FULLTEXT INDEX fact_content IF NOT EXISTS
                FOR (n:Fact) ON EACH [n.name, n.content]
            """)
        except Exception as e:
            if "already exists" not in str(e).lower():
                print(f"  Warning: {e}")

    print("\nSchema created successfully!")


def verify_schema(driver):
    """Verify schema was created."""

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
