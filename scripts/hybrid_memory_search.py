#!/usr/bin/env python3
"""
Hybrid Memory Search - Neo4j Vector + Graph + File Search

Searches across:
1. Neo4j vector index (semantic similarity)
2. Neo4j knowledge graph (relationships)
3. FAISS local embeddings (fallback)
4. Memory files (text search)

Usage:
    python3 hybrid_memory_search.py "your query" --max-results 5
    python3 hybrid_memory_search.py "your query" --graph --max-results 5
    python3 hybrid_memory_search.py "your query" --use-embeddings
    python3 hybrid_memory_search.py "your query" --files-only
"""

import argparse
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import subprocess
import json

# Load environment
load_dotenv(Path.home() / ".openclaw" / "workspace" / ".env.neo4j")

MEMORY_DIR = Path.home() / ".openclaw" / "workspace" / "memory"

def search_neo4j_vector(query: str, max_results: int = 5):
    """Search Neo4j vector index for semantic similarity."""
    try:
        from neo4j import GraphDatabase
        import ollama

        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        username = os.getenv("NEO4J_USERNAME", "neo4j")
        password = os.getenv("NEO4J_PASSWORD")

        if not password:
            return []

        # Get embedding from Ollama
        response = ollama.embeddings(model="nomic-embed-text", prompt=query)
        embedding = response["embedding"]

        driver = GraphDatabase.driver(uri, auth=(username, password))

        with driver.session() as session:
            # Vector similarity search
            cypher = """
            CALL db.index.vector.queryNodes('fact_embeddings', $k, $embedding)
            YIELD node, score
            RETURN node.id as id, node.name as name, node.summary as summary,
                   node.content as content, score
            ORDER BY score DESC
            LIMIT $k
            """

            result = session.run(cypher, k=max_results, embedding=embedding)
            results = []

            for record in result:
                results.append({
                    "source": f"neo4j://Fact/{record['name']}",
                    "score": round(record["score"], 1),
                    "name": record["name"],
                    "summary": record["summary"],
                    "content": record["content"][:500] if record["content"] else None
                })

            driver.close()
            return results

    except Exception as e:
        print(f"Neo4j search error: {e}", file=sys.stderr)
        return []

def search_neo4j_graph(query: str, max_results: int = 5):
    """Search Neo4j knowledge graph for relationships."""
    try:
        from neo4j import GraphDatabase

        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        username = os.getenv("NEO4J_USERNAME", "neo4j")
        password = os.getenv("NEO4J_PASSWORD")

        if not password:
            return []

        driver = GraphDatabase.driver(uri, auth=(username, password))

        with driver.session() as session:
            # Full-text search + relationship traversal
            cypher = """
            CALL db.index.fulltext.queryNodes('fact_content', $query)
            YIELD node, score
            OPTIONAL MATCH (node)-[r]-(related:Fact)
            RETURN node.id as id, node.name as name, node.summary as summary,
                   score, collect(DISTINCT {rel: type(r), name: related.name}) as relationships
            ORDER BY score DESC
            LIMIT $limit
            """

            result = session.run(cypher, query=query, limit=max_results)
            results = []

            for record in result:
                rels = record["relationships"] if record["relationships"] else []
                rel_str = ", ".join([f"{r['rel']}: {r['name']}" for r in rels if r['name']])

                results.append({
                    "source": f"neo4j://Fact/{record['name']}",
                    "score": round(record["score"], 1),
                    "name": record["name"],
                    "summary": record["summary"],
                    "relationships": rel_str
                })

            driver.close()
            return results

    except Exception as e:
        print(f"Neo4j graph search error: {e}", file=sys.stderr)
        return []

def search_files(query: str, max_results: int = 5):
    """Search memory files via grep."""
    results = []

    try:
        # Search MEMORY.md
        memory_file = MEMORY_DIR.parent / "MEMORY.md"
        if memory_file.exists():
            result = subprocess.run(
                ["grep", "-i", "-C", "2", query, str(memory_file)],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                results.append({
                    "source": str(memory_file),
                    "score": 5.0,
                    "content": result.stdout[:500]
                })

        # Search daily files
        daily_dir = MEMORY_DIR
        daily_files = sorted(daily_dir.glob("*.md"), reverse=True)[:30]  # Last 30 days

        for f in daily_files:
            result = subprocess.run(
                ["grep", "-i", "-C", "2", query, str(f)],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                results.append({
                    "source": str(f),
                    "score": 3.0,
                    "content": result.stdout[:300]
                })

                if len(results) >= max_results:
                    break

    except Exception as e:
        print(f"File search error: {e}", file=sys.stderr)

    return results

def format_output(results: list, query_type: str):
    """Format results for display."""
    if not results:
        print(f"No results found ({query_type})")
        return

    print("=" * 60)
    for r in results:
        print(f"Source: {r['source']}")
        print(f"Score: {r.get('score', 'N/A')}")
        if r.get('name'):
            print(f"Name: {r['name']}")
        if r.get('summary'):
            print(f"Summary: {r['summary']}")
        if r.get('relationships'):
            print(f"Related: {r['relationships']}")
        if r.get('content'):
            print(f"Content:\n{r['content'][:500]}")
        print()

def main():
    parser = argparse.ArgumentParser(description="Hybrid memory search")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--max-results", "-n", type=int, default=5, help="Max results per source")
    parser.add_argument("--graph", action="store_true", help="Include graph relationships")
    parser.add_argument("--use-embeddings", action="store_true", help="Use FAISS embeddings")
    parser.add_argument("--files-only", action="store_true", help="Only search files")

    args = parser.parse_args()

    if args.files_only:
        results = search_files(args.query, args.max_results)
        format_output(results, "Files")
        return

    # Neo4j vector search
    print("Semantic result (Neo4j Vector)")
    vector_results = search_neo4j_vector(args.query, args.max_results)
    format_output(vector_results, "Neo4j Vector")

    # Graph search (if requested)
    if args.graph:
        print("\nGraph Relationships (Neo4j)")
        graph_results = search_neo4j_graph(args.query, args.max_results)
        format_output(graph_results, "Neo4j Graph")

    # File search
    print("\nFile Search (grep)")
    file_results = search_files(args.query, args.max_results)
    format_output(file_results, "Files")

if __name__ == "__main__":
    main()