"""Export reviewed incident cards with conditions, sources and separate verification states."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from dotenv import load_dotenv

from tracebridge.incident_memory import db_location, export_manual


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Explicit SQLite file to export")
    parser.add_argument("--project", required=True, help="Required project scope; not authentication")
    parser.add_argument("--card", action="append", help="Select a card ID; repeat to select more")
    parser.add_argument("--output", type=Path, help="Write Markdown here and print JSON metadata; otherwise print Markdown")
    args = parser.parse_args()
    try:
        if not db_location(args.db).is_file():
            raise ValueError("Export requires an existing incident DB")
        if args.output is not None and (args.output.suffix.lower() != ".md" or args.output.resolve() == db_location(args.db).resolve()):
            raise ValueError("Manual output must be a .md file separate from the DB")
        result = export_manual(args.project, db_path=args.db, card_ids=args.card)
        if args.output is None:
            print(result["markdown"], end="")
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(result["markdown"], encoding="utf-8")
            print(json.dumps({key: value for key, value in result.items() if key != "markdown"} | {"output": str(args.output)}, ensure_ascii=False, indent=2))
    except (OSError, ValueError, sqlite3.Error, TypeError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
