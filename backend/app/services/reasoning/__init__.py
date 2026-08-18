"""The stage machine above `agent_loop`: plan, retrieve, execute, verify.

Two rules survive from the loop and are the reason this is an extension rather
than a replacement: nothing generative decides control flow, and nothing here
raises. The planner is arithmetic, the reflection is mechanical, and a failure
becomes an honest `Outcome` plus a persisted trace.
"""
from __future__ import annotations

from app.services.reasoning import planner, runner
from app.services.reasoning.planner import (
    COMPLEXITY_COMPLEX,
    COMPLEXITY_MODERATE,
    COMPLEXITY_SIMPLE,
    COMPLEXITY_THRESHOLD_SIMPLE,
    Plan,
    Subtask,
    classify,
    complexity_score,
    plan,
)
from app.services.reasoning.runner import TaskResult, run_task

__all__ = [
    "COMPLEXITY_COMPLEX",
    "COMPLEXITY_MODERATE",
    "COMPLEXITY_SIMPLE",
    "COMPLEXITY_THRESHOLD_SIMPLE",
    "Plan",
    "Subtask",
    "TaskResult",
    "classify",
    "complexity_score",
    "plan",
    "planner",
    "run_task",
    "runner",
]
