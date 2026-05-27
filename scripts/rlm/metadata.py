#!/usr/bin/env python3
"""
RLM Metadata & Lazy Loading Helpers (Phase 4)

This module contains the core logic for lightweight metadata retrieval
and field selection — the foundation of lazy Recursive Language Model behavior.

Instead of always loading full Fact content (summary, key_points, embeddings),
agents can first work with cheap metadata (name, teaser, counts) and only
load full details on demand.

These patterns are extracted from the advanced private system.
"""

from typing import Dict, Any, List, Optional


def apply_metadata_only(result: Dict[str, Any]) -> Dict[str, Any]:
    """
    Strip a search result down to lightweight metadata-only representation.

    This is the core of RLM lazy loading: return just enough for the agent
    to decide whether it wants the full fact.
    """
    name = result.get('name', result.get('id', ''))
    summary = result.get('summary', '')
    content = result.get('content', '')
    teaser_src = summary or content
    teaser = teaser_src[:150] if teaser_src else ''
    if len(teaser_src) > 150:
        teaser = ' '.join(teaser[:150].split()[:-1]) + '...'

    score = result.get('rrf_score', result.get('score', 0))

    return {
        'id': name,
        'name': name,
        'score': score,
        'teaser': teaser,
        'kp_count': len(result.get('key_points') or []),
        'related_count': result.get('relation_count', result.get('related_count', 0)),
        'top_words': result.get('top_words', []),
        'source': result.get('source', '')
    }


def apply_fields_filter(result: Dict[str, Any], fields: List[str]) -> Dict[str, Any]:
    """Return only the requested fields from a result."""
    return {f: result.get(f) for f in fields if f in result}


def make_teaser(summary: str) -> str:
    """Create a short teaser from a summary."""
    if not summary:
        return ''
    teaser = summary[:150]
    if len(summary) > 150:
        teaser = ' '.join(teaser.split()[:-1]) + '...'
    return teaser
