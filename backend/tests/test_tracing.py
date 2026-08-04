"""Tracing must be invisible when off, and harmless when it breaks.

Observability is not worth an outage, so the properties worth pinning are not
"a run appears in LangSmith" -- that needs the network and a real project -- but
the three that decide whether this layer can hurt the product:

  * with no key it does nothing at all, so tests and local development never
    post to a shared project and nobody has to remember to switch it off;
  * candidate answers and job descriptions do not leave the process by default;
  * an SDK failure degrades to an untraced call rather than a failed one.
"""
from __future__ import annotations

import pytest

from app.services import tracing


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("LANGSMITH_API_KEY", "LANGSMITH_TRACING", "LANGSMITH_TRACE_CONTENT"):
        monkeypatch.delenv(name, raising=False)


# ── Off by default ───────────────────────────────────────────────────────────

def test_disabled_without_a_key() -> None:
    assert tracing.is_enabled() is False


def test_enabled_with_a_key() -> None:
    import os

    os.environ["LANGSMITH_API_KEY"] = "lsv2_sk_test"
    try:
        assert tracing.is_enabled() is True
    finally:
        del os.environ["LANGSMITH_API_KEY"]


def test_an_explicit_off_switch_wins_over_a_present_key(monkeypatch) -> None:
    """So a key mounted on every service can still be silenced per environment
    without unmounting it."""
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_sk_test")
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    assert tracing.is_enabled() is False


def test_the_context_manager_is_a_no_op_when_disabled() -> None:
    """It must allocate nothing and make no network call -- this runs on every
    LLM call in the test suite."""
    with tracing.trace_llm("behavioral_assessment", messages=[{"content": "x"}]) as run:
        assert run is None


# ── Candidate content does not leave the process by default ──────────────────

def test_content_is_not_traced_by_default(monkeypatch) -> None:
    """A prompt here carries a real candidate's answers and a real job
    description. Sending that to a third party is a decision for whoever owns
    the data, not a default this module makes quietly."""
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_sk_test")
    assert tracing.trace_content_enabled() is False


def test_content_can_be_opted_into(monkeypatch) -> None:
    monkeypatch.setenv("LANGSMITH_TRACE_CONTENT", "true")
    assert tracing.trace_content_enabled() is True


# ── A broken SDK must not break the call ─────────────────────────────────────

def test_an_sdk_failure_degrades_to_an_untraced_call(monkeypatch) -> None:
    """Bad key, unreachable endpoint, unknown project. The call still happens."""
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_sk_test")

    import langsmith

    def _explode(*args, **kwargs):
        raise RuntimeError("langsmith is having a bad day")

    monkeypatch.setattr(langsmith, "trace", _explode)

    entered = False
    with tracing.trace_llm("report_synthesis") as run:
        entered = True
        assert run is None
    assert entered, "the caller's body did not run; a tracing failure broke the call"


def test_ending_a_run_never_raises(monkeypatch) -> None:
    class _Bad:
        def end(self, **kwargs):
            raise RuntimeError("cannot write run")

    handle = tracing._Handle(_Bad())
    handle.end(output="fine", provider="groq")  # must not raise


# ── The router still returns its result with tracing on ──────────────────────

@pytest.mark.asyncio
async def test_invoke_llm_returns_its_result_through_the_tracing_wrapper(
    monkeypatch,
) -> None:
    """`invoke_llm` is now a wrapper around `_invoke_llm_inner`. A wrapper that
    forgot to return would break every LLM call in the product, and no unit
    test of the router internals would notice."""
    from app.services import llm_router

    async def _inner(*args, **kwargs):
        return "the answer"

    monkeypatch.setattr(llm_router, "_invoke_llm_inner", _inner)
    result = await llm_router.invoke_llm("behavioral_assessment", [{"role": "user", "content": "q"}])
    assert result == "the answer"


@pytest.mark.asyncio
async def test_invoke_llm_propagates_failures_through_the_wrapper(monkeypatch) -> None:
    """The caller's own fallback depends on this raising, not swallowing."""
    from app.services import llm_router

    async def _inner(*args, **kwargs):
        raise llm_router.LLMUnavailableError("all providers down")

    monkeypatch.setattr(llm_router, "_invoke_llm_inner", _inner)
    with pytest.raises(llm_router.LLMUnavailableError):
        await llm_router.invoke_llm("report_synthesis", [{"role": "user", "content": "q"}])
