"""Register read-only TraceBridge evidence tools with NeMo Agent Toolkit."""

from __future__ import annotations

import json
import os
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
import asyncio
from uuid import uuid4

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig

from .evidence import get_backend_evidence, get_contract, get_migration_state, get_trace
from .nat_observability import InvestigationObserver, NATEventSink


_OBSERVER = ContextVar("tracebridge_nat_fixture_observer", default=None)


@contextmanager
def observed_fixture_run(observer: InvestigationObserver):
    """Bind an isolated fixture observer; never counts as main-flow instrumentation."""
    if observer.origin != "nat_fixture":
        raise ValueError("Fixture tools require nat_fixture event provenance")
    token = _OBSERVER.set(observer)
    try:
        yield
    finally:
        _OBSERVER.reset(token)


def _bounded_tool(name: str, trace_id: str, provider) -> str:
    allowed = os.getenv("TRACEBRIDGE_ALLOWED_TRACE_ID")
    if allowed and trace_id != allowed:
        raise ValueError("Tool scope cannot move to a different trace ID")
    observer = _OBSERVER.get()
    if observer is None:
        result = provider(trace_id)
    else:
        with observer.tool(name, phase="nat_fixture"):
            result = provider(trace_id)
    return json.dumps(result, ensure_ascii=False)


class TraceConfig(FunctionBaseConfig, name="tracebridge_trace"):
    pass


class ContractConfig(FunctionBaseConfig, name="tracebridge_contract"):
    pass


class BackendConfig(FunctionBaseConfig, name="tracebridge_backend"):
    pass


class MigrationConfig(FunctionBaseConfig, name="tracebridge_migration"):
    pass


class MainInvestigationConfig(FunctionBaseConfig, name="tracebridge_main_investigation"):
    """Optional direct wrapper for the same public main function; offline by default."""
    repo_path: str | None = None
    profile_path: str | None = None
    output_dir: str = "output/parallel-d/nat-main"
    memory_enabled: bool = True
    use_nvidia: bool = False


@register_function(config_type=MainInvestigationConfig)
async def register_main_investigation(config: MainInvestigationConfig, _builder: Builder):
    if not config.repo_path and not config.profile_path:
        raise ValueError("Direct main NAT workflow requires an explicit repo or project profile")
    async def _inner(report: str) -> str:
        """Run the current report agent with isolated DB and NAT metadata events."""
        from nat.builder.context import Context
        from .report_agent import investigate_submission

        observer = InvestigationObserver(uuid4().hex, config.output_dir, origin="main_investigation",
            nat_sink=NATEventSink(Context.get().intermediate_step_manager))
        result = await asyncio.to_thread(investigate_submission, report,
            repo=Path(config.repo_path) if config.repo_path else None,
            project_profile=config.profile_path, observer=observer,
            memory_enabled=config.memory_enabled, use_nvidia=config.use_nvidia,
            db_path=observer.output_dir / "memory.db")
        # This only enables an instrumentation path. Actual main flow verification
        # remains false until a frozen integration execution is reviewed.
        return json.dumps(result, ensure_ascii=False)

    yield FunctionInfo.from_fn(_inner, description="TraceBridge main investigation; offline unless configured otherwise, metadata-only NAT tracing")


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
