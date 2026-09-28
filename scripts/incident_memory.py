"""Inspect local runs, review cards, search clues or retry saving an existing result."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from dotenv import load_dotenv

from tracebridge.incident_memory import IncidentStore


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, help="SQLite file; default is output/tracebridge/incidents.sqlite3")
    parser.add_argument("--project", required=True, help="Required local project scope; not authentication")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="List up to 50 recent incidents")
    for name, id_name in (("incident", "incident_id"), ("run", "run_id"), ("card", "card_id")):
        commands.add_parser(name).add_argument(id_name)
    review = commands.add_parser("review", help="Review approval and edits do not change factual verification")
    review.add_argument("card_id", help="Card ID equals its source run ID")
    review.add_argument("--action", choices=("approve", "edit", "reject"), required=True)
    review.add_argument("--reviewer", required=True, help="Local audit label, not a verified identity")
    review.add_argument("--note", default="")
    review.add_argument("--symptom")
    review.add_argument("--finding")
    review.add_argument("--next-action")
    search = commands.add_parser("search", help="Exact fields first, then lexical FTS5; at most two reviewed cards")
    search.add_argument("--query", default="")
    for name in ("error-code", "path", "exception", "stack-fingerprint"):
        search.add_argument("--" + name, action="append", default=[])
    save = commands.add_parser("save", help="Retry saving an existing JSON result; no investigation or model call")
    save.add_argument("--file", type=Path, required=True)
    args = parser.parse_args()
    try:
        with IncidentStore(args.db) as store:
            if args.command == "list":
                result = store.list_incidents(args.project)
            elif args.command == "incident":
                result = store.get_incident(args.project, args.incident_id)
            elif args.command == "run":
                result = store.get_run(args.project, args.run_id)
            elif args.command == "card":
                result = store.get_card(args.project, args.card_id)
            elif args.command == "review":
                changes = {key: getattr(args, key) for key in ("symptom", "finding", "next_action") if getattr(args, key) is not None}
                result = store.review_card(args.project, args.card_id, args.action, reviewer=args.reviewer, changes=changes, note=args.note)
            elif args.command == "search":
                signals = {"error_codes": args.error_code, "paths": args.path, "exceptions": args.exception, "stack_fingerprints": args.stack_fingerprint}
                result = store.search(args.project, args.query, signals=signals if any(signals.values()) else None)
            else:
                if args.file.stat().st_size > 1_000_000:
                    raise ValueError("Saved result JSON exceeds 1 MB")
                result = json.loads(args.file.read_text(encoding="utf-8"))
                if not isinstance(result, dict) or result.get("project_id") != args.project:
                    raise ValueError("Saved result project scope mismatch")
                result = {"status": store.save_run(result), "run_id": result["run_id"]}
    except (OSError, ValueError, sqlite3.Error, TypeError, KeyError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
