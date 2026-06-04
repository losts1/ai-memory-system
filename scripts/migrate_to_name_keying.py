#!/usr/bin/env python3
"""
v1.1 → v1.2 schema migration helper (issue #N18).

After v1.2.0, ``Fact.name`` is the canonical identity (was ``Fact.id``).
On graphs that ran the v1.1 ``neo4j_sync.py`` path, the same conceptual
fact may exist as multiple nodes with identical ``name`` but different
``id`` hashes — the new ``fact_name_unique`` constraint can't be created
until those duplicates are merged.

This script:
  1. Counts duplicate-name Facts and shows the worst offenders.
  2. With ``--apply``, merges them via APOC's ``mergeNodes`` (properties
     combined, relationships consolidated). Requires APOC.
  3. Re-runs ``scripts/neo4j_seed.py`` to install the constraint.

Usage:
    python3 scripts/migrate_to_name_keying.py            # dry run (default)
    python3 scripts/migrate_to_name_keying.py --apply    # do the merges
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_memory._config import get_driver  # noqa: E402
from ai_memory.exceptions import Neo4jConnectionError  # noqa: E402


COUNT_DUPS_CYPHER = """
MATCH (f:Fact)
WITH f.name AS name, count(*) AS n
WHERE n > 1
RETURN name, n
ORDER BY n DESC
"""

# 'combine' preserves all property values as arrays on collision;
# 'discard' would only keep the first node's properties.
MERGE_DUPS_CYPHER = """
MATCH (f:Fact)
WITH f.name AS name, collect(f) AS dups
WHERE size(dups) > 1
CALL apoc.refactor.mergeNodes(dups,
    {properties: 'discard', mergeRels: true}
) YIELD node
RETURN count(node) AS merged
"""


def main():
    p = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true",
                   help="Actually merge duplicates (default: dry run only)")
    p.add_argument("--top", type=int, default=10,
                   help="Show this many worst-offender duplicate names")
    args = p.parse_args()

    try:
        driver = get_driver()
    except Neo4jConnectionError as e:
        print(f"Cannot connect to Neo4j: {e}", file=sys.stderr)
        return 2

    try:
        with driver.session() as s:
            dup_groups = list(s.run(COUNT_DUPS_CYPHER))
            total_groups = len(dup_groups)
            extra_nodes = sum(r["n"] - 1 for r in dup_groups)
            print(f"Duplicate-name groups: {total_groups}")
            print(f"Extra nodes that will be merged away: {extra_nodes}")
            if total_groups == 0:
                print("Nothing to migrate. The fact_name_unique constraint can be installed safely.")
                print("Run: python3 scripts/neo4j_seed.py")
                return 0

            print(f"\nTop {min(args.top, total_groups)} duplicate names:")
            for r in dup_groups[: args.top]:
                print(f"  {r['n']:3}x  {r['name']!r}")

            if not args.apply:
                print("\nDry run only. Re-run with --apply to perform the merge.")
                print("Required: Neo4j with the APOC procedures library installed.")
                return 0

            # Sanity check: APOC available?
            try:
                s.run("CALL apoc.help('refactor') YIELD name RETURN name LIMIT 1").single()
            except Exception:
                print("\nAPOC not available. Install the APOC plugin in Neo4j to use --apply.",
                      file=sys.stderr)
                return 3

            confirm = input(f"\nMerge {extra_nodes} duplicates into {total_groups} canonical Facts? [type 'yes' to confirm] ")
            if confirm.strip().lower() != "yes":
                print("Aborted.")
                return 1

            merged = s.run(MERGE_DUPS_CYPHER).single()["merged"]
            print(f"Merged {merged} duplicate-name groups.")
            print("Now run: python3 scripts/neo4j_seed.py  (to install fact_name_unique)")
        return 0
    finally:
        driver.close()


if __name__ == "__main__":
    sys.exit(main())
