#!/usr/bin/env python3
"""
Sync learned topics from daily memory files to Neo4j as Fact nodes (Phase 4 RLM tool).

Parses memory/YYYY-MM-DD.md (and learner-sessions archives), extracts "## Learned:"
sections, and creates well-structured Fact nodes + Word index + embeddings.

Key features:
- Efficient Word index linking (avoids O(n²) pitfalls)
- Embedding + vector index updates
- Parameter / prerequisite relationship extraction (optional)

Usage examples:
    python3 neo4j_learn_sync.py
    python3 neo4j_learn_sync.py --days 7 --full
    python3 neo4j_learn_sync.py --extract-params
    python3 neo4j_learn_sync.py --rebuild-graph

================================================================================
PHASE 4 — ADVANCED RLM TOOLING (EXPERIMENTAL)

This is one of the core "learn sync" patterns from the private system.

It is responsible for turning raw notes into the high-signal Facts that power
the rest of the RLM (traversal, lazy state, etc.).

**Warning:** This is advanced Phase 4 material. It may still contain private-era
assumptions and will need further adaptation for general use.

See UPGRADE_PLAN.md and scripts/rlm/README.md for context.
================================================================================
"""

import argparse
import os
import re
import sys
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from neo4j import GraphDatabase

# Standard public package workspace handling (Phase 4 adaptation)
_WORKSPACE = Path(os.getenv("AI_MEMORY_DIR", str(Path.home() / ".ai-memory")))
load_dotenv(_WORKSPACE / ".env.neo4j")

NEO4J_URI = os.getenv('NEO4J_URI', 'bolt://localhost:7687')
NEO4J_USER = os.getenv('NEO4J_USERNAME', 'neo4j')
NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD')

if not NEO4J_PASSWORD:
    print("ERROR: NEO4J_PASSWORD not set in .env.neo4j")
    sys.exit(1)

# Optional: embedding index for semantic search (Phase 4 RLM)
EMBEDDING_INDEX_AVAILABLE = False
NEO4J_VECTOR_AVAILABLE = False

try:
    from hybrid_memory_search import EmbeddingIndex, FAISS_AVAILABLE, REQUESTS_AVAILABLE
    EMBEDDING_INDEX_AVAILABLE = FAISS_AVAILABLE and REQUESTS_AVAILABLE
except ImportError:
    # Try relative import if running from within the package
    try:
        from ..hybrid_memory_search import EmbeddingIndex, FAISS_AVAILABLE, REQUESTS_AVAILABLE
        EMBEDDING_INDEX_AVAILABLE = FAISS_AVAILABLE and REQUESTS_AVAILABLE
    except ImportError:
        pass

# Optional: requests for direct vector index updates
try:
    import requests
    NEO4J_VECTOR_AVAILABLE = True
except ImportError:
    pass


def get_driver():
    """Create a Neo4j driver using the standard public package pattern."""
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


def _load_sync_state(force_full: bool = False) -> dict:
    """Load the learn sync state file, or return a fresh state if --full or file missing."""
    if STATE_FILE.exists() and not force_full:
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:
            print("Warning: corrupt sync state file, starting fresh", file=sys.stderr)
    return {'last_sync': None, 'synced_files': []}


def _find_memory_files_to_sync(memory_dir: Path, cutoff: datetime, already_synced: list[str]) -> list[Path]:
    """Find daily + learner session files that are new or within the time window."""
    memory_files = []

    # Daily files: YYYY-MM-DD.md
    for f in memory_dir.glob('*.md'):
        if re.match(r'\d{4}-\d{2}-\d{2}\.md$', f.name):
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime > cutoff or f.name not in already_synced:
                memory_files.append(f)

    # Learner session archives
    learner_sessions_dir = memory_dir / 'learner-sessions'
    if learner_sessions_dir.exists():
        for f in learner_sessions_dir.glob('*.md'):
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime > cutoff and f.name not in already_synced:
                memory_files.append(f)

    return memory_files


