"""Ceilings for work made of several bounded loops.

`agent_loop` bounds ONE loop and `llm_router` bounds one provider chain. This
package bounds the TASK above them: what a report costs and how many times a
plan may be revised.

The `degradation` module (full, degraded, stub) was deleted in the Vivekium
release: its only callers were the unreachable reasoning runner and an eval
case. A caller that needs a fallback states it where the fallback happens
(`agent_loop.run_loop` returns `degraded=True`), so there is still exactly one
record of a degradation.
"""
from __future__ import annotations

from app.services.reliability import budget
from app.services.reliability.budget import (
    HARD_COST_CEILING_USD,
    MAX_ITERATIONS,
    MAX_REPLANS,
    Budget,
    BudgetExceeded,
)

__all__ = [
    "Budget",
    "BudgetExceeded",
    "HARD_COST_CEILING_USD",
    "MAX_ITERATIONS",
    "MAX_REPLANS",
    "budget",
]
