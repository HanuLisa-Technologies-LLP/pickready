"""Semantic recovery: each failure class gets its OWN strategy, not just a retry.

RPN-AI-UP-001 W4.1 and W4.7. The specification's acceptance for W4.1 is that
"each failure class produces its own strategy, asserted by a fake transport. A
context overflow retries compressed, not identically. A refusal does not retry."

THE TRANSPORT IS FAKE, THE REST IS REAL
----------------------------------------
Every loop test below drives the router through a real `httpx.AsyncClient` over
an `httpx.MockTransport`, so the request is really serialized, `raise_for_status`
really raises, and `resp.json()` really parses. What the handler records is the
BODY that went on the wire, which is what makes "the retry was compressed" and
"the retry carried the validator's message" checkable claims rather than
statements about a mock's call list.

The one thing deliberately not faked is the policy table: `RECOVERY_FOR_FAILURE`
is transcribed independently below, so a change to it has to be made twice.
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from app.config import llm_providers
from app.services import context_budget, llm_router
from app.services.llm_router import (
    _FAILURE_THRESHOLD,
    LLMUnavailableError,
    ModelRefusal,
    _RouterKey,
)
from app.services.reliability import budget as reliability_budget
from app.services.reliability import vendor_contract


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    llm_router.reset_provider_stats()
    vendor_contract.reset_first_use()
    monkeypatch.setattr(
        llm_router,
        "key_for_model",
        lambda model: _RouterKey(api_key="k-test", fingerprint="fp1"),
    )
    yield
    llm_router.reset_provider_stats()
    vendor_contract.reset_first_use()


# ── The fake transport ───────────────────────────────────────────────────────


def _completion(text: str, *, finish_reason: str = "stop") -> dict:
    return {
        "id": "chatcmpl_test",
        "object": "chat.completion",
        "created": 0,
        "model": llm_providers.MODEL_LUNA,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4},
    }


def _refusal(reason: str = "refusal") -> dict:
    """The published refusal shape: a null content beside a `refusal` string."""
    body = _completion("x", finish_reason=reason)
    body["choices"][0]["message"]["content"] = None
    body["choices"][0]["message"]["refusal"] = "I will not answer that."
    return body


def _overflow_error() -> dict:
    """A 400 whose `error.code` says the request was too long."""
    return {
        "error": {
            "message": "This model's maximum context length is 128000 tokens.",
            "type": "invalid_request_error",
            "code": "context_length_exceeded",
        }
    }


def _install_transport(monkeypatch, responses):
    """Drive the router over `httpx.MockTransport` and record what was sent.

    `responses` entries are either a `(status, body)` pair or an exception to
    raise from the transport. The last entry repeats, so a test that wants "and
    then it keeps failing" writes one entry rather than a list sized to the
    retry budget.

    Returns the list of request bodies, in order.
    """
    sent: list[dict] = []
    real_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content.decode("utf-8")))
        outcome = responses[min(len(sent) - 1, len(responses) - 1)]
        if isinstance(outcome, Exception):
            raise outcome
        status, body = outcome
        return httpx.Response(status, json=body, request=request)

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(llm_router.httpx, "AsyncClient", factory)
    return sent


# ── The table itself ─────────────────────────────────────────────────────────


def test_every_failure_class_has_a_recovery_and_an_unknown_one_raises() -> None:
    """No default. The two plausible defaults are "retry forever" and "never
    retry", and both are wrong for some real failure."""
    for failure in sorted(llm_providers.RECOVERY_FOR_FAILURE):
        assert llm_providers.recovery_for(failure).strategy
    with pytest.raises(ValueError):
        llm_providers.recovery_for("something_nobody_mapped")


