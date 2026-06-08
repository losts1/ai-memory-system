#!/usr/bin/env python3
"""
Verify that the live Neo4j schema matches the set expected by neo4j_seed.py.

Usage:
    python3 scripts/verify_schema.py
    python3 scripts/verify_schema.py --strict  # also list unexpected extras

Exit 0 = all required items present.
Exit 1 = one or more required items missing.
Exit 2 = configuration error (no password).
"""

import argparse
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

_WORKSPACE = Path(os.getenv("AI_MEMORY_DIR", str(Path.home() / ".ai-memory")))
load_dotenv(_WORKSPACE / ".env.neo4j")

# ---------------------------------------------------------------------------
# Expected schema — mirrors what neo4j_seed.py creates.
# Keep in sync when neo4j_seed.py is updated.
# ---------------------------------------------------------------------------

EXPECTED_CONSTRAINTS = {
    "fact_name_unique",        # Fact.name — canonical v1.2+ identity
    "fact_id_unique",          # Fact.id  — legacy hash-based compat
    "session_id_unique",       # Session.id
    "assistant_id_unique",     # Assistant.id
    "source_name_unique",      # Source.name — provenance nodes written by learn._sync_fact_tx
    # word_text_unique is created at runtime by ai_memory/learn.sync_facts(),
    # not by neo4j_seed.py, so it is intentionally excluded here.
}

# Constraint-backing indexes created at runtime (not by neo4j_seed.py).
# Their backing RANGE indexes appear in SHOW INDEXES, so we suppress them
# from the --strict extras list to avoid false-positive noise.
EXPECTED_RUNTIME_CONSTRAINTS = {"word_text_unique"}

EXPECTED_INDEXES = {
    "fact_assistant_idx",      # Fact.assistant — multi-tenancy filter
    "fact_source_idx",         # Fact.source
    "fact_created_at_idx",     # Fact.created_at — temporal range queries
    "fact_source_file_idx",    # Fact.source_file — learn.py source lookups
    "session_date_idx",        # Session.date
    "session_assistant_idx",   # Session.assistant
}

EXPECTED_VECTOR_INDEX = os.getenv("NEO4J_VECTOR_INDEX", "fact_embeddings")
EXPECTED_VECTOR_DIMS  = 768   # nomic-embed-text
# NOTE: vector similarity function ('cosine') is set by neo4j_seed.py but not
# validated here. A euclidean index would pass this check with correct dims.
# Add similarity_function validation if strict similarity enforcement is needed.
EXPECTED_FULLTEXT     = os.getenv("NEO4J_FULLTEXT_INDEX", "fact_content")
EXPECTED_FULLTEXT_PROPS = {"name", "content", "summary"}


# ---------------------------------------------------------------------------
# Schema queries (require live Neo4j)
# ---------------------------------------------------------------------------

def get_live_schema(driver):
    """Return (constraints, indexes, vector, fulltext).

    constraints : set[str]
    indexes     : dict[str, dict]  — {name: {type, properties: set[str]}}
    vector      : dict | None      — {name, dims} for EXPECTED_VECTOR_INDEX
    fulltext    : dict | None      — {name, properties: set[str]} for fact_content
    """
    with driver.session() as session:
        result = session.run("SHOW CONSTRAINTS YIELD name RETURN name")
        constraints = {r["name"] for r in result}

        result = session.run(
            "SHOW INDEXES YIELD name, type, properties "
            "WHERE type <> 'LOOKUP' RETURN name, type, properties"
        )
        indexes = {}
        for r in result:
            name = r["name"]
            idx_type = r["type"]
            props = set(r["properties"] or [])
            indexes[name] = {"type": idx_type, "properties": props}

        # Fetch vector index options separately (properties alone don't carry dims)
        result = session.run(
            "SHOW INDEXES YIELD name, type, options "
            "WHERE type = 'VECTOR' AND name = $name RETURN options",
            name=EXPECTED_VECTOR_INDEX,
        )
        rec = result.single()
        vector = None
        if rec:
            config = (rec["options"] or {}).get("indexConfig", {})
            dims = config.get("vector.dimensions")
            vector = {"name": EXPECTED_VECTOR_INDEX, "dims": dims}

        # Fetch fulltext index properties
        fulltext = None
        if EXPECTED_FULLTEXT in indexes:
            fulltext = {
                "name": EXPECTED_FULLTEXT,
                "properties": indexes[EXPECTED_FULLTEXT]["properties"],
            }

    return constraints, indexes, vector, fulltext


