"""Register read-only TraceBridge evidence tools with NeMo Agent Toolkit."""

from __future__ import annotations

import json
import os
from pathlib import Path

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig

from .evidence import get_backend_evidence, get_contract, get_migration_state, get_trace


def _bounded_tool(name: str, trace_id: str, provider) -> str:
    allowed = os.getenv("TRACEBRIDGE_ALLOWED_TRACE_ID")
    if allowed and trace_id != allowed:
        raise ValueError("Tool scope cannot move to a different trace ID")
    result = provider(trace_id)
    run_dir = Path(__file__).resolve().parents[1] / "generated"
    run_dir.mkdir(exist_ok=True)
    with (run_dir / "nat_tool_calls.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"tool": name, "trace_id": trace_id}, ensure_ascii=False) + "\n")
    return json.dumps(result, ensure_ascii=False)


class TraceConfig(FunctionBaseConfig, name="tracebridge_trace"):
    pass


class ContractConfig(FunctionBaseConfig, name="tracebridge_contract"):
    pass


class BackendConfig(FunctionBaseConfig, name="tracebridge_backend"):
    pass


class MigrationConfig(FunctionBaseConfig, name="tracebridge_migration"):
    pass


@register_function(config_type=TraceConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def register_trace(_config: TraceConfig, _builder: Builder):
    async def _inner(trace_id: str) -> str:
        """Return only the observed request and response for one trace ID."""
        return _bounded_tool("trace", trace_id, get_trace)

    yield FunctionInfo.from_fn(_inner, description="Read the actual request/response for one trace_id. Use first to verify the report.")


@register_function(config_type=ContractConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def register_contract(_config: ContractConfig, _builder: Builder):
    async def _inner(trace_id: str) -> str:
        """Return the OpenAPI fields and backend DTO for one request."""
        return _bounded_tool("contract", trace_id, get_contract)

    yield FunctionInfo.from_fn(_inner, description="Compare one request's fields with its OpenAPI contract and backend DTO.")


@register_function(config_type=BackendConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def register_backend(_config: BackendConfig, _builder: Builder):
    async def _inner(trace_id: str) -> str:
        """Return bounded backend error evidence for one trace ID."""
        return _bounded_tool("backend", trace_id, get_backend_evidence)

    yield FunctionInfo.from_fn(_inner, description="Read backend errors for this trace only; use for unexplained 5xx responses.")


@register_function(config_type=MigrationConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def register_migration(_config: MigrationConfig, _builder: Builder):
    async def _inner(trace_id: str) -> str:
        """Return applied and expected migration versions for the trace environment."""
        return _bounded_tool("migration", trace_id, get_migration_state)

    yield FunctionInfo.from_fn(_inner, description="Check read-only database migration state after evidence of a schema mismatch.")
