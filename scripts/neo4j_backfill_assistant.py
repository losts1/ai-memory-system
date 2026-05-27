#!/usr/bin/env python3
"""
Neo4j Backfill Assistant

Phase 2 helper script to introduce multi-mind (submind) support
into an existing graph in a safe, backward-compatible way.

This script helps migrate an existing single-mind graph to support multiple
minds (subminds) by:

- Creating Assistant nodes
- Backfilling the `assistant` property on data nodes
- Optionally wiring up CREATED_BY relationships

Usage examples:
    # Safe dry run first (highly recommended)
    python3 neo4j_backfill_assistant.py --primary "Nova" --dry-run

    # Real run with default labels
    python3 neo4j_backfill_assistant.py --primary "Nova"

    # Also create relationships + register an additional mind
    python3 neo4j_backfill_assistant.py --primary "Nova" \
        --additional "Weft" --create-relationships

    # Custom batch size for very large graphs
    python3 neo4j_backfill_assistant.py --primary "Nova" --batch-size 200
"""

import argparse
import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Dict, List

from dotenv import load_dotenv
from neo4j import GraphDatabase

# Package conventions (match neo4j_seed.py / hybrid_memory_search.py)
_WORKSPACE = Path(os.getenv("AI_MEMORY_DIR", str(Path.home() / ".ai-memory")))
load_dotenv(_WORKSPACE / ".env.neo4j")


LOG_DIR = _WORKSPACE / "logs"
LOG_FILE = LOG_DIR / "neo4j_backfill_assistant.log"


# The labels that actually exist in the production ~12k-node graph.
# Sparse labels (ToolCall etc.) are handled gracefully (count = 0).
DEFAULT_NODE_LABELS: List[str] = [
    "Fact",
    "Session",
    "Event",
    "Decision",
    "ConversationTurn",
    "Word",
    "Source",
    "Project",
    "Meme",
    "ToolCall",
    "Opportunity",
    "Goal",
    "Question",
]


