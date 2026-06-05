"""
Graph traversal for the AI Memory System (Phase 4 RLM tooling).

Public API:
  traverse(start, ...)         — BFS neighbourhood expansion
  trace_parameter(start, ...) — RLM parameter tracing through the graph
  graph_stats(...)             — node/edge counts and average degree

All public functions create and close their own Neo4j driver per call.
For bulk operations use MemoryClient, which holds a persistent driver.
"""
import sys
from typing import Any, Dict, List, Optional, Set

from ai_memory._config import get_driver
from ai_memory.metadata import make_teaser

DEFAULT_DEPTH = 2
MAX_DEPTH_CAP = 3
DEFAULT_MAX_NODES = 50

_ALLOWED_RELS = {'RELATED_TO', 'HAS_WORD', 'SHARES_PARAMETER'}
# SHARES_PARAMETER is reserved for RLM parameter tracing (trace_parameter()).
# PREREQUISITE_OF was removed — no edges of this type exist and no code creates them.


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _format_node(
    record_name: str,
    record: Dict,
    fields: List[str],
    metadata_only: bool,
) -> Dict[str, Any]:
    """Format a graph record into a result dict."""
    if metadata_only:
        summary = record.get('summary', '') or ''
        node: Dict[str, Any] = {
            'name': record_name,
            'teaser': make_teaser(summary),
            'kp_count': len(record.get('key_points') or []),
            'related_count': record.get('related_count', 0),
            'top_words': record.get('top_words') or [],
        }
        if record.get('assistant_tag'):
            node['assistant'] = record['assistant_tag']
        return node

    if not fields or fields == ['name']:
        node = {'name': record_name}
        if record.get('assistant_tag'):
            node['assistant'] = record['assistant_tag']
        return node

    node = {'name': record_name}
    for f in fields:
        if f == 'key_points' and 'key_points' in record:
            node['key_points'] = record['key_points'] or []
        elif f == 'summary' and 'summary' in record:
            node['summary'] = record['summary'] or ''
        elif f == 'teaser':
            node['teaser'] = make_teaser(record.get('summary', '') or '')
        elif f == 'kp_count':
            node['kp_count'] = len(record.get('key_points') or [])
        elif f == 'related_count':
            node['related_count'] = record.get('related_count', 0)
        elif f == 'top_words':
            node['top_words'] = record.get('top_words') or []
    if record.get('assistant_tag'):
        node['assistant'] = record['assistant_tag']
    return node


def _build_traversal_cypher(
    rel_type: str, depth: int, fields: List[str], metadata_only: bool, needs_filter: bool
) -> str:
    need_summary = metadata_only or 'summary' in fields or 'teaser' in fields
    need_kp = metadata_only or 'key_points' in fields or 'kp_count' in fields or needs_filter
    need_words = metadata_only or 'top_words' in fields or 'related_count' in fields or needs_filter

    return_parts = ['f.name AS name', 'f.assistant AS assistant_tag']
    if need_summary:
        return_parts.append('f.summary AS summary')
    if need_kp:
        return_parts.append('f.key_points AS key_points')
    if need_words:
        return_parts.append('COUNT { MATCH (f)-[:RELATED_TO]->() } AS related_count')

    return_clause = ', '.join(return_parts)
    if need_words:
        words_join = "\n        OPTIONAL MATCH (f)-[:HAS_WORD]->(w:Word)\n        WITH f, collect(w.text) AS word_list"
        words_clause = ', word_list[0..5] AS top_words'
    else:
        words_join = ""
        words_clause = ""

    return f"""
        MATCH (start:Fact {{name: $start_name}})-[:{rel_type}*1..{depth}]-(f:Fact)
        WHERE f.name <> $start_name
          AND ($assistant IS NULL OR f.assistant = $assistant)
        WITH DISTINCT f{words_join}
        RETURN {return_clause}{words_clause}
        LIMIT $max_nodes
    """


def _node_matches_filter(rec: Dict, filter_word: str) -> bool:
    top_words = rec.get('top_words') or []
    kp_text = ' '.join(rec.get('key_points') or [])
    fw = filter_word.lower()
    return fw in [w.lower() for w in top_words] or fw in kp_text.lower()


