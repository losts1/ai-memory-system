#!/usr/bin/env python3
"""
ai-memory — Unified CLI for the AI Memory System (Phase 6)

Thin wrapper that provides a consistent command-line experience for the
most common operations across the public redistribution package.

This is intentionally minimal in v1. It dispatches to the existing scripts
so behavior stays identical while giving users a single entry point.

Usage:
    ai-memory --help
    ai-memory init
    ai-memory search "transformer attention" --assistant Weft
    ai-memory traverse "Attention Is All You Need" --parameter gamma
    ai-memory sync --assistant Weft
    ai-memory learn-sync --days 7 --assistant Weft
    ai-memory state --pending --session "weft:main"
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Allow running from source checkout
SCRIPT_DIR = Path(__file__).parent.resolve()
REPO_ROOT = SCRIPT_DIR.parent

def _run_script(rel_path: str, args: list[str]) -> int:
    """Execute one of the existing scripts with the given arguments."""
    script = (SCRIPT_DIR / rel_path).resolve()
    if not script.exists():
        print(f"Error: script not found: {script}", file=sys.stderr)
        return 1

    cmd = [sys.executable, str(script)] + args
    # Preserve the user's environment (important for .env.neo4j, AI_MEMORY_DIR, etc.)
    return subprocess.call(cmd, cwd=REPO_ROOT)


def cmd_init(args: argparse.Namespace) -> int:
    print("Initializing AI Memory workspace...")
    print()
    print("Recommended steps (from BOOTSTRAP.md and README):")
    print("  1. mkdir -p ~/.ai-memory/memory/{core,sessions,inbox,archive,learner-sessions,embeddings,projects}")
    print("  2. cp -r templates/* ~/.ai-memory/")
    print("  3. cp -r templates/core/* ~/.ai-memory/memory/core/")
    print("  4. cp templates/INDEX.qmd ~/.ai-memory/memory/")
    print("  5. cp -r scripts ~/.ai-memory/")
    print("  6. cp -r docs ~/.ai-memory/")
    print()
    print("Then activate your Neo4j instance and copy .env.neo4j into ~/.ai-memory/")
    print("See README.md and BOOTSTRAP.md for full details.")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    extra = []
    if args.assistant:
        extra += ["--assistant", args.assistant]
    if args.mind:
        extra += ["--mind", args.mind]
    if args.graph:
        extra += ["--graph"]
    if args.metadata_only:
        extra += ["--metadata-only"]
    if args.fields:
        extra += ["--fields", args.fields]
    if args.max_results:
        extra += ["--max-results", str(args.max_results)]

    return _run_script("hybrid_memory_search.py", [args.query] + extra)


def cmd_traverse(args: argparse.Namespace) -> int:
    extra = ["--start", args.start]
    if args.depth:
        extra += ["--depth", str(args.depth)]
    if args.parameter:
        extra += ["--parameter", args.parameter]
    if args.filter_word:
        extra += ["--filter-word", args.filter_word]
    if args.fields:
        extra += ["--fields", args.fields]
    if args.metadata_only:
        extra += ["--metadata-only"]
    if args.stats:
        extra += ["--stats"]

    return _run_script("rlm/neo4j_traverse.py", extra)


def cmd_sync(args: argparse.Namespace) -> int:
    extra = []
    if args.full:
        extra.append("--full")
    if args.assistant:
        extra += ["--assistant", args.assistant]
    if args.mind:
        extra += ["--mind", args.mind]

    return _run_script("neo4j_sync.py", extra)


def cmd_learn_sync(args: argparse.Namespace) -> int:
    extra = []
    if args.days:
        extra += ["--days", str(args.days)]
    if args.full:
        extra.append("--full")
    if args.extract_params:
        extra.append("--extract-params")
    if args.rebuild_graph:
        extra.append("--rebuild-graph")
    if args.assistant:
        extra += ["--assistant", args.assistant]
    if args.mind:
        extra += ["--mind", args.mind]

    return _run_script("rlm/neo4j_learn_sync.py", extra)


def cmd_state(args: argparse.Namespace) -> int:
    extra = []
    if args.session:
        extra += ["--session", args.session]
    if args.init:
        extra.append("--init")
    if args.record_query:
        extra += ["--record-query"]
    if args.pending:
        extra.append("--pending")
    if args.summary:
        extra.append("--summary")
    if args.mark_loaded:
        extra.append("--mark-loaded")
    if args.load_fact:
        extra.append("--load-fact")
    if args.cleanup:
        extra.append("--cleanup")

    # Pass through any remaining args
    if args.args:
        extra += args.args

    return _run_script("rlm/memory_state.py", extra)


def cmd_backfill(args: argparse.Namespace) -> int:
    extra = []
    if args.primary:
        extra += ["--primary", args.primary]
    if args.additional:
        for a in args.additional:
            extra += ["--additional", a]
    if args.dry_run:
        extra.append("--dry-run")
    if args.create_relationships:
        extra.append("--create-relationships")
    if args.batch_size:
        extra += ["--batch-size", str(args.batch_size)]

    return _run_script("neo4j_backfill_assistant.py", extra)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-memory",
        description="Unified CLI for the AI Memory System (public redistribution package)"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    p = subparsers.add_parser("init", help="Show workspace initialization instructions")
    p.set_defaults(func=cmd_init)

    # search
    p = subparsers.add_parser("search", help="Hybrid semantic + graph search (wrapper for hybrid_memory_search.py)")
    p.add_argument("query", help="Search query")
    p.add_argument("--assistant", "--mind", dest="assistant", help="Filter by assistant/mind")
    p.add_argument("--graph", action="store_true", help="Use graph/fulltext search instead of vector")
    p.add_argument("--metadata-only", action="store_true")
    p.add_argument("--fields", help="Comma-separated fields to return")
    p.add_argument("--max-results", type=int, default=10)
    p.set_defaults(func=cmd_search)

    # traverse
    p = subparsers.add_parser("traverse", help="Graph traversal with optional parameter tracing")
    p.add_argument("--start", required=True, help="Starting Fact name")
    p.add_argument("--depth", type=int, default=2)
    p.add_argument("--parameter", help="Parameter tracing mode (e.g. gamma, inventory)")
    p.add_argument("--filter-word", help="Filter traversal to nodes containing this word")
    p.add_argument("--fields", help="Fields to return")
    p.add_argument("--metadata-only", action="store_true")
    p.add_argument("--stats", action="store_true", help="Show graph statistics")
    p.set_defaults(func=cmd_traverse)

    # sync
    p = subparsers.add_parser("sync", help="Sync markdown sessions to Neo4j")
    p.add_argument("--full", action="store_true")
    p.add_argument("--assistant", "--mind", dest="assistant")
    p.set_defaults(func=cmd_sync)

    # learn-sync
    p = subparsers.add_parser("learn-sync", help="Sync learned topics from daily notes to Neo4j (RLM ingestion)")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--full", action="store_true")
    p.add_argument("--extract-params", action="store_true")
    p.add_argument("--rebuild-graph", action="store_true")
    p.add_argument("--assistant", "--mind", dest="assistant")
    p.set_defaults(func=cmd_learn_sync)

    # state (memory_state)
    p = subparsers.add_parser("state", help="Per-session memory state (lazy loading) - wrapper for memory_state.py")
    p.add_argument("--session", required=False)
    p.add_argument("--init", action="store_true")
    p.add_argument("--pending", action="store_true")
    p.add_argument("--summary", action="store_true")
    p.add_argument("--record-query", action="store_true")
    p.add_argument("--mark-loaded", action="store_true")
    p.add_argument("--load-fact", action="store_true")
    p.add_argument("--cleanup", action="store_true")
    p.add_argument("args", nargs=argparse.REMAINDER, help="Additional arguments passed through")
    p.set_defaults(func=cmd_state)

    # backfill / attach
    p = subparsers.add_parser("backfill", help="Backfill assistant properties on an existing graph (multi-mind)")
    p.add_argument("--primary", required=True)
    p.add_argument("--additional", action="append")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--create-relationships", action="store_true")
    p.add_argument("--batch-size", type=int, default=100)
    p.set_defaults(func=cmd_backfill)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())