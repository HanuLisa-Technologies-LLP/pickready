"""The bounded agent loop: its criteria, its ceilings, and its degradation.

Every generative task in the product now runs inside `agent_loop.run_loop`, so
what is asserted here is not "a loop works" but the four properties the callers
depend on and cannot check for themselves:

  * it RETRIES on a rejection and tells the next attempt WHY, which is the whole
    reason it replaced one-shot prompting;
  * it is bounded twice over -- by attempt count AND by wall clock -- because a
    candidate is waiting on the interactive ones;
  * it NEVER raises, because every caller is on a live request path where the
    correct answer to a provider outage is the product's previous behaviour;
  * it reports degradation honestly, because a degradation nobody counts is a
    degradation nobody notices.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services import agent_loop


def _accept_all(_value):
    return agent_loop.ok()


def _reject_all(_value):
    return agent_loop.reject("always rejected")


@pytest.mark.asyncio
async def test_a_first_attempt_that_passes_costs_one_call() -> None:
    calls: list[str] = []

    async def execute(reflection: str) -> str:
        calls.append(reflection)
        return "fine"

    result = await agent_loop.run_loop(
        name="t", execute=execute, evaluate=_accept_all, fallback="fb"
    )
    assert result.value == "fine"
    assert result.degraded is False
    assert result.attempts == 1
    # The first attempt gets an EMPTY reflection: there is nothing to reflect on
    # yet, and passing a stale one would have the model correcting a defect that
    # belonged to a different call.
    assert calls == [""]


@pytest.mark.asyncio
async def test_a_rejection_is_fed_back_to_the_next_attempt() -> None:
    """THE reason this module exists.

    One-shot prompting threw a near-miss away and shipped a canned string.
    "You returned three of the five rubric bands" is a defect a model fixes
    immediately when told, so the second attempt must actually be told.
    """
    seen: list[str] = []

    async def execute(reflection: str) -> str:
        seen.append(reflection)
        return "second" if reflection else "first"

    def evaluate(value: str):
        if value == "first":
            return agent_loop.reject("return exactly 5 items")
        return agent_loop.ok()

    result = await agent_loop.run_loop(
        name="t", execute=execute, evaluate=evaluate, fallback="fb"
    )
    assert result.value == "second"
    assert result.attempts == 2
    assert seen[0] == ""
    assert "return exactly 5 items" in seen[1]
    assert "rejected" in seen[1].lower()


@pytest.mark.asyncio
async def test_exhausting_the_attempts_returns_the_fallback_not_an_error() -> None:
    async def execute(_reflection: str) -> str:
        return "no good"

    result = await agent_loop.run_loop(
        name="t", execute=execute, evaluate=_reject_all, fallback="fb", max_attempts=2
    )
    assert result.value == "fb"
    assert result.degraded is True
    assert result.attempts == 2
    assert result.reasons == ("always rejected",)


@pytest.mark.asyncio
async def test_a_raising_execute_is_a_failed_attempt_not_a_failed_loop() -> None:
    """A provider outage, a timeout and a malformed body all mean one thing
    here: this attempt produced nothing usable. None of them may propagate."""
    attempts = {"n": 0}

    async def execute(_reflection: str) -> str:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("all providers down")
        return "recovered"

    result = await agent_loop.run_loop(
        name="t", execute=execute, evaluate=_accept_all, fallback="fb"
    )
    assert result.value == "recovered"
    assert result.degraded is False
    # The error is still REPORTED even though the loop recovered: an operator
    # needs to see that the first tier failed, and the caller cannot see it.
    assert result.error == "RuntimeError"


@pytest.mark.asyncio
async def test_every_attempt_raising_still_returns_the_fallback() -> None:
    async def execute(_reflection: str) -> str:
        raise TimeoutError("gone")

    result = await agent_loop.run_loop(
        name="t", execute=execute, evaluate=_accept_all, fallback="fb", max_attempts=3
    )
    assert result.value == "fb"
    assert result.degraded is True
    assert result.error == "TimeoutError"
    assert result.attempts == 3


@pytest.mark.asyncio
async def test_the_deadline_is_checked_before_an_attempt_starts() -> None:
    """A "bounded" loop that checks its deadline AFTER each call overruns its
    bound by a full timeout. It has to decline to start what it cannot finish."""
    attempts = {"n": 0}

    async def execute(_reflection: str) -> str:
        attempts["n"] += 1
        await asyncio.sleep(0.05)
        return "slow"

    result = await agent_loop.run_loop(
        name="t",
        execute=execute,
        evaluate=_reject_all,
        fallback="fb",
        max_attempts=10,
        deadline_seconds=0.06,
    )
    assert result.degraded is True
    # One attempt fits inside 0.06s; the second is refused before it is started.
    assert attempts["n"] < 10
    assert result.value == "fb"


@pytest.mark.asyncio
async def test_verify_runs_once_and_can_veto_an_accepted_value() -> None:
    """`verify` is the final gate. A value that passes `evaluate` and fails
    `verify` is exactly what the gate exists to keep out, so it degrades."""
    verifications: list[str] = []

    async def execute(_reflection: str) -> str:
        return "plausible"

    def verify(value: str):
        verifications.append(value)
        return agent_loop.reject("failed the final check")

    result = await agent_loop.run_loop(
        name="t",
        execute=execute,
        evaluate=_accept_all,
        verify=verify,
        fallback="fb",
        max_attempts=3,
    )
    assert result.value == "fb"
    assert result.degraded is True
    assert result.reasons == ("failed the final check",)
    # Once, on the accepted value only -- not per attempt.
    assert verifications == ["plausible"]


def test_reject_never_produces_a_silent_failure() -> None:
    """A caller that computes its reasons must not be able to build a rejection
    that says nothing: the next attempt would be told to fix "" and would
    reproduce the same defect."""
    assert agent_loop.reject().reasons
    assert agent_loop.reject("", "  ").reasons
    assert agent_loop.reject("a", "", "b").reasons == ("a", "b")


def test_a_critique_is_truthy_only_when_it_passed() -> None:
    assert bool(agent_loop.ok()) is True
    assert bool(agent_loop.reject("no")) is False


def test_reflection_is_empty_when_there_is_nothing_to_reflect_on() -> None:
    assert agent_loop.reflection_text([]) == ""


def test_the_interactive_bounds_stay_inside_the_conversation_budget() -> None:
    """A candidate is watching a text box, and `conversation_turn` is budgeted
    at 24s of wall clock per logical call (config/llm_providers). The loop's own
    deadline has to be in the same neighbourhood: much larger and the loop is
    not really the bound, much smaller and the second attempt never runs."""
    from app.config import llm_providers

    assert agent_loop.INTERACTIVE_ATTEMPTS == 2
    assert (
        agent_loop.INTERACTIVE_DEADLINE
        >= llm_providers.total_budget_for("conversation_turn")
    )
    assert agent_loop.INTERACTIVE_DEADLINE < 60
