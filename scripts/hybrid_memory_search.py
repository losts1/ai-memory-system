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
from ai_memory.search import search_faiss, search_files, search_hybrid
from ai_memory.metadata import apply_fields_filter, apply_metadata_only


_MEANINGFUL_FIELDS = frozenset({
    "source", "name", "teaser", "summary", "content",
    "relationships", "related_count", "key_points",
})


def _has_meaningful_fields(results: list) -> bool:
    """True iff at least one result carries a field worth displaying.

    `score` alone is not enough — it has no context. Used by both
    format_output (body) and main (section header) to gate the
    --fields-stripped case (issue #43).
    """
    return any(set(r.keys()) & _MEANINGFUL_FIELDS for r in results)


def format_output(results: list, query_type: str) -> None:
    if not results:
        print(f"No results found ({query_type})")
        return
    # Issue #43: if `--fields` stripped every meaningful display field, the
    # section would render as just a separator + a bare Score line. Suppress
    # the body silently; main() also suppresses the section header.
    if not _has_meaningful_fields(results):
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
    parser.add_argument("--graph", action="store_true",
                        help="alias for --mode hybrid (kept for compatibility; no longer "
                             "adds a relationships section)")
    parser.add_argument("--files-only", action="store_true", help="Only search files")
    parser.add_argument("--use-embeddings", action="store_true",
                        help="Use local FAISS index instead of Neo4j vector search")
    parser.add_argument("--assistant", "--mind", dest="assistant",
                        help="Filter to this assistant/mind (Phase 2 multi-tenancy)")
    parser.add_argument("--metadata-only", action="store_true",
                        help="Return lightweight metadata only (Phase 4 RLM lazy loading)")
    parser.add_argument("--fields", default=None,
                        help="Comma-separated fields to return (e.g. name,summary)")
    parser.add_argument("--space", default=None, help="Filter to a space (e.g. shared)")
    parser.add_argument("--mode", choices=("hybrid", "fulltext", "vector"), default="hybrid",
                        help="hybrid (default), fulltext-only, or vector-only")
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
        results = search_faiss(args.query, workspace=workspace, max_results=args.max_results)
        label = "FAISS"
    else:
        results = search_hybrid(args.query, workspace=workspace, k=args.max_results,
                                assistant=assistant, space=args.space,
                                mode="hybrid" if args.graph else args.mode)
        label = f"hybrid{' [' + assistant + ']' if assistant else ''}"

    file_results = search_files(args.query, workspace=workspace, max_results=args.max_results)

    # Apply Phase 4 transforms before output
    if args.metadata_only:
        results = [apply_metadata_only(r) for r in results]
        file_results = [apply_metadata_only(r) for r in file_results]

    if args.fields:
        requested = [f.strip() for f in args.fields.split(',')]
        results = [apply_fields_filter(r, requested) for r in results]
        file_results = [apply_fields_filter(r, requested) for r in file_results]

    def _emit_section(header: str, results: list, query_type: str) -> None:
        # Issue #43: if --fields stripped every meaningful display field,
        # don't render the section header either (the body suppression in
        # format_output would otherwise leave an orphan header).
        if results and not _has_meaningful_fields(results):
            return
        print(header)
        format_output(results, query_type)

    _emit_section(f"Semantic result ({label})", results, label)
    _emit_section("\nFile Search (grep)", file_results, "Files")


if __name__ == "__main__":
    main()
