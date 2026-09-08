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
from __future__ import annotations

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
    if args.space:
        extra += ["--space", args.space]
    if args.mode:
        extra += ["--mode", args.mode]

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


def cmd_eval(args: argparse.Namespace) -> int:
    from ai_memory.eval.harness import main as harness_main
    return harness_main(args.args)


def _open_driver():
    from ai_memory._config import get_driver
    return get_driver()


def cmd_embed(args: argparse.Namespace) -> int:
    import json as _json

    import ai_memory.embed as E
    driver = _open_driver()
    try:
        if args.drop_prev:
            n = E.drop_prev(driver); print(f"removed embedding_prev from {n} facts"); result = {"dropped_prev": n}
        elif args.rollback:
            n = E.rollback_prev(driver); print(f"restored embedding_prev on {n} facts (provenance cleared)"); result = {"rolled_back": n}
        else:
            publish = not (args.no_publish or args.stale_only)
            result = E.embed_all(driver, keep_prev=args.keep_prev, publish=publish, stale_only=args.stale_only)
            cas_skipped_names = result.get("cas_skipped_names", [])
            for k, v in result.items():
                if k == "cas_skipped_names":
                    continue
                print(f"{k:<16} {v}")
            for name in cas_skipped_names:
                print(name)
        if args.json_out:
            Path(args.json_out).write_text(_json.dumps(result, indent=2), encoding="utf-8")
        return 0
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001, S110
            pass


def cmd_stats(args: argparse.Namespace) -> int:
    import json as _json

    import ai_memory.embed as E
    driver = _open_driver()
    try:
        st = E.vector_stats(driver)
        for k, v in st.items():
            print(f"{k:<18} {v}")
        if args.json_out:
            Path(args.json_out).write_text(_json.dumps(st, indent=2), encoding="utf-8")
        return 0
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001, S110
            pass


def _print_edges_report(report: dict) -> None:
    print(f"n_facts          {report['n_facts']}")
    print(f"edges            {report['edges']}")
    print(f"isolated         {report['isolated']} ({report['isolated_pct']:.1f}%)")
    print(f"max_degree       {report['max_degree']}")
    print(f"p95_degree       {report['p95_degree']}")
    print(f"edge_floor       {report['edge_floor']:.4f}")
    if report.get("dry_run"):
        print(f"rule_version_next {report['rule_version_next']}")
    else:
        print(f"rule_version     {report['rule_version']}")
        print(f"edges_deleted    {report['edges_deleted']}")


def cmd_edges(args: argparse.Namespace) -> int:
    import json as _json

    import ai_memory.wordindex as W
    driver = _open_driver()
    try:
        try:
            report = W.rebuild_edges(driver, seed=args.seed, pairs=args.pairs, k=args.k, dry_run=args.dry_run)
        except RuntimeError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        _print_edges_report(report)
        if args.json_out:
            Path(args.json_out).write_text(_json.dumps(_edges_json(report, args.dump_edges), indent=2), encoding="utf-8")
        return 0
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001, S110
            pass


def _edges_json(report: dict, dump_edges: bool) -> dict:
    """The edge report for --json. `edge_list` is every edge (2.6 MB on a 4.5k-edge
    graph, review #1) and is only included on --dump-edges."""
    if dump_edges:
        return report
    return {k: v for k, v in report.items() if k != "edge_list"}


def cmd_eval_edges(args: argparse.Namespace) -> int:
    from ai_memory.eval import edges
    return edges.main(list(args.args))


