"""Per-assessment cost attribution: what one application cost to run.

THE QUESTION THIS ANSWERS, AND WHY NOTHING COULD ANSWER IT BEFORE
------------------------------------------------------------------
`llm_router` has counted tokens and estimated cost since the single-vendor
consolidation, per process and per model. That is the right shape for the
health endpoint and the wrong shape for unit economics. It cannot say what one
assessment cost, because a scoring run is a Fargate container that exits and a
conversation turn is a request on an API task, and the spend for ONE candidate
is spread across both with nothing tying the pieces together. It also cannot
survive a restart, so every figure it holds is "since this worker started".

THE ROUTER IS THE ONLY PLACE THE SPEND IS VISIBLE, SO THAT IS WHERE IT IS READ
-------------------------------------------------------------------------------
`note_usage` is called from `llm_router` on every successful call and is a
NO-OP unless something has bound a scope. The alternative was passing an
accounting handle down through every service that makes a model call, and the
failure mode of that is the one this repository has already paid for twice: a
rule enforced at N call sites is a rule the N+1th call site breaks, silently,
with a bill as the only symptom.

Binding is a `contextvars` stack rather than a parameter for the same reason
`otel._active_span` is. Each asyncio task gets its own copy, so a scope bound
in one request cannot leak into another, and a Fargate container running one
scoring job has exactly one scope for its whole life.

TWO LAYERS, AND ONLY ONE OF THEM TOUCHES A DATABASE
-----------------------------------------------------
`collect()` is an in-memory tally over a block of work. It needs no ids and no
session, and it is what lets `RequestTrace.add_cost` finally be called with
real numbers rather than not at all.

`record_for()` is the durable half: it carries the tenant, the job and the
application, and on exit it accumulates its tally into `assessment_cost_records`
through the one writer below. A scope bound with `record_for` is also a
`collect()` scope, so a caller never has to choose.

FAILING TO OBSERVE MUST NEVER FAIL THE WORK
---------------------------------------------
Same rule `observability/trace.persist` states and for the same reason. The
assessment has already happened by the time there is anything to write; failing
the candidate's turn because a counter could not be stored would make the
accounting the least reliable thing in the request path. The write is attempted
inside a SAVEPOINT so a failure cannot poison the caller's transaction.

THAT COVERS A DATABASE THAT REFUSED THE WRITE, AND NOTHING ELSE. `flush` catches
`SQLAlchemyError` and only `SQLAlchemyError`. A `TypeError` from the merge
arithmetic is not an operational failure the accounting should absorb: it fails
on every call rather than on an unlucky one, so absorbing it produces a cost
record that never appears with a warning naming only the exception class as the
symptom. That is exactly what happened to the missing column defaults, so the
handler is narrow and what it does catch is logged with its traceback.

EVERY NUMBER HERE IS AN ESTIMATE AND SAYS SO
----------------------------------------------
The rates come from `config/llm_providers.TOKEN_PRICES_USD_PER_MILLION`, which
is the one source: no literal price appears in this module. Those rates are
unverified for the two model ids in use, prompt caching is counted but not yet
discounted, and a call whose response omitted `usage` contributes a character
heuristic rather than a measurement. `calls_with_usage` beside `calls` is what
lets a reader see how much of a total was measured, and `cost_basis` on the row
is what stops an estimate being read as an invoice.
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from datetime import datetime as datetime_type
from datetime import timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.llm_providers import (
    PROVIDER,
    TOKEN_PRICES_USD_PER_MILLION,
    cached_input_price_known,
    estimate_cost_usd,
    is_priced,
)
from app.models.cost import (
    COST_BASIS_ESTIMATED,
    COST_BASIS_FINALIZED,
    AssessmentCostRecord,
)

logger = logging.getLogger(__name__)

#: The router task type that IS Siddhi's synthesis call. Broken out onto its
#: own line on the record because it is routinely the largest single call in an
#: assessment, and a total that hides it cannot answer "is the report or the
#: conversation what costs money".
SYNTHESIS_TASK_TYPE = "report_synthesis"


@dataclass(frozen=True)
class UsageEvent:
    """One completed model call, as the accounting layer sees it.

    A frozen dataclass with a closed field set rather than a dict, for the
    reason `miti.EvaluatorInput` gives: a free-form mapping is how a prompt, an
    answer or a candidate's name eventually arrives somewhere it must never be.
    Nothing here is content.
    """

    task_type: str
    model: str
    provider: str
    prompt_tokens: int
    completion_tokens: int
    #: None means the response did not report a prompt-cache hit at all. It is
    #: NOT zero, and the distinction survives all the way to the record.
    cached_prompt_tokens: int | None
    #: Whether the vendor sent a `usage` block. False means the token counts
    #: above are an estimate rather than a measurement.
    had_usage: bool

    @property
    def estimated_cost_usd(self) -> float:
        return estimate_cost_usd(
            self.model,
            self.prompt_tokens,
            self.completion_tokens,
            cached_prompt_tokens=self.cached_prompt_tokens or 0,
        )


@dataclass
class UsageTally:
    """Model usage accumulated over one block of work.

    In memory and id-free on purpose: this is the shape a caller wants when it
    only needs to know what a stage cost, and the shape `record_for` writes
    from when it also knows which application to bill it to.
    """

    calls: int = 0
    calls_with_usage: int = 0
    calls_reporting_cache: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_prompt_tokens: int = 0
    estimated_cost_usd: float = 0.0
    synthesis_prompt_tokens: int = 0
    synthesis_completion_tokens: int = 0
    synthesis_cost_usd: float = 0.0
    #: `{model: {calls, prompt_tokens, completion_tokens, cached_prompt_tokens,
    #: estimated_cost_usd, priced, cache_priced}}`.
    by_model: dict[str, dict[str, Any]] = field(default_factory=dict)

    def add(self, event: UsageEvent) -> None:
        cost = event.estimated_cost_usd
        self.calls += 1
        self.prompt_tokens += event.prompt_tokens
        self.completion_tokens += event.completion_tokens
        self.estimated_cost_usd += cost
        if event.had_usage:
            self.calls_with_usage += 1
        if event.cached_prompt_tokens is not None:
            self.cached_prompt_tokens += event.cached_prompt_tokens
            self.calls_reporting_cache += 1
        if event.task_type == SYNTHESIS_TASK_TYPE:
            self.synthesis_prompt_tokens += event.prompt_tokens
            self.synthesis_completion_tokens += event.completion_tokens
            self.synthesis_cost_usd += cost

        row = self.by_model.setdefault(
            event.model,
            {
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cached_prompt_tokens": 0,
                "estimated_cost_usd": 0.0,
                # Carried per model so a reader of an old row can tell a total
                # that was priced from one that could not be, without having to
                # know what the price table looked like that day.
                "priced": is_priced(event.model),
                "cache_priced": cached_input_price_known(event.model),
            },
        )
        row["calls"] += 1
        row["prompt_tokens"] += event.prompt_tokens
        row["completion_tokens"] += event.completion_tokens
        row["cached_prompt_tokens"] += event.cached_prompt_tokens or 0
        row["estimated_cost_usd"] = round(
            float(row["estimated_cost_usd"]) + cost, 6
        )

    @property
    def is_empty(self) -> bool:
        return self.calls == 0


@dataclass(frozen=True)
class AssessmentScope:
    """Which application the spend inside this scope belongs to."""

    tenant_id: uuid.UUID
    job_id: uuid.UUID
    link_id: uuid.UUID


#: The stack of live tallies. A tuple rather than a list because a ContextVar
#: holding a mutable container is shared across every task that inherited it,
#: which is precisely the leak this is meant to prevent.
_tallies: contextvars.ContextVar[tuple[UsageTally, ...]] = contextvars.ContextVar(
    "cost_telemetry_tallies", default=()
)


def note_usage(
    *,
    task_type: str,
    model: str,
    provider: str,
    prompt_tokens: int,
    completion_tokens: int,
    cached_prompt_tokens: int | None,
    had_usage: bool,
) -> bool:
    """Record one successful model call against every live tally.

    Returns whether anything was listening, so a caller that expected a scope
    and found none can say so. `llm_router` deliberately ignores the answer:
    most calls in this product legitimately run outside an assessment, and
    warning on each of those would be noise that trains people to ignore the
    log line that matters.
    """
    tallies = _tallies.get()
    if not tallies:
        return False
    event = UsageEvent(
        task_type=task_type,
        model=model,
        provider=provider,
        prompt_tokens=int(prompt_tokens),
        completion_tokens=int(completion_tokens),
        cached_prompt_tokens=(
            None if cached_prompt_tokens is None else int(cached_prompt_tokens)
        ),
        had_usage=bool(had_usage),
    )
    for tally in tallies:
        tally.add(event)
    return True


def begin() -> UsageTally:
    """Start a tally whose scope is the ENCLOSING TASK, and never unwind it.

    `collect()` is the right tool wherever the work fits inside a block. A
    request handler often does not: the model calls are scattered through
    several hundred lines with early refusals between them, and wrapping the
    whole body in a `with` to gain a tally would be a four-hundred-line indent
    whose diff hides every real change in it.

    Not unwinding is SAFE HERE AND ONLY HERE, and for a precise reason. A
    `contextvars` value set inside an asyncio task is written to that task's own
    copy of the context; FastAPI runs every request in its own task, and a
    worker invocation runs its task under `asyncio.run`. So the tally dies with
    the task that started it and cannot reach another request. Calling this
    from module scope, or from a long-lived task that serves more than one
    logical unit of work, would leak it into everything that follows -- which
    is why this is a named function with this paragraph attached rather than a
    convenience anybody can reach for.
    """
    tally = UsageTally()
    _tallies.set(_tallies.get() + (tally,))
    return tally


@contextlib.contextmanager
def collect() -> Iterator[UsageTally]:
    """Tally every model call made inside this block. No ids, no database.

    Nestable: an inner scope does not hide the calls from an outer one, because
    a stage's own cost and the whole run's cost are both real questions and a
    scope that captured exclusively would make the outer total wrong.
    """
    tally = UsageTally()
    token = _tallies.set(_tallies.get() + (tally,))
    try:
        yield tally
    finally:
        _tallies.reset(token)


@contextlib.asynccontextmanager
async def record_for(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    job_id: uuid.UUID,
    link_id: uuid.UUID,
    conversation_turns: int = 0,
    questions_asked: int | None = None,
    pricing_tier: str | None = None,
) -> AsyncIterator[UsageTally]:
    """Tally this block's model calls and accumulate them onto the application.

    The flush runs in a `finally`, so work that raised part way through still
    reports what it had already spent. That is the case worth protecting: a
    scoring run that died after four evaluator calls cost real money, and a
    record that only exists for runs that succeeded would under-report exactly
    the spend an operator most wants to see.
    """
    with collect() as tally:
        try:
            yield tally
        finally:
            await flush(
                session,
                scope=AssessmentScope(
                    tenant_id=tenant_id, job_id=job_id, link_id=link_id
                ),
                tally=tally,
                conversation_turns=conversation_turns,
                questions_asked=questions_asked,
                pricing_tier=pricing_tier,
            )


@contextlib.asynccontextmanager
async def _savepoint(session: Any) -> AsyncIterator[None]:
    """Run the accounting write inside a SAVEPOINT.

    A try/except alone is not enough, and this repository has already paid for
    learning that once in `functional_assessment._savepoint`: an INSERT that
    reaches Postgres and fails aborts the surrounding transaction, so every
    later statement in it fails too. Swallowing the exception would then turn
    "the cost counter could not be written" into "the candidate's turn was
    lost", which is the exact inversion of the rule this module is written to.

    A session with no `begin_nested` simply runs the body, so a test double
    degrades to the try/except around this rather than to a crash.
    """
    nested = getattr(session, "begin_nested", None)
    if nested is None:
        yield
        return
    async with nested():
        yield


async def flush(
    session: AsyncSession,
    *,
    scope: AssessmentScope,
    tally: UsageTally,
    conversation_turns: int = 0,
    questions_asked: int | None = None,
    pricing_tier: str | None = None,
) -> bool:
    """Accumulate a tally onto the application's cost record. Never raises.

    Returns whether the row landed, so a caller that cares can log it. The
    tally is DRAINED on a successful write, so calling this twice adds the same
    spend once: the second call finds an empty tally and has nothing to add.

    `pricing_tier` is resolved HERE and only when the row does not already
    carry one, rather than by every caller before every call. A conversation
    turn calls this on each answer and the tier is stamped once on the first
    piece of spend, so looking it up in the caller would run a join on every
    turn of every interview to discard the answer. Passing one explicitly still
    wins, which is what lets a test state the tier without seeding a plan.

    THE ROW IS LOCKED AND THEN MERGED IN PYTHON rather than upserted in SQL.
    `models_json` is a per-model breakdown that has to be added key by key, and
    expressing that as a `jsonb` expression inside an `ON CONFLICT` clause
    would put the arithmetic somewhere no test can read it. Contention is not a
    concern: a conversation turn and a scoring run cannot overlap, because
    scoring is only dispatched once the conversation is complete.
    """
    if tally.is_empty and not conversation_turns and questions_asked is None:
        return False
    try:
        async with _savepoint(session):
            row = (
                await session.execute(
                    select(AssessmentCostRecord)
                    .where(
                        AssessmentCostRecord.job_candidate_link_id == scope.link_id
                    )
                    .with_for_update()
                )
            ).scalars().first()
            if row is None:
                row = AssessmentCostRecord(
                    tenant_id=scope.tenant_id,
                    job_id=scope.job_id,
                    job_candidate_link_id=scope.link_id,
                    cost_basis=COST_BASIS_ESTIMATED,
                )
                session.add(row)
                # THE INSERT IS FLUSHED BEFORE ANYTHING READS THE ROW, because
                # every counter on it is a Python-side column `default=` and a
                # Python-side default is applied at INSERT, not at __init__. A
                # freshly constructed row therefore carries None in `calls`,
                # `input_tokens` and `estimated_cost_usd`, and `_merge` below
                # accumulates ONTO those values: `None += 1`. Restating the
                # zeros here instead would put the defaults in two places and
                # let them drift from the column that declares them, so the
                # row is made real and the model stays the one author of what
                # an empty record contains.
                await session.flush()
            if pricing_tier is None and row.pricing_tier is None:
                pricing_tier = await pricing_tier_for(session, scope.tenant_id)
            _merge(
                row,
                tally,
                conversation_turns=conversation_turns,
                questions_asked=questions_asked,
                pricing_tier=pricing_tier,
            )
            await session.flush()
            logger.info(
                "cost_telemetry.recorded link_id=%s tenant_id=%s calls=%d in=%d "
                "out=%d cached_in=%d cost_usd=%.6f basis=%s",
                scope.link_id,
                scope.tenant_id,
                row.calls,
                row.input_tokens,
                row.output_tokens,
                row.cached_input_tokens,
                float(row.estimated_cost_usd),
                row.cost_basis,
            )
    except SQLAlchemyError:
        # NARROW ON PURPOSE, AND THE WIDTH IS THE WHOLE POINT. The rule this
        # module is written to is that failing to observe must never fail the
        # work, and a database that refused the accounting write is exactly
        # that case: the assessment already happened, the savepoint has rolled
        # back, and the caller's transaction is intact. A `TypeError`, an
        # `AttributeError` or a `KeyError` is not that case. It is a defect in
        # the arithmetic below, it will fail on every call rather than on an
        # unlucky one, and catching it here turns a bug into a cost record
        # that silently never appears. Those propagate.
        #
        # `logger.exception` rather than the exception's class name: a class
        # name is enough to know something failed and not enough to fix it,
        # which is how the missing column defaults above survived a release.
        # The traceback carries identifiers and arithmetic, never content.
        logger.exception("cost_telemetry.flush_failed link_id=%s", scope.link_id)
        return False
    _drain(tally)
    return True


def _merge(
    row: AssessmentCostRecord,
    tally: UsageTally,
    *,
    conversation_turns: int,
    questions_asked: int | None,
    pricing_tier: str | None,
) -> None:
    """Add one tally's numbers onto an existing record."""
    row.calls += tally.calls
    row.calls_with_usage += tally.calls_with_usage
    row.calls_reporting_cache += tally.calls_reporting_cache
    row.input_tokens += tally.prompt_tokens
    row.output_tokens += tally.completion_tokens
    row.cached_input_tokens += tally.cached_prompt_tokens
    row.synthesis_input_tokens += tally.synthesis_prompt_tokens
    row.synthesis_output_tokens += tally.synthesis_completion_tokens
    row.synthesis_cost_usd = _money(
        row.synthesis_cost_usd + Decimal(repr(round(tally.synthesis_cost_usd, 6)))
    )
    row.estimated_cost_usd = _money(
        row.estimated_cost_usd + Decimal(repr(round(tally.estimated_cost_usd, 6)))
    )
    row.conversation_turns += max(0, int(conversation_turns))
    if questions_asked is not None:
        # An ABSOLUTE reading, and it may only go up. A later request re-reads
        # the same conversation, so taking the larger of the two is what keeps
        # a retry or an out-of-order write from walking the count backwards.
        row.questions_asked = max(row.questions_asked, int(questions_asked))
    if pricing_tier is not None and row.pricing_tier is None:
        # Written ONCE, on the first piece of spend. The tenant may change plan
        # mid-assessment, and re-stamping would report work that was done under
        # the old plan as though it had been done under the new one.
        row.pricing_tier = pricing_tier
    row.models_json = _merged_models(dict(row.models_json or {}), tally.by_model)
    row.pricing_json = _pricing_snapshot(row.models_json)


