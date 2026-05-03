#!/usr/bin/env python3
"""
Neo4j Schema Initialization for AI Memory System

Creates the knowledge graph schema:
- Fact nodes (learned topics with embeddings)
- Session nodes (raw session logs)
- Relationships (LEARNED_IN, RELATED_TO, etc.)
- Vector index for semantic search

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

def create_schema(driver):
    """Create Neo4j schema for memory system."""

    with driver.session() as session:
        # 1. Create constraints (unique IDs)
        print("Creating constraints...")

        constraints = [
            "CREATE CONSTRAINT IF NOT EXISTS FOR (f:Fact) REQUIRE f.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (s:Session) REQUIRE s.id IS UNIQUE",
            "CREATE CONSTRAINT IF NOT EXISTS FOR (t:Topic) REQUIRE t.name IS UNIQUE",
        ]

        for constraint in constraints:
            try:
                session.run(constraint)
            except Exception as e:
                if "already exists" not in str(e).lower():
                    print(f"  Warning: {e}")

        # 2. Create indexes
        print("Creating indexes...")

        indexes = [
            "CREATE INDEX IF NOT EXISTS FOR (f:Fact) ON (f.created)",
            "CREATE INDEX IF NOT EXISTS FOR (f:Fact) ON (f.priority)",
            "CREATE INDEX IF NOT EXISTS FOR (s:Session) ON (s.date)",
            "CREATE INDEX IF NOT EXISTS FOR (t:Topic) ON (t.category)",
        ]

        for index in indexes:
            try:
                session.run(index)
            except Exception as e:
                if "already exists" not in str(e).lower():
                    print(f"  Warning: {e}")

        # 3. Create vector index for embeddings (Neo4j 5.x+)
        print("Creating vector index...")

        vector_index = """
        CALL db.index.vector.createNodeIndex IF NOT EXISTS
        ('fact_embeddings', 'Fact', 'embedding', 768, 'cosine')
        """

        try:
            session.run(vector_index)
            print("  Vector index created (768-dim cosine)")
        except Exception as e:
            # May fail on Neo4j < 5.x or if already exists
            if "already exists" not in str(e).lower():
                print(f"  Note: {e}")
                print("  (Vector search requires Neo4j 5.x+)")

        # 4. Create full-text index for text search
        print("Creating full-text index...")

        fulltext_index = """
        CALL db.index.fulltext.createNodeIndex IF NOT EXISTS
        ('fact_content', ['Fact'], ['content', 'summary'])
        """

        try:
            session.run(fulltext_index)
        except Exception as e:
            if "already exists" not in str(e).lower():
                print(f"  Warning: {e}")

    print("\nSchema created successfully!")

def verify_schema(driver):
    """Verify schema was created."""

    with driver.session() as session:
        # Check constraints
        result = session.run("SHOW CONSTRAINTS")
        constraints = list(result)
        print(f"Constraints: {len(constraints)}")

        # Check indexes
        result = session.run("SHOW INDEXES")
        indexes = list(result)
        print(f"Indexes: {len(indexes)}")

        # Check vector index (Neo4j 5.x+)
        try:
            result = session.run("CALL db.indexes() YIELD name WHERE name CONTAINS 'vector' RETURN name")
            vector_indexes = list(result)
            if vector_indexes:
                print(f"Vector indexes: {[v['name'] for v in vector_indexes]}")
        except:
            pass

def main():
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    username = os.getenv("NEO4J_USERNAME", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")

    if not password:
        print("Error: NEO4J_PASSWORD not set in .env.neo4j")
        sys.exit(1)

    print(f"Connecting to Neo4j at {uri}...")
    driver = GraphDatabase.driver(uri, auth=(username, password))

    try:
        # Test connection
        with driver.session() as session:
            result = session.run("RETURN 1")
            result.single()
        print("Connection successful!\n")

        create_schema(driver)
        print()
        verify_schema(driver)

    finally:
        driver.close()

if __name__ == "__main__":
    main()