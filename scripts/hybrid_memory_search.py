#!/usr/bin/env python3
"""
Hybrid Memory Search - Neo4j Vector + Graph + FAISS + File Search

Searches across:
1. Neo4j vector index (semantic similarity via Fact embeddings)
2. Neo4j knowledge graph (fulltext + relationships)
3. FAISS local index (offline semantic search, Layer 5)
4. Memory files (grep fallback)

Supports multi-mind filtering via the Phase 2 `assistant` property.

Usage:
    python3 hybrid_memory_search.py "inventory management" --max-results 8
    python3 hybrid_memory_search.py "inventory management" --assistant Weft
    python3 hybrid_memory_search.py "HJB" --graph --mind Nova
    python3 hybrid_memory_search.py "your query" --use-embeddings
    python3 hybrid_memory_search.py "your query" --files-only
"""

import argparse
import os
import re
import sys
from pathlib import Path
from dotenv import load_dotenv
import subprocess

# Workspace directory: set AI_MEMORY_DIR env var to override default (~/.ai-memory)
_WORKSPACE = Path(os.getenv("AI_MEMORY_DIR", str(Path.home() / ".ai-memory")))
load_dotenv(_WORKSPACE / ".env.neo4j")

MEMORY_DIR = _WORKSPACE / "memory"
# Override with NEO4J_VECTOR_INDEX env var if your existing index has a different name
VECTOR_INDEX = os.getenv("NEO4J_VECTOR_INDEX", "fact_embeddings")

INDEX_PATH = MEMORY_DIR / "embeddings" / "faiss.index"
META_PATH = MEMORY_DIR / "embeddings" / "faiss_meta.pkl"


def escape_lucene(query: str) -> str:
    """Escape Lucene special characters for fulltext search."""
    special = r'[\+\-\&\|\!\(\)\{\}\[\]\^\"\~\*\?\:\/\\]'
    return re.sub(special, lambda m: "\\" + m.group(), query)


def search_neo4j_vector(query: str, max_results: int = 5, assistant: str | None = None):
    """Search Neo4j vector index for semantic similarity.

    If assistant is provided, only returns nodes where node.assistant matches.
    This enables multi-mind / submind scoped search (Phase 2).
    """
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
        try:
            with driver.session() as session:
                cypher = """
                CALL db.index.vector.queryNodes($vector_index, $k, $embedding)
                YIELD node, score
                WHERE $assistant IS NULL OR node.assistant = $assistant
                RETURN node.id AS id, node.name AS name, node.content AS content, 
                       node.assistant AS assistant, score
                ORDER BY score DESC
                LIMIT $k
                """
                result = session.run(cypher, vector_index=VECTOR_INDEX, k=max_results, 
                                     embedding=embedding, assistant=assistant)
                results = []
                for record in result:
                    r = {
                        "source": f"neo4j://Fact/{record['name']}",
                        "score": round(record["score"], 3),
                        "name": record["name"],
                        "content": record["content"][:500] if record["content"] else None,
                    }
                    if record.get("assistant"):
                        r["assistant"] = record["assistant"]
                    results.append(r)
        finally:
            driver.close()

        return results

    except Exception as e:
        print(f"Neo4j vector search error: {e}", file=sys.stderr)
        return []


def search_neo4j_graph(query: str, max_results: int = 5, assistant: str | None = None):
    """Search Neo4j knowledge graph via fulltext index + relationship traversal.

    If assistant is provided, filters to nodes tagged with that assistant.
    """
    try:
        from neo4j import GraphDatabase

        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        username = os.getenv("NEO4J_USERNAME", "neo4j")
        password = os.getenv("NEO4J_PASSWORD")

        if not password:
            return []

        # Escape Lucene special chars to prevent parse errors
        lucene_query = escape_lucene(query)

        driver = GraphDatabase.driver(uri, auth=(username, password))
        try:
            with driver.session() as session:
                cypher = """
                CALL db.index.fulltext.queryNodes('fact_content', $lucene_query)
                YIELD node, score
                WHERE $assistant IS NULL OR node.assistant = $assistant
                OPTIONAL MATCH (node)-[:LEARNED_IN]->(s:Session)<-[:LEARNED_IN]-(related:Fact)
                WHERE related.id <> node.id
                  AND ($assistant IS NULL OR related.assistant = $assistant)
                RETURN node.id AS id, node.name AS name,
                       node.assistant AS assistant,
                       score, collect(DISTINCT related.name)[0..5] AS related_facts
                ORDER BY score DESC
                LIMIT $limit
                """
                result = session.run(cypher, lucene_query=lucene_query, limit=max_results, assistant=assistant)
                results = []
                for record in result:
                    related = record["related_facts"] if record["related_facts"] else []
                    rel_str = ", ".join([r for r in related if r])
                    r = {
                        "source": f"neo4j://Fact/{record['name']}",
                        "score": round(record["score"], 3),
                        "name": record["name"],
                        "relationships": rel_str,
                    }
                    if record.get("assistant"):
                        r["assistant"] = record["assistant"]
                    results.append(r)
        finally:
            driver.close()

        return results

    except Exception as e:
        print(f"Neo4j graph search error: {e}", file=sys.stderr)
        return []


