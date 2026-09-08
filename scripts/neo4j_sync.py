#!/usr/bin/env python3
"""
Neo4j Session Sync - Sync Memory Files to Knowledge Graph

Reads session files and creates/updates Session and Fact nodes in Neo4j.
Maintains sync state to only process new/modified files.

Supports tagging new data with an `assistant` / mind for multi-tenancy (Phase 2).

Usage:
    python3 neo4j_sync.py
    python3 neo4j_sync.py --assistant Weft
    python3 neo4j_sync.py --full --assistant Nova
"""
from __future__ import annotations  # `X | None` annotations must not evaluate on Python 3.9

import argparse
import fcntl
import hashlib
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

# Workspace directory: set AI_MEMORY_DIR env var to override default (~/.ai-memory)
_WORKSPACE = Path(os.getenv("AI_MEMORY_DIR", str(Path.home() / ".ai-memory")))
load_dotenv(_WORKSPACE / ".env.neo4j")

from neo4j import GraphDatabase

from ai_memory.embed import (
    build_embed_subquery,
    embed_params,
    embed_text,
    fact_embed_text,
    text_sha,
)
from ai_memory.learn import maintain_edges_after_write, owner_blocks_write, refuse_owner_conflict
from ai_memory.retrieval_config import load_retrieval_config
from ai_memory.wordindex import tokenize

WORKSPACE = _WORKSPACE
MEMORY_DIR = WORKSPACE / "memory"
STATE_FILE = MEMORY_DIR / "neo4j_sync_state.json"
LOCK_FILE = MEMORY_DIR / ".neo4j_sync.lock"
FACTS_PER_SESSION = 10


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


def write_fact_with_embedding(neo4j_session, fact: dict, *, relative_path: str, assistant, cfg, embed_fn=embed_text) -> str:
    """MERGE the Fact's text, its Word index, and — when possible — its canonical-text
    embedding in ONE statement. CAS on summary/key_points (owned by other writers);
    content is ours. Returns "owner_conflict" | "embedded" | "cas_skipped" | "text_only".

    The same read that supplies the CAS values also reads the existing Fact's
    `assistant`: this MERGE-by-name is an in-place overwrite, so a Fact tagged with
    another mind is refused ("owner_conflict") and nothing is written. An untagged
    Fact is library memory this writer may update and claim (`owner_blocks_write`)."""
    seen = neo4j_session.run(
        "OPTIONAL MATCH (f:Fact {name: $name}) "
        "RETURN f.summary AS summary, f.key_points AS key_points, f.assistant AS owner",
        name=fact["name"],
    ).single()
    owner = seen.get("owner") if seen else None
    if owner_blocks_write(owner, assistant):
        refuse_owner_conflict(fact["name"], owner, assistant)
        return "owner_conflict"

    fact_id = hashlib.sha256(f"{relative_path}:{fact['name']}".encode()).hexdigest()[:16]
    content = fact["content"][:2000]
    params = {"id": fact_id, "name": fact["name"], "content": content, "source": fact["source"], "session_id": relative_path}
    fact_set = "SET f.content = $content, f.source = $source, f.id = coalesce(f.id, $id)"
    if assistant:
        fact_set += ", f.assistant = $assistant"
        params["assistant"] = assistant

    summary_seen = seen["summary"] if seen else None
    kp_seen = seen["key_points"] if seen else None
    boilerplate = cfg.boilerplate if cfg is not None else ()
    text = fact_embed_text(fact["name"], summary_seen, kp_seen, content, boilerplate)
    params["words"] = tokenize(text, fact["name"])

    embed_block, embed_return = "", ""
    if cfg is not None and embed_fn is not None:
        vec = embed_fn(text)
        if vec:
            embed_block = "WITH f\n" + build_embed_subquery(["summary", "key_points"]) + "\n"
            embed_return = ", embedded"
            params.update(embed_params(vec, text_sha(text, cfg.version), cfg.version, cas={"summary": summary_seen, "key_points": kp_seen}))
    rec = neo4j_session.run(
        f"""
        MERGE (f:Fact {{name: $name}})
        {fact_set}
        WITH f
        MATCH (s:Session {{id: $session_id}})
        MERGE (f)-[:LEARNED_IN]->(s)
        WITH f
        OPTIONAL MATCH (f)-[old:HAS_WORD]->(:Word)
        DELETE old
        WITH DISTINCT f
        FOREACH (word IN $words | MERGE (w:Word {{text: word}}) MERGE (f)-[:HAS_WORD]->(w))
        {embed_block}RETURN f.name AS name{embed_return}
        """,
        **params,
    ).single()
    if not rec or not embed_return:
        return "text_only"
    return "embedded" if rec["embedded"] else "cas_skipped"