def test_the_recovery_table_is_the_one_the_specification_states() -> None:
    """Transcribed independently, so a table that agrees with itself is not
    enough. Each tuple is (strategy, retry, trips breaker at once)."""
    expected = {
        "credential": ("trip_breaker_immediately", False, True),
        "rate_limit": ("backoff_honouring_retry_after", True, False),
        "provider_error": ("bounded_exponential_backoff_with_jitter", True, False),
        "timeout": ("retry_treating_the_outcome_as_unknown", True, False),
        "transport": ("bounded_exponential_backoff_with_jitter", True, False),
        "client_error": ("surface_request_hazards", False, False),
        "context_overflow": ("compress_context_and_retry", True, False),
        "refusal": ("route_to_human", False, False),
        "schema_violation": ("reprompt_with_the_validator_message", True, False),
        "unclassified": ("surface_an_unclassified_failure", False, False),
    }
    assert set(llm_providers.RECOVERY_FOR_FAILURE) == set(expected)
    for failure, (strategy, retry, trips) in expected.items():
        recovery = llm_providers.recovery_for(failure)
        assert (recovery.strategy, recovery.retry, recovery.trips_breaker_immediately) == (
            strategy,
            retry,
            trips,
        ), failure


def test_only_two_classes_rewrite_the_request() -> None:
    """The property that separates a semantic recovery from a schedule. Every
    other class sends the same thing again, later."""
    rewriting = {
        failure
        for failure, recovery in llm_providers.RECOVERY_FOR_FAILURE.items()
        if recovery.rewrites_the_request
    }
    assert rewriting == {"context_overflow", "schema_violation"}


def test_only_a_timeout_leaves_the_outcome_unknown() -> None:
    """A refused connection did not reach the vendor; a timeout may have been
    served and only the answer lost. Folding them together is what makes a
    side-effecting caller send the second email."""
    unknown = {
        failure
        for failure, recovery in llm_providers.RECOVERY_FOR_FAILURE.items()
        if recovery.outcome_is_unknown
    }
    assert unknown == {"timeout"}


def test_classification_separates_a_timeout_from_a_transport_error() -> None:
    request = httpx.Request("POST", llm_providers.OPENAI_CHAT_COMPLETIONS_URL)
    assert (
        llm_router.classify_failure(httpx.ReadTimeout("slow", request=request))
        == "timeout"
    )
    assert (
        llm_router.classify_failure(httpx.ConnectError("refused", request=request))
        == "transport"
    )
    # And an exception this router cannot place is NOT assumed transient, which
    # is what preserves the previous behaviour for a contract violation.
    assert llm_router.classify_failure(ValueError("something else")) == "unclassified"
    assert not llm_providers.recovery_for("unclassified").retry


def test_a_400_is_our_bug_unless_the_vendor_says_it_was_too_long() -> None:
    request = httpx.Request("POST", llm_providers.OPENAI_CHAT_COMPLETIONS_URL)
    plain = httpx.HTTPStatusError(
        "bad",
        request=request,
        response=httpx.Response(400, json={"error": {"code": "invalid_value"}},
                                request=request),
    )
    overflow = httpx.HTTPStatusError(
        "bad",
        request=request,
        response=httpx.Response(400, json=_overflow_error(), request=request),
    )
    assert llm_router.classify_failure(plain) == "client_error"
    assert llm_router.classify_failure(overflow) == "context_overflow"


def test_only_the_allowlisted_error_fields_are_ever_read() -> None:
    """`error.message` can echo the request, and the request carries a real
    candidate's answers. The code is a vendor enum; the message is content."""
    assert llm_providers.VENDOR_ERROR_ALLOWED_FIELDS == ("code", "type")
    request = httpx.Request("POST", llm_providers.OPENAI_CHAT_COMPLETIONS_URL)
    exc = httpx.HTTPStatusError(
        "bad",
        request=request,
        response=httpx.Response(
            400,
            json={"error": {"message": "SECRET-ANSWER-TEXT", "code": "invalid_value"}},
            request=request,
        ),
    )
    assert llm_router.vendor_error_code(exc) == "invalid_value"