def _post_process_graph(session, driver) -> None:
    """Run post-sync graph maintenance: word frequencies, related fact linking, orphaned word cleanup."""
    # Update word frequencies (for stopwords filtering)
    session.run("""
        MATCH (w:Word)<-[:HAS_WORD]-(f:Fact)
        WITH w, count(DISTINCT f) AS df
        SET w.df = df
    """)

    # Link related facts (using Word index, tighter params to reduce noise)
    session.execute_write(link_related_facts, max_df_ratio=0.1, min_shared=2)

    # Cleanup orphaned Word nodes
    session.execute_write(cleanup_orphaned_words)


def _update_indexes_and_optional_extraction(topics: list, driver, extract_params: bool) -> None:
    """Update embedding/vector indexes and optionally run parameter extraction."""
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
            print("Warning: neo4j_param_extract.py not found, skipping param extraction", file=sys.stderr)
        except Exception as e:
            print(f"Warning: param extraction failed: {e}", file=sys.stderr)


def _save_sync_state(state: dict, memory_files: list[Path]) -> None:
    """Persist the list of scanned files so we don't re-process them unnecessarily."""
    state['last_sync'] = datetime.now().isoformat()
    state['synced_files'].extend(f.name for f in memory_files)
    state['synced_files'] = list(set(state['synced_files']))
    STATE_FILE.write_text(json.dumps(state, indent=2))

MEMORY_DIR = _WORKSPACE / 'memory'
STATE_FILE = MEMORY_DIR / 'neo4j_learn_sync_state.json'

# Short important tokens to preserve (df >= 3 filter skips them otherwise)
SHORT_WORDS = {'ai', 'ml', 'sql', 'gpu', 'nlp', 'rl', 'api', 'cli', 'qa', 'etl'}

# Metadata noise — excluded from word index entirely.
# These appear in nearly every session name and create false graph edges.
STOP_WORDS = {
    # Timezone abbreviations
    'utc', 'edt', 'est', 'pst', 'pdt', 'cst', 'cdt', 'gmt',
    # Year numbers (show up in date suffixes like "— 2026-04-07")
    '2024', '2025', '2026', '2027',
    # Month abbreviations (from "Mar 21", "Apr 16" remnants)
    'jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec',
    # Session/file metadata words
    'session', 'learner', 'learned', 'daily', 'log', 'notes',
    # Filler words that slip past the min_length=3 filter
    'the', 'and', 'for', 'with', 'from', 'via', 'per',
    # Domain-generic words: df > 100 in this corpus — so generic they connect everything
    # to everything and add no signal. Real relationships are built on specific terms.
    'market', 'making', 'trading', 'systems', 'theory',
}

# Characters to normalize for word extraction
NORMALIZE_CHARS = '()&,.:—-'


def _date_from_filepath(filepath: Path) -> str:
    """Extract ISO datetime string from filename.

    Handles:
      2026-05-03_21-00_slug.md  → 2026-05-03T21:00:00Z
      2026-05-03-slug.md        → 2026-05-03T00:00:00Z
      2026-05-03.md             → 2026-05-03T00:00:00Z
    Falls back to current UTC time if no date found.
    """
    m = re.match(r'(\d{4}-\d{2}-\d{2})(?:[_-](\d{2})[_-](\d{2}))?', filepath.name)
    if m:
        date = m.group(1)
        hour = m.group(2) or '00'
        minute = m.group(3) or '00'
        return f"{date}T{hour}:{minute}:00Z"
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def normalize_name(name: str) -> str:
    """Normalize fact name for word extraction."""
    result = name.lower()
    for char in NORMALIZE_CHARS:
        result = result.replace(char, ' ')
    return result


