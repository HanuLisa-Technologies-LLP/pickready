"""Cost and iteration ceilings, and the stop conditions that make loops finite.

WHY A COST BUDGET WHEN THE LOOPS ARE ALREADY BOUNDED
-----------------------------------------------------
`agent_loop` bounds one loop by attempts, wall clock and generated tokens. That
is not the same as bounding a TASK, because a task runs several loops: one
report is a technical pass, a PPI pass, a synthesis and a gap analysis, each
individually well-behaved. Nothing before this module could answer "what did
that report cost", which means nothing could refuse when the answer got silly.

THE CEILING IS PER TASK TYPE AND IS DELIBERATELY GENEROUS
----------------------------------------------------------
Roughly an order of magnitude above the observed cost of a healthy run. A budget
set near the median converts an unusually long JD into a failure, which is worse
than the overspend it prevents. This exists to stop a runaway, not to shave
margins -- one credit is charged whatever the tokens cost, so the product's
economics do not turn on this number.

STOP CONDITIONS ARE SEPARATE FROM BUDGET AND BOTH ARE NEEDED
--------------------------------------------------------------
A loop can spin without spending: replanning that keeps producing the same plan
costs iterations, not dollars. `MAX_ITERATIONS` and `MAX_REPLANS` bound that,
and they are checked BEFORE work rather than after, so the last iteration that
would exceed the ceiling is never started.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: Cost ceiling per task type, in USD. Passing it is an operational alarm, not a
#: normal outcome.
COST_BUDGET_USD: dict[str, float] = {
    "ranking": 0.15,
    "ppi_report": 0.24,
    "email": 0.06,
    "probe": 0.09,
    "interviewer": 0.05,
    "job_setup": 0.30,
    # Per ANSWER, not per assessment. Miti runs once for every reply a candidate
    # sends, so the ceiling that matters is the small one multiplied by up to
    # ~28 turns rather than a single generous allowance.
    "scoring": 0.04,
}
DEFAULT_COST_BUDGET_USD = 0.20

#: The absolute backstop, whatever the task type claims. Nothing in this product
#: legitimately costs a dollar in one task.
HARD_COST_CEILING_USD = 1.0

#: Iterations of the outer task loop, and replans within it. Three replans is
#: already a task that is not converging; a fourth is a loop.
MAX_ITERATIONS = 10
MAX_REPLANS = 3


class BudgetExceeded(RuntimeError):
    """A ceiling was reached. Carries which one, because they mean different things."""

    def __init__(self, kind: str, detail: str) -> None:
        super().__init__(f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


@dataclass
class Budget:
    """A task's remaining allowance, checked before work rather than after."""

    task_type: str
    cost_limit_usd: float = 0.0
    spent_usd: float = 0.0
    iterations: int = 0
    replans: int = 0
    max_iterations: int = MAX_ITERATIONS
    max_replans: int = MAX_REPLANS
    #: Every refusal, for the trace. A budget that stopped something without
    #: recording it is indistinguishable from a task that simply finished.
    refusals: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.cost_limit_usd:
            self.cost_limit_usd = COST_BUDGET_USD.get(
                self.task_type, DEFAULT_COST_BUDGET_USD
            )
        self.cost_limit_usd = min(self.cost_limit_usd, HARD_COST_CEILING_USD)

    @property
    def remaining_usd(self) -> float:
        return round(max(0.0, self.cost_limit_usd - self.spent_usd), 6)

    @property
    def exhausted(self) -> bool:
        return self.remaining_usd <= 0

    def spend(self, amount_usd: float) -> None:
        self.spent_usd = round(self.spent_usd + max(0.0, amount_usd), 6)

    def check(self, *, estimated_usd: float = 0.0) -> None:
        """Refuse BEFORE the work, given what it is expected to cost.

        Checking after would mean the overspend has already happened and the
        ceiling is a report rather than a limit.
        """
        if self.iterations >= self.max_iterations:
            self._refuse("iterations", f"{self.iterations} iterations reached the ceiling")
        if self.replans >= self.max_replans:
            self._refuse("replans", f"{self.replans} replans reached the ceiling")
        if self.spent_usd + estimated_usd > self.cost_limit_usd:
            self._refuse(
                "cost",
                f"{self.spent_usd + estimated_usd:.4f} USD would exceed the "
                f"{self.cost_limit_usd:.4f} USD ceiling for {self.task_type}",
            )

    def _refuse(self, kind: str, detail: str) -> None:
        self.refusals.append(f"{kind}: {detail}")
        raise BudgetExceeded(kind, detail)

    def begin_iteration(self, *, estimated_usd: float = 0.0) -> None:
        self.check(estimated_usd=estimated_usd)
        self.iterations += 1

    def begin_replan(self) -> None:
        self.check()
        self.replans += 1

    def as_dict(self) -> dict[str, float | int | list[str]]:
        return {
            "task_type": self.task_type,
            "cost_limit_usd": self.cost_limit_usd,
            "spent_usd": self.spent_usd,
            "remaining_usd": self.remaining_usd,
            "iterations": self.iterations,
            "replans": self.replans,
            "refusals": list(self.refusals),
        }