def _merged_models(
    existing: dict[str, Any], incoming: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    merged: dict[str, Any] = {key: dict(value) for key, value in existing.items()}
    for model, stats in incoming.items():
        target = merged.setdefault(
            model,
            {
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "cached_prompt_tokens": 0,
                "estimated_cost_usd": 0.0,
                "priced": stats["priced"],
                "cache_priced": stats["cache_priced"],
            },
        )
        for key in (
            "calls",
            "prompt_tokens",
            "completion_tokens",
            "cached_prompt_tokens",
        ):
            target[key] = int(target.get(key, 0)) + int(stats[key])
        target["estimated_cost_usd"] = round(
            float(target.get("estimated_cost_usd", 0.0))
            + float(stats["estimated_cost_usd"]),
            6,
        )
        target["priced"] = bool(stats["priced"])
        target["cache_priced"] = bool(stats["cache_priced"])
    return merged


def _pricing_snapshot(models: dict[str, Any]) -> dict[str, Any]:
    """The rates that were applied, for the models this record actually used.

    Snapshotted rather than joined at read time because the price table is
    explicitly expected to change: the two rows in it are carried forward from
    a previous roster and will be corrected the day a real price sheet is read.
    Without this, every total written before that edit becomes unexplainable.
    """
    return {
        "provider": PROVIDER,
        "unit": "usd_per_million_tokens",
        "basis": "list price estimate, not an invoice",
        "rates": {
            model: dict(TOKEN_PRICES_USD_PER_MILLION[model])
            for model in sorted(models)
            if model in TOKEN_PRICES_USD_PER_MILLION
        },
    }


def _money(value: Decimal) -> Decimal:
    """Quantise to the column's scale. `Numeric(14, 6)` rounds anyway; doing it
    here means the in-memory object and the stored row agree immediately rather
    than after a refresh."""
    return value.quantize(Decimal("0.000001"))


def _drain(tally: UsageTally) -> None:
    """Reset a tally that has been written, so a second flush adds nothing."""
    tally.calls = 0
    tally.calls_with_usage = 0
    tally.calls_reporting_cache = 0
    tally.prompt_tokens = 0
    tally.completion_tokens = 0
    tally.cached_prompt_tokens = 0
    tally.estimated_cost_usd = 0.0
    tally.synthesis_prompt_tokens = 0
    tally.synthesis_completion_tokens = 0
    tally.synthesis_cost_usd = 0.0
    tally.by_model = {}


# ── The owner's rollup ───────────────────────────────────────────────────────
#
# READ BY EXACTLY ONE ROUTE, `GET /admin/cost/assessments`, behind
# `get_superadmin_db`. These are operational numbers: dollars, tokens, call
# counts. Rule 1 forbids a number reaching a CLIENT, and the platform owner is
# not a client in that rule's sense -- but the boundary has to be structural
# rather than a comment, so it is the AUDIENCE that enforces it. There is no
# employer-side and no candidate-side route that reads this table, and the
# owner console's dependency is the same one `/admin/llm/stats` already uses.
#
# `VIEW_INTELLIGENCE_DASHBOARDS` is deliberately NOT what gates this. That
# capability is held by tenant users, and a per-client cost breakdown handed to
# one of those clients would show them every other client's spend.

#: An UNAVAILABLE reading serialises with no numeric key in it at all, so there
#: is nothing for a dashboard, a threshold or a diff to read a zero out of.
#:
#: The same contract `app/evaluation/metrics.Measurement` states and
#: deliberately NOT that class: `tests/test_judge_isolation.py` asserts by AST
#: that nothing under `app/services/` imports `app/evaluation/`, and a cost
#: rollup is not worth reopening that boundary for. The shape is copied; the
#: import is not.
def _unavailable(reason: str) -> dict[str, Any]:
    return {"status": "unavailable", "reason": reason}


def _available(usd: float, rate: float) -> dict[str, Any]:
    return {
        "status": "available",
        "usd": round(usd, 6),
        "inr": round(usd * rate, 2),
    }


def _average(
    total_usd: float, assessments: int, rate: float, *, what: str
) -> dict[str, Any]:
    """An average, or an honest absence. Never 0.0 over an empty set.

    Dividing by zero assessments is not a small number, it is no number, and
    reporting it as zero would put "this month was free" on a dashboard beside
    "this month had no assessments". The two need to look different because an
    operator acts on them differently.
    """
    if assessments <= 0:
        return _unavailable(f"no {what} in this window")
    return _available(total_usd / assessments, rate)


async def owner_cost_summary(
    session: AsyncSession,
    *,
    window_start: datetime_type,
    window_end: datetime_type,
    usd_to_inr: float,
    alert_threshold_inr: float,
) -> dict[str, Any]:
    """Assessment spend over one window: overall, by tier, by client.

    The window is passed in rather than computed here so the caller owns the
    definition of "this month" and a test can ask for any window without
    waiting for a calendar. Half-open, `[start, end)`, which is the only form
    that tiles a year without double-counting the instant at a boundary.

    EVERY FIGURE IS A LIST-PRICE ESTIMATE and the payload says so in place,
    exactly as `/admin/llm/stats` does. The per-token rates are unverified for
    the two model ids in use, prompt caching is counted but not discounted, and
    the rupee figures ride on a fixed FX rate from settings. The ordering these
    support -- which client, which tier, which direction the average is moving
    -- is stable under a uniform error in any of those.
    """
    from app.models.tenant import Tenant  # noqa: PLC0415

    window = (
        AssessmentCostRecord.created_at >= window_start,
        AssessmentCostRecord.created_at < window_end,
    )

    totals = (
        await session.execute(
            select(
                func.count(AssessmentCostRecord.id),
                func.coalesce(func.sum(AssessmentCostRecord.estimated_cost_usd), 0),
                func.coalesce(func.sum(AssessmentCostRecord.synthesis_cost_usd), 0),
                func.coalesce(func.sum(AssessmentCostRecord.calls), 0),
                func.coalesce(func.sum(AssessmentCostRecord.calls_with_usage), 0),
                func.coalesce(
                    func.sum(AssessmentCostRecord.calls_reporting_cache), 0
                ),
                func.coalesce(func.sum(AssessmentCostRecord.input_tokens), 0),
                func.coalesce(func.sum(AssessmentCostRecord.output_tokens), 0),
                func.coalesce(func.sum(AssessmentCostRecord.cached_input_tokens), 0),
            ).where(*window)
        )
    ).one()
    (
        assessments,
        total_usd,
        synthesis_usd,
        calls,
        calls_with_usage,
        calls_reporting_cache,
        input_tokens,
        output_tokens,
        cached_input_tokens,
    ) = totals

    tier_rows = (
        await session.execute(
            select(
                AssessmentCostRecord.pricing_tier,
                func.count(AssessmentCostRecord.id),
                func.coalesce(func.sum(AssessmentCostRecord.estimated_cost_usd), 0),
            )
            .where(*window)
            .group_by(AssessmentCostRecord.pricing_tier)
            .order_by(AssessmentCostRecord.pricing_tier)
        )
    ).all()

    client_rows = (
        await session.execute(
            select(
                AssessmentCostRecord.tenant_id,
                Tenant.name,
                func.count(AssessmentCostRecord.id),
                func.coalesce(func.sum(AssessmentCostRecord.estimated_cost_usd), 0),
            )
            .join(Tenant, Tenant.id == AssessmentCostRecord.tenant_id)
            .where(*window)
            .group_by(AssessmentCostRecord.tenant_id, Tenant.name)
            .order_by(
                func.coalesce(
                    func.sum(AssessmentCostRecord.estimated_cost_usd), 0
                ).desc(),
                Tenant.name,
            )
        )
    ).all()

    average = _average(
        float(total_usd), int(assessments), usd_to_inr, what="assessments"
    )
    return {
        "window": {
            "start": window_start.isoformat(),
            "end": window_end.isoformat(),
            "bounds": "half open, [start, end)",
        },
        "assessments": int(assessments),
        "total_spend": _available(float(total_usd), usd_to_inr),
        "synthesis_spend": _available(float(synthesis_usd), usd_to_inr),
        "average_per_assessment": average,
        "alert": _alert(average, alert_threshold_inr),
        "by_pricing_tier": [
            {
                # None is a REAL state: a demonstration tenant and a customer
                # between subscriptions both legitimately have no plan, and
                # they are reported under their own heading rather than folded
                # into whichever tier happened to sort first.
                "pricing_tier": tier,
                "assessments": int(count),
                "total_spend": _available(float(spend), usd_to_inr),
                "average_per_assessment": _average(
                    float(spend), int(count), usd_to_inr, what="assessments"
                ),
            }
            for tier, count, spend in tier_rows
        ],
        "by_client": [
            {
                "tenant_id": str(tenant_id),
                "tenant_name": name,
                "assessments": int(count),
                "total_spend": _available(float(spend), usd_to_inr),
                "average_per_assessment": _average(
                    float(spend), int(count), usd_to_inr, what="assessments"
                ),
            }
            for tenant_id, name, count, spend in client_rows
        ],
        # HOW MUCH OF THE TOTAL WAS ACTUALLY MEASURED. Without this the figures
        # above are unreadable: a total built from calls that reported no usage
        # is a character heuristic wearing a dollar sign, and nothing else on
        # this payload would say so.
        "coverage": {
            "calls": int(calls),
            "calls_with_usage": int(calls_with_usage),
            "calls_reporting_cache": int(calls_reporting_cache),
            "input_tokens": int(input_tokens),
            "output_tokens": int(output_tokens),
            "cached_input_tokens": int(cached_input_tokens),
        },
        "prompt_cache": {
            "cached_input_tokens": int(cached_input_tokens),
            "calls_reporting_cache": int(calls_reporting_cache),
            "saving": _unavailable(
                "no separate cached-input rate is on file for either model, so "
                "a cached token is priced at the full uncached rate"
            ),
        },
        # NOT MEASURABLE PER ASSESSMENT, and absent rather than zero. The
        # analysis service is a standing ECS service with a flat bill and the
        # vendor invoices per account per month. A zero here would read as
        # "this assessment used no media", which for a video interview is
        # simply false.
        "media_cost": _unavailable(
            "the analysis service is billed per account, not per assessment"
        ),
        "proctoring_cost": _unavailable(
            "proctoring runs in the candidate's browser and stores no media, "
            "so it carries no per-assessment vendor cost to report"
        ),
        "fx": {
            "usd_to_inr": usd_to_inr,
            "basis": "a fixed rate from settings, so every INR figure is approximate",
        },
        "cost_basis": "list price estimate, not an invoice",
    }


def _alert(average: dict[str, Any], threshold_inr: float) -> dict[str, Any]:
    """Whether the month's average crossed the owner's line.

    UNKNOWN IS A THIRD OUTCOME HERE, not a rounding of False. An average that
    could not be computed has not been shown to be under the threshold, and
    answering `exceeded: false` would put a green tick on a month nobody
    measured -- the same failure `release_gate` refuses when it declines to
    treat an uncomputable metric as a pass.
    """
    if average.get("status") != "available":
        return {
            "threshold_inr": threshold_inr,
            "exceeded": None,
            "reason": average.get("reason", "the average could not be computed"),
        }
    return {
        "threshold_inr": threshold_inr,
        "exceeded": float(average["inr"]) > threshold_inr,
        "average_inr": average["inr"],
    }


async def pricing_tier_for(
    session: AsyncSession, tenant_id: uuid.UUID
) -> str | None:
    """The tenant's current plan slug, or None when they are on no plan.

    None is a real state and is not a failure: a demonstration tenant and a
    customer between subscriptions both legitimately have no plan, and the
    dashboard reports them under an explicit "no plan" heading rather than
    inventing one. Read through the ORM rather than joined into the cost query
    so the value is SNAPSHOTTED onto the record: a customer who upgrades next
    month must not retroactively change which tier last month's assessments are
    reported under.
    """
    from app.models.billing import PricingPlan  # noqa: PLC0415
    from app.models.tenant import Tenant  # noqa: PLC0415

    slug = (
        await session.execute(
            select(PricingPlan.slug)
            .join(Tenant, Tenant.current_plan_id == PricingPlan.id)
            .where(Tenant.id == tenant_id)
        )
    ).scalars().first()
    return str(slug) if slug else None


async def finalize(
    session: AsyncSession,
    *,
    link_id: uuid.UUID,
    media_cost_usd: Decimal | None = None,
    proctoring_cost_usd: Decimal | None = None,
    note: str | None = None,
) -> bool:
    """Write real figures over the estimate for one application.

    Nothing calls this yet, and that is deliberate rather than an oversight.
    The two costs it exists to carry are not measurable per assessment today:
    the analysis service is a standing ECS service with a flat bill and the
    vendor's invoice arrives per account per month, not per candidate. What
    this provides is the SHAPE that makes those figures writable the day
    somebody has them, without a migration and without the estimate having been
    presented as a fact in the meantime.

    A None argument leaves its column alone, so the two costs can be settled
    independently. `cost_basis` only flips to `finalized` when at least one real
    figure has actually been written.
    """
    row = (
        await session.execute(
            select(AssessmentCostRecord).where(
                AssessmentCostRecord.job_candidate_link_id == link_id
            )
        )
    ).scalars().first()
    if row is None:
        return False
    if media_cost_usd is not None:
        row.media_cost_usd = _money(media_cost_usd)
    if proctoring_cost_usd is not None:
        row.proctoring_cost_usd = _money(proctoring_cost_usd)
    if note is not None:
        row.notes = note
    if media_cost_usd is not None or proctoring_cost_usd is not None:
        row.cost_basis = COST_BASIS_FINALIZED
        row.finalized_at = datetime_type.now(timezone.utc)
    await session.flush()
    return True