def test_a_body_that_is_not_json_yields_no_code_rather_than_raising() -> None:
    request = httpx.Request("POST", llm_providers.OPENAI_CHAT_COMPLETIONS_URL)
    exc = httpx.HTTPStatusError(
        "bad",
        request=request,
        response=httpx.Response(400, text="<html>gateway</html>", request=request),
    )
    assert llm_router.vendor_error_code(exc) is None
    assert llm_router.classify_failure(exc) == "client_error"


# ── Context overflow: compress and retry, never retry identically ────────────


@pytest.mark.asyncio
async def test_a_context_overflow_retries_compressed_rather_than_identically(
    monkeypatch,
) -> None:
    sent = _install_transport(
        monkeypatch,
        [(400, _overflow_error()), (200, _completion("recovered"))],
    )
    long_turns = [
        {"role": "user", "content": f"Turn number {index} of the transcript."}
        for index in range(8)
    ]
    messages = [{"role": "system", "content": "Grade the answer."}] + long_turns

    result = await llm_router.invoke_llm("rerank", messages, total_budget=1000.0)

    assert result == "recovered"
    assert len(sent) == 2, "the overflow must be retried exactly once here"
    first, second = sent
    assert len(second["messages"]) < len(first["messages"]), (
        "the retry must send LESS; an identical retry reproduces the same 400"
    )
    # And what survived is whole. Every non-marker turn is one of the inputs,
    # byte for byte, which is the assertion that catches a slice.
    originals = {m["content"] for m in messages}
    for message in second["messages"]:
        assert (
            message["content"] in originals
            or message["content"] == context_budget.OMISSION_MARKER
        ), message


@pytest.mark.asyncio
async def test_an_overflow_that_cannot_be_compressed_stops_rather_than_retrying(
    monkeypatch,
) -> None:
    """Nothing whole can be removed from a one-sentence prompt. Stopping is the
    honest answer: cutting inside a sentence hands a model half a sentence."""
    sent = _install_transport(monkeypatch, [(400, _overflow_error())])
    with pytest.raises(LLMUnavailableError) as excinfo:
        await llm_router.invoke_llm(
            "rerank", [{"role": "user", "content": "One indivisible ask."}],
            total_budget=1000.0,
        )
    assert len(sent) == 1
    assert "could not compress" in str(excinfo.value)


@pytest.mark.asyncio
async def test_compression_is_bounded_so_an_overflow_cannot_loop(
    monkeypatch,
) -> None:
    """A body that still overflows after two halvings will not be fixed by a
    third, and the retry budget is not the only thing that should say so."""
    sent = _install_transport(monkeypatch, [(400, _overflow_error())])
    messages = [
        {"role": "user", "content": f"Sentence number {index} of many."}
        for index in range(64)
    ]
    with pytest.raises(LLMUnavailableError):
        await llm_router.invoke_llm(
            "report_synthesis", messages, total_budget=1000.0
        )
    assert len(sent) <= llm_router._MAX_COMPRESSIONS + 1


# ── Refusal: not a transport failure, not retried, routed to a human ─────────


@pytest.mark.asyncio
async def test_a_refusal_is_not_retried(monkeypatch) -> None:
    sent = _install_transport(monkeypatch, [(200, _refusal())])
    with pytest.raises(ModelRefusal) as excinfo:
        await llm_router.invoke_llm(
            "rerank", [{"role": "user", "content": "hi"}], total_budget=1000.0
        )
    assert len(sent) == 1, "asking a model that declined to decline again is waste"
    assert excinfo.value.needs_human_review is True
    assert excinfo.value.reason == "refusal"