def search_files(query: str, max_results: int = 5):
    """Search memory files via grep (fixed-string, flag-safe)."""
    results = []

    try:
        # Search MEMORY.md
        memory_file = MEMORY_DIR.parent / "MEMORY.md"
        if memory_file.exists():
            result = subprocess.run(
                ["grep", "-F", "-i", "-C", "2", "--", query, str(memory_file)],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                results.append({
                    "source": str(memory_file),
                    "score": 5.0,
                    "content": result.stdout[:500],
                })

        # Search daily files (most recent 30)
        daily_files = sorted(MEMORY_DIR.glob("*.md"), reverse=True)[:30]

        for f in daily_files:
            result = subprocess.run(
                ["grep", "-F", "-i", "-C", "2", "--", query, str(f)],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                results.append({
                    "source": str(f),
                    "score": 3.0,
                    "content": result.stdout[:300],
                })
                if len(results) >= max_results:
                    break

    except Exception as e:
        print(f"File search error: {e}", file=sys.stderr)

    return results


def search_faiss(query: str, max_results: int = 5):
    """Search local FAISS index for semantic similarity (Layer 5 — offline fallback)."""
    if not INDEX_PATH.exists() or not META_PATH.exists():
        return []

    try:
        import faiss
        import numpy as np
        import pickle
        import ollama

        embedding = ollama.embeddings(model="nomic-embed-text", prompt=query)["embedding"]

        index = faiss.read_index(str(INDEX_PATH))
        with open(META_PATH, "rb") as f:
            meta = pickle.load(f)

        q = np.array([embedding], dtype=np.float32)
        distances, indices = index.search(q, max_results)

        return [
            {
                "source": meta[i]["source"],
                "score": float(distances[0][j]),
                "name": meta[i]["name"],
            }
            for j, i in enumerate(indices[0])
            if i != -1
        ]

    except Exception as e:
        print(f"FAISS search error: {e}", file=sys.stderr)
        return []


def format_output(results: list, query_type: str):
    """Format results for display."""
    if not results:
        print(f"No results found ({query_type})")
        return

    print("=" * 60)
    for r in results:
        print(f"Source: {r['source']}")
        print(f"Score: {r.get('score', 'N/A')}")
        if r.get("assistant"):
            print(f"Assistant: {r['assistant']}")
        if r.get("name"):
            print(f"Name: {r['name']}")
        if r.get("relationships"):
            print(f"Related: {r['relationships']}")
        if r.get("content"):
            print(f"Content:\n{r['content'][:500]}")
        print()


def main():
    parser = argparse.ArgumentParser(description="Hybrid memory search")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--max-results", "-n", type=int, default=5, help="Max results per source")
    parser.add_argument("--graph", action="store_true", help="Include graph relationships")
    parser.add_argument("--files-only", action="store_true", help="Only search files")
    parser.add_argument("--use-embeddings", action="store_true",
                        help="Use local FAISS index instead of Neo4j vector search")
    parser.add_argument("--assistant", "--mind", dest="assistant",
                        help="Filter results to only those created by this assistant/mind (e.g. Weft, Nova). Matches the Phase 2 assistant property on Fact/Session nodes.")

    args = parser.parse_args()

    assistant = args.assistant

    if args.files_only:
        results = search_files(args.query, args.max_results)
        format_output(results, "Files")
        return

    # Semantic search — FAISS (local) or Neo4j vector
    if args.use_embeddings:
        print("Semantic result (FAISS Local)")
        semantic_results = search_faiss(args.query, args.max_results)
        format_output(semantic_results, "FAISS")
    else:
        print(f"Semantic result (Neo4j Vector){' [' + assistant + ']' if assistant else ''}")
        semantic_results = search_neo4j_vector(args.query, args.max_results, assistant=assistant)
        format_output(semantic_results, "Neo4j Vector")

    # Graph search (if requested)
    if args.graph:
        print(f"\nGraph Relationships (Neo4j){' [' + assistant + ']' if assistant else ''}")
        graph_results = search_neo4j_graph(args.query, args.max_results, assistant=assistant)
        format_output(graph_results, "Neo4j Graph")

    # File search
    print("\nFile Search (grep)")
    file_results = search_files(args.query, args.max_results)
    format_output(file_results, "Files")


if __name__ == "__main__":
    main()
