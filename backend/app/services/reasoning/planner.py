"""Deciding, before any work happens, how much work this task deserves.

WHY THE PLANNER CALLS NO MODEL
-------------------------------
Asking a model to plan means a provider outage costs you the ability to decide
what to do about a provider outage. It also makes the plan unreproducible: the
same task planned twice takes two different paths, so a latency regression
cannot be separated from a sampling difference. Complexity here is arithmetic
over features that are already known -- how many candidates, how long the JD,
which grade -- and the same inputs always produce the same plan.

WHAT COMPLEXITY IS ACTUALLY FOR
--------------------------------
One decision: fast path or deep path. Everything else the score is used for is
reporting. A fast path skips reflection and replanning, which are worth roughly
a second and a second model call, and are worth nothing at all on a task whose
output has one field. Spending them anyway is how an email draft ends up on the
same latency budget as a report.

THE THRESHOLD IS DELIBERATELY LOW
----------------------------------
0.3, so most real work takes the deep path. The asymmetry is the reason: a fast
path on a task that needed reflection produces a worse report, permanently,
while a deep path on a simple task costs a second. Under uncertainty, spend the
second.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Below this, and under the token estimate, a task takes the fast path.
COMPLEXITY_THRESHOLD_SIMPLE = 0.3
TOKEN_THRESHOLD_SIMPLE = 2000

COMPLEXITY_SIMPLE = "simple"
COMPLEXITY_MODERATE = "moderate"
COMPLEXITY_COMPLEX = "complex"

#: Base difficulty per task type, before the task's own inputs are considered.
#: A report is intrinsically harder than an email no matter how short the JD is.
_BASE_COMPLEXITY: dict[str, float] = {
    "email": 0.05,
    "ranking": 0.35,
    "probe": 0.4,
    "interviewer": 0.3,
    "ppi_report": 0.6,
    "job_setup": 0.55,
}
_DEFAULT_BASE = 0.4

#: A CXO assessment has fewer questions and more judgement per question.
_GRADE_WEIGHT: dict[str, float] = {
    "non_managerial": 0.0,
    "managerial": 0.05,
    "leadership": 0.1,
    "cxo": 0.15,
}


@dataclass(frozen=True)
class Subtask:
    name: str
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class Plan:
    """What will be done, in what order, and how much it is allowed to cost."""

    task_type: str
    agent_type: str
    complexity_score: float
    complexity: str
    fast_path: bool
    subtasks: tuple[Subtask, ...] = ()
    estimated_tokens: int = 0
    #: Trustworthy fixes from experience memory, prepended to the prompt as
    #: guidance. Never a gate: see `services.memory.experience`.
    hints: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default=())

    @property
    def order(self) -> tuple[str, ...]:
        return tuple(subtask.name for subtask in self.subtasks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "agent_type": self.agent_type,
            "complexity": self.complexity,
            "complexity_score": self.complexity_score,
            "fast_path": self.fast_path,
            "order": list(self.order),
            "estimated_tokens": self.estimated_tokens,
            "hint_count": len(self.hints),
        }


def _topological(subtasks: list[Subtask]) -> tuple[Subtask, ...]:
    """Order by dependency, stably.

    Stable because two runs of the same task must produce the same order: a plan
    that reorders between runs makes a trace comparison meaningless. A cycle
    yields the remaining nodes in declaration order rather than raising -- the
    subtask lists here are written by hand and short, and failing a live task
    over an authoring mistake is worse than running it in a defensible order.
    """
    resolved: list[Subtask] = []
    done: set[str] = set()
    remaining = list(subtasks)
    while remaining:
        ready = [item for item in remaining if set(item.depends_on) <= done]
        if not ready:
            resolved.extend(remaining)
            break
        for item in ready:
            resolved.append(item)
            done.add(item.name)
        remaining = [item for item in remaining if item not in ready]
    return tuple(resolved)


def complexity_score(
    task_type: str,
    *,
    candidate_count: int = 1,
    jd_chars: int = 0,
    grade: str | None = None,
    competency_count: int = 0,
    transcript_exchanges: int = 0,
) -> float:
    """A bounded 0-1 score from features that are known before any work starts."""
    score = _BASE_COMPLEXITY.get(task_type, _DEFAULT_BASE)
    # Fan-out. Logarithmic in spirit but written as bands, because the exact
    # curve is unjustifiable and the bands are reviewable.
    if candidate_count > 50:
        score += 0.2
    elif candidate_count > 20:
        score += 0.12
    elif candidate_count > 5:
        score += 0.05

    if jd_chars > 8000:
        score += 0.12
    elif jd_chars > 3000:
        score += 0.06

    score += _GRADE_WEIGHT.get(str(grade or ""), 0.0)

    if competency_count > 15:
        score += 0.08
    if transcript_exchanges > 30:
        score += 0.08

    return round(max(0.0, min(1.0, score)), 4)


def classify(score: float) -> str:
    if score < COMPLEXITY_THRESHOLD_SIMPLE:
        return COMPLEXITY_SIMPLE
    if score < 0.6:
        return COMPLEXITY_MODERATE
    return COMPLEXITY_COMPLEX


def estimate_tokens(task_type: str, *, jd_chars: int = 0, transcript_chars: int = 0) -> int:
    """Conservative input estimate, four characters per token.

    The same estimate `agent_loop` and `rag.context` use. Being consistently
    approximate is what makes two budgets comparable; being separately precise
    would not.
    """
    overhead = {"email": 400, "ranking": 900, "ppi_report": 1400}.get(task_type, 800)
    return overhead + (jd_chars + transcript_chars) // 4


#: The stages each task type actually runs. Written out rather than derived, so
#: the plan for a task is something a person can read in one place.
_SUBTASKS: dict[str, tuple[Subtask, ...]] = {
    "ranking": (
        Subtask("extract_jd"),
        Subtask("retrieve_context", ("extract_jd",)),
        Subtask("score_candidates", ("extract_jd", "retrieve_context")),
        Subtask("verify", ("score_candidates",)),
    ),
    "ppi_report": (
        Subtask("extract_jd"),
        Subtask("extract_framework", ("extract_jd",)),
        Subtask("extract_assessment"),
        Subtask("retrieve_context", ("extract_assessment",)),
        Subtask("score_items", ("extract_framework", "retrieve_context")),
        Subtask("synthesise", ("score_items",)),
        Subtask("verify", ("synthesise",)),
    ),
    "probe": (
        Subtask("extract_framework"),
        Subtask("extract_assessment"),
        Subtask("write_probes", ("extract_framework", "extract_assessment")),
        Subtask("verify", ("write_probes",)),
    ),
    "email": (Subtask("draft"), Subtask("verify", ("draft",))),
}


def plan(
    task_type: str,
    agent_type: str,
    *,
    hints: tuple[str, ...] = (),
    **features: Any,
) -> Plan:
    """Build the plan. Pure: no I/O, no model call, same inputs same output."""
    score = complexity_score(task_type, **features)
    tokens = estimate_tokens(
        task_type,
        jd_chars=int(features.get("jd_chars") or 0),
        transcript_chars=int(features.get("transcript_chars") or 0),
    )
    subtasks = _topological(list(_SUBTASKS.get(task_type, ())))

    fast = score < COMPLEXITY_THRESHOLD_SIMPLE and tokens < TOKEN_THRESHOLD_SIMPLE
    notes: list[str] = []
    if fast:
        notes.append("fast path: reflection and replanning are skipped")
    if hints:
        notes.append(f"{len(hints)} learned hint(s) applied as guidance")

    return Plan(
        task_type=task_type,
        agent_type=agent_type,
        complexity_score=score,
        complexity=classify(score),
        fast_path=fast,
        subtasks=subtasks,
        estimated_tokens=tokens,
        hints=hints,
        notes=tuple(notes),
    )
