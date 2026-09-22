"""What an evaluator is handed, and the two answers it is allowed to invent.

WHY EVERY EVALUATOR TAKES THE WHOLE RUN
-----------------------------------------
HARNESS.md section 7 asks for evaluators that are "layered, independent,
deterministic wherever deterministic is possible", and that a single pass/fail
number hides which dimension moved. Independence here means they do not read
each other's answers; it does not mean each one sees a slice. `tenancy_isolation`
needs the world (how many tenants are there), the observations (what came back)
and the reader (what does the database actually hold), and an input type that
handed it only the observations would force it to infer the other two.

THE TWO HELPERS, AND WHY `unavailable` HAS ITS OWN
----------------------------------------------------
`passed` and `failed` build a verdict; `cannot_compute` builds an
`unavailable`. There is deliberately no helper that turns a missing measurement
into a zero, and `EvaluationOutcome` refuses a score on an unavailable outcome
anyway. `app/evaluation/metrics.py` already raises `InsufficientData` for this
reason and `release_gate.py` already states it: "a metric reported as 0.0, or
waved through as a pass, is how a broken harness releases everything while
showing green".

A SCORE COMES WITH ITS INTERVAL OR NOT AT ALL
-----------------------------------------------
`scored` takes a count of successes out of a total and builds a Wilson interval
from `metrics.py`. Four checks all passing is not evidence of a 100% pass rate,
and the interval is what says so. Nothing here computes a point estimate by
hand.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.evaluation.metrics import InsufficientData, wilson_interval

from harness.context import ScenarioContext, StateReader
from harness.run import FAIL, PASS, UNAVAILABLE, Check, EvaluationOutcome
from harness.scenario import Scenario

__all__ = [
    "EvaluationInput",
    "cannot_compute",
    "failed",
    "passed",
    "scored",
    "verdict",
]


@dataclass(frozen=True)
class EvaluationInput:
    """Everything one evaluator may read, and nothing it may write.

    Frozen, and the `checks` are a tuple, because an evaluator that could
    append a check would be changing the ground truth it is being asked to
    judge against. The one thing an evaluator produces is its own outcome.
    """

    scenario: Scenario
    ctx: ScenarioContext
    checks: tuple[Check, ...]
    reader: StateReader | None
    faults_applied: tuple[str, ...]

    def of_kind(self, kind: str) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if check.kind == kind)

    @property
    def server_errors(self) -> tuple[str, ...]:
        return tuple(
            f"{item.method} {item.path} answered {item.status}"
            for item in self.ctx.observations
            if item.status >= 500
        )


def passed(name: str, reason: str, **details: Any) -> EvaluationOutcome:
    return EvaluationOutcome(
        evaluator=name, outcome=PASS, reason=reason, details=dict(details)
    )


def failed(name: str, reason: str, **details: Any) -> EvaluationOutcome:
    return EvaluationOutcome(
        evaluator=name, outcome=FAIL, reason=reason, details=dict(details)
    )


def cannot_compute(name: str, reason: str, **details: Any) -> EvaluationOutcome:
    """An evaluator that cannot compute reports `unavailable`, never `0.0`."""
    return EvaluationOutcome(
        evaluator=name, outcome=UNAVAILABLE, reason=reason, details=dict(details)
    )


def scored(
    name: str,
    *,
    successes: int,
    total: int,
    reason: str,
    failing: bool,
    **details: Any,
) -> EvaluationOutcome:
    """A verdict carrying a proportion and the interval that qualifies it.

    `failing` is decided by the CALLER rather than derived from the proportion,
    because the two are genuinely different questions: a dimension may be a
    failure at one miss out of fifty (a tenant boundary) and acceptable at ten
    out of fifty (a latency budget), and a threshold buried in here would apply
    one answer to both.
    """
    try:
        interval = wilson_interval(successes, total)
    except InsufficientData as exc:
        return cannot_compute(name, f"{reason} ({exc})", **details)
    return EvaluationOutcome(
        evaluator=name,
        outcome=FAIL if failing else PASS,
        reason=reason,
        score=successes / total,
        interval=(interval.low, interval.high),
        sample_size=total,
        details=dict(details),
    )


def verdict(
    name: str,
    checks: Sequence[Check],
    *,
    nothing_to_judge: str,
    all_held: str,
    some_failed: str,
    **details: Any,
) -> EvaluationOutcome:
    """The common shape: score a set of checks, or say there were none.

    NO CHECKS IS `unavailable` AND NOT A PASS. A scenario that named an
    evaluator and gave it nothing to judge has measured nothing, and reporting
    that as a pass is the exact failure `report._check_pass_rate` already
    refuses for the same input.
    """
    if not checks:
        return cannot_compute(name, nothing_to_judge, **details)
    failures = [check for check in checks if not check.passed]
    return scored(
        name,
        successes=len(checks) - len(failures),
        total=len(checks),
        failing=bool(failures),
        reason=(
            all_held
            if not failures
            else f"{some_failed}: "
            + "; ".join(check.expression for check in failures[:5])
        ),
        **details,
    )
