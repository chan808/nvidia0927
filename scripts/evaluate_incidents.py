"""Offline/double evaluation. --live is deliberately not supported."""

from __future__ import annotations

import argparse
import json

from tracebridge.evaluation import CONDITIONS, DEFAULT_SUITE, ImportedAdapter, evaluate_suite, load_suite


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="TraceBridge 12-case draft evaluation; no external model/OCR calls")
    parser.add_argument("--suite", default=str(DEFAULT_SUITE))
    parser.add_argument("--output", default="output/parallel-d/evaluations")
    parser.add_argument("--mode", choices=("local", "doubles"), default="local")
    parser.add_argument("--conditions", nargs="+", choices=list(CONDITIONS), default=list(CONDITIONS))
    parser.add_argument("--import-observations", help="Measured baseline records for this exact suite/resource contract")
    parser.add_argument("--initial-db", help="Read-only initial reviewed DB; each case receives a fresh SQLite backup")
    args = parser.parse_args(argv)
    adapters = {}
    if args.import_observations:
        imported = ImportedAdapter(args.import_observations, load_suite(args.suite)["fingerprint"])
        available = {name for name, _case in imported.rows}
        adapters = {name: imported for name in args.conditions if name in available}
    summary = evaluate_suite(suite_path=args.suite, output_root=args.output, conditions=args.conditions,
                             mode=args.mode, adapters=adapters, initial_db=args.initial_db)
    print(json.dumps({key: summary[key] for key in ("artifact_status", "output_dir", "cases", "result_rows",
        "code_changed_during_run", "performance_claim", "memory_effect", "external_calls_performed_by_runner")}, ensure_ascii=False, indent=2))
    groups = list(summary["groups"].values())
    if any(group["assessments"].get("FAIL") or group["executions"].get("FAILED") for group in groups):
        return 1
    return 2 if any(group["executions"].get("BLOCKED") for group in groups) else 0


if __name__ == "__main__":
    raise SystemExit(main())