# ---------------------------------------------------------------------------
# Diff logic (pure — no Neo4j, fully testable)
# ---------------------------------------------------------------------------

def diff_schema(live_constraints, live_indexes, live_vector, live_fulltext):
    """Return list of issue strings. Empty list = schema is valid.

    Extra indexes in live_indexes beyond EXPECTED_INDEXES are ignored;
    they are surfaced separately by the CLI in --strict mode.
    """
    issues = []

    for c in sorted(EXPECTED_CONSTRAINTS - live_constraints):
        issues.append(f"MISSING constraint: {c}")

    for i in sorted(EXPECTED_INDEXES - set(live_indexes)):
        issues.append(f"MISSING index: {i} — re-run neo4j_seed.py")

    if live_vector is None:
        issues.append(
            f"MISSING vector index: {EXPECTED_VECTOR_INDEX!r} "
            f"(set NEO4J_VECTOR_INDEX env var if the index has a different name)"
        )
    elif live_vector["dims"] is None:
        issues.append(
            f"Vector index {EXPECTED_VECTOR_INDEX!r} has no dimensions configured"
        )
    elif live_vector["dims"] != EXPECTED_VECTOR_DIMS:
        issues.append(
            f"Vector index {EXPECTED_VECTOR_INDEX!r} has wrong dimensions: "
            f"expected {EXPECTED_VECTOR_DIMS}, got {live_vector['dims']}"
        )

    if live_fulltext is None:
        issues.append(
            f"MISSING fulltext index: {EXPECTED_FULLTEXT!r} — run neo4j_seed.py"
        )
    else:
        missing_props = EXPECTED_FULLTEXT_PROPS - live_fulltext["properties"]
        if missing_props:
            issues.append(
                f"Fulltext index {EXPECTED_FULLTEXT!r} missing properties: "
                f"{sorted(missing_props)} — "
                f"DROP INDEX {EXPECTED_FULLTEXT} then re-run neo4j_seed.py"
            )

    return issues


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _mark(ok):
    return "✓" if ok else "✗"


def main():
    parser = argparse.ArgumentParser(
        description="Verify Neo4j schema against the expected set from neo4j_seed.py"
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Also list unexpected extra indexes (not treated as errors)"
    )
    args = parser.parse_args()

    uri      = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    username = os.getenv("NEO4J_USERNAME", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")

    if not password:
        print("Error: NEO4J_PASSWORD not set in .env.neo4j", file=sys.stderr)
        sys.exit(2)

    from neo4j import GraphDatabase
    driver = GraphDatabase.driver(uri, auth=(username, password))
    try:
        constraints, indexes, vector, fulltext = get_live_schema(driver)
    finally:
        driver.close()

    issues = diff_schema(constraints, indexes, vector, fulltext)

    print("Schema Verification")
    print("=" * 40)

    for name in sorted(EXPECTED_CONSTRAINTS):
        print(f"  {_mark(name in constraints)}  {name:35} (constraint)")

    for name in sorted(EXPECTED_INDEXES):
        print(f"  {_mark(name in indexes)}  {name:35} (range index)")

    vec_ok = vector is not None and vector["dims"] == EXPECTED_VECTOR_DIMS
    print(f"  {_mark(vec_ok)}  {EXPECTED_VECTOR_INDEX:35} (vector, {EXPECTED_VECTOR_DIMS}-dim)")

    if fulltext:
        missing = EXPECTED_FULLTEXT_PROPS - fulltext["properties"]
        ft_ok = not missing
    else:
        ft_ok = False
    print(f"  {_mark(ft_ok)}  {EXPECTED_FULLTEXT:35} (fulltext)")

    if args.strict:
        expected_all = EXPECTED_INDEXES | {EXPECTED_VECTOR_INDEX, EXPECTED_FULLTEXT}
        extras = set(indexes) - expected_all - EXPECTED_CONSTRAINTS - EXPECTED_RUNTIME_CONSTRAINTS
        if extras:
            print(f"\n  [INFO] Extra indexes in DB (not required):")
            for e in sorted(extras):
                print(f"         {e}")

    if issues:
        print(f"\nResult: {len(issues)} issue(s) found")
        for issue in issues:
            print(f"  ✗ {issue}")
        sys.exit(1)
    else:
        print("\nResult: schema OK")
        sys.exit(0)


if __name__ == "__main__":
    main()
