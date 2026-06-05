"""
Search functions for the AI Memory System.

Four backends:
  search_vector — Neo4j vector index (semantic similarity via embeddings)
  search_graph  — Neo4j fulltext + RELATED_TO/LEARNED_IN graph traversal
  search_files  — grep over markdown memory files
  search_faiss  — local FAISS index (offline fallback, Layer 5)

Error semantics (issue #N4):
  * Query ran, zero hits          → return ``[]`` (unchanged)
  * Neo4j unreachable / auth fail → raise ``Neo4jConnectionError``
  * Vector index name not found   → raise ``Neo4jIndexNotFoundError``
  * Other Cypher errors           → raise ``Neo4jQueryError``

Drivers can be supplied externally via the ``driver`` keyword to enable pooling
across calls (issue #N1). If absent, the function creates and closes its own.
"""
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from neo4j.exceptions import ClientError

from ai_memory._config import get_driver, get_query_timeout, get_workspace
from ai_memory.exceptions import (
    Neo4jConnectionError,
    Neo4jIndexNotFoundError,
    Neo4jQueryError,
)


def _list_vector_indexes(driver) -> list:
    """Return all VECTOR index names. Used to build actionable error messages."""
    try:
        with driver.session() as s:
            return [r["name"] for r in s.run(
                "SHOW INDEXES YIELD name, type WHERE type = 'VECTOR' RETURN name"
            )]
    except Exception:
        return []


def _classify_client_error(driver, e: ClientError, vector_index: Optional[str] = None):
    """Map a neo4j ClientError to an ai_memory exception class with context.

    The vector-index-missing case (very common when users don't override
    NEO4J_VECTOR_INDEX) gets a discoverability hint.
    """
    msg = str(e)
    if "no such vector schema index" in msg.lower() or "no such index" in msg.lower():
        available = _list_vector_indexes(driver) if driver is not None else []
        return Neo4jIndexNotFoundError(vector_index or "<unknown>",
                                       kind="vector", available=available)
    return Neo4jQueryError(msg)


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
    driver=None,
) -> List[dict]:
    """
    Semantic similarity search via Neo4j vector index.

    Requires Ollama (nomic-embed-text) and a running Neo4j instance.
    Empty / whitespace-only queries return [] without contacting Ollama.

    Pass an existing ``driver`` to reuse it across calls (MemoryClient does
    this automatically). When omitted, a one-shot driver is created+closed.

    Raises:
      Neo4jConnectionError    — Neo4j unreachable or auth failed.
      Neo4jIndexNotFoundError — configured vector index doesn't exist on the DB.
      Neo4jQueryError         — other Cypher / runtime error.
    Returns [] only when Ollama is unreachable, the query is empty,
    or the query ran successfully with zero matches.
    """
    if not query or not query.strip():
        return []
    try:
        import ollama
        response = ollama.embeddings(model="nomic-embed-text", prompt=query)
        embedding = response["embedding"]
    except Exception as e:
        # Ollama unavailable. Don't promote this to a Neo4jError — it's a
        # separate dependency. Surface to stderr for ops visibility, return [].
        print(f"Embedding error (Ollama unreachable?): {e}", file=sys.stderr)
        return []

    owns_driver = driver is None
    if owns_driver:
        driver = get_driver(workspace)  # raises Neo4jConnectionError on its own
    vector_index = os.getenv("NEO4J_VECTOR_INDEX", "fact_embeddings")
    try:
        with driver.session() as session:
            cypher = """
            CALL db.index.vector.queryNodes($vector_index, $k, $embedding)
            YIELD node, score
            WHERE $assistant IS NULL OR node.assistant = $assistant
            RETURN node.name AS name,
                   coalesce(node.content, node.summary) AS content,
                   node.summary AS summary,
                   node.key_points AS key_points,
                   node.assistant AS assistant, score
            ORDER BY score DESC
            LIMIT $k
            """
            try:
                result = session.run(
                    cypher,
                    vector_index=vector_index,
                    k=max_results,
                    embedding=embedding,
                    assistant=assistant,
                    timeout=get_query_timeout(),
                )
                rows = list(result)
            except ClientError as e:
                raise _classify_client_error(driver, e, vector_index=vector_index) from e
            results = []
            for record in rows:
                r = {
                    "source": f"neo4j://Fact/{record['name']}",
                    "score": round(record["score"], 3),
                    "name": record["name"],
                    "content": record["content"][:500] if record["content"] else None,
                }
                if record.get("summary"):
                    r["summary"] = record["summary"]
                if record.get("key_points"):
                    r["key_points"] = record["key_points"]
                if record.get("assistant"):
                    r["assistant"] = record["assistant"]
                results.append(r)
        return results
    finally:
        if owns_driver:
            driver.close()


