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

from ai_memory._config import get_driver, get_workspace, validate_schema
from ai_memory.exceptions import (
    AIMemoryError,
    Neo4jConnectionError,
    Neo4jIndexNotFoundError,
    Neo4jQueryError,
)
from ai_memory.metadata import apply_fields_filter, apply_metadata_only, make_teaser
from ai_memory.search import search_faiss, search_files, search_graph, search_vector
from ai_memory.graph import graph_stats, trace_parameter as _trace_parameter, traverse as _traverse
from ai_memory.state import MemoryStateManager
from ai_memory.learn import (
    parse_frontmatter_topic,
    parse_learned_topics,
    sync_facts as _sync_facts,
    rebuild_graph as _rebuild_graph,
)

from pathlib import Path
from typing import List, Optional

# Re-export module-level functions under their original names so
# `from ai_memory import traverse` works as expected.
traverse = _traverse
trace_parameter = _trace_parameter
sync_facts = _sync_facts
rebuild_graph = _rebuild_graph


class MemoryClient:
    """
    High-level facade for all AI Memory System operations.

    Holds a workspace path and (lazily) a cached Neo4j driver. Use as a
    context manager so the driver is closed exactly once at exit.

    Args:
        workspace: Path to the ai-memory workspace. Defaults to
                   AI_MEMORY_DIR env var or ~/.ai-memory.

    Driver pooling:
        Before v1.3.2 every search/traverse call opened its own driver and
        closed it, paying ~28ms per call in handshake overhead. v1.3.2
        caches the driver on the client and reuses it across calls.
        Pass ``driver=`` directly to library functions to bypass the cache.
    """

    def __init__(self, workspace=None):
        self._workspace = get_workspace(workspace)
        self._driver = None  # lazily created on first use

    # ------------------------------------------------------------------
    # Driver lifecycle
    # ------------------------------------------------------------------

    def driver(self):
        """Return the cached Neo4j driver, creating it lazily on first call.

        Raises ``Neo4jConnectionError`` if Neo4j is unreachable.
        """
        if self._driver is None:
            self._driver = get_driver(self._workspace)
        return self._driver

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        """Close the cached driver if one was created."""
        if self._driver is not None:
            self._driver.close()
            self._driver = None

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
            List of result dicts. Empty list if Ollama unavailable or query empty.

        Raises:
            Neo4jConnectionError / Neo4jIndexNotFoundError / Neo4jQueryError —
            see ai_memory.exceptions.
        """
        ws = self._workspace
        if use_embeddings:
            results = search_faiss(query, workspace=ws, max_results=max_results)
        else:
            results = search_vector(
                query, workspace=ws, max_results=max_results,
                assistant=assistant, driver=self.driver(),
            )

        if graph:
            graph_results = search_graph(
                query, workspace=ws, max_results=max_results,
                assistant=assistant, driver=self.driver(),
            )
            # Dedupe by name: vector/FAISS results win, graph fills the rest.
            seen = {r.get("name") for r in results if r.get("name")}
            for r in graph_results:
                name = r.get("name")
                if name and name not in seen:
                    results.append(r)
                    seen.add(name)

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

    def state(self, session_id: Optional[str] = None) -> MemoryStateManager:
        """
        Return a MemoryStateManager, optionally bound to ``session_id``.

        When ``session_id`` is provided, manager methods can omit it; an
        explicit per-call ``session_id`` still overrides the bound default.

        The returned manager holds a Neo4j connection. Caller must call
        ``manager.close()`` when done, or use it as a context manager.

        Example:
            with client.state("weft:main") as mgr:
                mgr.init_session()
                pending = mgr.get_pending()
        """
        return MemoryStateManager(workspace=self._workspace, session_id=session_id)

    # ------------------------------------------------------------------
    # Learn sync
    # ------------------------------------------------------------------

    def learn(
        self,
        days: int = 30,
        *,
        assistant: Optional[str] = None,
    ) -> int:
        """Parse learned topics from the last `days` days and sync to Neo4j.

        Scans {workspace}/memory/*.md for daily notes in the time window.
        Returns the count of successfully synced Fact nodes.

        Note: does not apply saturation filtering. For production sync with
        deduplication, use the `ai-memory learn-sync` CLI command.
        """
        import re
        from datetime import datetime, timedelta

        memory_dir = self._workspace / 'memory'
        if not memory_dir.exists():
            return 0

        cutoff = datetime.now() - timedelta(days=days)
        topics = []
        for filepath in sorted(memory_dir.glob('*.md')):
            if not re.match(r'\d{4}-\d{2}-\d{2}\.md$', filepath.name):
                continue
            try:
                mtime = datetime.fromtimestamp(filepath.stat().st_mtime)
            except OSError:
                continue
            if mtime < cutoff:
                continue
            try:
                content = filepath.read_text(encoding='utf-8', errors='replace')
            except OSError:
                continue
            topics.extend(parse_learned_topics(content, filepath))

        return _sync_facts(topics, workspace=self._workspace, assistant=assistant)


__all__ = [
    'MemoryClient',
    'MemoryStateManager',
    'get_driver',
    'get_workspace',
    'validate_schema',
    'AIMemoryError',
    'Neo4jConnectionError',
    'Neo4jIndexNotFoundError',
    'Neo4jQueryError',
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
    'parse_frontmatter_topic',
    'parse_learned_topics',
    'sync_facts',
    'rebuild_graph',
]
