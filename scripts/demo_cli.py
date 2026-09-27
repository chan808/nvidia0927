"""Run one synthetic incident from the terminal."""

import argparse
import sys

from tracebridge.agent import run_live, run_offline
from tracebridge.fixtures import get_case
from tracebridge.repro import demonstrate_red_green

sys.stdout.reconfigure(encoding="utf-8")


parser = argparse.ArgumentParser()
parser.add_argument("trace_id", choices=["contract-001", "migration-002", "claim-003", "unknown"])
parser.add_argument("--offline", action="store_true")
parser.add_argument("--repro", action="store_true")
args = parser.parse_args()

case = get_case(args.trace_id)
claim = case["claim"] if case else "회원가입 API에서 500이 납니다"
result = run_offline(args.trace_id, claim) if args.offline else run_live(args.trace_id, claim)
verdict = result["verdict"]
print(f"mode={result['mode']} model={result.get('model', 'none')}")
print(f"claim={verdict['claim_status']} finding={verdict['finding_status']} diagnosis={verdict['diagnosis_type']}")
print(f"tools={[step['tool'] for step in result['steps']]}")
print(f"usage={result['usage']} elapsed_ms={result['elapsed_ms']}")
print(f"agent_message={result['agent_message'][:500]}")
if args.repro and verdict["repro_eligible"]:
    repro = demonstrate_red_green(verdict["diagnosis_type"])
    print(f"repro_before={repro['before']['exit_code']} after_candidate_fix={repro['after_candidate_fix']['exit_code']}")
    print(f"test_path={repro['test_path']}")