def extract_words(name: str, min_length: int = 3) -> list[str]:
    """Extract words from a fact name.

    Returns unique words, preserving short important tokens (AI, ML, SQL, etc)
    and filtering metadata noise (timezones, year numbers, session words).
    """
    normalized = normalize_name(name)
    words = [w.strip() for w in normalized.split() if w.strip()]

    result = []
    for w in words:
        if w in STOP_WORDS:
            continue
        if len(w) >= min_length:
            result.append(w)
        elif w in SHORT_WORDS:
            result.append(w)

    return list(set(result))


def _clean_learned_title(title: str) -> str:
    """Clean up learned topic titles by removing header prefixes and various time/date annotations."""
    title = re.sub(r'^(?:Learned|Learner Session):?\s*', '', title)
    title = re.sub(r'\s*\(Learner Cron[^)]*\)\s*$', '', title)
    title = re.sub(r'\s*\(Learner\s*$', '', title)
    title = re.sub(r'\s*\(\d+:\d+\s*[AP]M\s*EDT\)\s*$', '', title)
    title = re.sub(r'\s*\(\d+:\d+\s*[AP]M\)\s*$', '', title)
    title = re.sub(r'\s*\(\d+:\d+\s*UTC\)\s*$', '', title)
    title = re.sub(r'\s*—\s*\d{4}-\d{2}-\d{2}\s+\d+:\d+.*$', '', title)
    title = re.sub(r'\s*\(\d{4}-\d{2}-\d{2}\)\s*$', '', title)
    return title.strip()


def _parse_key_points_and_summary(body: str) -> tuple[list[str], str]:
    """Separate key points (bullet/numbered lists) from summary paragraphs in a learned topic body."""
    lines = body.split('\n')
    key_points = []
    summary_lines = []
    in_list = False

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if line.startswith('-') or re.match(r'^\d+\.', line):
            point = re.sub(r'^[-\d\.]+\s*', '', line).strip()
            if point:
                key_points.append(point)
            in_list = True
        elif in_list and not line.startswith('#') and not line.startswith('**'):
            if key_points and not line.startswith('|'):
                pass  # continuation of previous point (currently ignored for simplicity)
            summary_lines.append(line)
        else:
            summary_lines.append(line)
            in_list = False

    summary = ' '.join(summary_lines[:3])
    if len(summary) > 500:
        summary = summary[:497] + '...'

    return key_points, summary


def parse_learned_topics(content: str, filepath: Path) -> list[dict]:
    """Extract learned topics from a memory file.

    Looks for '## Learned:', '## Learner Session:', or '# Learner Session:' sections.
    Returns list of topic dicts with name, summary, and key points.
    """
    topics = []

    # Match all learner section header variants:
    # 1. ## Learned: Topic  (legacy daily format)
    # 2. ## ~HH:MM — Learned: Topic  (legacy timestamped)
    # 3. ## Learner Session: Topic (time)  (current daily format)
    # 4. # Learner Session: Topic (time)  (archive file format)
    #
    # Lookahead stops only at the NEXT session header (not at H2 subsections
    # inside the session body — those belong to the captured content).
    session_header = r'#{1,2} (?:~[\d:]+\s+—\s+)?(?:Learned|Learner Session):'
    pattern = (
        r'^' + session_header + r'\s*([^\n]+)\n'
        r'(.*?)'
        r'(?=\n' + session_header + r'|\Z)'
    )

    matches = re.findall(pattern, content, re.MULTILINE | re.DOTALL)
    
    for title, body in matches:
        title = title.strip()
        body = body.strip()
        
        # Skip if body is empty or just whitespace
        if not body:
            continue
        
        key_points, summary = _parse_key_points_and_summary(body)
        
        title = _clean_learned_title(title)
        if not title:
            title = 'Untitled Topic'
        
        topics.append({
            'name': title,
            'summary': summary,
            'key_points': key_points[:10],  # Limit to 10 key points
            'source_file': filepath.name,
            'created_at': _date_from_filepath(filepath),
        })
    
    return topics


