"""plan -> retrieve -> execute -> observe -> reflect -> verify, for a whole task.

WHERE THIS SITS
---------------
`agent_loop.run_loop` is the bounded retry around ONE generative call, and it is
not replaced here. `run_task` is the stage machine ABOVE it: it plans, gathers
context, calls the caller's generative step (which will normally be a
`run_loop`), applies a domain critic, and records the whole thing as one trace.

The division is what keeps both simple. The loop knows about attempts and
deadlines and nothing about jobs. The runner knows about stages, budgets and
verifiers and nothing about providers.

WHY REFLECTION IS STILL NOT AN LLM
-----------------------------------
The specification puts a reflection node in the graph and it is here, but it
reads the verifier's findings and turns them into an instruction mechanically --
`agent_loop.reflection_text`, unchanged. Asking a model to critique its own
output is one more call that fails exactly when the provider is already failing,
and it makes the criteria unfalsifiable. The reflection is real; it is just not
generative.

WHAT REPLANNING ACTUALLY DOES
------------------------------
It re-executes with the findings appended, bounded by `Budget.max_replans`. It
does not invent a new strategy, because there is no second strategy to invent:
the deterministic criteria did not change, so the only lever is telling the
model precisely what it got wrong. That is the same insight `agent_loop` was
built on, applied one level up.

NOTHING HERE RAISES
-------------------
Same contract as the loop, for the same reason. A candidate is mid-assessment or
a recruiter is opening a report; the correct answer to a failure is the
product's previous behaviour plus an honest record, not a 500. The record is the
`Outcome` level and the persisted trace.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Generic, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import agent_loop
from app.services.memory import experience
from app.services.memory.working import WorkingMemory
from app.services.observability import trace as tracing
from app.services.reliability import budget as budgeting
from app.services.reliability import degradation
from app.services.verification import base as verification

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: A verifier that accepts everything. The default, so a caller that has no
#: domain critic yet still gets planning, tracing and budgeting -- rather than
#: being pushed to write a fake critic to opt in.
def _accept_all(_value: Any) -> verification.Verdict:
    return verification.verdict("none", [])


@dataclass
class TaskResult(Generic[T]):
    """The value, how it was produced, and the trace that explains it."""

    outcome: degradation.Outcome[T]
    trace: tracing.RequestTrace
    verdict: verification.Verdict | None = None
    memory: WorkingMemory | None = None

    @property
    def value(self) -> T:
        return self.outcome.value

    @property
    def degraded(self) -> bool:
        return self.outcome.degraded


async def run_task(
    *,
    plan: Any,
    execute: Callable[[str], Awaitable[T]],
    fallback: T,
    verify: Callable[[T], verification.Verdict] = _accept_all,
    retrieve: Callable[[], Awaitable[Any]] | None = None,
    session: AsyncSession | None = None,
    tenant_id: Any = None,
    job_id: Any = None,
    link_id: Any = None,
    budget: budgeting.Budget | None = None,
) -> TaskResult[T]:
    """Run one task through every stage its plan calls for.

    `execute` receives the accumulated instruction string -- empty on the first
    attempt, then the verifier's findings verbatim on each replan -- which is
    exactly the signature `agent_loop.run_loop` already hands its own `execute`.
    A caller can therefore pass the same function to both.
    """
    trace = tracing.RequestTrace(
        agent_type=plan.agent_type,
        task_type=plan.task_type,
        tenant_id=tenant_id,
        job_id=job_id,
        link_id=link_id,
        complexity=plan.complexity,
        fast_path=plan.fast_path,
    )
    memory = WorkingMemory()
    memory.put("plan", plan.as_dict(), stage="plan")
    budget = budget or budgeting.Budget(plan.task_type)

    trace.start("plan")
    trace.end(count=len(plan.order))

    # ── retrieve ─────────────────────────────────────────────────────────────
    if retrieve is not None:
        trace.start("retrieve")
        try:
            context = await retrieve()
            memory.put("context", context, stage="retrieve")
            trace.end(count=len(getattr(context, "chunks", ()) or ()))
        except Exception as exc:  # noqa: BLE001
            # Retrieval failing is a degradation, never a task failure. The
            # generative step has a JD and a resume regardless; what it loses is
            # the sharpest evidence, not all evidence.
            logger.warning(
                "reasoning.retrieval_failed task=%s err=%s",
                plan.task_type,
                type(exc).__name__,
            )
            trace.end(status="degraded")
            trace.note(stage="retrieve", status="degraded", detail_code="retrieval_failed")

    # ── execute / observe / reflect / verify ─────────────────────────────────
    instruction = _initial_instruction(plan)
    verdict: verification.Verdict | None = None
    value: T | None = None
    skipped: list[str] = []
    reasons: list[str] = []

    while True:
        try:
            budget.begin_iteration()
        except budgeting.BudgetExceeded as exc:
            trace.error = str(exc)
            reasons.append(str(exc))
            break

        trace.start("execute")
        try:
            value = await execute(instruction)
            trace.attempts += 1
            trace.end()
        except Exception as exc:  # noqa: BLE001
            trace.attempts += 1
            trace.end(status="error")
            trace.error = f"{type(exc).__name__}: {exc}"
            reasons.append(trace.error)
            value = None
            break

        trace.start("observe")
        trace.generated_tokens += _estimate(value)
        trace.end()

        trace.start("verify")
        verdict = verify(value)
        trace.add_defects([finding.as_defect() for finding in verdict.findings])
        trace.confidence = verdict.confidence
        trace.end(status="ok" if verdict.passed else "rejected", count=len(verdict.findings))

        if verdict.passed:
            break

        if plan.fast_path:
            # The fast path's whole point. Reflection is what it trades away, so
            # a rejection on it is reported rather than retried.
            skipped.extend(["reflect", "replan"])
            reasons.extend(verdict.to_critique().reasons)
            break

        trace.start("reflect")
        instruction = agent_loop.reflection_text(list(verdict.to_critique().reasons))
        trace.end(count=len(verdict.findings))

        try:
            budget.begin_replan()
        except budgeting.BudgetExceeded as exc:
            reasons.append(str(exc))
            reasons.extend(verdict.to_critique().reasons)
            break

    # ── outcome ──────────────────────────────────────────────────────────────
    if value is not None and verdict is not None and verdict.passed:
        outcome = degradation.full(value, confidence=verdict.confidence)
    elif value is not None:
        outcome = degradation.degraded(
            value,
            reasons=tuple(reasons),
            skipped=tuple(skipped),
            confidence=verdict.confidence if verdict else None,
        )
        trace.degraded = True
    else:
        outcome = degradation.stub(fallback, reasons=tuple(reasons))
        trace.degraded = True

    memory.put("outcome", outcome.as_dict(), stage="verify")
    trace.note(stage="budget", status="ok", count=int(budget.spent_usd * 1_000_000))
    trace.log()

    if session is not None:
        await tracing.persist(session, trace)
        await _learn(session, plan, verdict, outcome, instruction)

    return TaskResult(outcome=outcome, trace=trace, verdict=verdict, memory=memory)


def _initial_instruction(plan: Any) -> str:
    """Learned hints, as guidance, on the first attempt.

    Prepended as plain instruction text. It cannot relax a criterion because the
    criteria are code the verifier runs afterwards, and the verifier does not
    read this string.
    """
    if not getattr(plan, "hints", ()):
        return ""
    bullets = "\n".join(f"- {hint}" for hint in plan.hints)
    return (
        "Apply these lessons from previous attempts at this task:\n" + bullets
    )


def _estimate(value: Any) -> int:
    import json
    import math

    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        serialized = str(value)
    return max(1, math.ceil(len(serialized) / 4))


async def _learn(
    session: AsyncSession,
    plan: Any,
    verdict: verification.Verdict | None,
    outcome: degradation.Outcome,
    instruction: str,
) -> None:
    """Turn this run into experience memory, when there is anything to learn.

    Only records a pattern that came with an actual instruction, because a
    learning whose fix is an empty string is a row that can never help anything.
    Never raises: a task must not fail because a lesson could not be written.
    """
    if verdict is None or not verdict.findings or not instruction.strip():
        return
    try:
        pattern = verdict.findings[0].issue
        await experience.record_failure(
            session,
            agent_type=plan.agent_type,
            task_type=plan.task_type,
            pattern=pattern,
            fix=verdict.findings[0].recommendation,
        )
        if not outcome.degraded:
            await experience.record_success(
                session,
                agent_type=plan.agent_type,
                task_type=plan.task_type,
                pattern=pattern,
            )
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        logger.warning("reasoning.learning_failed err=%s", type(exc).__name__)