def setup_logging(verbose: bool = False) -> logging.Logger:
    """Structured logging to both console and rotating file (audit trail)."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    level = logging.DEBUG if verbose else logging.INFO

    logger = logging.getLogger("backfill_assistant")
    logger.setLevel(level)
    logger.handlers.clear()

    # Console (human-friendly, one line per event)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)

    # Rotating file for later inspection
    fh = RotatingFileHandler(
        LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(fh)

    return logger


def get_driver():
    """Driver using the same .env.neo4j conventions as the rest of the package."""
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USERNAME", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")
    if not password:
        print("NEO4J_PASSWORD not set in .env.neo4j (or environment)", file=sys.stderr)
        sys.exit(1)
    return GraphDatabase.driver(uri, auth=(user, password))


def ensure_assistant_node(driver, assistant_id: str, name: str, assistant_type: str = "primary"):
    """Idempotent Assistant node creation (MERGE)."""
    query = """
        MERGE (a:Assistant {id: $id})
        ON CREATE SET
            a.name = $name,
            a.type = $type,
            a.created_at = datetime()
        ON MATCH SET
            a.name = coalesce(a.name, $name),
            a.type = coalesce(a.type, $type)
    """
    with driver.session() as session:
        session.run(query, id=assistant_id, name=name, type=assistant_type)


def count_nodes_needing_backfill(driver, label: str) -> int:
    """Fast count-only (no data rows returned)."""
    query = f"""
        MATCH (n:{label})
        WHERE n.assistant IS NULL
        RETURN count(n) AS c
    """
    try:
        with driver.session() as session:
            rec = session.run(query).single()
            return rec["c"] if rec else 0
    except Exception:
        # Label does not exist in this graph — zero work
        return 0


def backfill_label(
    driver,
    label: str,
    assistant_id: str,
    batch_size: int,
    dry_run: bool,
    logger: logging.Logger,
) -> int:
    """
    Count-first, then batched UNWIND updates with progress every 100 nodes.

    Much safer on large graphs (7k+ ConversationTurn nodes) than long-running
    MATCH ... SET transactions.
    """
    total_to_do = count_nodes_needing_backfill(driver, label)
    if total_to_do == 0:
        logger.info(f"  {label}: already fully tagged (0 nodes need work)")
        return 0

    logger.info(f"  {label}: {total_to_do} nodes need the assistant property")

    if dry_run:
        logger.info(f"    [DRY RUN] would tag {total_to_do} {label} nodes with assistant='{assistant_id}'")
        return total_to_do

    updated = 0
    while True:
        # Stage 1: collect IDs (small, safe)
        id_query = f"""
            MATCH (n:{label})
            WHERE n.assistant IS NULL
            RETURN n.id AS id
            LIMIT $batch
        """
        with driver.session() as session:
            ids = [r["id"] for r in session.run(id_query, batch=batch_size)]

        if not ids:
            break

        # Stage 2: tight UNWIND + SET
        update_query = """
            UNWIND $ids AS nid
            MATCH (n) WHERE n.id = nid AND n.assistant IS NULL
            SET n.assistant = $assistant
            RETURN count(n) AS done
        """
        with driver.session() as session:
            result = session.run(update_query, ids=ids, assistant=assistant_id)
            done = result.single()["done"]
            updated += done

        if updated % 100 == 0 or updated >= total_to_do:
            logger.info(f"    ... {updated}/{total_to_do} {label} nodes tagged")

        if len(ids) < batch_size:
            break

    logger.info(f"    → {updated} {label} nodes tagged with assistant='{assistant_id}'")
    return updated


def create_created_by_relationships(
    driver,
    assistant_ids: List[str],
    labels: List[str],
    batch_size: int,
    dry_run: bool,
    logger: logging.Logger,
) -> Dict[tuple, int]:
    """
    Create (Assistant)-[:CREATED_BY]->(DataNode) for historical data.
    Returns {(assistant_id, label): count, ...}
    """
    rel_totals: Dict[tuple, int] = {}

    for aid in assistant_ids:
        for label in labels:
            count_q = f"""
                MATCH (a:Assistant {{id: $aid}})
                MATCH (n:{label} {{assistant: $aid}})
                WHERE NOT (a)-[:CREATED_BY]->(n)
                RETURN count(n) AS c
            """
            try:
                with driver.session() as s:
                    c = s.run(count_q, aid=aid).single()["c"]
            except Exception:
                c = 0

            if c == 0:
                rel_totals[(aid, label)] = 0
                continue

            action = "would create" if dry_run else "creating"
            logger.info(f"  {label} for {aid}: {action} {c} CREATED_BY relationships...")

            if dry_run:
                rel_totals[(aid, label)] = c
                continue

            created = 0
            while True:
                pair_q = f"""
                    MATCH (a:Assistant {{id: $aid}})
                    MATCH (n:{label} {{assistant: $aid}})
                    WHERE NOT (a)-[:CREATED_BY]->(n)
                    RETURN n.id AS nid, a.id AS aid
                    LIMIT $batch
                """
                with driver.session() as s:
                    pairs = [(r["nid"], r["aid"]) for r in s.run(pair_q, aid=aid, batch=batch_size)]

                if not pairs:
                    break

                create_q = """
                    UNWIND $pairs AS p
                    MATCH (a:Assistant {id: p[1]})
                    MATCH (n {id: p[0]})
                    WHERE NOT (a)-[:CREATED_BY]->(n)
                    CREATE (a)-[:CREATED_BY]->(n)
                    RETURN count(*) AS done
                """
                with driver.session() as s:
                    done = s.run(create_q, pairs=pairs).single()["done"]
                    created += done

                if created % 100 == 0:
                    logger.debug(f"    ... {created} {label} CREATED_BY links so far")

            rel_totals[(aid, label)] = created
            logger.info(f"    → {created} CREATED_BY links for {label} / {aid}")

    return rel_totals


def main():
    parser = argparse.ArgumentParser(
        description="Backfill Assistant nodes + assistant property for multi-mind support (Phase 2)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Always dry-run first on a real graph
  python3 scripts/neo4j_backfill_assistant.py --primary "Nova" --dry-run

  # Tag everything belonging to the historical primary mind
  python3 scripts/neo4j_backfill_assistant.py --primary "Nova"

  # Register a new submind (read-only Option A usage usually stops here)
  python3 scripts/neo4j_backfill_assistant.py --primary "Nova" --additional "Weft"

  # Full provenance wiring (only if you actually want CREATED_BY on old data)
  python3 scripts/neo4j_backfill_assistant.py --primary "Nova" \\
      --additional "Weft" --create-relationships --batch-size 200

See docs/PHASE2-SCHEMA-PROPOSAL.md for the schema rationale and pure-Cypher equivalents.
""",
    )
    parser.add_argument(
        "--primary",
        default="Nova",
        help="Name/ID of the primary (original) mind that owns most historical data",
    )
    parser.add_argument(
        "--additional",
        nargs="*",
        default=[],
        help="Additional Assistant nodes to create (e.g. Weft, ResearchBot). No data is tagged for them unless you also pass --create-relationships.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report exactly what would change without touching the database (strongly recommended first step)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Nodes per batch (default 500). Lower for very large graphs or constrained memory.",
    )
    parser.add_argument(
        "--create-relationships",
        action="store_true",
        help="Also create (Assistant)-[:CREATED_BY]->(Data) links for historical nodes (can be slow on 10k+ node graphs)",
    )
    parser.add_argument(
        "--labels",
        nargs="*",
        default=DEFAULT_NODE_LABELS,
        help="Override the list of labels to process (advanced). Default covers the full production schema.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging to console + log file"
    )

    args = parser.parse_args()

    logger = setup_logging(args.verbose)

    logger.info("=" * 60)
    logger.info("Neo4j Assistant Backfill — Phase 2 Multi-Tenancy Migration")
    logger.info("=" * 60)
    logger.info(f"Primary mind : {args.primary}")
    if args.additional:
        logger.info(f"Additional   : {', '.join(args.additional)}")
    logger.info(f"Batch size   : {args.batch_size}")
    logger.info(f"Labels       : {', '.join(args.labels)}")
    logger.info(f"Dry run      : {args.dry_run}")
    logger.info(f"Relationships: {args.create_relationships}")
    logger.info(f"Log file     : {LOG_FILE}")
    logger.info("")

    driver = get_driver()

    try:
        # 1. Ensure Assistant nodes exist (cheap + idempotent)
        logger.info("Ensuring Assistant nodes...")
        ensure_assistant_node(driver, args.primary, args.primary, "primary")
        logger.info(f"  ✓ {args.primary} (primary)")

        for mind in args.additional:
            ensure_assistant_node(driver, mind, mind, "submind")
            logger.info(f"  ✓ {mind} (submind)")

        # 2. Backfill assistant property on every relevant label
        logger.info("\nBackfilling assistant property (count-first, batched UNWIND)...")
        grand_total = 0
        for label in args.labels:
            updated = backfill_label(
                driver, label, args.primary, args.batch_size, args.dry_run, logger
            )
            grand_total += updated

        logger.info(f"\nTotal nodes {'that would be ' if args.dry_run else ''}tagged: {grand_total}")

        # 3. Optional CREATED_BY wiring
        rel_totals: Dict[tuple, int] = {}
        if args.create_relationships:
            logger.info("\nCreating CREATED_BY relationships (this can take a while on large graphs)...")
            all_assistants = [args.primary] + args.additional
            rel_totals = create_created_by_relationships(
                driver, all_assistants, args.labels, args.batch_size, args.dry_run, logger
            )

        # Final summary
        logger.info("\n" + "=" * 60)
        if args.dry_run:
            logger.info("DRY RUN COMPLETE — No changes were made to the database")
        else:
            logger.info("BACKFILL COMPLETE")

        logger.info(f"Primary mind      : {args.primary}")
        if args.additional:
            logger.info(f"Additional minds  : {', '.join(args.additional)}")
        logger.info(f"Labels processed  : {', '.join(args.labels)}")
        logger.info(f"Nodes backfilled  : {grand_total}")

        if args.create_relationships and rel_totals:
            total_rels = sum(rel_totals.values())
            action = "would have been" if args.dry_run else "were"
            logger.info(f"Relationships     : {total_rels} CREATED_BY links {action} created")

        logger.info("=" * 60)
        if not args.dry_run:
            logger.info(f"Detailed log written to {LOG_FILE}")

    finally:
        driver.close()


if __name__ == "__main__":
    main()