def sync_fact(tx, topic: dict) -> bool:
    """Create or update a Fact node in Neo4j with Word index."""
    try:
        words = extract_words(topic['name'])
        
        # Create/update Fact node and delete old HAS_WORD edges
        # created_at only set on first creation (coalesce preserves existing)
        result = tx.run("""
            MERGE (f:Fact {name: $name})
            SET f.summary = $summary,
                f.key_points = $key_points,
                f.source_file = $source_file,
                f.created_at = coalesce(f.created_at, $created_at),
                f.updated_at = $created_at
            WITH f
            OPTIONAL MATCH (f)-[old:HAS_WORD]->(:Word)
            DELETE old
            WITH f
            MERGE (s:Source {name: $source_file})
            MERGE (f)-[:FROM_SOURCE]->(s)
            RETURN f.name as name
        """, 
            name=topic['name'],
            summary=topic['summary'],
            key_points=topic['key_points'],
            source_file=topic['source_file'],
            created_at=topic['created_at']
        )
        
        if not result.single():
            return False
        
        # Create Word nodes and HAS_WORD edges
        if words:
            tx.run("""
                MATCH (f:Fact {name: $name})
                UNWIND $words AS word
                MERGE (w:Word {text: word})
                MERGE (f)-[:HAS_WORD]->(w)
            """, name=topic['name'], words=words)
        
        return True
    except Exception as e:
        print(f"Error syncing topic '{topic.get('name', 'unknown')}': {e}")
        return False


def link_related_facts(tx, max_df_ratio: float = 0.3, min_shared: int = 1):
    """Link facts that share keywords using Word index (O(n × avg_words) instead of O(n²)).

    Args:
        max_df_ratio: Skip words appearing in > this fraction of facts (stopwords)
        min_shared: Minimum number of shared words to create relationship
    
    Notes:
        - Uses Word index for efficient lookup instead of cartesian product
        - Filters high-frequency words to reduce noise (trading, market, data, etc.)
        - Uses id(f1) < id(f2) for stable pairing
        - Deletes all old RELATED_TO edges first to prevent stale data
        - Updates word frequencies (w.df) for future filtering
    """

    # Update word frequencies first
    tx.run("""
        MATCH (w:Word)<-[:HAS_WORD]-(f:Fact)
        WITH w, count(DISTINCT f) AS df
        SET w.df = df
    """)

    # Get total fact count for df threshold
    result = tx.run("MATCH (f:Fact) RETURN count(f) AS total")
    total_facts = result.single()['total']
    max_df = int(total_facts * max_df_ratio)

    # Remove old RELATED_TO edges to avoid stale properties / duplicates
    tx.run("MATCH ()-[r:RELATED_TO]->() DELETE r")

    # Link facts with shared Word nodes (indexed, not cartesian)
    # Filter out high-frequency words (stopwords)
    tx.run("""
        MATCH (f1:Fact)-[:HAS_WORD]->(w:Word)<-[:HAS_WORD]-(f2:Fact)
        WHERE elementId(f1) < elementId(f2) AND (w.df IS NULL OR w.df <= $max_df)
        WITH f1, f2, collect(DISTINCT w.text) AS shared
        WHERE size(shared) >= $min_shared
        MERGE (f1)-[r:RELATED_TO]->(f2)
        SET r.shared_keywords = shared,
            r.shared_count = size(shared)
    """, max_df=max_df, min_shared=min_shared)


def cleanup_orphaned_words(tx):
    """Delete Word nodes with no associated Facts."""
    tx.run("""
        MATCH (w:Word)
        WHERE NOT (()-[:HAS_WORD]->(w))
        DELETE w
    """)


def create_constraints(tx):
    """Ensure Word.text uniqueness constraint exists."""
    tx.run("""
        CREATE CONSTRAINT word_text_unique IF NOT EXISTS
        FOR (w:Word) REQUIRE w.text IS UNIQUE
    """)


