#!/usr/bin/env python3
"""
CLI wrapper: sync learned topics from memory files to Neo4j (Phase 4 RLM tool).

Parses memory/YYYY-MM-DD.md (and learner-sessions archives), extracts "## Learned:"
sections, and creates well-structured Fact nodes + Word index + embeddings.

This file is a thin CLI wrapper. Core parsing and Neo4j write logic lives in
ai_memory/learn.py.

Usage examples:
    python3 neo4j_learn_sync.py
    python3 neo4j_learn_sync.py --days 7 --full
    python3 neo4j_learn_sync.py --extract-params
    python3 neo4j_learn_sync.py --rebuild-graph
    python3 neo4j_learn_sync.py --assistant Weft
    python3 neo4j_learn_sync.py --assistant Weft --mind   # --mind is alias
"""
from __future__ import annotations  # `X | None` annotations must not evaluate on Python 3.9

import argparse
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Ensure ai_memory package is importable when run as a script
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ai_memory.embed import embed_fact, embed_text
from ai_memory.learn import (
    is_topic_saturated,
    parse_learned_topics,
    rebuild_graph,
    sync_facts,
)
from ai_memory.retrieval_config import load_retrieval_config

_WORKSPACE = Path(os.getenv("AI_MEMORY_DIR", str(Path.home() / ".ai-memory")))
MEMORY_DIR = _WORKSPACE / 'memory'
STATE_FILE = MEMORY_DIR / 'neo4j_learn_sync_state.json'

# Optional: embedding index for semantic search
EMBEDDING_INDEX_AVAILABLE = False
NEO4J_VECTOR_AVAILABLE = False

try:
    from hybrid_memory_search import EmbeddingIndex, FAISS_AVAILABLE, REQUESTS_AVAILABLE
    EMBEDDING_INDEX_AVAILABLE = FAISS_AVAILABLE and REQUESTS_AVAILABLE
except ImportError:
    try:
        from ..hybrid_memory_search import EmbeddingIndex, FAISS_AVAILABLE, REQUESTS_AVAILABLE
        EMBEDDING_INDEX_AVAILABLE = FAISS_AVAILABLE and REQUESTS_AVAILABLE
    except ImportError:
        pass

NEO4J_VECTOR_AVAILABLE = importlib.util.find_spec("ollama") is not None


def _load_sync_state(force_full: bool = False) -> dict:
    """Load the learn sync state file, or return a fresh state if --full or file missing."""
    if STATE_FILE.exists() and not force_full:
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            print("Warning: corrupt sync state file, starting fresh", file=sys.stderr)
    return {'last_sync': None, 'synced_files': []}


def _find_memory_files_to_sync(cutoff: datetime, already_synced: list) -> list:
    """Find daily + learner session files that are new or within the time window."""
    memory_files = []
    for f in MEMORY_DIR.glob('*.md'):
        if re.match(r'\d{4}-\d{2}-\d{2}\.md$', f.name):
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime > cutoff or f.name not in already_synced:
                memory_files.append(f)
    learner_sessions_dir = MEMORY_DIR / 'learner-sessions'
    if learner_sessions_dir.exists():
        for f in learner_sessions_dir.glob('*.md'):
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime > cutoff and f.name not in already_synced:
                memory_files.append(f)
    return memory_files


def _save_sync_state(state: dict, memory_files: list) -> None:
    """Persist the list of scanned files so we don't re-process them unnecessarily."""
    state['last_sync'] = datetime.now().isoformat()
    state['synced_files'].extend(f.name for f in memory_files)
    state['synced_files'] = list(set(state['synced_files']))
    STATE_FILE.write_text(json.dumps(state, indent=2))


def _prepare_embedding_text(key_points: list) -> str | None:
    if not key_points:
        return None
    parts = [kp for kp in key_points[:5] if kp and kp.strip()]
    text = ' '.join(parts)
    return text.strip() or None


def _build_fact_metadata(topic: dict) -> dict:
    name = topic.get('name', '')
    return {
        'name': name,
        'source_file': topic.get('source_file', ''),
        'key_points': topic.get('key_points', [])[:5],
        'summary': (topic.get('summary') or '')[:200],
    }


def _update_indexes_and_optional_extraction(topics: list, driver, extract_params: bool) -> None:
    if EMBEDDING_INDEX_AVAILABLE:
        update_embedding_index(topics)
    if NEO4J_VECTOR_AVAILABLE:
        update_neo4j_vector(topics, driver)
    if extract_params:
        print("\nRunning parameter extraction...")
        try:
            from neo4j_param_extract import run_extraction, load_all_facts
            all_facts = load_all_facts(driver)
            run_extraction(driver, all_facts)
        except ImportError:
            print("Warning: neo4j_param_extract.py not found, skipping", file=sys.stderr)
        except Exception as e:
            print(f"Warning: param extraction failed: {e}", file=sys.stderr)


def _sync_text_only(topics: list, assistant) -> int:
    """Write topics to Neo4j as text only (embed_fn=None) — update_neo4j_vector below is the
    script's single embedding pass, so sync_facts must not embed each topic a second time."""
    return sync_facts(topics, assistant=assistant, embed_fn=None)