def cmd_nightly(args: argparse.Namespace) -> int:
    import json as _json

    import ai_memory.embed as E
    import ai_memory.wordindex as W
    driver = _open_driver()
    try:
        try:
            embed_report = E.embed_all(driver, publish=True)
        except RuntimeError as e:
            print(f"Error (embed): {e}", file=sys.stderr)
            return 1
        for k, v in embed_report.items():
            if k == "cas_skipped_names":
                continue
            print(f"{k:<16} {v}")

        # review #1: a re-embed with failures must not cut the edge layer over — the
        # previous rule_version stays live and the operator gets exit 1, as the systemd
        # README promises. embed_all only raises on a hard error, so check the count.
        failed = int(embed_report.get("embed_failed") or 0)
        if failed > 0:
            print(f"Error (embed): embed_failed={failed}; edge layer not rebuilt, previous edges stay in place",
                  file=sys.stderr)
            if args.json_out:
                Path(args.json_out).write_text(
                    _json.dumps({"embed": embed_report, "edges": None}, indent=2), encoding="utf-8"
                )
            return 1

        try:
            edges_report = W.rebuild_edges(driver, seed=args.seed)
        except RuntimeError as e:
            print(f"Error (edges): {e}", file=sys.stderr)
            return 1
        _print_edges_report(edges_report)

        if args.json_out:
            Path(args.json_out).write_text(
                _json.dumps({"embed": embed_report, "edges": _edges_json(edges_report, args.dump_edges)}, indent=2),
                encoding="utf-8",
            )
        return 0
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001, S110
            pass


def cmd_duplicates(args: argparse.Namespace) -> int:
    import json as _json

    import ai_memory.duplicates as D
    driver = _open_driver()
    try:
        try:
            report = D.duplicate_report(
                driver,
                threshold=args.cos,
                k=args.k,
                include_handled=args.include_handled,
                index=args.index,
            )
        except (RuntimeError, ValueError) as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

        s = report["summary"]
        print(
            "groups={groups} facts={facts} handled={handled} "
            "needs_owner_decision={needs_owner_decision} "
            "suggested_commands={suggested_commands} "
            "near_copy_pairs={near_copy_pairs} "
            "supersedes_loaded={supersedes_loaded}".format(**s)
        )

        markdown = D.render_markdown(report)
        if args.markdown_out:
            Path(args.markdown_out).write_text(markdown, encoding="utf-8")
        else:
            print(markdown)

        if args.json_out:
            Path(args.json_out).write_text(_json.dumps(report, indent=2), encoding="utf-8")
        return 0
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001, S110
            pass


def _parse_decisions_file(path: str) -> list[dict] | None:
    """Strictly validate a --from-file decisions document.

    Returns a list of ((new, old), apply) tuples on success. On any validation failure, prints
    "Error: decisions file ...: <what is wrong>" to stderr and returns None —
    the caller must not open a driver in that case.
    """
    import json as _json

    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as e:
        print(f"Error: decisions file {path}: {e}", file=sys.stderr)
        return None
    try:
        rows = _json.loads(text)
    except ValueError as e:
        print(f"Error: decisions file {path}: invalid JSON: {e}", file=sys.stderr)
        return None
    if not isinstance(rows, list):
        print(f"Error: decisions file {path}: must be a JSON list, got {type(rows).__name__}", file=sys.stderr)
        return None

    pairs: list[tuple[str, str]] = []
    apply_flags: list[bool] = []
    for i, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            print(f"Error: decisions file {path} row {i}: must be a JSON object, got {type(row).__name__}", file=sys.stderr)
            return None
        new = row.get("new")
        old = row.get("old")
        if not isinstance(new, str) or not new:
            print(f"Error: decisions file {path} row {i}: \"new\" must be a non-empty string", file=sys.stderr)
            return None
        if not isinstance(old, str) or not old:
            print(f"Error: decisions file {path} row {i}: \"old\" must be a non-empty string", file=sys.stderr)
            return None
        apply_flag = row.get("apply", False)
        if not isinstance(apply_flag, bool):
            print(f"Error: decisions file {path} row {i}: \"apply\" must be a JSON bool, got {type(apply_flag).__name__}", file=sys.stderr)
            return None
        pairs.append((new, old))
        apply_flags.append(apply_flag is True)

    return list(zip(pairs, apply_flags))