def _process_traversal_records(
    records,
    visited: Set[str],
    max_nodes: int,
    fields: List[str],
    metadata_only: bool,
    filter_fn=None,
) -> List[Dict[str, Any]]:
    nodes = []
    for record in records:
        node_name = record['name']
        if node_name in visited:
            continue
        visited.add(node_name)
        rec_dict = dict(record)
        if filter_fn and not filter_fn(rec_dict):
            continue
        nodes.append(_format_node(node_name, rec_dict, fields, metadata_only))
        if len(nodes) >= max_nodes:
            break
    return nodes


def _get_label_counts(session) -> Dict[str, int]:
    try:
        label_result = session.run(
            "CALL db.labels() YIELD label "
            "CALL apoc.cypher.run('MATCH (n:' + label + ') RETURN count(n) as cnt', {}) YIELD value "
            "RETURN label, value.cnt AS count ORDER BY count DESC"
        )
        return {record['label']: record['count'] for record in label_result}
    except Exception:
        label_counts = {}
        for label in ['Fact', 'Word', 'Session', 'Event']:
            try:
                r = session.run(f"MATCH (n:{label}) RETURN count(n) AS cnt")
                rec = r.single()
                label_counts[label] = rec['cnt'] if rec else 0
            except Exception:
                label_counts[label] = -1
        return label_counts


def _get_edge_counts(session) -> Dict[str, int]:
    try:
        result = session.run("CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType")
        edge_types = [r['relationshipType'] for r in result]
    except Exception:
        edge_types = ['RELATED_TO', 'HAS_WORD', 'SHARES_PARAMETER']

    edge_counts = {}
    for etype in edge_types:
        try:
            r = session.run(f"MATCH ()-[r:{etype}]->() RETURN count(r) AS cnt")
            rec = r.single()
            edge_counts[etype] = rec['cnt'] if rec else 0
        except Exception:
            edge_counts[etype] = -1
    return edge_counts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def traverse(
    start: str,
    *,
    workspace=None,
    depth: int = DEFAULT_DEPTH,
    relationship: str = 'RELATED_TO',
    fields: Optional[List[str]] = None,
    filter_word: Optional[str] = None,
    max_nodes: int = DEFAULT_MAX_NODES,
    metadata_only: bool = False,
    assistant: Optional[str] = None,
    driver=None,
) -> Dict[str, Any]:
    """
    Expand the neighbourhood of a starting Fact node up to `depth` hops.

    Returns a result dict with keys: success, start, depth, relationship,
    assistant, total_nodes, nodes.

    Pass an existing ``driver`` to reuse it across calls (MemoryClient does
    this automatically). When omitted, a one-shot driver is created+closed.
    """
    depth = max(1, min(depth, MAX_DEPTH_CAP))
    fields = fields or ['name']

    if relationship not in _ALLOWED_RELS:
        return {
            'success': False,
            'error': f'Unknown relationship type: {relationship}. Allowed: {sorted(_ALLOWED_RELS)}',
        }

    cypher = _build_traversal_cypher(relationship, depth, fields, metadata_only, bool(filter_word))
    owns_driver = driver is None
    if owns_driver:
        driver = get_driver(workspace)
    try:
        nodes: List[Dict] = []
        visited: Set[str] = {start}
        with driver.session() as session:
            check = session.run(
                "MATCH (f:Fact {name: $name}) RETURN f.name LIMIT 1", {'name': start}
            )
            if not check.single():
                return {'success': False, 'error': f'Fact node not found: {start!r}'}
            try:
                records = session.run(cypher, {
                    'start_name': start,
                    'max_nodes': max_nodes,
                    'assistant': assistant,
                })

                def filter_fn(rec):
                    return _node_matches_filter(rec, filter_word) if filter_word else True

                nodes = _process_traversal_records(
                    records, visited, max_nodes, fields, metadata_only, filter_fn
                )
            except Exception as e:
                return {'success': False, 'error': f'Traversal query failed: {e}'}
    finally:
        if owns_driver and driver is not None:
            driver.close()

    return {
        'success': True,
        'start': start,
        'depth': depth,
        'relationship': relationship,
        'assistant': assistant,
        'total_nodes': len(nodes),
        'nodes': nodes,
    }


