"""Triage a report against an explicit, local event catalog."""

import argparse
import json
from pathlib import Path
import sys

from dotenv import load_dotenv

from tracebridge.evidence import EvidenceError
from tracebridge.report_intake import LocalEventCatalog, triage_report
from tracebridge.incident_memory import current_signals, persist_result, recheck_memory, search_memory


def main() -> None:
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", required=True, help="Local JSON catalog of observed events")
    parser.add_argument("--report", required=True, help="Natural-language bug report")
    parser.add_argument("--trace-id", help="Request/trace ID, if captured separately")
    parser.add_argument("--environment", help="Environment from the report or client capture")
    parser.add_argument("--occurred-at", help="ISO 8601 time with timezone")
    parser.add_argument("--method", help="HTTP method from client capture")
    parser.add_argument("--path", help="API path from client capture")
    parser.add_argument("--service", help="Service from client capture")
    parser.add_argument("--operation", help="Known screen action; never exact identity by itself")
    parser.add_argument("--db", help="Local SQLite records; defaults to TRACEBRIDGE_DB_PATH or workspace output/tracebridge/incidents.sqlite3")
    args = parser.parse_args()
    try:
        catalog = LocalEventCatalog.from_file(args.events)
        result = triage_report(
            args.report,
            catalog,
            trace_id=args.trace_id,
            environment=args.environment,
            occurred_at=args.occurred_at,
            method=args.method,
            path=args.path,
            service=args.service,
            operation=args.operation,
        )
        search = search_memory(catalog.project_id, args.report, signals=current_signals(result), exclude_incident_id=result["incident_id"], db_path=args.db)
        result["memory_search"] = recheck_memory(search, result)
        persist_result(result, args.db)
    except (EvidenceError, ValueError, OSError) as exc:
        parser.error(str(exc))
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