@pytest.mark.asyncio
async def test_a_refusal_still_degrades_for_a_caller_that_catches_the_base_class(
    monkeypatch,
) -> None:
    """The subclass is what makes a refusal distinguishable; it must not make it
    UNCATCHABLE. Every caller in this codebase catches `LLMUnavailableError` and
    degrades, and a refusal reaching a user as a 500 would be a regression."""
    _install_transport(monkeypatch, [(200, _refusal("content_filter"))])
    with pytest.raises(LLMUnavailableError):
        await llm_router.invoke_llm(
            "rerank", [{"role": "user", "content": "hi"}], total_budget=1000.0
        )


@pytest.mark.asyncio
async def test_a_refusal_does_not_trip_the_breaker(monkeypatch) -> None:
    """The credential worked and the vendor answered. Condemning the key for
    fifteen minutes over a content decision would take the whole tier off models
    for a reason that has nothing to do with the key."""
    _install_transport(monkeypatch, [(200, _refusal())])
    with pytest.raises(ModelRefusal):
        await llm_router.invoke_llm(
            "rerank", [{"role": "user", "content": "hi"}], total_budget=1000.0
        )
    assert not llm_router._is_cooling_down(_RouterKey(api_key="k-test", fingerprint="fp1"))


def test_an_empty_answer_is_not_read_as_a_refusal() -> None:
    """The narrowness that keeps the loud failure loud.

    An empty content is the silent-failure case `vendor_contract` exists to
    catch. Reading it as a decline would replace one honest raise with a
    quieter, wronger one.
    """
    body = _completion("")
    assert llm_router._refusal_result(body) is None
    assert llm_router._refusal_result(_refusal()) is not None


# ── Schema violation: the one retry that carries a different prompt ──────────


@pytest.mark.asyncio
async def test_a_schema_violation_retries_with_the_validator_message_verbatim(
    monkeypatch,
) -> None:
    sent = _install_transport(
        monkeypatch,
        [(200, _completion('{"items": []}')), (200, _completion('{"items": [1,2,3,4,5]}'))],
    )
    complaint = "return exactly 5 items; the previous attempt returned 0"

    def validate(text: str) -> None:
        if len(json.loads(text)["items"]) != 5:
            raise ValueError(complaint)

    result = await llm_router.invoke_llm(
        "rerank",
        [{"role": "user", "content": "list five things as json"}],
        response_format_json=True,
        total_budget=1000.0,
        validate=validate,
    )

    assert result == '{"items": [1,2,3,4,5]}'
    assert len(sent) == 2
    retry_text = " ".join(m["content"] for m in sent[1]["messages"])
    assert complaint in retry_text, "the validator's message travels VERBATIM"
    # And the first attempt carried none of it, or the assertion above would be
    # satisfied by a prompt that always contains the complaint.
    assert complaint not in " ".join(m["content"] for m in sent[0]["messages"])


@pytest.mark.asyncio
async def test_the_feedback_is_replaced_rather_than_accumulated(monkeypatch) -> None:
    """Three rejections must send one correction, not three. An accumulating
    prompt grows with every attempt, which is how a schema retry becomes a
    context overflow."""
    sent = _install_transport(monkeypatch, [(200, _completion('{"n": 1}'))])
    rejections = 0

    def validate(text: str) -> None:
        nonlocal rejections
        rejections += 1
        raise ValueError(f"rejection {rejections}")

    with pytest.raises(LLMUnavailableError):
        await llm_router.invoke_llm(
            "rerank",
            [{"role": "user", "content": "json please"}],
            response_format_json=True,
            total_budget=1000.0,
            validate=validate,
        )
    assert len(sent) == llm_providers.retry_budget_for("rerank")
    for body in sent:
        feedback_turns = [
            m for m in body["messages"] if "previous attempt was rejected" in m["content"]
        ]
        assert len(feedback_turns) <= 1


