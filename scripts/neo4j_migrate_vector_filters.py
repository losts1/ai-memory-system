#!/usr/bin/env python3
"""Vector index filter-property migration (spec §4 "Index rebuild").

  --preflight   create <index>_v2 WITH the filter properties, measure population, probe, drop it
  --migrate     DROP <index>; CREATE it WITH the filter properties; wait ONLINE/100%; gate
  --dry-run     with --migrate: print the statements, run nothing

During --migrate, callers using db.index.vector.queryNodes(<index>) fail until the index is
ONLINE (measured ~1 s for 1,502 Facts); the library's search_vector degrades to lexical-only.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_memory import vector_index as VI


def _open_driver():
    from ai_memory._config import get_driver
    return get_driver()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--preflight", action="store_true")
    mode.add_argument("--migrate", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--index", default=None,
                    help="default: NEO4J_VECTOR_INDEX from the workspace .env.neo4j, else fact_embeddings")
    ap.add_argument("--props", default=",".join(VI.DEFAULT_FILTER_PROPS))
    ap.add_argument("--json", dest="json_out", default=None)
    a = ap.parse_args(argv)
    if a.preflight and a.dry_run:
        ap.error("--dry-run applies to --migrate only")
    props = tuple(p.strip() for p in a.props.split(",") if p.strip())
    if not props:
        ap.error("--props must name at least one property")
    driver = _open_driver()
    mode = "preflight" if a.preflight else "migrate"
    try:
        # NEO4J_VECTOR_INDEX isn't loaded until _open_driver()'s get_driver() runs
        # load_dotenv(<workspace>/.env.neo4j), so the default can't be resolved at
        # argparse time (it would silently target a wrong/nonexistent index).
        index = a.index or os.getenv("NEO4J_VECTOR_INDEX", "fact_embeddings")
        try:
            if a.preflight:
                rep = VI.preflight(driver, index, props)
            else:
                rep = VI.migrate(driver, index, props, dry_run=a.dry_run)
        except Exception as e:  # noqa: BLE001 — CLI boundary: report as JSON, no traceback
            rep = {"ok": False, "mode": mode, "index": index, "error": str(e)}
    finally:
        try:
            driver.close()
        except Exception:  # noqa: BLE001, S110
            pass
    print(json.dumps(rep, indent=2, default=str))
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(rep, indent=2, default=str), encoding="utf-8")
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
