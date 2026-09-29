"""Initialize, inventory, migrate and reverse-export central TraceBridge storage."""
import argparse
import json
import os
from pathlib import Path
from dotenv import load_dotenv

from tracebridge.storage import open_control_store, configured_target
from tracebridge.storage.transfer import compare_records, export_sqlite, import_sqlite, inventory, postgres_records, read_sqlite

def main():
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    for name in ("inventory", "import-sqlite", "verify-sqlite"):
        action = commands.add_parser(name)
        action.add_argument("--control", type=Path, required=True)
        action.add_argument("--incidents", type=Path, required=True)
        if name == "import-sqlite":
            action.add_argument("--source-frozen", action="store_true", help="Source server/runner writers are stopped; active work resolved")
    export = commands.add_parser("export-sqlite")
    export.add_argument("--directory", type=Path, required=True)
    export.add_argument("--source-frozen", action="store_true")
    args = parser.parse_args()
    storage = None
    try:
        if args.command == "inventory":
            result = inventory(read_sqlite(args.control, args.incidents))
        else:
            target = configured_target()
            if not target:
                parser.error("Configure TRACEBRIDGE_DATABASE_URL without putting credentials on the command line")
            storage = open_control_store(target)
            if not storage.postgres:
                parser.error("PostgreSQL target required")
            if args.command == "init":
                result = {"status": "READY", "backend": "POSTGRESQL"}
            elif args.command == "import-sqlite":
                result = import_sqlite(storage, args.control, args.incidents, source_frozen=args.source_frozen)
            elif args.command == "verify-sqlite":
                result = compare_records(read_sqlite(args.control, args.incidents), postgres_records(storage))
            else:
                result = export_sqlite(storage, args.directory, source_frozen=args.source_frozen)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("status") == "MISMATCH":
            raise SystemExit(1)
    except (KeyboardInterrupt, SystemExit):
        raise
    except ValueError as exc:
        parser.error(str(exc))
    except Exception as exc:
        parser.error(type(exc).__name__)
    finally:
        if storage is not None:
            storage.close()

if __name__ == "__main__":
    main()
