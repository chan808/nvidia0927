"""Isolated NAT fixture check. External workflow calls require explicit --live."""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from tracebridge.evidence import FIXTURE_SOURCE
from tracebridge.nat_observability import InvestigationObserver
from tracebridge.triage import analyze


def run_main_report(args) -> int:
    """Use NAT's actual workflow runner around the same public report function."""
    import yaml
    from nat.utils import run_workflow
    import tracebridge.nat_plugin  # noqa: F401 - register the direct main wrapper

    run_dir = Path(args.output) / "main" / uuid4().hex
    run_dir.mkdir(parents=True, exist_ok=False)
    workflow = {"_type": "tracebridge_main_investigation", "output_dir": str((run_dir / "events").resolve()),
                "memory_enabled": not args.memory_off, "use_nvidia": args.live}
    if args.repo:
        workflow["repo_path"] = str(Path(args.repo).resolve())
    if args.project_profile:
        workflow["profile_path"] = str(Path(args.project_profile).resolve())
    if not args.repo and not args.project_profile:
        raise ValueError("Main NAT execution needs an explicitly selected repo or project profile")
    config = run_dir / "main-workflow.yml"
    config.write_text(yaml.safe_dump({"workflow": workflow}, allow_unicode=True, sort_keys=False), encoding="utf-8")
    try:
        raw = asyncio.run(asyncio.wait_for(run_workflow(config_file=str(config), prompt=args.main_report), timeout=150))
        result = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as exc:
        result = {"run_status": "FAILED", "error_type": type(exc).__name__, "main_flow_verified": False}
    output = {"artifact_status": "DRAFT", "external_model_mode": args.live, "result": result,
              "main_flow_verified": False, "config_ref": config.name,
              "limits": "Local read-only main workflow tracing; live model/OCR and frozen integration gate remain separate"}
    (run_dir / "result.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"artifact_status": "DRAFT", "output_dir": str(run_dir), "run_status": result.get("run_status"),
                      "observability": result.get("observability"), "main_flow_verified": False}, ensure_ascii=False))
    return 1 if result.get("run_status") in {"FAILED", "PARTIAL_FAILURE", "TIMED_OUT", "BUDGET_EXHAUSTED"} else 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="DRAFT fixture check; main NAT integration is a separate gate")
    parser.add_argument("trace_id", nargs="?", choices=["contract-001", "migration-002", "claim-003"])
    parser.add_argument("--main-report", help="Run the public main investigation through the direct NAT wrapper")
    parser.add_argument("--repo", help="Explicit read-only code root for --main-report")
    parser.add_argument("--project-profile", help="Registered project settings for --main-report")
    parser.add_argument("--memory-off", action="store_true")
    parser.add_argument("--live", action="store_true", help="Explicit external NIM/NAT workflow; do not run before user resumes live validation")
    parser.add_argument("--output", default="output/parallel-d/nat")
    args = parser.parse_args(argv)
    if bool(args.trace_id) == bool(args.main_report):
        parser.error("Choose a fixture trace_id or --main-report")
    if args.main_report:
        if not args.repo and not args.project_profile:
            parser.error("--main-report needs --repo or --project-profile")
        return run_main_report(args)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:10]
    observer = InvestigationObserver(run_id, args.output, origin="nat_fixture")
    if not args.live:
        class Source:
            def __getattr__(self, name):
                provider = getattr(FIXTURE_SOURCE, name)
                def call(trace_id):
                    with observer.tool(name, phase="offline_fixture"):
                        return provider(trace_id)
                return call
        result = analyze(args.trace_id, "회원가입 API에서 500이 납니다", source=Source())
        summary = observer.finish("COMPLETED", stop_reason="offline_fixture_only",
                                  expected_tool_calls=len(observer.events), expected_model_calls=0)
        summary["workflow_execution"] = "NOT_ATTEMPTED"
    else:
        from dotenv import load_dotenv
        from nat.utils import run_workflow
        import yaml
        from tracebridge.nat_plugin import observed_fixture_run

        load_dotenv()
        key = os.getenv("NVIDIA_API_KEY")
        if not key:
            observer.finish("BLOCKED", stop_reason="model_key_missing")
            print("NVIDIA_API_KEY missing; external workflow not started")
            return 2
        config = Path(__file__).resolve().parents[1] / "nat_workflow.yml"
        settings = yaml.safe_load(config.read_text(encoding="utf-8"))
        settings["workflow"]["system_prompt"] += "\nProject skill:\n" + (config.parent / "skills/tracebridge-triage/SKILL.md").read_text(encoding="utf-8")
        runtime_config = observer.output_dir / "workflow.yml"
        runtime_config.write_text(yaml.safe_dump(settings, allow_unicode=True, sort_keys=False), encoding="utf-8")
        saved = {name: os.environ.get(name) for name in ("OPENAI_API_KEY", "TRACEBRIDGE_ALLOWED_TRACE_ID")}
        os.environ.update(OPENAI_API_KEY=key, TRACEBRIDGE_ALLOWED_TRACE_ID=args.trace_id)
        status = "COMPLETED"
        try:
            with observed_fixture_run(observer):
                result = asyncio.run(asyncio.wait_for(run_workflow(config_file=str(runtime_config),
                    prompt=f"Investigate synthetic report '회원가입 API에서 500이 납니다' for trace_id={args.trace_id}. Cite checked observations."), timeout=150))
        except Exception as exc:
            status = "FAILED"
            result = {"error_type": type(exc).__name__, "successful_workflow": False}
        finally:
            for name, value in saved.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        # Wrapper catches tools only. NAT-internal model calls/usage are unknown.
        summary = observer.finish(status, stop_reason="fixture_workflow_only", expected_model_calls=None)
        summary["workflow_execution"] = status
        summary["model_usage_coverage"] = "NOT_CAPTURED_NAT_INTERNAL"
    output = {"artifact_status": "DRAFT", "result": str(result) if args.live and not isinstance(result, dict) else result,
              "telemetry": summary, "main_flow_verified": False}
    (observer.output_dir / "result.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"artifact_status": "DRAFT", "output_dir": str(observer.output_dir),
                      "workflow_execution": summary["workflow_execution"], "main_flow_verified": False}, ensure_ascii=False))
    return 1 if summary["run_status"] == "FAILED" else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
