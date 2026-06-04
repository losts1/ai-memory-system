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
    ai-memory traverse --start "Attention Is All You Need" --parameter gamma
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
    print("AI Memory workspace — bootstrap instructions")
    print("(This command PRINTS instructions only. Nothing is created on disk.)")
    print()
    print("Run these yourself to bootstrap a workspace (see BOOTSTRAP.md, README.md):")
    print("  1. mkdir -p ~/.ai-memory/memory/{core,sessions,inbox,archive,learner-sessions,embeddings,projects}")
    print("  2. cp -r templates/* ~/.ai-memory/")
    print("  3. cp -r templates/core/* ~/.ai-memory/memory/core/")
    print("  4. cp templates/INDEX.qmd ~/.ai-memory/memory/")
    print("  5. cp -r scripts ~/.ai-memory/")
    print("  6. cp -r docs ~/.ai-memory/")
    print()
    print("Then activate your Neo4j instance and copy .env.neo4j into ~/.ai-memory/")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    extra = []
    if args.assistant:
        extra += ["--assistant", args.assistant]
    if args.graph:
        extra += ["--graph"]
    if args.files_only:
        extra += ["--files-only"]
    if args.use_embeddings:
        extra += ["--use-embeddings"]
    if args.metadata_only:
        extra += ["--metadata-only"]
    if args.fields:
        extra += ["--fields", args.fields]
    if args.max_results is not None:
        extra += ["--max-results", str(args.max_results)]

    return _run_script("hybrid_memory_search.py", [args.query] + extra)


def cmd_traverse(args: argparse.Namespace) -> int:
    extra = []
    if args.start:
        extra += ["--start", args.start]
    if args.depth is not None:
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
    if args.assistant:
        extra += ["--assistant", args.assistant]

    return _run_script("rlm/neo4j_traverse.py", extra)


def cmd_sync(args: argparse.Namespace) -> int:
    extra = []
    if args.full:
        extra.append("--full")
    if args.assistant:
        extra += ["--assistant", args.assistant]

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

    return _run_script("rlm/neo4j_learn_sync.py", extra)


def cmd_state(args: argparse.Namespace) -> int:
    # memory_state.py uses positional subcommands so the action must come
    # first in argv. The CLI exposes actions as boolean flags and per-action
    # extras as named flags, then re-shapes argv here. Each spec entry is:
    #   (cli_attr, script_subcommand, [(cli_attr_for_extra, --script-flag), ...])
    STATE_ACTIONS = [
        ("init",          "init",          [("session",        "--session")]),
        ("pending",       "pending",       [("session",        "--session")]),
        ("summary",       "summary",       [("session",        "--session")]),
        ("record_query",  "record-query",  [("session",        "--session"),
                                             ("query",          "--query"),
                                             ("results",        "--results"),
                                             ("scores",         "--scores"),
                                             ("state_value",    "--state")]),
        ("mark_loaded",   "mark-loaded",   [("session",        "--session"),
                                             ("facts",          "--facts")]),
        ("load_fact",     "load-fact",     [("session",        "--session"),
                                             ("fact",           "--fact")]),
        ("load_next",     "load-next",     [("session",        "--session"),
                                             ("count",          "--count")]),
        ("cleanup",       "cleanup",       [("max_age_hours",  "--max-age-hours")]),
        ("list_sessions", "list-sessions", []),
    ]
    chosen = [(attr, sub, fwd) for attr, sub, fwd in STATE_ACTIONS
              if getattr(args, attr, False)]
    if len(chosen) > 1:
        names = ", ".join("--" + a[0].replace("_", "-") for a in chosen)
        print(f"Error: pick exactly one action; got {names}", file=sys.stderr)
        return 2
    if not chosen:
        all_flags = ", ".join("--" + attr.replace("_", "-") for attr, _, _ in STATE_ACTIONS)
        print(
            f"Error: state requires one action flag — one of {all_flags}",
            file=sys.stderr,
        )
        return 2

    attr, subcommand, fwd_map = chosen[0]
    extra = [subcommand]
    for cli_attr, script_flag in fwd_map:
        val = getattr(args, cli_attr, None)
        if val is not None:
            extra += [script_flag, str(val)]
    # Anything truly unmodeled gets passed through. We strip a single leading
    # `--` because users may add it as an argparse-terminator habit; the
    # underlying memory_state.py subparsers would interpret it as the
    # option-terminator and treat the rest as positionals.
    if args.args:
        passthrough = list(args.args)
        if passthrough and passthrough[0] == "--":
            passthrough = passthrough[1:]
        extra += passthrough

    return _run_script("rlm/memory_state.py", extra)


