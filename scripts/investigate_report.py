"""Investigate a natural-language or image-only report using local tools and optional NVIDIA services."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

from dotenv import load_dotenv

from tracebridge.report_agent import follow_up_submission, investigate_submission
from tracebridge.report_contract import ReportContext
from tracebridge.report_intake import LocalEventCatalog
from tracebridge.incident_memory import IncidentStore


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", default="", help="Rough symptom or natural-language instruction; optional with --image")
    parser.add_argument("--image", type=Path, help="PNG or JPEG screenshot; optional with --report")
    parser.add_argument("--repo", type=Path, help="Agolive checkout; defaults to TRACEBRIDGE_AGOLIVE_REPO")
    parser.add_argument("--logs-file", type=Path, help="Registered local log source, read only if selected by the agent")
    parser.add_argument("--events", type=Path, help="Explicit bounded event catalog; uses the same checked triage as the UI")
    parser.add_argument("--answer", action="append", default=[], help="Same-incident follow-up answer; may be repeated in this process")
    parser.add_argument("--db", type=Path, help="Local SQLite records; defaults to TRACEBRIDGE_DB_PATH or workspace output/tracebridge/incidents.sqlite3")
    parser.add_argument("--resume", help="Resume a stored incident with --answer and the same current source settings")
    parser.add_argument("--project", help="Stored project scope for --resume; defaults to the selected catalog or agolive")
    parser.add_argument("--environment", help="Optional captured environment")
    parser.add_argument("--service", help="Optional captured service")
    parser.add_argument("--occurred-at", help="Optional ISO 8601 time including timezone")
    parser.add_argument("--trace-id", help="Optional captured request identifier")
    parser.add_argument("--operation", help="Optional known screen action")
    parser.add_argument("--since-minutes", type=int, default=30, help="Recent Docker lookup window (1..120 minutes)")
    parser.add_argument("--max-seconds", type=float, default=90, help="Bounded investigation time (1..180 seconds)")
    parser.add_argument("--docker-logs", action="store_true", help="Allow selected local Docker log collection")
    parser.add_argument("--live", action="store_true", help="Send the image and selected redacted text/code/logs to NVIDIA services")
    parser.add_argument("--output", type=Path, help="Write the redacted investigation record as JSON")
    args = parser.parse_args()
    try:
        image = None
        if args.image:
            if args.image.stat().st_size > 8_000_000:
                parser.error("Image exceeds 8 MB limit")
            image = args.image.read_bytes()
        catalog = LocalEventCatalog.from_file(args.events) if args.events else None
        context = ReportContext(environment=args.environment, service=args.service, occurred_at=args.occurred_at, trace_id=args.trace_id, operation=args.operation)
        options = dict(repo=args.repo, log_file=args.logs_file, catalog=catalog,
            include_docker_logs=args.docker_logs, use_nvidia=args.live,
            since_minutes=args.since_minutes, max_seconds=args.max_seconds, db_path=args.db)
        if args.resume:
            if not args.answer or args.report or image:
                parser.error("--resume requires --answer; use --report/--image for a new incident")
            with IncidentStore(args.db) as store:
                result = store.resume_result(args.project or (catalog.project_id if catalog else "agolive"), args.resume)
        else:
            result = investigate_submission(args.report, image=image, context=context, **options)
        for answer in args.answer:
            result = follow_up_submission(result, answer, context=context, **options)
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        parser.error(str(exc))
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized, encoding="utf-8")
    print(serialized)


if __name__ == "__main__":
    main()
