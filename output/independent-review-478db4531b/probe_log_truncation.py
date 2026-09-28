"""Independent, offline reproduction of lost counter-evidence in bounded log reads.

Run from the project with its existing virtual environment. The script creates
only a new audit folder; it does not change application sources or call models.
Use --source-root to compare a captured snapshot or a subsequently fixed tree.
"""

from argparse import ArgumentParser
from copy import deepcopy
import json
from pathlib import Path
import sys
from uuid import uuid4


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    root = args.source_root.resolve(strict=True)
    sys.path.insert(0, str(root))
    from tracebridge.report_agent import investigate_submission
    from tracebridge.report_contract import ReportContext
    from tracebridge.report_intake import LocalEventCatalog

    sys.stdout.reconfigure(encoding="utf-8")
    work = Path(__file__).resolve().parent / ("truncation-replay-" + uuid4().hex[:10])
    work.mkdir()
    record = json.loads((root / "examples/scoped_agolive.log").read_text(encoding="utf-8").splitlines()[0])
    record["trace"]["project_id"] = "agolive"
    counter = deepcopy(record)
    counter["trace"]["response_status"] = 500
    counter.update(level="ERROR", message="current contradictory 500")
    event = {"trace": record["trace"], "contract": record["contract"], "dto": record["dto"], "logs": ["validation snapshot"]}
    catalog = LocalEventCatalog({"project_id": "agolive", "events": [event]})
    context = ReportContext(environment="dev", service="backend", occurred_at=record["trace"]["occurred_at"])
    first = json.dumps(counter, ensure_ascii=False) + "\n"
    last = json.dumps(record, ensure_ascii=False) + "\n"
    results = []
    for name, padding in (
        ("complete_short_log", ""),
        ("byte_truncated_log", "unrelated line\n" * 30000),
        ("line_truncated_log", "x\n" * 6000),
    ):
        log = work / (name + ".log")
        log.write_text(first + padding + last, encoding="utf-8")
        result = investigate_submission(
            "500 requestId=intake-guidance",
            repo=root / "tests/fixtures/agolive_repo",
            catalog=catalog,
            context=context,
            log_file=log,
            registered_log_scope={"environment": "dev", "service": "backend", "timezone": "+09:00"},
            db_path=work / (name + ".sqlite3"),
        )
        results.append({
            "case": name,
            "bytes": log.stat().st_size,
            "route": result["route"],
            "observed_status": result["observed_status"],
            "claim_status": result["claim_status"],
            "run_status": result["run_status"],
            "model_calls": result["model_calls"],
            "aggregate": result["log_scope"]["aggregate"],
            "sources": result["log_scope"]["sources"],
            "notes": result["notes"],
        })
    destination = work / "results.json"
    destination.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"results_file": str(destination), "results": results}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