def search_graph(
    query: str,
    *,
    workspace=None,
    max_results: int = 5,
    assistant: Optional[str] = None,
    driver=None,
) -> List[dict]:
    """
    Fulltext + relationship graph search via Neo4j.

    Uses the fulltext index (NEO4J_FULLTEXT_INDEX env var, default 'fact_content')
    created by neo4j_seed.py and walks both RELATED_TO (the modern Word-index
    path) and LEARNED_IN (the legacy Session path) to compute ``related_facts``. Empty / whitespace-only
    queries return [] before Lucene escaping.

    Raises the same Neo4j exception hierarchy as ``search_vector``.
    """
    if not query or not query.strip():
        return []
    lucene_query = _escape_lucene(query)

    fulltext_index = os.getenv("NEO4J_FULLTEXT_INDEX", "fact_content")
    owns_driver = driver is None
    if owns_driver:
        driver = get_driver(workspace)
    try:
        with driver.session() as session:
            # Issue #N3: walk RELATED_TO (covers ~77% of facts in real graphs)
            # AND LEARNED_IN (covers the legacy ~4%); UNION via the alternation
            # pattern. Previously only LEARNED_IN was traversed, dropping
            # related_facts for 73% of results silently.
            cypher = """
            CALL db.index.fulltext.queryNodes($fulltext_index, $lucene_query)
            YIELD node, score
            WHERE $assistant IS NULL OR node.assistant = $assistant
            OPTIONAL MATCH (node)-[:RELATED_TO|LEARNED_IN]-(related:Fact)
            WHERE related.name <> node.name
              AND ($assistant IS NULL OR related.assistant = $assistant)
            RETURN node.name AS name,
                   coalesce(node.content, node.summary) AS content,
                   node.summary AS summary,
                   node.key_points AS key_points,
                   node.assistant AS assistant,
                   score, collect(DISTINCT related.name)[0..5] AS related_facts
            ORDER BY score DESC
            LIMIT $limit
            """
            try:
                result = session.run(
                    cypher,
                    fulltext_index=fulltext_index,
                    lucene_query=lucene_query,
                    limit=max_results,
                    assistant=assistant,
                    timeout=get_query_timeout(),
                )
                rows = list(result)
            except ClientError as e:
                raise _classify_client_error(driver, e) from e
            results = []
            for record in rows:
                related = record["related_facts"] if record["related_facts"] else []
                r = {
                    "source": f"neo4j://Fact/{record['name']}",
                    "score": round(record["score"], 3),
                    "name": record["name"],
                    "relationships": ", ".join([rn for rn in related if rn]),
                    "related_count": len(related),
                }
                if record["content"]:
                    r["content"] = record["content"][:500]
                if record.get("summary"):
                    r["summary"] = record["summary"]
                if record.get("key_points"):
                    r["key_points"] = record["key_points"]
                if record.get("assistant"):
                    r["assistant"] = record["assistant"]
                results.append(r)
        return results
    finally:
        if owns_driver:
            driver.close()


def search_files(
    query: str,
    *,
    workspace=None,
    max_results: int = 5,
    max_files: Optional[int] = None,
) -> List[dict]:
    """
    Search memory files via grep (fixed-string, case-insensitive).

    Empty / whitespace-only queries short-circuit to [] — ``grep -F ""``
    matches every line of every file, so unguarded empty queries can flood
    output (issue #40, sibling to #39's guard on the Neo4j/FAISS backends).

    Searches MEMORY.md first (score 5.0), then *.md files in ``memory/``
    (score 3.0), sorted by mtime descending so the most recently touched
    files are searched first.

    ``max_files`` caps how many *.md files are scanned (None = no cap).
    Useful when the memory dir is small and you want full coverage; the
    previous behaviour was a hard 30-file cap reverse-alphabetic, which
    silently missed half of any semantically-named corpus.

    ``MEMORY.md`` is looked up first at ``workspace/MEMORY.md`` (the
    project-redistribution convention) and then at
    ``workspace/memory/MEMORY.md`` (the Claude-style index inside the
    memory dir). First match wins.

    Returns [] if no matches or memory directory doesn't exist.
    """
    if not query or not query.strip():
        return []
    ws = get_workspace(workspace)
    memory_dir = ws / "memory"
    results = []

    try:
        for memory_file in (ws / "MEMORY.md", memory_dir / "MEMORY.md"):
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
                break

        if memory_dir.exists():
            # Sort by mtime descending — works for both YYYY-MM-DD daily notes
            # (newest first) and semantically-named files (recently touched first).
            # Skip MEMORY.md — it was already searched above at score 5.0.
            def _safe_mtime(f: Path) -> float:
                # stat() can raise on broken symlinks or permission issues;
                # treat unreachable files as oldest rather than crash the sort.
                try:
                    return f.stat().st_mtime
                except OSError:
                    return 0.0
            daily_files = sorted(
                (f for f in memory_dir.glob("*.md") if f.name != "MEMORY.md"),
                key=_safe_mtime,
                reverse=True,
            )
            if max_files is not None:
                daily_files = daily_files[:max_files]
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

    Returns [] if the FAISS index hasn't been built yet or the query is empty.
    """
    if not query or not query.strip():
        return []
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
