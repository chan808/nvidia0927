"""Run the same demo through NVIDIA NeMo Agent Toolkit's tool-calling agent."""

import argparse
import asyncio
import os
from pathlib import Path
import sys

from dotenv import load_dotenv
from nat.utils import run_workflow
import yaml
import tracebridge.nat_plugin  # noqa: F401 - register local tools before workflow load


load_dotenv()
sys.stdout.reconfigure(encoding="utf-8")
if not os.getenv("NVIDIA_API_KEY"):
    raise SystemExit("NVIDIA_API_KEY is missing from .env")
os.environ["OPENAI_API_KEY"] = os.environ["NVIDIA_API_KEY"]  # OpenAI-compatible NAT client, NVIDIA endpoint
parser = argparse.ArgumentParser()
parser.add_argument("trace_id", choices=["contract-001", "migration-002", "claim-003"])
args = parser.parse_args()
os.environ["TRACEBRIDGE_ALLOWED_TRACE_ID"] = args.trace_id
config = Path(__file__).resolve().parents[1] / "nat_workflow.yml"
skill = config.parent / "skills" / "tracebridge-triage" / "SKILL.md"
runtime_config = config.parent / "generated" / "nat_workflow_runtime.yml"
runtime_config.parent.mkdir(exist_ok=True)
settings = yaml.safe_load(config.read_text(encoding="utf-8"))
settings["workflow"]["system_prompt"] += "\nProject skill:\n" + skill.read_text(encoding="utf-8")
runtime_config.write_text(yaml.safe_dump(settings, allow_unicode=True, sort_keys=False), encoding="utf-8")
tool_log = config.parent / "generated" / "nat_tool_calls.jsonl"
if tool_log.exists():
    tool_log.unlink()
prompt = f"Investigate report '회원가입 API에서 500이 납니다' for trace_id={args.trace_id}. Verify the claim and cite tool evidence."
try:
    result = asyncio.run(asyncio.wait_for(run_workflow(config_file=str(runtime_config), prompt=prompt), timeout=150))
    print(result)
except Exception as exc:
    print(f"Toolkit workflow failed: {type(exc).__name__}: {exc}")
    raise SystemExit(1) from None
if tool_log.exists():
    print(f"\nTool calls: {tool_log.read_text(encoding='utf-8').strip()}")
