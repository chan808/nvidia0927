"""Read-only Agolive report investigation from the terminal."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from tracebridge.project_investigation import investigate_agolive_report
from tracebridge.project_sources import agolive_repo_path


sys.stdout.reconfigure(encoding="utf-8")

parser = argparse.ArgumentParser()
parser.add_argument("--report", required=True, help="Natural-language bug report")
parser.add_argument("--repo", type=Path, default=agolive_repo_path(), help="Agolive checkout path")
parser.add_argument("--logs-file", type=Path, help="Optional local log excerpt; stays on this machine")
parser.add_argument("--docker", action="store_true", help="Read local Docker Compose api/realtime logs")
parser.add_argument("--since-minutes", type=int, default=30)
parser.add_argument("--gpt", action="store_true", help="Send selected redacted report/code/log evidence to OpenAI")
parser.add_argument("--json", action="store_true", help="Print structured result")
args = parser.parse_args()

log_text = ""
if args.logs_file:
    try:
        with args.logs_file.open("rb") as stream:
            data = stream.read(300_001)
    except OSError as exc:
        parser.error(f"Cannot read logs file: {exc}")
    if len(data) > 300_000:
        parser.error("Logs file exceeds 300 KB")
    log_text = data.decode("utf-8", errors="replace")

try:
    result = investigate_agolive_report(
        args.report,
        repo=args.repo,
        provided_logs=log_text,
        include_docker_logs=args.docker,
        since_minutes=args.since_minutes,
        use_gpt=args.gpt,
    )
except (OSError, ValueError) as exc:
    parser.error(str(exc))

if args.json:
    print(json.dumps(result, ensure_ascii=False, indent=2))
else:
    print(f"project={result['project']} revision={result['repository_revision']} deployed_revision=unknown")
    print(f"gpt_used={result['gpt_used']} model={result['gpt_model'] or 'none'}")
    for note in result["notes"]:
        print(f"note={note}")
    for item in result["hypotheses"]:
        print(f"hypothesis={item['status']} {item['cause']} evidence={item['supporting_evidence_ids']}")
        print(f"  verify={item['verification_step']}")
        print(f"  possible_fix={item['possible_fix']} (not applied)")
    for item in result["evidence"]:
        print(f"evidence={item['id']} {item['source']} {item['content']}")
    for item in result["missing_information"]:
        print(f"missing={item}")
