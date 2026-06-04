#!/usr/bin/env python3
"""
RLM Example 1: Lazy Loading Session

Demonstrates the RLM (Recursive Language Model) lazy loading pattern.
Instead of loading all context at once, an agent records search results
as "pending" and loads individual facts into context on demand.

Prerequisites:
    pip install -e .                  # from repo root
    Neo4j running at bolt://localhost:7687
    AI_MEMORY_DIR set (or ~/.ai-memory exists with a seeded graph)
    At least a few Fact nodes in the graph (run neo4j_seed.py + neo4j_sync.py first)

Usage:
    python3 examples/01_lazy_loading_session.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_memory import MemoryClient

SESSION_ID = "example:lazy-loading-demo"


def run():
    with MemoryClient() as client:

        # Step 1: Search for relevant facts
        print("Step 1: Searching for 'attention mechanism'...")
        results = client.search("attention mechanism", metadata_only=True, max_results=5)
        if not results:
            print("  No results — is Neo4j running and the graph populated?")
            print("  Try: python3 scripts/neo4j_sync.py --full")
            return
        print(f"  Found {len(results)} facts")

        # Step 2: Initialize a session and record results as "pending"
        print("\nStep 2: Recording search results into session state...")
        with client.state(SESSION_ID) as mgr:
            mgr.init_session(SESSION_ID)
            mgr.record_query(SESSION_ID, "attention mechanism", results, state="pending")
            print(f"  Recorded {len(results)} facts as pending (not yet in context)")

            # Step 3: Inspect the pending queue
            pending = mgr.get_pending(SESSION_ID)
            print(f"\nStep 3: Pending facts ({len(pending)}):")
            for p in pending[:5]:
                print(f"  [{p['score']:.3f}] {p['fact_name']}")

            # Step 4: Load the top 2 facts (highest relevance score)
            print("\nStep 4: Loading top 2 pending facts into context...")
            loaded = mgr.load_next(SESSION_ID, count=2)
            for fact in loaded:
                kps = fact.get('key_points') or []
                print(f"\n  [{fact['name']}]")
                if kps:
                    for kp in kps[:2]:
                        print(f"    • {kp}")

            # Step 5: Summary
            summary = mgr.get_summary(SESSION_ID)
            print(f"\nStep 5: Session summary")
            print(f"  Total facts seen:  {summary['facts_total']}")
            print(f"  Loaded (in ctx):   {summary['facts_loaded']}")
            print(f"  Pending (queued):  {summary['facts_pending']}")

            # Cleanup: remove this demo session from Neo4j
            mgr.cleanup(max_age_hours=0)
            print("\n  Session cleaned up.")

    print("\nDemo complete.")


if __name__ == "__main__":
    run()
