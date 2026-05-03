#!/usr/bin/env python3
"""
Neo4j Session Sync - Sync Memory Files to Knowledge Graph

Reads session files and creates/updates Session and Fact nodes in Neo4j.
Maintains sync state to only process new/modified files.

Usage:
    python3 neo4j_sync.py [--full]
"""

import os
import sys
import json
import hashlib
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(Path.home() / ".openclaw" / "workspace" / ".env.neo4j")

from neo4j import GraphDatabase

WORKSPACE = Path.home() / ".openclaw" / "workspace"
MEMORY_DIR = WORKSPACE / "memory"
STATE_FILE = MEMORY_DIR / "neo4j_sync_state.json"


def compute_file_hash(filepath: Path) -> str:
    """Compute MD5 hash of file for change detection."""
    with open(filepath, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def load_sync_state() -> dict:
    """Load last sync state."""
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"files": {}}


def save_sync_state(state: dict):
    """Save sync state."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def extract_facts(content: str, source: str) -> list:
    """
    Extract learnable facts from session content.

    This is a simple extraction. For production, use an LLM to
    identify key learnings and create structured Fact nodes.
    """
    facts = []

    # Look for "## Learned:" sections
    lines = content.split("\n")
    current_fact = None

    for line in lines:
        if line.startswith("## Learned:") or line.startswith("### "):
            if current_fact:
                facts.append(current_fact)
            current_fact = {
                "name": line.replace("## Learned:", "").replace("###", "").strip(),
                "content": "",
                "source": source
            }
        elif current_fact:
            current_fact["content"] += line + "\n"

    if current_fact:
        facts.append(current_fact)

    return facts


def sync_file(driver, filepath: Path, state: dict) -> dict:
    """Sync a single session file to Neo4j."""
    file_hash = compute_file_hash(filepath)
    relative_path = filepath.name

    # Skip if unchanged
    if not os.getenv("FULL_SYNC") and state["files"].get(relative_path) == file_hash:
        return {"status": "skipped", "file": relative_path}

    # Read content
    with open(filepath) as f:
        content = f.read()

    # Extract date from filename
    date_match = filepath.stem[:10]  # YYYY-MM-DD
    try:
        session_date = datetime.strptime(date_match, "%Y-%m-%d").isoformat()
    except:
        session_date = datetime.now().isoformat()

    with driver.session() as session:
        # Create/update Session node
        cypher = """
        MERGE (s:Session {id: $id})
        SET s.date = $date, s.content = $content, s.updated = datetime()
        RETURN s
        """
        session.run(cypher, id=relative_path, date=session_date, content=content[:5000])

        # Extract and create Fact nodes
        facts = extract_facts(content, relative_path)
        for fact in facts[:10]:  # Limit to 10 facts per session
            fact_id = f"{relative_path}:{fact['name']}"[:100]
            cypher = """
            MERGE (f:Fact {id: $id})
            SET f.name = $name, f.content = $content, f.source = $source
            MERGE (s:Session {id: $session_id})
            MERGE (f)-[:LEARNED_IN]->(s)
            """
            session.run(cypher, id=fact_id, name=fact["name"],
                       content=fact["content"][:2000], source=fact["source"],
                       session_id=relative_path)

    # Update state
    state["files"][relative_path] = file_hash
    return {"status": "synced", "file": relative_path, "facts": len(facts)}


def main():
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    username = os.getenv("NEO4J_USERNAME", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")

    if not password:
        print("Error: NEO4J_PASSWORD not set")
        sys.exit(1)

    print(f"Connecting to Neo4j at {uri}...")
    driver = GraphDatabase.driver(uri, auth=(username, password))

    try:
        state = load_sync_state()
        synced = 0
        skipped = 0
        total_facts = 0

        # Process session files
        session_files = sorted(MEMORY_DIR.glob("*.md"), reverse=True)

        for filepath in session_files:
            if filepath.name.startswith("."):
                continue

            result = sync_file(driver, filepath, state)

            if result["status"] == "synced":
                synced += 1
                total_facts += result.get("facts", 0)
                print(f"  Synced: {filepath.name} ({result.get('facts', 0)} facts)")
            else:
                skipped += 1

        # Save state
        save_sync_state(state)

        print(f"\nSync complete: {synced} files synced, {skipped} skipped, {total_facts} facts extracted")

    finally:
        driver.close()


if __name__ == "__main__":
    if "--full" in sys.argv:
        os.environ["FULL_SYNC"] = "1"
    main()