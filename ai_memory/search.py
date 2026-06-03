"""
Search functions for the AI Memory System.

Four backends:
  search_vector — Neo4j vector index (semantic similarity via embeddings)
  search_graph  — Neo4j fulltext index + LEARNED_IN relationship traversal
  search_files  — grep over markdown memory files
  search_faiss  — local FAISS index (offline fallback, Layer 5)

All functions return a list of result dicts and never raise — errors are
printed to stderr and an empty list is returned.
"""
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from ai_memory._config import get_driver, get_workspace


def _escape_lucene(query: str) -> str:
    """Escape Lucene special characters for fulltext search."""
    special = r'[\+\-\&\|\!\(\)\{\}\[\]\^\"\~\*\?\:\/\\]'
    return re.sub(special, lambda m: "\\" + m.group(), query)


def search_vector(
    query: str,
    *,
    workspace=None,
    max_results: int = 5,
    assistant: Optional[str] = None,
) -> List[dict]:
    """
    Semantic similarity search via Neo4j vector index.

    Requires Ollama (nomic-embed-text) and a running Neo4j instance.
    Returns [] on any error (Ollama unavailable, Neo4j unreachable, etc.).
    """
    try:
        import ollama

        response = ollama.embeddings(model="nomic-embed-text", prompt=query)
        embedding = response["embedding"]

        vector_index = os.getenv("NEO4J_VECTOR_INDEX", "fact_embeddings")
        driver = get_driver(workspace)
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
                result = session.run(
                    cypher,
                    vector_index=vector_index,
                    k=max_results,
                    embedding=embedding,
                    assistant=assistant,
                )
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


def search_graph(
    query: str,
    *,
    workspace=None,
    max_results: int = 5,
    assistant: Optional[str] = None,
) -> List[dict]:
    """
    Fulltext + relationship graph search via Neo4j.

    Uses the 'fact_content' fulltext index created by neo4j_seed.py.
    Returns [] on any error.
    """
    try:
        lucene_query = _escape_lucene(query)
        driver = get_driver(workspace)
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
                result = session.run(
                    cypher,
                    lucene_query=lucene_query,
                    limit=max_results,
                    assistant=assistant,
                )
                results = []
                for record in result:
                    related = record["related_facts"] if record["related_facts"] else []
                    r = {
                        "source": f"neo4j://Fact/{record['name']}",
                        "score": round(record["score"], 3),
                        "name": record["name"],
                        "relationships": ", ".join([r for r in related if r]),
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


def search_files(
    query: str,
    *,
    workspace=None,
    max_results: int = 5,
) -> List[dict]:
    """
    Search memory files via grep (fixed-string, case-insensitive).

    Searches MEMORY.md first (score 5.0), then daily *.md files (score 3.0).
    Returns [] if no matches or memory directory doesn't exist.
    """
    ws = get_workspace(workspace)
    memory_dir = ws / "memory"
    results = []

    try:
        memory_file = ws / "MEMORY.md"
        if memory_file.exists():
            proc = subprocess.run(
                ["grep", "-F", "-i", "-C", "2", "--", query, str(memory_file)],
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0:
                results.append({
                    "source": str(memory_file),
                    "score": 5.0,
                    "content": proc.stdout[:500],
                })

        if memory_dir.exists():
            daily_files = sorted(memory_dir.glob("*.md"), reverse=True)[:30]
            for f in daily_files:
                proc = subprocess.run(
                    ["grep", "-F", "-i", "-C", "2", "--", query, str(f)],
                    capture_output=True,
                    text=True,
                )
                if proc.returncode == 0:
                    results.append({
                        "source": str(f),
                        "score": 3.0,
                        "content": proc.stdout[:300],
                    })
                    if len(results) >= max_results:
                        break

    except Exception as e:
        print(f"File search error: {e}", file=sys.stderr)

    return results


def search_faiss(
    query: str,
    *,
    workspace=None,
    max_results: int = 5,
) -> List[dict]:
    """
    Search local FAISS index for semantic similarity (Layer 5 — offline fallback).

    Returns [] if the FAISS index hasn't been built yet.
    """
    ws = get_workspace(workspace)
    index_path = ws / "memory" / "embeddings" / "faiss.index"
    meta_path = ws / "memory" / "embeddings" / "faiss_meta.pkl"

    if not index_path.exists() or not meta_path.exists():
        return []

    try:
        import faiss
        import numpy as np
        import pickle
        import ollama

        embedding = ollama.embeddings(model="nomic-embed-text", prompt=query)["embedding"]
        index = faiss.read_index(str(index_path))
        with open(meta_path, "rb") as f:
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
