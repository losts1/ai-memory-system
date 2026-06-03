"""
ai_memory — Core library for the AI Memory System.

Quick start:
    from ai_memory import MemoryClient

    with MemoryClient() as client:
        # Semantic search (requires Neo4j + Ollama)
        results = client.search("transformer attention mechanisms")
        results = client.search("inventory management", assistant="Weft")

        # Graph traversal (requires Neo4j)
        facts = client.traverse("Attention Is All You Need", depth=2)

        # RLM parameter tracing
        matches = client.trace_parameter("Avellaneda-Stoikov", "gamma")

        # Per-session memory state
        mgr = client.state("weft:main")
        mgr.init_session("weft:main")
        # ... use mgr methods ...
        mgr.close()

See README.md for full documentation.
"""

from ai_memory._config import get_driver, get_workspace
from ai_memory.metadata import apply_fields_filter, apply_metadata_only, make_teaser
from ai_memory.search import search_faiss, search_files, search_graph, search_vector
from ai_memory.graph import graph_stats, trace_parameter as _trace_parameter, traverse as _traverse
from ai_memory.state import MemoryStateManager

from pathlib import Path
from typing import List, Optional

# Re-export module-level functions under their original names so
# `from ai_memory import traverse` works as expected.
traverse = _traverse
trace_parameter = _trace_parameter


class MemoryClient:
    """
    High-level facade for all AI Memory System operations.

    Holds a workspace path and can be used as a context manager to ensure
    clean resource lifecycle. The underlying Neo4j driver is created lazily
    on first use and closed on exit.

    Args:
        workspace: Path to the ai-memory workspace. Defaults to
                   AI_MEMORY_DIR env var or ~/.ai-memory.
    """

    def __init__(self, workspace=None):
        self._workspace = get_workspace(workspace)

    # ------------------------------------------------------------------
    # Context manager (optional but recommended for long-running use)
    # ------------------------------------------------------------------

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        """No persistent driver in the facade — nothing to close."""
        pass

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        assistant: Optional[str] = None,
        max_results: int = 5,
        graph: bool = False,
        use_embeddings: bool = False,
        metadata_only: bool = False,
        fields: Optional[List[str]] = None,
    ) -> List[dict]:
        """
        Hybrid search across Neo4j vector index and optionally the graph.

        Args:
            query:          Search string.
            assistant:      Filter results to this assistant/mind (Phase 2).
            max_results:    Max results per backend.
            graph:          Also run fulltext + relationship graph search.
            use_embeddings: Use local FAISS instead of Neo4j vector search.
            metadata_only:  Return lightweight metadata only (name, teaser, counts).
            fields:         Return only these fields from each result.

        Returns:
            List of result dicts. Empty list if Neo4j/Ollama unavailable.
        """
        ws = self._workspace
        if use_embeddings:
            results = search_faiss(query, workspace=ws, max_results=max_results)
        else:
            results = search_vector(query, workspace=ws, max_results=max_results, assistant=assistant)

        if graph:
            results += search_graph(query, workspace=ws, max_results=max_results, assistant=assistant)

        if metadata_only:
            results = [apply_metadata_only(r) for r in results]
        if fields:
            results = [apply_fields_filter(r, fields) for r in results]

        return results

    # ------------------------------------------------------------------
    # Graph traversal
    # ------------------------------------------------------------------

    def traverse(
        self,
        start: str,
        *,
        depth: int = 2,
        relationship: str = 'RELATED_TO',
        fields: Optional[List[str]] = None,
        filter_word: Optional[str] = None,
        max_nodes: int = 50,
        metadata_only: bool = False,
        assistant: Optional[str] = None,
    ) -> dict:
        """
        Expand the neighbourhood of a Fact node up to `depth` hops.

        Returns a result dict with keys: success, start, depth, relationship,
        assistant, total_nodes, nodes.
        """
        return _traverse(
            start,
            workspace=self._workspace,
            depth=depth,
            relationship=relationship,
            fields=fields,
            filter_word=filter_word,
            max_nodes=max_nodes,
            metadata_only=metadata_only,
            assistant=assistant,
        )

    def trace_parameter(
        self,
        start: str,
        parameter: str,
        *,
        depth: int = 2,
        max_nodes: int = 50,
        metadata_only: bool = False,
        fields: Optional[List[str]] = None,
        assistant: Optional[str] = None,
    ) -> dict:
        """
        RLM-style parameter tracing: find related Facts that mention `parameter`.

        Returns a result dict with keys: success, start, parameter, depth,
        assistant, total_nodes, nodes.
        """
        return _trace_parameter(
            start,
            parameter,
            workspace=self._workspace,
            depth=depth,
            max_nodes=max_nodes,
            metadata_only=metadata_only,
            fields=fields,
            assistant=assistant,
        )

    # ------------------------------------------------------------------
    # Session state
    # ------------------------------------------------------------------

    def state(self, session_id: str) -> MemoryStateManager:
        """
        Return a MemoryStateManager for the given session.

        The returned manager holds a Neo4j connection. Caller must call
        manager.close() when done, or use it as a context manager.

        Example:
            with client.state("weft:main") as mgr:
                mgr.init_session("weft:main")
                pending = mgr.get_pending("weft:main")
        """
        return MemoryStateManager(workspace=self._workspace)


__all__ = [
    'MemoryClient',
    'MemoryStateManager',
    'get_driver',
    'get_workspace',
    'search_vector',
    'search_graph',
    'search_files',
    'search_faiss',
    'traverse',
    'trace_parameter',
    'graph_stats',
    'apply_metadata_only',
    'apply_fields_filter',
    'make_teaser',
]
