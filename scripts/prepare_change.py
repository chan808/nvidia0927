"""Internal A2 seed intake, bounded preparation, result lookup and save-only retry."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from tracebridge.change_policy import WORKSPACE, safe_path
from tracebridge.change_worker import DEFAULT_POLICY, _database, prepare_change, persist_change_result, seed_incident
from tracebridge.incident_memory import IncidentStore


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, help="Workspace-local DB; default output/changes/seed-incidents.sqlite3")
    parser.add_argument("--project", default="tracebridge-seed-signup", help="Internal project scope; not public authentication")
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("seed", help="Intake the explicitly labeled controlled seed report")
    seed.add_argument("--report", default="씨드 개발 회원가입이 실패해요. requestId=seed-signup-001")
    prepare = commands.add_parser("prepare", help="Prepare a candidate from a stored observed source run")
    prepare.add_argument("--source-run", required=True)
    prepare.add_argument("--policy", default=DEFAULT_POLICY, help="Owner-registered immutable policy ID")
    prepare.add_argument("--live", action="store_true", help="Send registered synthetic code/failure to the existing Nemotron connection")
    commands.add_parser("show").add_argument("work_id")
    commands.add_parser("list").add_argument("--incident")
    save = commands.add_parser("save", help="Save an obtained result only; never rerun checks or models")
    save.add_argument("--file", type=Path, required=True)
    args = parser.parse_args()
    try:
        database = _database(WORKSPACE, args.db)
        if args.command == "seed":
            if args.project != "tracebridge-seed-signup":
                raise ValueError("Seed intake is registered for tracebridge-seed-signup only")
            result = seed_incident(args.report, db_path=database)
        elif args.command == "prepare":
            result = prepare_change(args.project, args.source_run, policy_id=args.policy, db_path=database, live=args.live)
        elif args.command == "save":
            absolute = args.file.absolute()
            if not absolute.is_relative_to(WORKSPACE):
                raise ValueError("Obtained result must be inside the workspace")
            path = safe_path(WORKSPACE, absolute.relative_to(WORKSPACE).as_posix(), file=True)
            if path.stat().st_size > 1_000_000:
                raise ValueError("Change result exceeds 1 MB")
            obtained = json.loads(path.read_bytes())
            if obtained["job"]["project_id"] != args.project:
                raise ValueError("Obtained change result project scope mismatch")
            result = persist_change_result(obtained, database)
        else:
            with IncidentStore(database) as store:
                result = store.get_change(args.project, args.work_id) if args.command == "show" else store.list_changes(args.project, args.incident)
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
