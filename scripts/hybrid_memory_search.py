#!/usr/bin/env python3
"""
Hybrid Memory Search — CLI wrapper.

All search logic lives in ai_memory.search; this file is the command-line
entry point only.

Usage:
    python3 hybrid_memory_search.py "inventory management" --max-results 8
    python3 hybrid_memory_search.py "inventory management" --assistant Weft
    python3 hybrid_memory_search.py "HJB" --graph --mind Nova
    python3 hybrid_memory_search.py "your query" --use-embeddings
    python3 hybrid_memory_search.py "your query" --files-only
    python3 hybrid_memory_search.py "market making" --metadata-only
    python3 hybrid_memory_search.py "kill switch" --fields name,teaser
"""
import argparse
import sys

from ai_memory._config import get_workspace
from ai_memory.search import search_faiss, search_files, search_graph, search_vector
from ai_memory.metadata import apply_fields_filter, apply_metadata_only


def format_output(results: list, query_type: str) -> None:
    if not results:
        print(f"No results found ({query_type})")
        return
    print("=" * 60)
    for r in results:
        # All field accesses use .get() so that `--fields` (which strips
        # everything except the requested fields) doesn't trigger KeyError.
        if r.get("source"):
            print(f"Source: {r['source']}")
        if "score" in r:
            print(f"Score: {r['score']}")
        if r.get("assistant"):
            print(f"Assistant: {r['assistant']}")
        if r.get("name"):
            print(f"Name: {r['name']}")
        if r.get("teaser"):
            print(f"Teaser: {r['teaser']}")
        if r.get("relationships"):
            print(f"Related: {r['relationships']}")
        if r.get("summary"):
            print(f"Summary: {r['summary'][:500]}")
        if r.get("content"):
            print(f"Content:\n{r['content'][:500]}")
        if r.get("key_points"):
            print("Key points:")
            for kp in r["key_points"][:10]:
                print(f"  - {kp}")
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Hybrid memory search")
    parser.add_argument("query", help="Search query")
    parser.add_argument("--max-results", "-n", type=int, default=5)
    parser.add_argument("--graph", action="store_true", help="Include graph relationships")
    parser.add_argument("--files-only", action="store_true", help="Only search files")
    parser.add_argument("--use-embeddings", action="store_true",
                        help="Use local FAISS index instead of Neo4j vector search")
    parser.add_argument("--assistant", "--mind", dest="assistant",
                        help="Filter to this assistant/mind (Phase 2 multi-tenancy)")
    parser.add_argument("--metadata-only", action="store_true",
                        help="Return lightweight metadata only (Phase 4 RLM lazy loading)")
    parser.add_argument("--fields", default=None,
                        help="Comma-separated fields to return (e.g. name,summary)")
    args = parser.parse_args()

    workspace = get_workspace()
    assistant = args.assistant

    # FAISS has no tenant index, so --assistant would be silently dropped.
    # Refuse the combination rather than return cross-tenant results.
    if args.use_embeddings and assistant:
        print(
            "Error: --assistant is not supported with --use-embeddings "
            "(FAISS index is not tenant-aware). Drop --use-embeddings to "
            "use the Neo4j vector index, which filters by assistant.",
            file=sys.stderr,
        )
        sys.exit(2)

    if args.files_only:
        results = search_files(args.query, workspace=workspace, max_results=args.max_results)
        format_output(results, "Files")
        return

    # Collect results
    if args.use_embeddings:
        semantic_results = search_faiss(args.query, workspace=workspace, max_results=args.max_results)
        sem_label = "FAISS"
    else:
        semantic_results = search_vector(args.query, workspace=workspace,
                                         max_results=args.max_results, assistant=assistant)
        sem_label = f"Neo4j Vector{' [' + assistant + ']' if assistant else ''}"

    graph_results = []
    if args.graph:
        graph_results = search_graph(args.query, workspace=workspace,
                                     max_results=args.max_results, assistant=assistant)

    file_results = search_files(args.query, workspace=workspace, max_results=args.max_results)

    # Apply Phase 4 transforms before output
    if args.metadata_only:
        semantic_results = [apply_metadata_only(r) for r in semantic_results]
        graph_results = [apply_metadata_only(r) for r in graph_results]
        file_results = [apply_metadata_only(r) for r in file_results]

    if args.fields:
        requested = [f.strip() for f in args.fields.split(',')]
        semantic_results = [apply_fields_filter(r, requested) for r in semantic_results]
        graph_results = [apply_fields_filter(r, requested) for r in graph_results]
        file_results = [apply_fields_filter(r, requested) for r in file_results]

    print(f"Semantic result ({sem_label})")
    format_output(semantic_results, sem_label)

    if args.graph:
        print(f"\nGraph Relationships (Neo4j){' [' + assistant + ']' if assistant else ''}")
        format_output(graph_results, "Neo4j Graph")

    print("\nFile Search (grep)")
    format_output(file_results, "Files")


if __name__ == "__main__":
    main()
