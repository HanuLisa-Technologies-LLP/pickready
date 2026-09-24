"""LangSmith is gone, and OpenTelemetry is the one tracer. This keeps it so.

Owner ruling, 2026-09-24: "Tracing: OpenTelemetry only. LangSmith is removed."
Two tracers around one call were two answers to "what happened on this model
call", and the second one could carry prompt text off the process behind a flag.
`services/tracing.py` and its test are deleted; `llm_router.invoke_llm` keeps
only `otel.genai_span`, and `agent_loop.run_loop` uses `otel.agent_loop_span`
where it used to open a LangSmith chain.

`langsmith` itself is NOT in `requirements.txt` and never was: it arrives
transitively through `langchain-core`, so there is no pin to remove, and what
this file forbids is any code of ours NAMING it.
"""
from __future__ import annotations

import importlib.util
import re

from tests.removal_sweep import BACKEND, REPO, sweep

PATTERN = re.compile(r"langsmith|trace_llm|trace_agent_loop", re.I)

#: Every exemption, each with its reason. Nothing is inferred.
EXEMPT = (
    # This file has to name what it forbids.
    BACKEND / "tests" / "test_langsmith_removed.py",
    # FICTIONAL CANDIDATE RESUMES that list LangSmith as a skill a person has.
    # That is resume content about a technology, not a dependency of ours, and
    # rewriting a candidate's skills list to satisfy a sweep would be editing
    # evidence.
    REPO / "scripts" / "demo-resumes",
)


def test_the_tracing_module_is_gone() -> None:
    assert importlib.util.find_spec("app.services.tracing") is None
    assert not (BACKEND / "app" / "services" / "tracing.py").exists()


def test_no_source_names_langsmith() -> None:
    hits = sweep(PATTERN, exempt=EXEMPT)
    assert not hits, hits


def test_the_router_and_the_loop_open_opentelemetry_spans() -> None:
    """Asserted over the source, so a future edit cannot quietly drop the one
    tracer left while this sweep stays green."""
    import inspect

    from app.services import agent_loop, llm_router

    assert "otel.genai_span(" in inspect.getsource(llm_router.invoke_llm)
    assert "otel.agent_loop_span(" in inspect.getsource(agent_loop.run_loop)


def test_the_sweep_is_not_vacuous() -> None:
    """A sweep that reads nothing passes for ever. It must see the router."""
    hits = sweep(re.compile(r"genai_span"), roots=(BACKEND / "app",))
    assert any("llm_router.py" in hit for hit in hits), hits
