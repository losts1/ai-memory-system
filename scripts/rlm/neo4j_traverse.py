#!/usr/bin/env python3
"""
Neo4j Graph Traversal — CLI wrapper.

All traversal logic lives in ai_memory.graph; this file is the CLI entry point.

Usage:
    python3 neo4j_traverse.py --start "Avellaneda-Stoikov" --depth 2
    python3 neo4j_traverse.py --start "Avellaneda-Stoikov" --parameter gamma
    python3 neo4j_traverse.py --start "Avellaneda-Stoikov" --assistant Weft
    python3 neo4j_traverse.py --stats

================================================================================
PHASE 4 — ADVANCED RLM TOOLING (EXPERIMENTAL)
See UPGRADE_PLAN.md for the full Phase 3/4 context.
================================================================================
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

# Allow running this script directly from scripts/rlm/ without installing the package.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ai_memory.graph import (
    DEFAULT_DEPTH,
    DEFAULT_MAX_NODES,
    MAX_DEPTH_CAP,
    graph_stats,
    trace_parameter,
    traverse,
)


# ---------------------------------------------------------------------------
# Pretty-print helpers (CLI-only, not part of the library)
# ---------------------------------------------------------------------------

def _print_node(node: Dict[str, Any]) -> None:
    print(f"\n  {node.get('name', '?')}")
    if node.get('assistant'):
        print(f"    [assistant: {node['assistant']}]")
    if node.get('teaser'):
        print(f"    {node['teaser']}")
    if 'kp_count' in node or 'related_count' in node:
        print(f"    key_points: {node.get('kp_count', 0)}, related: {node.get('related_count', 0)}")
    if node.get('top_words'):
        print(f"    words: {', '.join(node['top_words'])}")
    if node.get('key_points'):
        for kp in node['key_points'][:3]:
            print(f"    - {kp[:100]}")
    if node.get('summary'):
        print(f"    summary: {node['summary'][:200]}")


def _print_traversal_result(result: Dict) -> None:
    print(f"Traversal from '{result['start']}' "
          f"(depth {result['depth']}, rel {result['relationship']})")
    if result.get('assistant'):
        print(f"  [assistant filter: {result['assistant']}]")
    print(f"Found {result['total_nodes']} nodes")
    print("=" * 60)
    for node in result['nodes']:
        _print_node(node)


def _print_parameter_result(result: Dict) -> None:
    print(f"\nRLM Parameter Trace: '{result['parameter']}' from '{result['start']}' "
          f"(depth {result['depth']})")
    if result.get('assistant'):
        print(f"  [assistant filter: {result['assistant']}]")
    print(f"Matches: {result['total_nodes']}")
    print("=" * 60)
    for node in result.get('nodes', []):
        _print_node(node)


def _print_stats(result: Dict) -> None:
    s = result['stats']
    print("Graph Statistics")
    print("=" * 40)
    for label, count in s.get('node_counts', {}).items():
        print(f"  {label}: {count}")
    print("\nEdges:")
    for etype, count in s.get('edge_counts', {}).items():
        print(f"  {etype}: {count}")
    print(f"\nTotal edges: {s.get('total_edges', '?')}")


def _print_result(result: Dict, as_json: bool, pretty_printer) -> None:
    if as_json:
        print(json.dumps(result, indent=2))
    elif result.get('success'):
        pretty_printer(result)
    else:
        print(f"Error: {result.get('error')}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Neo4j graph traversal from a starting Fact node',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 neo4j_traverse.py --start "Avellaneda-Stoikov" --depth 2
  python3 neo4j_traverse.py --start "Avellaneda-Stoikov" --fields name,key_points
  python3 neo4j_traverse.py --start "Avellaneda-Stoikov" --filter-word gamma
  python3 neo4j_traverse.py --start "Avellaneda-Stoikov" --parameter gamma
  python3 neo4j_traverse.py --stats
        """,
    )
    parser.add_argument('--start', help='Fact name to start traversal from')
    parser.add_argument('--depth', type=int, default=DEFAULT_DEPTH,
                        help=f'Max traversal depth (default: {DEFAULT_DEPTH}, cap: {MAX_DEPTH_CAP})')
    parser.add_argument('--relationship', default='RELATED_TO',
                        choices=['RELATED_TO', 'HAS_WORD', 'SHARES_PARAMETER', 'PREREQUISITE_OF'])
    parser.add_argument('--fields', default='name',
                        help='Comma-separated fields per node (default: name). '
                             'Options: name,key_points,summary,teaser,kp_count,related_count,top_words')
    parser.add_argument('--filter-word', help='Only include nodes whose words/key_points contain this term')
    parser.add_argument('--parameter',
                        help='RLM parameter tracing: return nodes whose key_points or Words contain this string')
    parser.add_argument('--stats', action='store_true', help='Print graph statistics')
    parser.add_argument('--max-nodes', type=int, default=DEFAULT_MAX_NODES)
    parser.add_argument('--json', action='store_true', help='Output as JSON')
    parser.add_argument('--metadata-only', action='store_true',
                        help='Return only metadata (name, teaser, kp_count, related_count, top_words)')
    parser.add_argument('--assistant', '--mind', dest='assistant',
                        help='Filter to Facts created by this assistant/mind (e.g. Weft, Nova)')

    args = parser.parse_args()

    if not args.stats and not args.start:
        parser.error('--start is required unless --stats is used')

    field_list = [f.strip() for f in args.fields.split(',')] if args.fields else ['name']

    if args.stats:
        try:
            result = graph_stats()
        except Exception as e:
            print(json.dumps({'success': False, 'error': f'Cannot connect to Neo4j: {e}'}, indent=2))
            sys.exit(1)
        _print_result(result, args.json, _print_stats)

    elif args.parameter:
        try:
            result = trace_parameter(
                args.start,
                args.parameter,
                depth=args.depth,
                max_nodes=args.max_nodes,
                metadata_only=args.metadata_only,
                fields=field_list,
                assistant=args.assistant,
            )
        except Exception as e:
            print(json.dumps({'success': False, 'error': f'Cannot connect to Neo4j: {e}'}, indent=2))
            sys.exit(1)
        _print_result(result, args.json, _print_parameter_result)

    else:
        try:
            result = traverse(
                args.start,
                depth=args.depth,
                relationship=args.relationship,
                fields=field_list,
                filter_word=args.filter_word,
                max_nodes=args.max_nodes,
                metadata_only=args.metadata_only,
                assistant=args.assistant,
            )
        except Exception as e:
            print(json.dumps({'success': False, 'error': f'Cannot connect to Neo4j: {e}'}, indent=2))
            sys.exit(1)
        _print_result(result, args.json, _print_traversal_result)


if __name__ == '__main__':
    main()