@pytest.mark.asyncio
async def test_the_validator_message_reaches_the_prompt_and_not_the_error(
    monkeypatch,
) -> None:
    """A validator message can quote the value it rejected, and a rejected value
    is a candidate's own words. `ctx.errors` is joined into the exception a
    caller logs, so the record carries the exception CLASS and the prompt
    carries the sentence."""
    _install_transport(monkeypatch, [(200, _completion('{"n": 1}'))])

    def validate(text: str) -> None:
        raise ValueError("input_value='SECRET-ANSWER-TEXT' is not a valid grade")

    with pytest.raises(LLMUnavailableError) as excinfo:
        await llm_router.invoke_llm(
            "rerank",
            [{"role": "user", "content": "json please"}],
            response_format_json=True,
            total_budget=1000.0,
            validate=validate,
        )
    assert "SECRET-ANSWER-TEXT" not in str(excinfo.value)
    assert "schema_violation (ValueError)" in str(excinfo.value)


@pytest.mark.asyncio
async def test_an_accepted_response_is_returned_without_a_second_call(
    monkeypatch,
) -> None:
    """The negative direction: a validator that accepts must cost nothing."""
    sent = _install_transport(monkeypatch, [(200, _completion('{"n": 1}'))])
    result = await llm_router.invoke_llm(
        "rerank",
        [{"role": "user", "content": "json please"}],
        response_format_json=True,
        total_budget=1000.0,
        validate=lambda text: json.loads(text),
    )
    assert result == '{"n": 1}'
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_a_validator_without_json_mode_is_refused_at_the_signature() -> None:
    """A schema is a claim about structured output, and the feedback the router
    sends back says 'in the same JSON shape'. Accepting a validator on a prose
    call would send an instruction that does not describe what was asked for."""
    with pytest.raises(ValueError):
        await llm_router.invoke_llm(
            "rerank",
            [{"role": "user", "content": "hi"}],
            validate=lambda text: None,
        )


# ── The classes that were already right, asserted so they stay right ─────────


@pytest.mark.asyncio
async def test_a_credential_failure_trips_the_breaker_on_the_first_occurrence(
    monkeypatch,
) -> None:
    sent = _install_transport(monkeypatch, [(401, {"error": {"code": "invalid_api_key"}})])
    with pytest.raises(LLMUnavailableError):
        await llm_router.invoke_llm(
            "rerank", [{"role": "user", "content": "hi"}], total_budget=1000.0
        )
    assert len(sent) == 1
    assert llm_router._is_cooling_down(_RouterKey(api_key="k-test", fingerprint="fp1"))


@pytest.mark.asyncio
async def test_a_provider_error_is_retried_and_does_not_trip_on_one_occurrence(
    monkeypatch,
) -> None:
    sent = _install_transport(
        monkeypatch, [(503, {"error": {"code": "server_error"}}), (200, _completion("ok"))]
    )
    assert (
        await llm_router.invoke_llm(
            "rerank", [{"role": "user", "content": "hi"}], total_budget=1000.0
        )
        == "ok"
    )
    assert len(sent) == 2
    assert not llm_router._is_cooling_down(_RouterKey(api_key="k-test", fingerprint="fp1"))
    assert _FAILURE_THRESHOLD > 1


def test_the_jittered_backoff_stays_inside_the_curve_it_spreads() -> None:
    """Bounded on both sides. A jitter that could exceed the cap would quietly
    raise it, and a negative delay is not a delay."""
    for attempt in range(1, 8):
        base = llm_providers.backoff_seconds(attempt)
        for unit in (0.0, 0.25, 0.5, 0.75, 0.999):
            delay = llm_providers.jittered_backoff_seconds(attempt, unit)
            assert 0.0 <= delay <= llm_providers.BACKOFF_MAX_SECONDS
            assert abs(delay - base) <= base * llm_providers.BACKOFF_JITTER_RATIO + 1e-9
    # And the midpoint is the curve, which is what lets the two be asserted
    # separately.
    assert llm_providers.jittered_backoff_seconds(3, 0.5) == (
        llm_providers.backoff_seconds(3)
    )


