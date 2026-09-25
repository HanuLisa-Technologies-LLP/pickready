"""Prompt-cache accounting and per-assessment cost attribution.

WHAT IS AND IS NOT PROVEN HERE, STATED FIRST
----------------------------------------------
Every assertion below is about THIS CODEBASE's behaviour: what
`parse_response` does with a body, what `estimate_cost_usd` computes from a
table, what order `prompt_cache` writes fields in, and whether
`RequestTrace.add_cost` has a caller.

NOTHING HERE IS EVIDENCE ABOUT THE VENDOR. Whether the configured endpoint
reports `usage.prompt_tokens_details.cached_tokens` for `gpt-5.6-terra` or
`gpt-5.6-luna` at all, and whether reordering a prompt actually produces a
cache hit on it, are UNPROVEN: neither model id has been sent a request from
this repository, and `docs/verification/VERIFICATION_PENDING.md` carries the
row. The fixtures below are shapes this code must handle, not transcripts of a
call that happened.

THE DEFECT THESE TESTS EXIST FOR
----------------------------------
`RequestTrace.add_cost` was defined on the day the trace was written and had NO
CALLER anywhere in the tree, so `agent_execution_traces.cost_usd` was 0.0 on
every row ever written. It was also wrong in a way that would have survived
being called: it passed a PROVIDER string to a price table keyed by MODEL, so
it would have returned 0.0 -- a wrong number that looks exactly like a free
one. Both halves are pinned below, and the second is pinned in the mutation
direction as well: the test asserts that the provider name prices to nothing,
because that is the bug and an assertion that only checks the happy path would
not have caught it.

The caller the 2026-09-22 fix gave it was the reasoning runner, which nothing
on the live path reached; it was deleted in the Vivekium release, so `add_cost`
and `persist` are recorded as uncalled in `test_ai_reachability.py` rather than
claimed as wired here.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from app.config import llm_providers
from app.services import cost_telemetry, llm_router, prompt_cache
from app.services.observability import trace as tracing


# ── 28A: reading the vendor's cache report ───────────────────────────────────


def _body(usage: dict | None) -> dict:
    payload: dict = {
        "choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}]
    }
    if usage is not None:
        payload["usage"] = usage
    return payload


def test_a_reported_cache_hit_is_read_off_the_usage_block() -> None:
    result = llm_router.parse_response(
        _body(
            {
                "prompt_tokens": 4096,
                "completion_tokens": 200,
                "prompt_tokens_details": {"cached_tokens": 3072},
            }
        ),
        json_mode=False,
    )
    assert result.prompt_tokens == 4096
    assert result.cached_prompt_tokens == 3072


def test_a_response_with_no_details_block_reports_unknown_and_not_zero() -> None:
    """The distinction this whole feature rests on.

    A provider that does not report caching sends no `prompt_tokens_details`.
    Flattening that to 0 would make "we cannot see the cache" arithmetically
    identical to "the cache did not hit", and every average built on top would
    then say the cache never works.
    """
    result = llm_router.parse_response(
        _body({"prompt_tokens": 4096, "completion_tokens": 200}), json_mode=False
    )
    assert result.cached_prompt_tokens is None
    assert result.prompt_tokens == 4096


def test_a_reported_zero_survives_as_a_zero() -> None:
    """The other half of the rule. A measured miss is a real observation."""
    result = llm_router.parse_response(
        _body(
            {
                "prompt_tokens": 120,
                "completion_tokens": 8,
                "prompt_tokens_details": {"cached_tokens": 0},
            }
        ),
        json_mode=False,
    )
    assert result.cached_prompt_tokens == 0


@pytest.mark.parametrize(
    "details",
    [
        {"cached_tokens": "3072"},
        {"cached_tokens": None},
        {"cached_tokens": True},
        {},
        "not-a-dict",
    ],
)
def test_an_unreadable_cache_figure_is_unknown_rather_than_guessed(details) -> None:
    result = llm_router.parse_response(
        _body(
            {
                "prompt_tokens": 100,
                "completion_tokens": 10,
                "prompt_tokens_details": details,
            }
        ),
        json_mode=False,
    )
    assert result.cached_prompt_tokens is None


def test_the_stats_counter_separates_reported_from_unreported() -> None:
    llm_router.reset_provider_stats()
    model = llm_providers.MODEL_LUNA
    llm_router._record(
        fingerprint="fp", model=model, ok=True, latency_ms=1.0,
        prompt_tokens=100, completion_tokens=10, had_usage=True,
        cached_prompt_tokens=None,
    )
    llm_router._record(
        fingerprint="fp", model=model, ok=True, latency_ms=1.0,
        prompt_tokens=100, completion_tokens=10, had_usage=True,
        cached_prompt_tokens=40,
    )
    stats = llm_router.model_stats()[model]
    assert stats["calls_reporting_cache"] == 1
    assert stats["cached_prompt_tokens"] == 40
    llm_router.reset_provider_stats()


# ── 28A: pricing a cached token ──────────────────────────────────────────────


def test_no_cached_rate_is_on_file_so_no_discount_is_invented() -> None:
    """A cached token is charged at the FULL rate until a rate says otherwise.

    The uncached rates themselves are carried forward from a previous roster
    and have never been read off a price sheet for these ids. Inventing a
    cache discount on top of that would be a second unverified number
    multiplying the first, and it would move the estimate in the unsafe
    direction: under-stating a bill.
    """
    for model in sorted(llm_providers.ALLOWED_MODELS):
        assert not llm_providers.cached_input_price_known(model), model
        full = llm_providers.estimate_cost_usd(model, 1_000_000, 0)
        with_cache = llm_providers.estimate_cost_usd(
            model, 1_000_000, 0, cached_prompt_tokens=900_000
        )
        assert with_cache == full


def test_a_cached_rate_would_be_applied_the_day_one_is_written(monkeypatch) -> None:
    """The arithmetic is there and is exercised, so the table is the only edit.

    Without this the "if and only if the table can express it" half of the
    design is unfalsifiable: every assertion above would also pass against a
    function that ignored the argument entirely.
    """
    model = llm_providers.MODEL_LUNA
    monkeypatch.setitem(
        llm_providers.TOKEN_PRICES_USD_PER_MILLION,
        model,
        {"prompt": 1.00, "cached_prompt": 0.10, "completion": 5.00},
    )
    assert llm_providers.cached_input_price_known(model)
    # 900k cached at 0.10 + 100k uncached at 1.00 = 0.09 + 0.10.
    assert llm_providers.estimate_cost_usd(
        model, 1_000_000, 0, cached_prompt_tokens=900_000
    ) == pytest.approx(0.19)


def test_cached_tokens_are_a_subset_and_can_never_exceed_the_prompt() -> None:
    """A vendor figure larger than the prompt total is clamped, not trusted.

    Left unclamped it would produce a NEGATIVE uncached count and therefore a
    cost below zero once a cached rate exists, which is an estimate that
    silently pays the platform.
    """
    model = llm_providers.MODEL_TERRA
    assert llm_providers.estimate_cost_usd(
        model, 100, 0, cached_prompt_tokens=10_000
    ) == llm_providers.estimate_cost_usd(model, 100, 0)


# ── 28A: the prefix is what makes caching possible at all ────────────────────


def _segments(*, competency: str) -> dict:
    return {
        "job_description": {"job_description": "Build streaming pipelines." * 40},
        "candidate": {"candidate_resume": "Eight years on Kafka." * 20},
        "turn": {
            "competency_to_probe": competency,
            "conversation_so_far": f"...{competency}...",
        },
    }


def test_the_static_context_is_a_literal_prefix_of_the_assembled_body() -> None:
    """Not merely "the static keys are present": a PREFIX, byte for byte.

    A cache matches on a leading run of identical bytes. A payload that carried
    the same static fields in a different place would satisfy a weaker
    assertion and share nothing with anything.
    """
    segments = _segments(competency="Kafka")
    body = json.dumps(prompt_cache.ordered_fields(segments), ensure_ascii=False)
    static = prompt_cache.static_prefix_text(segments)
    # `static` is a complete JSON object, so its final `}` is where the full
    # body continues instead of closing.
    assert body.startswith(static[:-1])


def test_two_turns_of_one_conversation_share_the_static_prefix() -> None:
    first = _segments(competency="Kafka")
    second = _segments(competency="Postgres")
    assert prompt_cache.ordered_fields(first) != prompt_cache.ordered_fields(second)
    assert prompt_cache.static_prefix_text(first) == prompt_cache.static_prefix_text(
        second
    )
    assert prompt_cache.identity(
        tenant_id="t", job_id="j", task_type="conversation_turn", segments=first
    ) == prompt_cache.identity(
        tenant_id="t", job_id="j", task_type="conversation_turn", segments=second
    )


def test_the_volatile_material_is_written_last() -> None:
    keys = list(prompt_cache.ordered_fields(_segments(competency="Kafka")))
    assert keys.index("job_description") < keys.index("competency_to_probe")
    assert keys[-1] == "conversation_so_far"


def test_an_edited_job_description_is_a_different_prefix() -> None:
    """A version number alone would claim a shared prefix with replaced text."""
    original = _segments(competency="Kafka")
    edited = _segments(competency="Kafka")
    edited["job_description"] = {"job_description": "Rewritten brief."}
    assert prompt_cache.identity(
        tenant_id="t", job_id="j", task_type="conversation_turn", segments=original
    ) != prompt_cache.identity(
        tenant_id="t", job_id="j", task_type="conversation_turn", segments=edited
    )


def test_an_identity_never_crosses_a_tenant_or_a_job() -> None:
    segments = _segments(competency="Kafka")
    base = prompt_cache.identity(
        tenant_id="tenant-a", job_id="job-1", task_type="conversation_turn",
        segments=segments,
    )
    assert "tenant-a" in base
    assert base != prompt_cache.identity(
        tenant_id="tenant-b", job_id="job-1", task_type="conversation_turn",
        segments=segments,
    )
    assert base != prompt_cache.identity(
        tenant_id="tenant-a", job_id="job-2", task_type="conversation_turn",
        segments=segments,
    )
    assert base != prompt_cache.identity(
        tenant_id="tenant-a", job_id="job-1", task_type="jd_generation",
        segments=segments,
    )


def test_an_unknown_segment_is_refused_rather_than_dropped() -> None:
    """A dropped segment is content silently missing from a graded prompt."""
    with pytest.raises(prompt_cache.SegmentOrderError):
        prompt_cache.ordered_fields({"scratchpad": {"x": 1}})


def test_the_question_writer_payload_leads_with_its_stable_half() -> None:
    """The live question writer sends its stable half FIRST.

    The interviewer's deleted delivery graph (2026-09-24) once opened its body
    with the most volatile field it had, so the JD and the resume behind it
    were re-read at full price on every turn. The live writer
    is `ppi_interview.write_question`, and its payload builder keeps the job
    description and the resume, which do not change for a candidate's whole
    conversation, ahead of the per-turn fields. The SET is asserted separately:
    a payload that quietly gained or lost a field would be a product change.
    """
    from app.models.job import Job
    from app.services import ppi_interview

    payload = ppi_interview.request_payload(
        job=Job(jd_markdown="JD"),
        resume_excerpt="CV",
        recent=[],
        asked_before=[],
    )
    assert list(payload)[:2] == ["job_description", "candidate_resume"]
    assert set(payload) == {
        "job_description",
        "candidate_resume",
        "conversation_so_far",
        "already_asked",
    }


# ── The two defects in `RequestTrace.add_cost` ───────────────────────────────


def test_add_cost_prices_by_model_and_a_provider_name_prices_to_nothing() -> None:
    """The mutation direction is the assertion that matters.

    `add_cost(provider, ...)` was the original signature and the price table is
    keyed by MODEL, so the method would have contributed 0.0 for every call
    even once somebody called it. Asserting only that the model works would
    pass against the broken version too, because the broken version also
    "works" when you hand it a model.
    """
    assert llm_providers.estimate_cost_usd(llm_providers.PROVIDER, 1_000_000, 0) == 0.0
    assert not llm_providers.is_priced(llm_providers.PROVIDER)

    trace = tracing.RequestTrace(agent_type="siddhi", task_type="report_synthesis")
    trace.add_cost(llm_providers.MODEL_TERRA, 1_000_000, 100_000)
    assert trace.cost_usd == pytest.approx(3.00 + 1.50)
    assert trace.measured_prompt_tokens == 1_000_000
    assert trace.measured_completion_tokens == 100_000


def test_a_trace_keeps_the_estimate_and_the_measurement_apart() -> None:
    trace = tracing.RequestTrace(agent_type="siddhi", task_type="report_synthesis")
    trace.generated_tokens = 42
    trace.add_cost(llm_providers.MODEL_LUNA, 1000, 100, cached_prompt_tokens=800)
    body = trace.as_dict()
    assert body["generated_tokens"] == 42
    assert body["measured_prompt_tokens"] == 1000
    assert body["measured_cached_prompt_tokens"] == 800
    assert "heuristic" in body["generated_tokens_basis"]
    assert "not an invoice" in body["cost_basis"]


# ── The tally ────────────────────────────────────────────────────────────────


def _usage(task_type: str, model: str, *, cached: int | None = None) -> None:
    cost_telemetry.note_usage(
        task_type=task_type,
        model=model,
        provider=llm_providers.PROVIDER,
        prompt_tokens=1_000_000,
        completion_tokens=0,
        cached_prompt_tokens=cached,
        had_usage=True,
    )


def test_nothing_is_recorded_when_no_scope_is_bound() -> None:
    assert cost_telemetry.note_usage(
        task_type="extraction",
        model=llm_providers.MODEL_LUNA,
        provider=llm_providers.PROVIDER,
        prompt_tokens=10,
        completion_tokens=1,
        cached_prompt_tokens=None,
        had_usage=True,
    ) is False


def test_synthesis_is_attributed_to_its_own_line_by_task_type() -> None:
    with cost_telemetry.collect() as tally:
        _usage("dimension_evaluation", llm_providers.MODEL_TERRA)
        _usage("report_synthesis", llm_providers.MODEL_TERRA)
    assert tally.calls == 2
    assert tally.estimated_cost_usd == pytest.approx(6.00)
    assert tally.synthesis_prompt_tokens == 1_000_000
    assert tally.synthesis_cost_usd == pytest.approx(3.00)


def test_an_inner_scope_does_not_hide_the_spend_from_an_outer_one() -> None:
    with cost_telemetry.collect() as outer:
        _usage("extraction", llm_providers.MODEL_LUNA)
        with cost_telemetry.collect() as inner:
            _usage("extraction", llm_providers.MODEL_LUNA)
    assert inner.calls == 1
    assert outer.calls == 2


def test_the_tally_counts_the_calls_that_reported_a_cache_separately() -> None:
    with cost_telemetry.collect() as tally:
        _usage("extraction", llm_providers.MODEL_LUNA, cached=None)
        _usage("extraction", llm_providers.MODEL_LUNA, cached=250_000)
    assert tally.calls == 2
    assert tally.calls_reporting_cache == 1
    assert tally.cached_prompt_tokens == 250_000
    assert tally.by_model[llm_providers.MODEL_LUNA]["calls"] == 2
    assert tally.by_model[llm_providers.MODEL_LUNA]["cache_priced"] is False


def test_a_scope_bound_in_one_task_cannot_reach_the_next_one() -> None:
    """The property that makes `begin()` safe in a request handler.

    `respond` binds a tally and deliberately never unwinds it, which is only
    defensible because a `contextvars` write lands in the writing TASK's own
    copy of the context. If that were not true, one candidate's turn would be
    billed to every request the worker served afterwards -- which is exactly
    what a request handler is: one task, then the next task, on one worker.

    So the two tasks here are SIBLINGS, the shape two requests actually have.
    (A CHILD task inheriting its parent's scope is correct and is asserted by
    the nesting test above; that is a stage inside a run, not a second run.)
    """

    async def _leaky() -> None:
        cost_telemetry.begin()
        _usage("extraction", llm_providers.MODEL_LUNA)

    async def _next_request() -> tuple[bool, int]:
        with cost_telemetry.collect() as tally:
            _usage("extraction", llm_providers.MODEL_LUNA)
            return (
                # No scope survives into a task that bound none of its own.
                cost_telemetry.note_usage(
                    task_type="extraction",
                    model=llm_providers.MODEL_LUNA,
                    provider=llm_providers.PROVIDER,
                    prompt_tokens=1,
                    completion_tokens=1,
                    cached_prompt_tokens=None,
                    had_usage=True,
                ),
                tally.calls,
            )

    async def _main() -> tuple[bool, int]:
        await asyncio.create_task(_leaky())
        return await asyncio.create_task(_next_request())

    listened, calls = asyncio.run(_main())
    # The second task saw exactly its own two calls and nothing of the first.
    assert listened is True
    assert calls == 2
