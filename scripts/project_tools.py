"""Register real repositories, inspect readiness and prepare saved bug candidates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from tracebridge.incident_memory import IncidentStore
from tracebridge.project_health import inspect_project
from tracebridge.project_profile import load_project_profile
from tracebridge.project_registry import registry_directory, save_profile
from tracebridge.project_repair import save_repair_policy, prepare_project_change, persist_project_change, apply_project_change, run_project_checks


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", required=True)
    parser.add_argument("--db", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    register = commands.add_parser("register")
    register.add_argument("--file", type=Path, required=True)
    commands.add_parser("doctor")
    check = commands.add_parser("check")
    check.add_argument("--check-id", action="append")
    check.add_argument("--policy")
    policy = commands.add_parser("policy")
    policy.add_argument("--file", type=Path, required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("--source-run", required=True)
    prepare.add_argument("--policy")
    prepare.add_argument("--live", action="store_true")
    show = commands.add_parser("show")
    show.add_argument("--work-id", required=True)
    save = commands.add_parser("save")
    save.add_argument("--file", required=True, type=Path)
    apply = commands.add_parser("apply")
    apply.add_argument("--work-id", required=True)
    apply.add_argument("--diff-sha256", required=True, help="Hash of the reviewed and verified candidate diff")
    args = parser.parse_args()
    try:
        if args.command == "register":
            data = json.loads(args.file.read_bytes())
            if data.get("project_id") != args.project:
                raise ValueError("Project ID does not match registration")
            # Relative profile paths keep their source-file base, not the registry base.
            loaded = load_project_profile(args.file)
            from tracebridge.project_registry import profile_data
            result = {"profile_path": str(save_profile(profile_data(loaded)))}
        else:
            profile = load_project_profile(registry_directory() / (args.project + ".json"))
            if args.command == "doctor":
                result = inspect_project(profile)
            elif args.command == "check":
                result = run_project_checks(profile, policy_id=args.policy, check_ids=args.check_id)
            elif args.command == "policy":
                result = {"policy_path": str(save_repair_policy(json.loads(args.file.read_bytes()), profile))}
            elif args.command == "prepare":
                with IncidentStore(args.db) as store:
                    source = store.get_run(args.project, args.source_run)
                result = prepare_project_change(source, profile, policy_id=args.policy, db_path=args.db, live=args.live)
            elif args.command == "show":
                with IncidentStore(args.db) as store:
                    result = store.get_change(args.project, args.work_id)
            elif args.command == "apply":
                result = apply_project_change(args.project, args.work_id, profile, expected_diff_sha256=args.diff_sha256, db_path=args.db)
            else:
                obtained = json.loads(args.file.read_bytes())
                if obtained["job"]["project_id"] != args.project:
                    raise ValueError("Change result belongs to another project")
                result = persist_project_change(obtained, args.db)
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