def is_topic_saturated(topic_name: str, existing_names: set[str], threshold: int = 3) -> bool:
    """Return True if this topic's specific keywords already dominate the graph.

    Extracts the meaningful (non-stopword) words from the topic name, then counts
    how many existing fact names contain ALL of those words.  If that count reaches
    `threshold` the topic is considered saturated — the graph already knows enough
    about this concept and adding another near-duplicate would only dilute search
    precision.

    Threshold of 3 is intentionally lenient: we want to allow depth (e.g. three
    facts about "asyncio event loop" each from different angles), but block the
    tenth near-duplicate that contributes nothing new.
    """
    words = extract_words(topic_name)
    # Use only substantive words (length > 4) to avoid matching on generic short tokens
    specific = [w for w in words if len(w) > 4]
    if not specific:
        return False  # Can't determine saturation without specific words

    matches = sum(
        1 for name in existing_names
        if all(w in name.lower() for w in specific)
    )
    return matches >= threshold


def main():
    parser = argparse.ArgumentParser(description='Sync learned topics from memory files to Neo4j (Phase 4 RLM tool)')
    parser.add_argument('--days', type=int, default=30,
                        help='How many days back to look for new memory files (default: 30)')
    parser.add_argument('--full', action='store_true',
                        help='Ignore sync state and re-process all recent files')
    parser.add_argument('--extract-params', action='store_true',
                        help='Run parameter extraction (SHARES_PARAMETER / PREREQUISITE_OF) after syncing')
    parser.add_argument('--rebuild-graph', action='store_true',
                        help='Delete all RELATED_TO edges and rebuild from current Word index (no new sync needed)')
    args = parser.parse_args()

    # --rebuild-graph: just clean + rebuild RELATED_TO, no file sync
    if args.rebuild_graph:
        driver = get_driver()
        try:
            with driver.session() as session:
                print("Rebuilding RELATED_TO graph with tighter params (max_df_ratio=0.1, min_shared=2)...")
                # Delete polluted Word nodes (high-frequency stopwords that slipped in)
                session.run("""
                    MATCH (w:Word)
                    WHERE w.text IN $stop_words
                    DETACH DELETE w
                """, stop_words=list(STOP_WORDS))
                print("  Removed stopword Word nodes")
                # Rebuild
                session.execute_write(link_related_facts, max_df_ratio=0.1, min_shared=2)
                session.execute_write(cleanup_orphaned_words)
                result = session.run("MATCH ()-[r:RELATED_TO]->() RETURN count(r) AS cnt")
                edge_count = result.single()['cnt']
                print(f"  Done — {edge_count} RELATED_TO edges")
        finally:
            driver.close()
        return

    state = _load_sync_state(args.full)
    
    # Find memory files from the requested window
    cutoff = datetime.now() - timedelta(days=args.days)
    memory_files = _find_memory_files_to_sync(MEMORY_DIR, cutoff, state.get('synced_files', []))
    
    if not memory_files:
        print("No new memory files to sync")
        return
    
    # Get existing facts from Neo4j
    driver = get_driver()
    try:
        with driver.session() as session:
            # Ensure constraints exist
            session.execute_write(create_constraints)
            
            result = session.run("MATCH (f:Fact) RETURN f.name as name")
            existing_names = {r['name'] for r in result}
        
        # Parse and sync each file
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
        
        if all_topics:
            print(f"Found {len(all_topics)} new topics to sync")
            synced = 0
            with driver.session() as session:
                for topic in all_topics:
                    if session.execute_write(sync_fact, topic):
                        synced += 1
                        print(f"  ✓ {topic['name']}")

                _post_process_graph(session, driver)

            print(f"\nSynced {synced} topics to Neo4j")

            _update_indexes_and_optional_extraction(all_topics, driver, args.extract_params)
        else:
            print("No new topics to sync")

        _save_sync_state(state, memory_files)
            
    finally:
        driver.close()