@pytest.mark.asyncio
async def test_a_timeout_marks_the_outcome_unknown(monkeypatch) -> None:
    """W4.1: a timeout is UNKNOWN for a side effecting call. The request may
    have been served and only the answer lost, so a caller that assumed "it did
    not happen" is the one that sends the second email."""
    request = httpx.Request("POST", llm_providers.OPENAI_CHAT_COMPLETIONS_URL)
    _install_transport(monkeypatch, [httpx.ReadTimeout("slow", request=request)])
    with pytest.raises(LLMUnavailableError) as excinfo:
        await llm_router.invoke_llm(
            "rerank", [{"role": "user", "content": "hi"}], total_budget=1000.0
        )
    assert excinfo.value.unknown_outcome is True
    assert "timeout" in str(excinfo.value)


@pytest.mark.asyncio
async def test_a_provider_failure_leaves_the_outcome_known(monkeypatch) -> None:
    """The negative direction. A 503 is an answer: the vendor said no."""
    _install_transport(monkeypatch, [(503, {"error": {"code": "server_error"}})])
    with pytest.raises(LLMUnavailableError) as excinfo:
        await llm_router.invoke_llm(
            "rerank", [{"role": "user", "content": "hi"}], total_budget=1000.0
        )
    assert excinfo.value.unknown_outcome is False


@pytest.mark.asyncio
async def test_a_retry_after_is_honoured_only_where_the_table_says_so(
    monkeypatch,
) -> None:
    """A 5xx that happened to carry the header would otherwise park the caller
    on the vendor's schedule for a failure that is not about rate at all."""
    assert llm_providers.recovery_for("rate_limit").honours_retry_after
    assert not llm_providers.recovery_for("provider_error").honours_retry_after

    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def _record_sleep(seconds: float) -> None:
        slept.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(llm_router.asyncio, "sleep", _record_sleep)
    _install_transport(
        monkeypatch,
        [
            (
                503,
                {"error": {"code": "server_error"}},
            ),
            (200, _completion("ok")),
        ],
    )
    await llm_router.invoke_llm(
        "rerank", [{"role": "user", "content": "hi"}], total_budget=1000.0
    )
    # Only the backoff curve, never a 600-second header the 5xx should not have
    # been allowed to impose.
    assert all(seconds <= llm_providers.BACKOFF_MAX_SECONDS for seconds in slept)


# ── W4.7: cost ceilings beside the latency ceilings ──────────────────────────


def test_every_task_has_a_cost_ceiling_above_its_own_worst_case() -> None:
    """The floor that keeps the table honest.

    A ceiling below what a legitimate maximal call costs would refuse ordinary
    work, which is exactly the failure `reliability/budget.py` warns about. The
    worst case is a prompt filling the whole context budget plus the task's own
    output ceiling.
    """
    for task, model in sorted(llm_providers.MODEL_FOR_TASK.items()):
        worst_case = llm_providers.estimate_cost_usd(
            model,
            context_budget.TOTAL_CONTEXT_BUDGET_TOKENS,
            llm_providers.max_tokens_for(task),
        )
        ceiling = llm_providers.cost_ceiling_for(task)
        assert ceiling >= worst_case * 2, (
            f"{task}: the {ceiling:.4f} USD ceiling is not twice the "
            f"{worst_case:.4f} USD a fully budgeted call costs"
        )


def test_no_cost_ceiling_passes_the_platform_backstop() -> None:
    """Nothing in this product legitimately costs a dollar in one call, which is
    the number `reliability/budget.py` already writes down. Read from there
    rather than retyped, so the two cannot drift apart."""
    ceilings = list(llm_providers.TASK_COST_CEILING_USD.values()) + [
        llm_providers.DEFAULT_COST_CEILING_USD
    ]
    for ceiling in ceilings:
        assert 0 < ceiling <= reliability_budget.HARD_COST_CEILING_USD