def main():
    parser = argparse.ArgumentParser(
        description='Sync learned topics from memory files to Neo4j (Phase 4 RLM tool)'
    )
    parser.add_argument('--days', type=int, default=30,
                        help='How many days back to look for new memory files (default: 30)')
    parser.add_argument('--full', action='store_true',
                        help='Ignore sync state and re-process all recent files')
    parser.add_argument('--extract-params', action='store_true',
                        help='Run parameter extraction after syncing')
    parser.add_argument('--rebuild-graph', action='store_true',
                        help='Rebuild RELATED_TO edges from current Word index (no new sync)')
    parser.add_argument('--assistant', '--mind', dest='assistant',
                        help='Tag created Fact nodes with this assistant/mind name')
    args = parser.parse_args()

    if args.rebuild_graph:
        print("Rebuilding RELATED_TO graph (nightly full edge rebuild: TF-IDF + embedding z-blend, top-k picks)...")
        try:
            edge_count = rebuild_graph()
        except RuntimeError as e:
            print(f"  Failed: {e}", file=sys.stderr)
            sys.exit(1)
        print(f"  Done — {edge_count} RELATED_TO edges")
        return

    if args.assistant:
        print(f"Tagging new Facts with assistant: {args.assistant}")

    state = _load_sync_state(args.full)
    cutoff = datetime.now() - timedelta(days=args.days)
    memory_files = _find_memory_files_to_sync(cutoff, state.get('synced_files', []))

    if not memory_files:
        print("No new memory files to sync")
        return

    # Get existing fact names for saturation filtering
    existing_names: set = set()
    try:
        from ai_memory._config import get_driver as _get_driver
        _driver = _get_driver()
        with _driver.session() as s:
            result = s.run("MATCH (f:Fact) RETURN f.name as name")
            existing_names = {r['name'] for r in result}
        _driver.close()
    except Exception as e:
        print(f"Warning: could not fetch existing facts: {e}", file=sys.stderr)

    all_topics = []
    for filepath in sorted(memory_files):
        try:
            content = filepath.read_text(encoding='utf-8', errors='replace')
        except Exception as e:
            print(f"Warning: could not read {filepath.name}: {e}", file=sys.stderr)
            continue
        topics = parse_learned_topics(content, filepath)
        for topic in topics:
            if topic['name'] not in existing_names:
                if not is_topic_saturated(topic['name'], existing_names):
                    all_topics.append(topic)
                else:
                    print(f"  ~ skipped (saturated): {topic['name']}")

    if not all_topics:
        print("No new topics to sync")
        _save_sync_state(state, memory_files)
        return

    print(f"Found {len(all_topics)} new topics to sync")
    synced = _sync_text_only(all_topics, args.assistant)
    print(f"\nSynced {synced}/{len(all_topics)} topics to Neo4j")

    # Optional embedding index updates (CLI-only, complex deps)
    if EMBEDDING_INDEX_AVAILABLE or NEO4J_VECTOR_AVAILABLE or args.extract_params:
        try:
            from ai_memory._config import get_driver as _get_driver
            _driver = _get_driver()
            _update_indexes_and_optional_extraction(all_topics, _driver, args.extract_params)
            _driver.close()
        except Exception as e:
            print(f"Warning: post-sync index update failed: {e}", file=sys.stderr)

    _save_sync_state(state, memory_files)


def update_embedding_index(topics: list) -> int:
    if not EMBEDDING_INDEX_AVAILABLE:
        return 0
    try:
        embedding_index = EmbeddingIndex()
        added = 0
        for topic in topics:
            text = _prepare_embedding_text(topic.get('key_points', []))
            if not text:
                continue
            doc_id = f"fact:{topic.get('name', '')}"
            metadata = _build_fact_metadata(topic)
            if embedding_index.add_document(doc_id, text, metadata):
                added += 1
        if added > 0:
            embedding_index._save_index()
            print(f"  Added {added} facts to FAISS embedding index")
        return added
    except Exception as e:
        print(f"Warning: Could not update embedding index: {e}", file=sys.stderr)
        return 0


def update_neo4j_vector(topics: list, driver, *, embed_fn=embed_text) -> int:
    """Embed each synced topic's Fact from its canonical text (spec §4) with provenance, CAS-guarded."""
    try:
        with driver.session() as session:
            cfg = load_retrieval_config(session)
            if cfg is None:
                print("  RetrievalConfig missing; skipping embeddings (run ai-memory embed --all)", file=sys.stderr)
                return 0
            counts = {"embedded": 0, "cas_skipped": 0, "embed_failed": 0, "missing": 0}
            for topic in topics:
                name = topic.get("name")
                if not name:
                    continue
                counts[embed_fact(session, name, cfg, embed_fn)] += 1
        if counts["embedded"]:
            print(f"  Embedded {counts['embedded']} facts (canonical text, config v{cfg.version}); "
                  f"cas_skipped={counts['cas_skipped']} failed={counts['embed_failed']}")
        return counts["embedded"]
    except Exception as e:  # noqa: BLE001
        print(f"Warning: Could not update Neo4j vectors: {e}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    main()