def sync_file(driver, filepath: Path, state: dict, assistant: str | None = None) -> dict:
    """Sync a single session file to Neo4j.

    If assistant is provided, tags the created Session and Fact nodes
    with assistant=<name> for multi-mind support (Phase 2).
    """
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
            # This writer now MERGEs (:Word {text}) itself (see write_fact_with_embedding);
            # ensure the uniqueness constraint sync_facts() also creates, so a fresh
            # deployment synced only by this script can't race into duplicate Word nodes.
            neo4j_session.run(
                "CREATE CONSTRAINT word_text_unique IF NOT EXISTS "
                "FOR (w:Word) REQUIRE w.text IS UNIQUE"
            )

            # Ensure the Assistant node exists if we're tagging data
            if assistant:
                neo4j_session.run(
                    """
                    MERGE (a:Assistant {id: $assistant})
                    ON CREATE SET a.name = $assistant, a.created_at = datetime()
                    """,
                    assistant=assistant,
                )

            # Session node — no raw content stored; files are the source of truth
            session_params = {
                "id": relative_path,
                "date": session_date,
                "source_file": str(filepath),
            }
            session_set = "SET s.date = $date, s.source_file = $source_file, s.updated = datetime()"
            if assistant:
                session_set += ", s.assistant = $assistant"
                session_params["assistant"] = assistant

            neo4j_session.run(
                f"""
                MERGE (s:Session {{id: $id}})
                {session_set}
                """,
                **session_params,
            )

            try:
                cfg = load_retrieval_config(neo4j_session)
            except Exception as e:  # noqa: BLE001
                print(f"  RetrievalConfig unavailable ({e}); syncing text without embeddings", file=sys.stderr)
                cfg = None

            embedding_failures = 0
            cas_skipped = 0
            owner_conflicts = 0
            for fact in synced_facts:
                status = write_fact_with_embedding(neo4j_session, fact, relative_path=relative_path, assistant=assistant, cfg=cfg, embed_fn=embed_text)
                if status == "owner_conflict":
                    # Refused: nothing was written, so there is nothing to re-edge either.
                    owner_conflicts += 1
                    continue
                if status == "text_only":
                    embedding_failures += 1
                elif status == "cas_skipped":
                    cas_skipped += 1
                try:
                    maintain_edges_after_write(neo4j_session, fact["name"])
                except Exception as e:  # noqa: BLE001 — the nightly rebuild repairs edges; never abort the sync
                    print(f"  Edge maintenance failed for {fact['name']!r}: {e}", file=sys.stderr)

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
        "cas_skipped": cas_skipped,
        "owner_conflicts": owner_conflicts,
    }


def main(assistant: str | None = None):
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    username = os.getenv("NEO4J_USERNAME", "neo4j")
    password = os.getenv("NEO4J_PASSWORD")

    if not password:
        print("Error: NEO4J_PASSWORD not set")
        sys.exit(1)

    if assistant:
        print(f"Tagging all synced data with assistant: {assistant}")

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
    driver = None
    try:
        driver = GraphDatabase.driver(uri, auth=(username, password))
        state = load_sync_state()
        synced = 0
        skipped = 0
        errors = 0
        total_facts = 0
        total_dropped = 0

        session_files = sorted(MEMORY_DIR.glob("*.md"), reverse=True)
        total_embed_failures = 0
        total_cas_skipped = 0
        total_owner_conflicts = 0

        for filepath in session_files:
            if filepath.name.startswith("."):
                continue

            result = sync_file(driver, filepath, state, assistant=assistant)

            if result["status"] == "synced":
                synced += 1
                total_facts += result.get("facts", 0)
                total_dropped += result.get("dropped", 0)
                total_embed_failures += result.get("embed_failures", 0)
                total_cas_skipped += result.get("cas_skipped", 0)
                total_owner_conflicts += result.get("owner_conflicts", 0)
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
        if total_cas_skipped:
            summary += f" ({total_cas_skipped} embedding CAS-skipped — concurrent edits, not failures)"
        if total_owner_conflicts:
            summary += (
                f"\n{total_owner_conflicts} facts refused — a Fact of that name is tagged with "
                "another assistant (see stderr); rename them or sync under that mind."
            )
        if total_embed_failures:
            summary += f"\nWarning: {total_embed_failures} facts have no embedding — Ollama or RetrievalConfig unavailable. Run `ai-memory embed --all` once they are."
        print(summary)

    finally:
        if driver is not None:
            driver.close()
        fcntl.flock(lock_fh, fcntl.LOCK_UN)
        lock_fh.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sync memory files to Neo4j")
    parser.add_argument("--full", action="store_true", help="Force full sync (ignore state file)")
    parser.add_argument("--assistant", "--mind", dest="assistant",
                        help="Tag all created Fact and Session nodes with this assistant/mind name")

    args = parser.parse_args()

    if args.full:
        os.environ["FULL_SYNC"] = "1"

    main(assistant=args.assistant)
