#!/usr/bin/env python3
"""
Memory State Manager — CLI wrapper.

All session-state logic lives in ai_memory.state.MemoryStateManager.
This file is the command-line entry point only.

Usage:
    python3 memory_state.py init --session "weft:main"
    python3 memory_state.py record-query --session "weft:main" --query "gamma" \
        --results Fact1,Fact2 --scores 0.95,0.89
    python3 memory_state.py mark-loaded --session "weft:main" --facts Fact1,Fact2
    python3 memory_state.py pending --session "weft:main"
    python3 memory_state.py summary --session "weft:main"
    python3 memory_state.py load-fact --session "weft:main" --fact "Merton-Portfolio"
    python3 memory_state.py load-next --session "weft:main" --count 3
    python3 memory_state.py cleanup --max-age-hours 24
    python3 memory_state.py list-sessions
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ai_memory.state import MemoryStateManager


def _parse_list(raw: str):
    """Parse comma-separated or bracket-enclosed list from CLI."""
    s = raw.strip()
    if s.startswith("[") and s.endswith("]"):
        s = s[1:-1]
    return [item.strip() for item in s.split(",") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Memory state management for Neo4j-backed session tracking (Phase 4 RLM tool)."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("init", help="Initialize or refresh a session")
    p.add_argument("--session", required=True)

    p = subparsers.add_parser("record-query", help="Record a search query and results")
    p.add_argument("--session", required=True)
    p.add_argument("--query", required=True)
    p.add_argument("--results", required=True, help="Comma-separated fact names")
    p.add_argument("--scores", help="Comma-separated scores (optional)")
    p.add_argument("--state", default="pending", choices=["pending", "loaded"])

    p = subparsers.add_parser("mark-loaded", help="Mark facts as loaded into context")
    p.add_argument("--session", required=True)
    p.add_argument("--facts", required=True, help="Comma-separated fact names")

    p = subparsers.add_parser("pending", help="Show pending facts for a session")
    p.add_argument("--session", required=True)

    p = subparsers.add_parser("summary", help="Show full state summary for a session")
    p.add_argument("--session", required=True)

    p = subparsers.add_parser("load-fact", help="Load one specific fact")
    p.add_argument("--session", required=False)
    p.add_argument("--fact", required=True)

    p = subparsers.add_parser("load-next", help="Load the next N pending facts")
    p.add_argument("--session", required=True)
    p.add_argument("--count", type=int, default=3)

    p = subparsers.add_parser("cleanup", help="Remove old sessions")
    p.add_argument("--max-age-hours", type=int, default=24)

    subparsers.add_parser("list-sessions", help="List all known sessions")

    args = parser.parse_args()

    try:
        manager = MemoryStateManager()
    except Exception as e:
        print(json.dumps({"success": False, "error": str(e)}))
        sys.exit(1)

    try:
        if args.command == "init":
            result = manager.init_session(args.session)
            print(json.dumps({"success": True, **result}))

        elif args.command == "record-query":
            names = _parse_list(args.results)
            scores = _parse_list(args.scores) if args.scores else []
            scores = [float(s) for s in scores] + [0.0] * (len(names) - len(scores))
            results = [{"name": n, "score": sc} for n, sc in zip(names, scores[:len(names)])]
            result = manager.record_query(args.session, args.query, results, state=args.state)
            print(json.dumps({"success": True, **result}))

        elif args.command == "mark-loaded":
            updated = manager.mark_loaded(args.session, _parse_list(args.facts))
            print(json.dumps({"success": True, "updated": updated}))

        elif args.command == "pending":
            pending = manager.get_pending(args.session)
            print(json.dumps({"success": True, "pending": pending, "count": len(pending)}))

        elif args.command == "summary":
            summary = manager.get_summary(args.session)
            if summary is None:
                print(json.dumps({"success": False, "error": f"Session not found: {args.session}"}))
                sys.exit(1)
            print(json.dumps({"success": True, **summary}))

        elif args.command == "load-fact":
            fact = manager.load_fact(args.session, args.fact)
            if fact is None:
                print(json.dumps({"success": False, "error": f"Fact not found: {args.fact}"}))
                sys.exit(1)
            print(json.dumps({"success": True, **fact}))

        elif args.command == "load-next":
            facts = manager.load_next(args.session, count=args.count)
            print(json.dumps({"success": True, "facts": facts, "count": len(facts)}))

        elif args.command == "cleanup":
            deleted = manager.cleanup(max_age_hours=args.max_age_hours)
            print(json.dumps({"success": True, "deleted_sessions": deleted}))

        elif args.command == "list-sessions":
            sessions = manager.list_sessions()
            print(json.dumps({"success": True, "sessions": sessions, "count": len(sessions)}))

    finally:
        manager.close()


if __name__ == "__main__":
    main()