def cmd_backfill(args: argparse.Namespace) -> int:
    extra = []
    if args.primary:
        extra += ["--primary", args.primary]
    if args.additional:
        # neo4j_backfill_assistant.py declares --additional with nargs="*",
        # so repeated `--additional X --additional Y` would overwrite.
        # Pass a single flag with all values to preserve every mind.
        extra += ["--additional", *args.additional]
    if args.dry_run:
        extra.append("--dry-run")
    if args.create_relationships:
        extra.append("--create-relationships")
    if args.batch_size is not None:
        extra += ["--batch-size", str(args.batch_size)]

    return _run_script("neo4j_backfill_assistant.py", extra)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ai-memory",
        description="Unified CLI for the AI Memory System (public redistribution package)"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # init
    p = subparsers.add_parser(
        "init",
        help="Print workspace bootstrap instructions (does not create files)",
    )
    p.set_defaults(func=cmd_init)

    # search
    p = subparsers.add_parser("search", help="Hybrid semantic + graph search (wrapper for hybrid_memory_search.py)")
    p.add_argument("query", help="Search query")
    p.add_argument("--assistant", "--mind", dest="assistant", help="Filter by assistant/mind")
    p.add_argument("--graph", action="store_true", help="Also run graph/fulltext search")
    p.add_argument("--files-only", action="store_true",
                   help="Search only the markdown memory files (grep-based)")
    p.add_argument("--use-embeddings", action="store_true",
                   help="Use the local FAISS index instead of Neo4j vector search "
                        "(not compatible with --assistant — FAISS is not tenant-aware)")
    p.add_argument("--metadata-only", action="store_true")
    p.add_argument("--fields", help="Comma-separated fields to return")
    # default=None so omission falls through to hybrid_memory_search.py's
    # own default (5). Avoids the CLI silently changing wrapped-script behavior.
    p.add_argument("--max-results", type=int, default=None)
    p.set_defaults(func=cmd_search)

    # traverse
    p = subparsers.add_parser("traverse", help="Graph traversal with optional parameter tracing")
    p.add_argument("--start", help="Starting Fact name")
    p.add_argument("--depth", type=int, default=2)
    p.add_argument("--parameter", help="Parameter tracing mode (e.g. gamma, inventory)")
    p.add_argument("--filter-word", help="Filter traversal to nodes containing this word")
    p.add_argument("--fields", help="Fields to return")
    p.add_argument("--metadata-only", action="store_true")
    p.add_argument("--stats", action="store_true", help="Show graph statistics")
    p.add_argument("--assistant", "--mind", dest="assistant",
                   help="Filter results to only Facts created by this assistant/mind "
                        "(e.g. Weft, Nova). Matches the Phase 2 assistant property.")
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
    # Actions — one of these must be set (mutually exclusive at the cmd_state layer).
    p.add_argument("--init", action="store_true")
    p.add_argument("--pending", action="store_true")
    p.add_argument("--summary", action="store_true")
    p.add_argument("--record-query", action="store_true")
    p.add_argument("--mark-loaded", action="store_true")
    p.add_argument("--load-fact", action="store_true")
    p.add_argument("--load-next", action="store_true")
    p.add_argument("--cleanup", action="store_true")
    p.add_argument("--list-sessions", action="store_true")
    # Per-action extras (issue #41): declared on the state subparser so they're
    # parseable by argparse and forwardable. The action-spec table in cmd_state
    # controls which flags are forwarded for each action.
    p.add_argument("--query")
    p.add_argument("--results")
    p.add_argument("--scores")
    p.add_argument("--state", dest="state_value", choices=["pending", "loaded"],
                   help="record-query: initial state for returned facts")
    p.add_argument("--facts")
    p.add_argument("--fact")
    p.add_argument("--max-age-hours", type=int, dest="max_age_hours")
    p.add_argument("--count", type=int)
    p.add_argument("args", nargs=argparse.REMAINDER, help="Additional arguments passed through")
    p.set_defaults(func=cmd_state)

    # backfill / attach
    p = subparsers.add_parser("backfill", help="Backfill assistant properties on an existing graph (multi-mind)")
    p.add_argument("--primary", required=True)
    p.add_argument("--additional", action="append")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--create-relationships", action="store_true")
    # default=None so omission falls through to neo4j_backfill_assistant.py's
    # own default (500). Avoids the CLI silently quartering the batch size.
    p.add_argument("--batch-size", type=int, default=None)
    p.set_defaults(func=cmd_backfill)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
