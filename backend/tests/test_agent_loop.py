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
async def test_a_slow_attempt_stops_the_loop_starting_another() -> None:
    """THE deadline has to PREDICT, not just observe.

    Checking `elapsed >= deadline` sounds right and is not. Measured on the real
    numbers: one `conversation_turn` call is bounded by the router at 24s and
    the interactive deadline is 26s, so after a slow first attempt elapsed is
    24, `24 >= 26` is False, attempt two starts, and the true worst case is 48
    seconds -- with a candidate watching a text box. The loop would have been
    advertising a bound it did not have.

    So the check is against elapsed PLUS what an attempt has actually been
    costing: an attempt that cannot finish inside the budget is never started.
    """
    attempts = {"n": 0}

    async def execute(_reflection: str) -> str:
        attempts["n"] += 1
        await asyncio.sleep(0.08)
        return "slow"

    result = await agent_loop.run_loop(
        name="t",
        execute=execute,
        evaluate=_reject_all,
        fallback="fb",
        max_attempts=10,
        # Two attempts would fit by elapsed time alone (0.08 < 0.12), and must
        # not: after one, 0.08 + 0.08 >= 0.12, so a second cannot FINISH.
        deadline_seconds=0.12,
    )
    assert result.degraded is True
    assert attempts["n"] == 1
    assert result.value == "fb"


@pytest.mark.asyncio
async def test_a_fast_attempt_still_permits_a_retry() -> None:
    """The predictive check must not become a ban on retrying at all -- the
    retry is the reason the loop exists. A 1ms attempt leaves room for another.
    """
    attempts = {"n": 0}

    async def execute(_reflection: str) -> str:
        attempts["n"] += 1
        return "second" if attempts["n"] > 1 else "first"

    def evaluate(value: str):
        return agent_loop.ok() if value == "second" else agent_loop.reject("again")

    result = await agent_loop.run_loop(
        name="t",
        execute=execute,
        evaluate=evaluate,
        fallback="fb",
        max_attempts=2,
        deadline_seconds=agent_loop.INTERACTIVE_DEADLINE,
    )
    assert result.value == "second"
    assert attempts["n"] == 2


@pytest.mark.asyncio
async def test_a_timed_out_attempt_counts_toward_the_estimate() -> None:
    """A timeout is the slowest and most informative thing that can happen.
    Ignoring its duration because it raised would let the loop keep starting
    attempts it has no time for."""
    attempts = {"n": 0}

    async def execute(_reflection: str) -> str:
        attempts["n"] += 1
        await asyncio.sleep(0.08)
        raise TimeoutError("provider timed out")

    result = await agent_loop.run_loop(
        name="t",
        execute=execute,
        evaluate=_accept_all,
        fallback="fb",
        max_attempts=10,
        deadline_seconds=0.12,
    )
    assert result.degraded is True
    assert attempts["n"] == 1
    assert result.error == "TimeoutError"


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


def test_rejections_always_carry_typed_defects() -> None:
    critique = agent_loop.reject("name the missing evidence")
    assert critique.defects == (
        agent_loop.Defect("quality", "output", "name the missing evidence"),
    )
    assert critique.reasons == ("name the missing evidence",)


@pytest.mark.asyncio
async def test_generated_token_budget_is_a_hard_loop_ceiling() -> None:
    calls = {"n": 0}

    async def execute(_reflection: str) -> str:
        calls["n"] += 1
        return "x" * 400

    result = await agent_loop.run_loop(
        name="token-bound",
        execute=execute,
        evaluate=_reject_all,
        fallback="fb",
        max_attempts=10,
        max_generated_tokens=50,
    )

    assert calls["n"] == 1
    assert result.degraded is True
    assert result.generated_tokens > 50
    assert result.defects[0].type == "budget"


def test_banned_phrase_gate_catches_punctuation_and_close_variants() -> None:
    exact = agent_loop.banned_phrase_gate(
        "The account is credible, but not exhaustive.",
        ["credible but not exhaustive"],
    )
    close = agent_loop.banned_phrase_gate(
        "The account remains credible yet not exhaustive.",
        ["credible but not exhaustive"],
        close_variant_threshold=0.72,
    )

    assert exact.defects[0].type == "banned_phrase"
    assert close.defects[0].type == "banned_phrase"


def test_a_short_banned_phrase_does_not_match_a_single_ordinary_word() -> None:
    """The window may be narrower than the phrase, and that needs a floor.

    Without one, a TWO-word banned phrase admits a ONE-word window, so "well
    rounded" matched the word "we" and "team player" matched the word "team".
    Almost every English sentence tripped the gate. Measured 2026-08-18 while
    building `services/verification/generic_language`, on exactly the first
    sentence below.
    """
    for text, phrase in (
        ("We would like to move ahead and will write again with the next step.", "well rounded"),
        ("The team shipped it.", "team player"),
        ("We ran the migration on Tuesday.", "well rounded"),
    ):
        assert agent_loop.banned_phrase_gate(text, [phrase]).ok, (text, phrase)


def test_a_short_banned_phrase_is_still_caught_when_actually_present() -> None:
    """The floor must not be a way to switch the gate off."""
    assert not agent_loop.banned_phrase_gate("they are a team player", ["team player"]).ok
    # Punctuation and casing still cannot evade it.
    assert not agent_loop.banned_phrase_gate("a Team  Player!", ["team player"]).ok


def test_a_partial_match_of_a_long_phrase_still_counts() -> None:
    """Three words dropped from a four-word phrase is a real near-miss.

    This is the behaviour the floor is calibrated to preserve: a partial match
    must be a PHRASE, not a word, and three words is where that line sits.
    """
    critique = agent_loop.banned_phrase_gate(
        "this usable evidence for it", ["produced usable evidence for"]
    )
    assert not critique.ok
    assert critique.defects[0].type == "banned_phrase"


def test_similarity_gate_names_the_conflicting_pair() -> None:
    critique = agent_loop.similarity_gate(
        [
            "The candidate tuned Kafka partitions after measuring consumer lag.",
            "The candidate tuned Kafka partitions after measuring the consumer lag.",
            "They redesigned a PostgreSQL index from an actual query plan.",
        ],
        maximum=0.85,
        location="narratives",
    )

    assert critique.ok is False
    assert critique.defects[0].type == "similarity"
    assert critique.defects[0].location == "narratives[0,1]"


@pytest.mark.asyncio
async def test_loop_trace_receives_gate_and_budget_metadata(monkeypatch) -> None:
    ended: list[dict] = []

    class Handle:
        def end(self, **payload):
            ended.append(payload)

    class Trace:
        def __enter__(self):
            return Handle()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        agent_loop.tracing,
        "trace_agent_loop",
        lambda *_args, **_kwargs: Trace(),
    )

    async def execute(_reflection: str) -> str:
        return "accepted"

    result = await agent_loop.run_loop(
        name="traced",
        execute=execute,
        evaluate=_accept_all,
        fallback="fb",
    )

    assert result.degraded is False
    assert ended == [
        {
            "attempts": 1,
            "degraded": False,
            "elapsed_ms": result.elapsed_ms,
            "generated_tokens": result.generated_tokens,
            "defects": [],
            "error": None,
        }
    ]


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