def cmd_supersede(args: argparse.Namespace) -> int:
    from neo4j.exceptions import Neo4jError, ServiceUnavailable

    import ai_memory.duplicates as D

    positional_given = args.new is not None or args.old is not None
    if args.from_file and positional_given:
        print("Error: pass NEW OLD or --from-file, not both", file=sys.stderr)
        return 2
    if not args.from_file and not (args.new and args.old):
        print("Error: supersede requires NEW OLD or --from-file PATH", file=sys.stderr)
        return 2

    if args.from_file:
        parsed = _parse_decisions_file(args.from_file)
        if parsed is None:
            return 1
        pairs = [pair for pair, _ in parsed]
        apply_flags = [flag for _, flag in parsed]
    else:
        pairs = [(args.new, args.old)]
        apply_flags = [True]

    driver = _open_driver()
    try:
        with driver.session() as session:
            try:
                supersedes = D.load_supersedes_strict(session)
                plan = D.plan_supersedes(session, pairs, supersedes)
            except (RuntimeError, ValueError, Neo4jError, ServiceUnavailable) as e:
                print(f"Error: {e}", file=sys.stderr)
                return 1

            refused = False
            for row, do_apply in zip(plan, apply_flags):
                status = "ok" if row["ok"] else "REFUSED"
                reason = f"  [{row['reason']}]" if row["reason"] else ""
                action = "apply" if (row["ok"] and do_apply) else "skip"
                print(f"{status}  {row['new']} -> {row['old']}{reason}  ({action})")
                if not row["ok"]:
                    refused = True

            if not args.apply:
                return 1 if refused else 0

            exit_code = 1 if refused else 0
            for row, do_apply in zip(plan, apply_flags):
                if not (row["ok"] and do_apply):
                    continue
                try:
                    result = D.supersede_fact(session, row["new"], row["old"], by=args.by)
                except (RuntimeError, ValueError) as e:
                    print(f"Error: {row['new']} -> {row['old']}: {e}", file=sys.stderr)
                    exit_code = 1
                    continue
                print(f"applied  {result['new']} -> {result['old']}  at={result['at']}")
            return exit_code
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001, S110
            pass


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
    p.add_argument("--graph", action="store_true",
                   help="alias for --mode hybrid (kept for compatibility; no longer "
                        "adds a relationships section)")
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
    p.add_argument("--space", default=None, help="Filter to a space (e.g. shared)")
    # default=None so omission falls through to hybrid_memory_search.py's
    # own default (hybrid).
    p.add_argument("--mode", choices=("hybrid", "fulltext", "vector"), default=None,
                   help="hybrid (default), fulltext-only, or vector-only")
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

    # embed (ai_memory.embed backfill)
    p = subparsers.add_parser("embed", help="Re-embed Facts from the canonical text with provenance (spec §4)")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--all", action="store_true", help="re-embed every Fact (nightly / first backfill)")
    mode.add_argument("--stale-only", action="store_true",
                       help="re-embed only Facts whose text sha changed; reuses the current RetrievalConfig (never publishes)")
    mode.add_argument("--drop-prev", action="store_true", help="remove embedding_prev after the gate passes")
    mode.add_argument("--rollback", action="store_true", help="restore embedding_prev and clear provenance")
    p.add_argument("--keep-prev", action="store_true", help="keep the previous vector in embedding_prev")
    p.add_argument("--no-publish", action="store_true", help="reuse the current RetrievalConfig instead of publishing a new version")
    p.add_argument("--json", dest="json_out", default=None)
    p.set_defaults(func=cmd_embed)

    # stats (vector drift)
    p = subparsers.add_parser("stats", help="Vector provenance drift: foreign / stale / wrong-model vectors, isolated Facts")
    p.add_argument("--json", dest="json_out", default=None)
    p.set_defaults(func=cmd_stats)

    # edges (ai_memory.wordindex nightly edge-layer rebuild, spec §7.5)
    p = subparsers.add_parser(
        "edges",
        help="Rebuild the RELATED_TO edge layer (word-index + embedding z-blend rule)",
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--rebuild", action="store_true", help="compute and cut over to a new edge layer")
    mode.add_argument("--dry-run", action="store_true", help="compute and report only; nothing is written")
    p.add_argument("--seed", type=int, default=0, help="RNG seed for the baseline pair sample")
    p.add_argument("--pairs", type=int, default=40000, help="number of random pairs sampled for the z-score baseline")
    p.add_argument("--k", type=int, default=5, help="max edges picked per Fact")
    p.add_argument("--json", dest="json_out", default=None)
    p.add_argument("--dump-edges", action="store_true",
                   help="include the full edge_list in --json (megabytes on a large graph; off by default)")
    p.set_defaults(func=cmd_edges)

    # nightly (embed_all + rebuild_edges, spec §7.5 order)
    p = subparsers.add_parser(
        "nightly",
        help="Nightly maintenance: publish boilerplate + re-embed, then rebuild edges and cut over",
        description="Runs in order (spec §7.5):\npublish boilerplate + re-embed, then rebuild edges and cut over.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--seed", type=int, default=0, help="RNG seed for the edge rebuild's baseline pair sample")
    p.add_argument("--json", dest="json_out", default=None)
    p.add_argument("--dump-edges", action="store_true",
                   help="include the full edge_list in --json (megabytes on a large graph; off by default)")
    p.set_defaults(func=cmd_nightly)

    # duplicates (ai_memory.duplicates owner-facing report)
    from ai_memory.wordindex import DUP_COS

    p = subparsers.add_parser(
        "duplicates",
        help="Owner-facing duplicate-Facts report (suffix groups + near-copy pairs)",
    )
    p.add_argument("--cos", type=float, default=DUP_COS,
                    help=f"cosine threshold for near-copy pairs (default {DUP_COS})")
    p.add_argument("--k", type=int, default=3, help="neighbors pooled per embedded Fact (default 3)")
    p.add_argument("--include-handled", action="store_true",
                    help="include already-handled groups in the report")
    p.add_argument("--index", default=None,
                    help="vector index name (default: $NEO4J_VECTOR_INDEX or fact_embeddings)")
    p.add_argument("--json", dest="json_out", default=None, help="write the report as JSON to PATH")
    p.add_argument("--markdown", dest="markdown_out", default=None,
                    help="write the markdown report to PATH instead of printing it to stdout")
    p.set_defaults(func=cmd_duplicates)

    # supersede (ai_memory.duplicates guarded supersede)
    p = subparsers.add_parser(
        "supersede",
        help="Mark OLD Fact superseded by NEW (guarded: refuses cycles, unknowns, double-supersede)",
    )
    p.add_argument("new", nargs="?", metavar="NEW", help="name of the Fact to keep")
    p.add_argument("old", nargs="?", metavar="OLD", help="name of the Fact to mark superseded")
    p.add_argument("--from-file", dest="from_file", default=None,
                    help="JSON list of {\"new\":..., \"old\":..., \"apply\": true|false} (mutually exclusive with NEW OLD)")
    p.add_argument("--by", default="ai-memory", help="attribution recorded on the SUPERSEDES edge")
    p.add_argument("--apply", action="store_true",
                    help="write the supersede(s); without this, only print the plan (dry run)")
    p.set_defaults(func=cmd_supersede)

    # eval (ai_memory.eval.harness)
    p = subparsers.add_parser(
        "eval",
        help="Retrieval evaluation against a golden set (--golden PATH, --rankers, --label)",
        description=" ".join([
            "--golden PATH", "--rankers a,b", "--label", "--judge-url URL",
            "--judge-model M", "--cache PATH", "--json PATH", "--k N", "--pool N",
        ]),
    )
    p.add_argument("args", nargs=argparse.REMAINDER,
                    help="passed to ai_memory.eval.harness (see --golden --rankers --label --judge-url)")
    p.set_defaults(func=cmd_eval)

    # eval-edges (ai_memory.eval.edges) — a real subparser so --help lists it (review #3);
    # dispatch still happens in main() before argparse, same as `eval`.
    p = subparsers.add_parser(
        "eval-edges",
        help="Judge a seeded RELATED_TO sample against the edge rubric (--sample, --seed, --gate-against)",
        description=" ".join([
            "--sample N", "--seed N", "--legacy | --rule-version N", "--judge-url URL",
            "--judge-model M", "--cache PATH", "--json PATH", "--gate-against PATH",
        ]),
    )
    p.add_argument("args", nargs=argparse.REMAINDER, help="passed to ai_memory.eval.edges")
    p.set_defaults(func=cmd_eval_edges)

    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "eval":
        # `ai-memory eval` forwards straight to the harness's own argparse
        # parser so it can accept flags (e.g. --label) that the top-level
        # parser doesn't declare. Dispatch here, before argparse sees them.
        from ai_memory.eval import harness
        return harness.main(argv[1:])
    if argv and argv[0] == "eval-edges":
        from ai_memory.eval import edges
        return edges.main(argv[1:])
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
