"""Run one synthetic incident from the terminal."""

import argparse
import sys

from tracebridge.agent import run_live, run_offline
from tracebridge.evidence import EvidenceError, LocalBundleEvidenceSource
from tracebridge.fixtures import get_case
from tracebridge.repro import demonstrate_red_green

sys.stdout.reconfigure(encoding="utf-8")


parser = argparse.ArgumentParser()
parser.add_argument("trace_id", nargs="?", choices=["contract-001", "migration-002", "claim-003", "unknown"])
parser.add_argument("--bundle", help="Local JSON incident bundle; always analyzed offline")
parser.add_argument("--claim", help="Optional report text for this incident")
parser.add_argument("--without-report", action="store_true", help="Investigate without a user report")
parser.add_argument("--offline", action="store_true")
parser.add_argument("--repro", action="store_true")
args = parser.parse_args()

if args.bundle and args.trace_id:
    parser.error("Choose a fixture trace ID or --bundle, not both")
if not args.bundle and not args.trace_id:
    parser.error("A fixture trace ID or --bundle is required")
if args.without_report and args.claim is not None:
    parser.error("--without-report and --claim cannot be used together")

source = None
if args.bundle:
    try:
        source = LocalBundleEvidenceSource.from_file(args.bundle)
    except (EvidenceError, OSError) as exc:
        parser.error(str(exc))
    trace_id = source.trace_id
    default_claim = None
else:
    trace_id = args.trace_id
    case = get_case(trace_id)
    default_claim = case["claim"] if case else "회원가입 API에서 500이 납니다"

claim = None if args.without_report else (args.claim if args.claim is not None else default_claim)
result = run_offline(trace_id, claim, source=source) if args.offline or source else run_live(trace_id, claim)
verdict = result["verdict"]
print(f"mode={result['mode']} model={result.get('model', 'none')}")
print(f"claim={verdict['claim_status']} finding={verdict['finding_status']} diagnosis={verdict['diagnosis_type']}")
print(f"claim_items={[(item['facet'], item['status']) for item in verdict['claim_items']]}")
for item in verdict["evidence"]:
    print(f"evidence={item['source']}: {item['fact']}")
print(f"tools={[step['tool'] for step in result['steps']]}")
print(f"usage={result['usage']} elapsed_ms={result['elapsed_ms']}")
print(f"agent_message={result['agent_message'][:500]}")
if args.repro and verdict["repro_eligible"]:
    repro = demonstrate_red_green(verdict["diagnosis_type"])
    print(f"repro_before={repro['before']['exit_code']} after_candidate_fix={repro['after_candidate_fix']['exit_code']}")
    print(f"test_path={repro['test_path']}")
