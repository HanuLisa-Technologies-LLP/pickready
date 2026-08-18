"""Ceilings and graceful failure for work made of several bounded loops.

`agent_loop` bounds ONE loop and `llm_router` bounds one provider chain. This
package bounds the TASK above them -- what a report costs, how many times a plan
may be revised, and what a caller receives when none of it worked.
"""
from __future__ import annotations

from app.services.reliability import budget, degradation
from app.services.reliability.budget import (
    HARD_COST_CEILING_USD,
    MAX_ITERATIONS,
    MAX_REPLANS,
    Budget,
    BudgetExceeded,
)
from app.services.reliability.degradation import (
    LEVEL_DEGRADED,
    LEVEL_FULL,
    LEVEL_STUB,
    Outcome,
    with_fallbacks,
)

__all__ = [
    "Budget",
    "BudgetExceeded",
    "HARD_COST_CEILING_USD",
    "LEVEL_DEGRADED",
    "LEVEL_FULL",
    "LEVEL_STUB",
    "MAX_ITERATIONS",
    "MAX_REPLANS",
    "Outcome",
    "budget",
    "degradation",
    "with_fallbacks",
]