def update_embedding_index(topics: list) -> int:
    """Update FAISS embedding index with new Facts. Returns number added."""
    if not EMBEDDING_INDEX_AVAILABLE:
        return 0
    
    try:
        embedding_index = EmbeddingIndex()
        
        # Add new topics to index
        added = 0
        for topic in topics:
            name = topic.get('name', '')
            key_points = topic.get('key_points', [])
            source_file = topic.get('source_file', '')
            summary = topic.get('summary', '')
            
            if not key_points:
                continue
            
            # Use key_points for embedding
            text_parts = [kp for kp in key_points[:5] if kp]
            if not text_parts:
                continue
            
            text = ' '.join(text_parts)
            doc_id = f"fact:{name}"
            metadata = {
                'name': name,
                'source_file': source_file,
                'key_points': key_points[:5],
                'summary': summary[:200] if summary else ''
            }
            
            if embedding_index.add_document(doc_id, text, metadata):
                added += 1
        
        if added > 0:
            embedding_index._save_index()
            print(f"  Added {added} facts to FAISS embedding index")
        
        return added
    except Exception as e:
        print(f"Warning: Could not update embedding index: {e}", file=sys.stderr)
        return 0


def update_neo4j_vector(topics: list, driver) -> int:
    """Update Neo4j Fact.embedding for vector search. Returns number updated."""
    if not NEO4J_VECTOR_AVAILABLE:
        return 0
    
    try:
        import hashlib
        import pickle
        from pathlib import Path
        
        cache_dir = _WORKSPACE / 'memory' / 'embeddings'
        model = os.environ.get('EMBEDDING_MODEL', 'nomic-embed-text')
        ollama_url = os.environ.get('OLLAMA_URL', 'http://localhost:11434')
        
        updated = 0
        
        # Batch get embeddings
        texts = []
        names = []
        for topic in topics:
            key_points = topic.get('key_points', [])
            if not key_points:
                continue
            text = ' '.join([kp for kp in key_points[:5] if kp])
            if text.strip():
                texts.append(text)
                names.append(topic.get('name', ''))
        
        if not texts:
            return 0
        
        # Get embeddings (with cache)
        from concurrent.futures import ThreadPoolExecutor, as_completed
        
        def get_embedding(i: int, text: str):
            text_hash = hashlib.md5(text[:2000].encode()).hexdigest()
            cache_file = cache_dir / f'{model}_{text_hash}.pkl'
            
            # Check cache
            if cache_file.exists():
                try:
                    with open(cache_file, 'rb') as f:
                        return (i, pickle.load(f))
                except:
                    pass
            
            # Get from API
            try:
                response = requests.post(
                    f'{ollama_url}/api/embeddings',
                    json={'model': model, 'prompt': text[:2000]},
                    timeout=30
                )
                response.raise_for_status()
                embedding = response.json().get('embedding')
                
                if embedding:
                    with open(cache_file, 'wb') as f:
                        pickle.dump(embedding, f)
                
                return (i, embedding)
            except:
                return (i, None)
        
        embeddings = [None] * len(texts)
        
        # Concurrent embedding requests
        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = {executor.submit(get_embedding, i, text): i for i, text in enumerate(texts)}
            for future in as_completed(futures):
                i, embedding = future.result()
                embeddings[i] = embedding
        
        # Update Neo4j
        with driver.session() as session:
            for name, embedding in zip(names, embeddings):
                if embedding:
                    try:
                        session.run('''
                            MATCH (f:Fact {name: $name})
                            SET f.embedding = $embedding
                        ''', name=name, embedding=embedding)
                        updated += 1
                    except Exception as e:
                        print(f"  Error updating {name}: {e}")
        
        if updated > 0:
            print(f"  Updated {updated} facts in Neo4j vector index")
        
        return updated
    except Exception as e:
        print(f"Warning: Could not update Neo4j vector index: {e}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    main()