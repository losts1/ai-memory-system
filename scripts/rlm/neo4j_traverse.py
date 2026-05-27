#!/usr/bin/env python3
"""
Neo4j Graph Traversal - Explore the knowledge graph from a starting Fact node.

================================================================================
PHASE 4 — ADVANCED RLM TOOLING (EXPERIMENTAL)

This script is part of Phase 4 of the ai-memory-system upgrade plan:
"Upstream the RLM Features".

It contains some of the most powerful Recursive Language Model patterns
developed in the full private system, especially:

- Rich graph traversal with cycle detection
- `--parameter` tracing mode (core RLM technique)
- Flexible field selection and metadata-only mode
- Word-based filtering

**Important:**
- This is more advanced than the basic redistribution package tools.
- It is currently provided as-is for early adopters and subminds.
- It may have dependencies or assumptions from the original private environment.
- Use at your own risk. Feedback is very welcome.

See UPGRADE_PLAN.md for the full Phase 3/4 context.
================================================================================

Usage:
    python3 neo4j_traverse.py --start "Avellaneda-Stoikov" --depth 2
    python3 neo4j_traverse.py --start "Avellaneda-Stoikov" --depth 3 --relationship RELATED_TO --fields name,key_points
    python3 neo4j_traverse.py --start "Avellaneda-Stoikov" --filter-word gamma
    python3 neo4j_traverse.py --start "Avellaneda-Stoikov" --parameter gamma
    python3 neo4j_traverse.py --stats

The --parameter mode is particularly powerful for RLM-style work:
it traces how a specific concept/parameter (e.g. "gamma", "inventory", "kill switch")
appears across related Facts in the graph.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional, Set

from dotenv import load_dotenv

# Workspace directory: set AI_MEMORY_DIR env var to override default (~/.ai-memory)
_WORKSPACE = Path(os.getenv("AI_MEMORY_DIR", str(Path.home() / ".ai-memory")))
load_dotenv(_WORKSPACE / ".env.neo4j")

try:
    from neo4j import GraphDatabase
except ImportError as e:
    print(json.dumps({'success': False, 'error': f'Neo4j driver not available: {e}'}))
    sys.exit(1)

# For advanced RLM tools we still allow overriding the full workspace if needed
WORKSPACE = Path(os.environ.get('OPENCLAW_WORKSPACE', str(_WORKSPACE)))
ENV_FILE = WORKSPACE / '.env.neo4j'

DEFAULT_DEPTH = 2
MAX_DEPTH_CAP = 3
DEFAULT_MAX_NODES = 50


def load_credentials() -> Dict[str, str]:
    """Load Neo4j credentials from .env.neo4j."""
    if not ENV_FILE.exists():
        raise FileNotFoundError(f"Credentials file not found: {ENV_FILE}")

    creds = {}
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                key, value = line.split('=', 1)
                creds[key.strip().lower()] = value.strip()

    return {
        'uri': creds.get('neo4j_uri', 'bolt://localhost:7687'),
        'username': creds.get('neo4j_username', 'neo4j'),
        'password': creds.get('neo4j_password', '')
    }


def _make_teaser(summary: str) -> str:
    """Return first 150 chars of summary, truncated at word boundary."""
    if not summary:
        return ''
    teaser = summary[:150]
    if len(summary) > 150:
        teaser = ' '.join(teaser.split()[:-1]) + '...'
    return teaser


def _format_node(record_name: str, record: Dict, fields: List[str], metadata_only: bool) -> Dict[str, Any]:
    """Format a node record according to requested fields / metadata_only flag."""
    if metadata_only:
        summary = record.get('summary', '') or ''
        return {
            'name': record_name,
            'teaser': _make_teaser(summary),
            'kp_count': len(record.get('key_points') or []),
            'related_count': record.get('related_count', 0),
            'top_words': record.get('top_words') or []
        }

    if not fields or fields == ['name']:
        return {'name': record_name}

    node: Dict[str, Any] = {'name': record_name}
    for f in fields:
        if f == 'key_points' and 'key_points' in record:
            node['key_points'] = record['key_points'] or []
        elif f == 'summary' and 'summary' in record:
            node['summary'] = record['summary'] or ''
        elif f == 'teaser':
            node['teaser'] = _make_teaser(record.get('summary', '') or '')
        elif f == 'kp_count':
            node['kp_count'] = len(record.get('key_points') or [])
        elif f == 'related_count':
            node['related_count'] = record.get('related_count', 0)
        elif f == 'top_words':
            node['top_words'] = record.get('top_words') or []
    return node


def traverse_neighborhood(
    driver,
    start: str,
    depth: int,
    relationship: str,
    fields: List[str],
    filter_word: Optional[str],
    max_nodes: int,
    metadata_only: bool
) -> Dict[str, Any]:
    """
    Expand the neighborhood of a starting Fact node up to `depth` hops.

    Uses cycle detection and a max-nodes limit to prevent explosion.
    """
    depth = min(depth, MAX_DEPTH_CAP)

    # Determine which properties to fetch
    need_summary = metadata_only or 'summary' in fields or 'teaser' in fields
    # Also fetch key_points when filter_word is active so we can match against them
    need_kp = metadata_only or 'key_points' in fields or 'kp_count' in fields or bool(filter_word)
    # Also fetch words when filter_word is active
    need_words = metadata_only or 'top_words' in fields or 'related_count' in fields or bool(filter_word)

    # BFS via Cypher with variable-length path
    # We use RELATED_TO (or user-specified relationship) between Fact nodes only
    rel_type = relationship if relationship else 'RELATED_TO'

    # Safety: only allow known relationship types to prevent Cypher injection
    allowed_rels = {'RELATED_TO', 'HAS_WORD', 'SHARES_PARAMETER', 'PREREQUISITE_OF'}
    if rel_type not in allowed_rels:
        return {'success': False, 'error': f'Unknown relationship type: {rel_type}. Allowed: {sorted(allowed_rels)}'}

    # Build WITH clause selecting needed fields, then OPTIONAL MATCH words separately
    # to avoid [:5] slice issue on list pattern comprehensions in older Neo4j versions
    with_parts = ['f']
    return_parts = ['f.name AS name']
    if need_summary:
        return_parts.append('f.summary AS summary')
    if need_kp:
        return_parts.append('f.key_points AS key_points')
    if need_words:
        return_parts.append('COUNT { MATCH (f)-[:RELATED_TO]->() } AS related_count')

    return_clause = ', '.join(return_parts)
    words_clause = ', word_list[0..5] AS top_words' if need_words else ''

    cypher = f"""
        MATCH (start:Fact {{name: $start_name}})-[:{rel_type}*1..{depth}]-(f:Fact)
        WHERE f.name <> $start_name
        WITH DISTINCT f
        OPTIONAL MATCH (f)-[:HAS_WORD]->(w:Word)
        WITH f, collect(w.text) AS word_list
        RETURN {return_clause}{words_clause}
        LIMIT $max_nodes
    """

    nodes: List[Dict[str, Any]] = []
    visited: Set[str] = {start}

    with driver.session() as session:
        # First verify start node exists
        check = session.run(
            "MATCH (f:Fact {name: $name}) RETURN f.name AS name LIMIT 1",
            {'name': start}
        )
        if not check.single():
            return {'success': False, 'error': f'Fact node not found: {start!r}'}

        try:
            records = session.run(cypher, {'start_name': start, 'max_nodes': max_nodes})
            for record in records:
                node_name = record['name']
                if node_name in visited:
                    continue
                visited.add(node_name)

                rec_dict = dict(record)

                # Apply word filter if requested
                if filter_word:
                    top_words = rec_dict.get('top_words') or []
                    kp_text = ' '.join(rec_dict.get('key_points') or [])
                    if (filter_word.lower() not in [w.lower() for w in top_words]
                            and filter_word.lower() not in kp_text.lower()):
                        continue

                nodes.append(_format_node(node_name, rec_dict, fields, metadata_only))

                if len(nodes) >= max_nodes:
                    break

        except Exception as e:
            return {'success': False, 'error': f'Traversal query failed: {e}'}

    return {
        'success': True,
        'start': start,
        'depth': depth,
        'relationship': rel_type,
        'total_nodes': len(nodes),
        'nodes': nodes
    }


def trace_parameter(
    driver,
    start: str,
    parameter: str,
    depth: int,
    max_nodes: int,
    metadata_only: bool,
    fields: List[str] = None
) -> Dict[str, Any]:
    """
    Trace a parameter through the graph (RLM-style).

    Follows RELATED_TO / SHARES_PARAMETER edges and filters nodes
    whose key_points or Words contain the parameter.
    """
    depth = min(depth, MAX_DEPTH_CAP)
    fields = fields or ['name', 'teaser', 'kp_count', 'related_count', 'top_words']

    cypher = """
        MATCH (start:Fact {name: $start_name})-[:RELATED_TO|SHARES_PARAMETER*1..%(depth)d]-(f:Fact)
        WHERE f.name <> $start_name
          AND (any(kp IN f.key_points WHERE toLower(kp) CONTAINS $param)
               OR EXISTS {
                   MATCH (f)-[:HAS_WORD]->(w:Word)
                   WHERE toLower(w.text) CONTAINS $param
               }
               OR EXISTS {
                   MATCH (start)-[:SHARES_PARAMETER]-(f)
               })
        WITH DISTINCT f
        OPTIONAL MATCH (f)-[:HAS_WORD]->(w:Word)
        WITH f, collect(w.text) AS word_list
        RETURN f.name AS name,
               f.summary AS summary,
               f.key_points AS key_points,
               COUNT { MATCH (f)-[:RELATED_TO]->() } AS related_count,
               word_list[0..5] AS top_words
        LIMIT $max_nodes
    """ % {'depth': depth}

    nodes: List[Dict[str, Any]] = []
    visited: Set[str] = {start}

    with driver.session() as session:
        check = session.run(
            "MATCH (f:Fact {name: $name}) RETURN f.name AS name LIMIT 1",
            {'name': start}
        )
        if not check.single():
            return {'success': False, 'error': f'Fact node not found: {start!r}'}

        try:
            records = session.run(cypher, {
                'start_name': start,
                'param': parameter.lower(),
                'max_nodes': max_nodes
            })
            for record in records:
                node_name = record['name']
                if node_name in visited:
                    continue
                visited.add(node_name)

                rec_dict = dict(record)
                nodes.append(_format_node(node_name, rec_dict, fields, metadata_only))

                if len(nodes) >= max_nodes:
                    break

        except Exception as e:
            return {'success': False, 'error': f'Parameter trace query failed: {e}'}

    return {
        'success': True,
        'start': start,
        'parameter': parameter,
        'depth': depth,
        'total_nodes': len(nodes),
        'nodes': nodes
    }


def get_graph_stats(driver) -> Dict[str, Any]:
    """Return graph statistics: node counts by label, edge counts, average degree."""
    stats: Dict[str, Any] = {}

    with driver.session() as session:
        # Node counts by label
        label_result = session.run(
            "CALL db.labels() YIELD label "
            "CALL apoc.cypher.run('MATCH (n:' + label + ') RETURN count(n) as cnt', {}) YIELD value "
            "RETURN label, value.cnt AS count "
            "ORDER BY count DESC"
        )
        label_counts: Dict[str, int] = {}
        try:
            for record in label_result:
                label_counts[record['label']] = record['count']
        except Exception:
            # APOC may not be available; fall back to individual queries
            for label in ['Fact', 'Word', 'Session', 'Event']:
                try:
                    r = session.run(f"MATCH (n:{label}) RETURN count(n) AS cnt")  # noqa: S608
                    rec = r.single()
                    label_counts[label] = rec['cnt'] if rec else 0
                except Exception:
                    label_counts[label] = -1

        stats['node_counts'] = label_counts

        # Edge counts by type
        edge_result = session.run(
            "CALL db.relationshipTypes() YIELD relationshipType "
            "RETURN relationshipType"
        )
        edge_types = [r['relationshipType'] for r in edge_result]
        edge_counts: Dict[str, int] = {}
        for etype in edge_types:
            try:
                r = session.run(f"MATCH ()-[r:{etype}]->() RETURN count(r) AS cnt")  # noqa: S608
                rec = r.single()
                edge_counts[etype] = rec['cnt'] if rec else 0
            except Exception:
                edge_counts[etype] = -1
        stats['edge_counts'] = edge_counts
        stats['total_edges'] = sum(v for v in edge_counts.values() if v >= 0)

        # Average out-degree for Fact nodes
        try:
            r = session.run(
                "MATCH (f:Fact) "
                "OPTIONAL MATCH (f)-[rel:RELATED_TO]->() "
                "WITH f, count(rel) AS deg "
                "RETURN avg(deg) AS avg_degree, count(f) AS total_facts"
            )
            rec = r.single()
            if rec:
                stats['fact_avg_related_to_degree'] = round(float(rec['avg_degree'] or 0), 2)
                stats['total_facts'] = rec['total_facts']
        except Exception:
            pass

    return {'success': True, 'stats': stats}


def main():
    parser = argparse.ArgumentParser(
        description='Neo4j graph traversal from a starting Fact node',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 neo4j_traverse.py --start "Avellaneda-Stoikov" --depth 2
  python3 neo4j_traverse.py --start "Avellaneda-Stoikov" --depth 3 --fields name,key_points
  python3 neo4j_traverse.py --start "Avellaneda-Stoikov" --filter-word gamma
  python3 neo4j_traverse.py --start "Avellaneda-Stoikov" --parameter gamma
  python3 neo4j_traverse.py --stats
        """
    )
    parser.add_argument('--start', help='Fact name to start traversal from')
    parser.add_argument('--depth', type=int, default=DEFAULT_DEPTH,
                        help=f'Max traversal depth (default: {DEFAULT_DEPTH}, cap: {MAX_DEPTH_CAP})')
    parser.add_argument('--relationship', default='RELATED_TO',
                        choices=['RELATED_TO', 'HAS_WORD', 'SHARES_PARAMETER', 'PREREQUISITE_OF'],
                        help='Edge type to follow (default: RELATED_TO)')
    parser.add_argument('--fields', default='name',
                        help='Comma-separated fields per node (default: name). '
                             'Options: name,key_points,summary,teaser,kp_count,related_count,top_words')
    parser.add_argument('--filter-word', help='Only include nodes whose words/key_points contain this term')
    parser.add_argument('--parameter', 
                        help='RLM-style parameter tracing: start from --start and follow '
                             'RELATED_TO edges, returning only nodes whose key_points or '
                             'associated Words contain the given parameter string. '
                             'Very useful for exploring how a concept appears across the graph.')
    parser.add_argument('--stats', action='store_true', help='Print graph statistics')
    parser.add_argument('--max-nodes', type=int, default=DEFAULT_MAX_NODES,
                        help=f'Max total nodes to return (default: {DEFAULT_MAX_NODES})')
    parser.add_argument('--json', action='store_true', help='Output as JSON')
    parser.add_argument('--metadata-only', action='store_true',
                        help='Return only metadata (name, teaser, kp_count, related_count, top_words) per node')

    args = parser.parse_args()

    if not args.stats and not args.start:
        parser.error('--start is required unless --stats is used')

    # Connect
    try:
        creds = load_credentials()
        driver = GraphDatabase.driver(creds['uri'], auth=(creds['username'], creds['password']))
        driver.verify_connectivity()
    except Exception as e:
        out = {'success': False, 'error': f'Cannot connect to Neo4j: {e}'}
        print(json.dumps(out, indent=2))
        sys.exit(1)

    try:
        field_list = [f.strip() for f in args.fields.split(',')]

        if args.stats:
            result = get_graph_stats(driver)
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                if result.get('success'):
                    s = result['stats']
                    print("Graph Statistics")
                    print("=" * 40)
                    print("\nNode counts:")
                    for label, count in s.get('node_counts', {}).items():
                        print(f"  {label}: {count}")
                    print("\nEdge counts:")
                    for etype, count in s.get('edge_counts', {}).items():
                        print(f"  {etype}: {count}")
                    print(f"\nTotal edges: {s.get('total_edges', '?')}")
                    if 'fact_avg_related_to_degree' in s:
                        print(f"Fact avg RELATED_TO degree: {s['fact_avg_related_to_degree']}")
                    if 'total_facts' in s:
                        print(f"Total Facts: {s['total_facts']}")
                else:
                    print(f"Error: {result.get('error')}")

        elif args.parameter:
            field_list = [f.strip() for f in args.fields.split(',')] if args.fields else None
            result = trace_parameter(
                driver,
                start=args.start,
                parameter=args.parameter,
                depth=args.depth,
                max_nodes=args.max_nodes,
                metadata_only=args.metadata_only,
                fields=field_list
            )
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                if result.get('success'):
                    print(f"\nRLM Parameter Trace")
                    print(f"Parameter : '{args.parameter}'")
                    print(f"Starting  : '{args.start}'")
                    print(f"Depth     : {result['depth']}")
                    print(f"Matches   : {result['total_nodes']} nodes")
                    print("=" * 60)
                    if result['total_nodes'] == 0:
                        print("No nodes matched this parameter within the depth limit.")
                    else:
                        for node in result['nodes']:
                            _print_node(node)
                else:
                    print(f"Error: {result.get('error')}")

        else:
            result = traverse_neighborhood(
                driver,
                start=args.start,
                depth=args.depth,
                relationship=args.relationship,
                fields=field_list,
                filter_word=args.filter_word,
                max_nodes=args.max_nodes,
                metadata_only=args.metadata_only
            )
            if args.json:
                print(json.dumps(result, indent=2))
            else:
                if result.get('success'):
                    print(f"Traversal from '{args.start}' "
                          f"(depth {result['depth']}, rel {result['relationship']})")
                    print(f"Found {result['total_nodes']} nodes")
                    print("=" * 60)
                    for node in result['nodes']:
                        _print_node(node)
                else:
                    print(f"Error: {result.get('error')}")

    finally:
        driver.close()


def _print_node(node: Dict[str, Any]) -> None:
    """Pretty-print a single node result."""
    print(f"\n  {node.get('name', '?')}")
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


if __name__ == '__main__':
    main()
