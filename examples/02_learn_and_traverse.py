#!/usr/bin/env python3
"""
RLM Example 2: Learn from notes, then traverse the graph

Demonstrates the full RLM pipeline:
  1. Parse a sample notes file for "## Learned:" sections
  2. Sync them to Neo4j as Fact nodes with Word index
  3. Traverse the resulting graph to explore relationships

Prerequisites:
    pip install -e .                  # from repo root
    Neo4j running at bolt://localhost:7687
    AI_MEMORY_DIR set (or ~/.ai-memory exists with neo4j_seed.py already run)

Usage:
    python3 examples/02_learn_and_traverse.py
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_memory import MemoryClient
from ai_memory.learn import parse_learned_topics, sync_facts, rebuild_graph

# Sample notes content (domain-neutral transformer architecture)
SAMPLE_NOTES = """\
## Learned: Transformer Self-Attention
- Uses queries, keys, and values to compute attention weights
- Scales dot products by sqrt(d_k) to prevent vanishing gradients
- Enables parallel processing unlike RNNs
Introduced in "Attention Is All You Need" (Vaswani et al. 2017)

## Learned: Multi-Head Attention
- Runs multiple attention functions in parallel
- Each head learns to attend to different representation subspaces
- Outputs are concatenated and projected
Extends single-head self-attention for richer representations

## Learned: Positional Encoding
- Adds position information to token embeddings
- Sine/cosine encoding at different frequencies
- Allows the model to use sequence order without recurrence
Required because self-attention is permutation-invariant
"""


def run():
    # Step 1: Parse sample notes into topic dicts
    print("Step 1: Parsing sample notes...")
    with tempfile.NamedTemporaryFile(suffix=".md", mode="w", delete=False) as f:
        f.write(SAMPLE_NOTES)
        notes_path = Path(f.name)

    topics = parse_learned_topics(SAMPLE_NOTES, notes_path)
    notes_path.unlink()

    print(f"  Parsed {len(topics)} topics:")
    for t in topics:
        print(f"    - {t['name']}")

    # Step 2: Sync to Neo4j
    print("\nStep 2: Syncing to Neo4j...")
    synced = sync_facts(topics, assistant="example-agent")
    print(f"  Synced {synced}/{len(topics)} facts")
    if synced == 0:
        print("  Nothing synced — is Neo4j running?")
        print("  Try: docker start neo4j  (or see README.md)")
        return

    # Step 3: Traverse from the first synced fact
    start_fact = topics[0]['name']
    print(f"\nStep 3: Traversing graph from '{start_fact}'...")
    with MemoryClient() as client:
        result = client.traverse(
            start_fact,
            depth=2,
            fields=["name", "teaser"],
        )
        if result.get("success"):
            print(f"  Found {result['total_nodes']} related facts:")
            for node in result["nodes"]:
                print(f"    [{node['name']}]")
                if node.get("teaser"):
                    print(f"      {node['teaser']}")
        else:
            print(f"  Traversal note: {result.get('error')}")
            print("  (The graph may need more facts for edges to form — try neo4j_sync.py --full)")

    print("\nDemo complete.")


if __name__ == "__main__":
    run()