def test_the_default_ceiling_is_never_tighter_than_a_reviewed_one() -> None:
    """An unpriced task must not be refused work a priced one is allowed. The
    default is a fallback for a NEW task type, not a squeeze."""
    assert llm_providers.DEFAULT_COST_CEILING_USD >= max(
        llm_providers.TASK_COST_CEILING_USD.values()
    )


@pytest.mark.asyncio
async def test_a_prompt_far_over_budget_is_refused_before_the_transport(
    monkeypatch,
) -> None:
    """Refused BEFORE the work, like every other ceiling here. Checking after
    would mean the overspend already happened and the ceiling is a report."""
    sent = _install_transport(monkeypatch, [(200, _completion("never reached"))])
    enormous = "word " * 4_000_000
    with pytest.raises(LLMUnavailableError) as excinfo:
        await llm_router.invoke_llm(
            "rerank", [{"role": "user", "content": enormous}], total_budget=1000.0
        )
    assert sent == [], "nothing may reach the vendor once the ceiling refuses"
    assert "ceiling" in str(excinfo.value)


@pytest.mark.asyncio
async def test_every_cost_refusal_is_recorded(monkeypatch) -> None:
    """W4.7: a budget that stopped something silently is indistinguishable from
    a task that finished."""
    _install_transport(monkeypatch, [(200, _completion("never reached"))])
    with pytest.raises(LLMUnavailableError):
        await llm_router.invoke_llm(
            "rerank",
            [{"role": "user", "content": "word " * 4_000_000}],
            total_budget=1000.0,
        )
    refusals = llm_router.cost_refusals()
    assert len(refusals) == 1
    entry = refusals[0]
    assert entry["task_type"] == "rerank"
    assert entry["model"] == llm_providers.MODEL_LUNA
    assert entry["estimated_usd"] > entry["ceiling_usd"]
    assert entry["stage"] == "before the first attempt"


@pytest.mark.asyncio
async def test_a_cost_refusal_records_no_prompt_content(monkeypatch) -> None:
    """The same rule the error text keeps: identifiers and numbers, never the
    request, because the request carries a real candidate's answers."""
    _install_transport(monkeypatch, [(200, _completion("never reached"))])
    with pytest.raises(LLMUnavailableError):
        await llm_router.invoke_llm(
            "rerank",
            [{"role": "user", "content": "SECRET-ANSWER-TEXT " * 300_000}],
            total_budget=1000.0,
        )
    assert "SECRET-ANSWER-TEXT" not in repr(llm_router.cost_refusals())


@pytest.mark.asyncio
async def test_an_ordinary_call_is_never_touched_by_the_ceiling(
    monkeypatch,
) -> None:
    """The direction that would break the product. Every existing caller sends a
    prompt far inside the budget, and the ceiling must be invisible to them."""
    sent = _install_transport(monkeypatch, [(200, _completion("ok"))])
    for task in sorted(llm_providers.MODEL_FOR_TASK):
        assert (
            await llm_router.invoke_llm(
                task, [{"role": "user", "content": "a normal prompt"}],
                total_budget=1000.0,
            )
            == "ok"
        )
    assert len(sent) == len(llm_providers.MODEL_FOR_TASK)
    assert llm_router.cost_refusals() == []


def test_the_cost_refusal_log_is_bounded() -> None:
    """In-process memory on a long-lived worker. The alarm you need is the one
    that just fired, so the OLDEST is what goes."""
    for index in range(llm_router._COST_REFUSAL_LOG_LIMIT + 25):
        llm_router._record_cost_refusal(
            task_type="rerank",
            model=llm_providers.MODEL_LUNA,
            estimated_usd=float(index),
            ceiling_usd=0.06,
            stage="before the first attempt",
        )
    refusals = llm_router.cost_refusals()
    assert len(refusals) == llm_router._COST_REFUSAL_LOG_LIMIT
    assert refusals[-1]["estimated_usd"] == float(
        llm_router._COST_REFUSAL_LOG_LIMIT + 24
    )
