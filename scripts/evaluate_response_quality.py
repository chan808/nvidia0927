"""Offline paired answer-quality check for the frozen synthetic incident suite."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from tracebridge.evaluation import DEFAULT_SUITE, evaluate_suite


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--output", type=Path, default=Path("output/knowledge-review/evaluations"))
    parser.add_argument("--mode", choices=("local", "doubles"), default="doubles")
    args = parser.parse_args(argv)
    summary = evaluate_suite(suite_path=args.suite, output_root=args.output,
        conditions=["tracebridge_memory_off", "tracebridge_memory_on"], mode=args.mode)
    quality = summary["response_quality"]
    destination = Path(summary["output_dir"]) / "response-quality.json"
    destination.write_text(json.dumps({"suite_sha256": summary["suite_sha256"], "mode": args.mode,
        "external_calls_performed_by_runner": summary["external_calls_performed_by_runner"],
        "code_changed_during_run": summary["code_changed_during_run"], **quality},
        ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({"report": str(destination), "mode": args.mode, "paired_cases": quality["paired_cases"],
        "memory_effect": quality["memory_effect"], "better_on_fixed_checks": quality["better_on_fixed_checks"],
        "worse_on_fixed_checks": quality["worse_on_fixed_checks"],
        "off_passed": quality["totals"]["tracebridge_memory_off"]["passed"],
        "on_passed": quality["totals"]["tracebridge_memory_on"]["passed"],
        "external_calls": summary["external_calls_performed_by_runner"]}, ensure_ascii=False, indent=2))
    expected = summary["cases"]
    return int(summary["code_changed_during_run"] or quality["paired_cases"] != expected
        or any(quality["totals"][name]["passed"] != expected for name in quality["totals"]))


if __name__ == "__main__":
    raise SystemExit(main())