def trace_parameter(
    start: str,
    parameter: str,
    *,
    workspace=None,
    depth: int = DEFAULT_DEPTH,
    max_nodes: int = DEFAULT_MAX_NODES,
    metadata_only: bool = False,
    fields: Optional[List[str]] = None,
    assistant: Optional[str] = None,
    driver=None,
) -> Dict[str, Any]:
    """
    RLM-style parameter tracing: follow RELATED_TO / SHARES_PARAMETER edges
    and return only nodes whose key_points or Words contain `parameter`.

    Pass an existing ``driver`` to reuse it across calls (MemoryClient does
    this automatically). When omitted, a one-shot driver is created+closed.
    """
    depth = max(1, min(depth, MAX_DEPTH_CAP))
    fields = fields or ['name', 'teaser', 'kp_count', 'related_count', 'top_words']

    cypher = """
        MATCH (start:Fact {name: $start_name})-[:RELATED_TO|SHARES_PARAMETER*1..%(depth)d]-(f:Fact)
        WHERE f.name <> $start_name
          AND ($assistant IS NULL OR f.assistant = $assistant)
          AND (any(kp IN f.key_points WHERE toLower(kp) CONTAINS $param)
               OR EXISTS {
                   MATCH (f)-[:HAS_WORD]->(w:Word)
                   WHERE toLower(w.text) CONTAINS $param
               }
               OR EXISTS { MATCH (start)-[:SHARES_PARAMETER]-(f) })
        WITH DISTINCT f
        OPTIONAL MATCH (f)-[:HAS_WORD]->(w:Word)
        WITH f, collect(w.text) AS word_list
        RETURN f.name AS name,
               f.summary AS summary,
               f.key_points AS key_points,
               f.assistant AS assistant_tag,
               COUNT { MATCH (f)-[:RELATED_TO]->() } AS related_count,
               word_list[0..5] AS top_words
        LIMIT $max_nodes
    """ % {'depth': depth}

    owns_driver = driver is None
    if owns_driver:
        driver = get_driver(workspace)
    try:
        nodes: List[Dict] = []
        visited: Set[str] = {start}
        with driver.session() as session:
            check = session.run(
                "MATCH (f:Fact {name: $name}) RETURN f.name AS name LIMIT 1", {'name': start}
            )
            if not check.single():
                return {'success': False, 'error': f'Fact node not found: {start!r}'}
            try:
                records = session.run(cypher, {
                    'start_name': start,
                    'param': parameter.lower(),
                    'max_nodes': max_nodes,
                    'assistant': assistant,
                })
                nodes = _process_traversal_records(
                    records, visited, max_nodes, fields, metadata_only, None
                )
            except Exception as e:
                return {'success': False, 'error': f'Parameter trace query failed: {e}'}
    finally:
        if owns_driver and driver is not None:
            driver.close()

    return {
        'success': True,
        'start': start,
        'parameter': parameter,
        'depth': depth,
        'assistant': assistant,
        'total_nodes': len(nodes),
        'nodes': nodes,
    }


def graph_stats(*, workspace=None, driver=None) -> Dict[str, Any]:
    """Return graph statistics: node counts by label, edge counts, average Fact degree.

    Pass an existing ``driver`` to reuse it across calls. When omitted, a
    one-shot driver is created+closed.
    """
    owns_driver = driver is None
    if owns_driver:
        driver = get_driver(workspace)
    try:
        stats: Dict[str, Any] = {}
        with driver.session() as session:
            stats['node_counts'] = _get_label_counts(session)
            edge_counts = _get_edge_counts(session)
            stats['edge_counts'] = edge_counts
            stats['total_edges'] = sum(v for v in edge_counts.values() if v >= 0)
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
    finally:
        if owns_driver and driver is not None:
            driver.close()
    return {'success': True, 'stats': stats}
