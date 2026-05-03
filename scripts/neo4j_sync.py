#!/usr/bin/env python3
"""
Neo4j Session Sync - Sync Memory Files to Knowledge Graph

Reads session files and creates/updates Session and Fact nodes in Neo4j.
Maintains sync state to only process new/modified files.

Usage:
    python3 neo4j_sync.py [--full]
"""

import fcntl
import hashlib
import json
import os
import sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(Path.home() / ".openclaw" / "workspace" / ".env.neo4j")

from neo4j import GraphDatabase

WORKSPACE = Path.home() / ".openclaw" / "workspace"
MEMORY_DIR = WORKSPACE / "memory"
STATE_FILE = MEMORY_DIR / "neo4j_sync_state.json"
LOCK_FILE = MEMORY_DIR / ".neo4j_sync.lock"
FACTS_PER_SESSION = 10
EMBED_MODEL = "nomic-embed-text"


def get_embedding(text: str) -> list | None:
    """Generate embedding via Ollama. Returns None if Ollama is unavailable."""
    try:
        import ollama
        response = ollama.embeddings(model=EMBED_MODEL, prompt=text[:2000])
        return response["embedding"]
    except Exception as e:
        # Ollama may not be running — degrade gracefully, vector search won't work
        # until embeddings are backfilled (re-run with --full once Ollama is available)
        return None


def compute_file_hash(filepath: Path) -> str:
    """Compute MD5 hash of file for change detection."""
    with open(filepath, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


def load_sync_state() -> dict:
    """Load last sync state. Returns empty state on corruption."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                data = json.load(f)
            # Validate expected schema — other sync scripts may write different formats
            if isinstance(data, dict) and "files" in data and isinstance(data["files"], dict):
                return data
            print("Warning: state file has unexpected schema, starting fresh", file=sys.stderr)
        except (json.JSONDecodeError, OSError) as e:
            print(f"Warning: corrupt state file ({e}), starting fresh", file=sys.stderr)
            try:
                STATE_FILE.rename(STATE_FILE.with_suffix(".corrupt"))
            except OSError:
                pass
    return {"files": {}}


def save_sync_state(state: dict):
    """Save sync state atomically (write-to-tmp + rename)."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2)
    os.replace(tmp, STATE_FILE)


def extract_facts(content: str, source: str) -> list:
    """
    Extract learnable facts from session content.

    Only starts a new fact on '## Learned:' headers.
    '### ' sub-headers are treated as content within the current fact,
    not as new facts — this prevents structural headers like
    '### References' from creating garbage Fact nodes.
    """
    facts = []
    lines = content.split("\n")
    current_fact = None

    for line in lines:
        if line.startswith("## Learned:"):
            if current_fact:
                facts.append(current_fact)
            current_fact = {
                "name": line.replace("## Learned:", "").strip(),
                "content": "",
                "source": source,
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

    with open(filepath) as f:
        content = f.read()

    # Extract date from filename (YYYY-MM-DD prefix)
    date_match = filepath.stem[:10]
    try:
        session_date = datetime.strptime(date_match, "%Y-%m-%d").isoformat()
    except ValueError:
        session_date = datetime.now().isoformat()

    facts = extract_facts(content, relative_path)
    synced_facts = facts[:FACTS_PER_SESSION]
    dropped = len(facts) - len(synced_facts)

    if dropped > 0:
        print(
            f"  Warning: {filepath.name} has {len(facts)} facts, "
            f"only syncing first {FACTS_PER_SESSION}",
            file=sys.stderr,
        )

    try:
        with driver.session() as neo4j_session:
            # Session node — no raw content stored; files are the source of truth
            neo4j_session.run(
                """
                MERGE (s:Session {id: $id})
                SET s.date = $date, s.source_file = $source_file, s.updated = datetime()
                """,
                id=relative_path,
                date=session_date,
                source_file=str(filepath),
            )

            embedding_failures = 0
            for fact in synced_facts:
                # Hash-based ID: no truncation collisions
                fact_id = hashlib.sha256(
                    f"{relative_path}:{fact['name']}".encode()
                ).hexdigest()[:16]

                neo4j_session.run(
                    """
                    MERGE (f:Fact {id: $id})
                    SET f.name = $name, f.content = $content, f.source = $source
                    WITH f
                    MATCH (s:Session {id: $session_id})
                    MERGE (f)-[:LEARNED_IN]->(s)
                    """,
                    id=fact_id,
                    name=fact["name"],
                    content=fact["content"][:2000],
                    source=fact["source"],
                    session_id=relative_path,
                )

                # Generate and store embedding for vector/semantic search
                embed_text = f"{fact['name']} {fact['content'][:500]}"
                embedding = get_embedding(embed_text)
                if embedding:
                    neo4j_session.run(
                        "MATCH (f:Fact {id: $id}) SET f.embedding = $embedding",
                        id=fact_id,
                        embedding=embedding,
                    )
                else:
                    embedding_failures += 1

    except Exception as e:
        print(f"  Error syncing {filepath.name}: {e}", file=sys.stderr)
        return {"status": "error", "file": relative_path, "error": str(e)}

    state["files"][relative_path] = file_hash
    return {
        "status": "synced",
        "file": relative_path,
        "facts": len(synced_facts),
        "dropped": dropped,
        "embed_failures": embedding_failures,
    }


def main():
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    username = os.getenv("NEO4J_USERNAME", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")

    if not password:
        print("Error: NEO4J_PASSWORD not set")
        sys.exit(1)

    # Prevent concurrent runs
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Another sync instance is running, exiting.")
        lock_fh.close()
        sys.exit(0)

    print(f"Connecting to Neo4j at {uri}...")
    driver = GraphDatabase.driver(uri, auth=(username, password))

    try:
        state = load_sync_state()
        synced = 0
        skipped = 0
        errors = 0
        total_facts = 0
        total_dropped = 0

        session_files = sorted(MEMORY_DIR.glob("*.md"), reverse=True)
        total_embed_failures = 0

        for filepath in session_files:
            if filepath.name.startswith("."):
                continue

            result = sync_file(driver, filepath, state)

            if result["status"] == "synced":
                synced += 1
                total_facts += result.get("facts", 0)
                total_dropped += result.get("dropped", 0)
                total_embed_failures += result.get("embed_failures", 0)
                msg = f"  Synced: {filepath.name} ({result['facts']} facts"
                if result.get("dropped"):
                    msg += f", {result['dropped']} dropped"
                print(msg + ")")
            elif result["status"] == "error":
                errors += 1
            else:
                skipped += 1

        save_sync_state(state)

        summary = f"\nSync complete: {synced} files synced, {skipped} skipped, {total_facts} facts written"
        if errors:
            summary += f", {errors} errors (check stderr)"
        if total_dropped:
            summary += f" ({total_dropped} dropped — sessions with >{FACTS_PER_SESSION} facts)"
        if total_embed_failures:
            summary += f"\nWarning: {total_embed_failures} facts have no embedding — Ollama unavailable. Re-run with --full once Ollama is running."
        print(summary)

    finally:
        driver.close()
        fcntl.flock(lock_fh, fcntl.LOCK_UN)
        lock_fh.close()


if __name__ == "__main__":
    if "--full" in sys.argv:
        os.environ["FULL_SYNC"] = "1"
    main()
