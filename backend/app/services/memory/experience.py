"""Lessons extracted from failures, retrieved as hints on later attempts.

HOW A LEARNING IS BORN
----------------------
A loop rejects an attempt with a typed defect, a later attempt at the SAME task
type succeeds, and the instruction that closed the gap is worth keeping. That is
`record_success`: it upgrades an observation into something with a success rate
attached, so a fix that stops working stops being applied.

WHY IT IS A HINT AND NEVER A GATE
----------------------------------
`hints_for` returns text to prepend to a prompt. Nothing here can relax a word
range, skip a verifier or lower a threshold. A mechanism that could would let
one unlucky run permanently lower the bar for every run after it, and it would
do so invisibly, because the code implementing the lowered bar would be a row in
a table rather than a line anybody reviews.

MINIMUM EVIDENCE BEFORE ANYTHING IS APPLIED
--------------------------------------------
A pattern seen once is an anecdote. `MIN_OBSERVATIONS` and `MIN_SUCCESS_RATE`
are what stop the framework from learning superstitions from a single provider
blip.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

MIN_OBSERVATIONS = 3
MIN_SUCCESS_RATE = 0.5
MAX_HINTS = 3


@dataclass(frozen=True)
class Learning:
    failure_pattern: str
    applied_fix: str
    observations: int
    successes: int

    @property
    def success_rate(self) -> float:
        return round(self.successes / self.observations, 4) if self.observations else 0.0

    @property
    def is_trustworthy(self) -> bool:
        return (
            self.observations >= MIN_OBSERVATIONS
            and self.success_rate >= MIN_SUCCESS_RATE
        )


async def record_failure(
    session: AsyncSession, *, agent_type: str, task_type: str, pattern: str, fix: str
) -> None:
    """Count one occurrence of a failure pattern and the fix that was tried."""
    if not pattern.strip() or not fix.strip():
        return
    await session.execute(
        text(
            """
            INSERT INTO agent_learnings (agent_type, task_type, failure_pattern, applied_fix)
            VALUES (:agent_type, :task_type, :pattern, :fix)
            ON CONFLICT (agent_type, task_type, failure_pattern) DO UPDATE SET
                observations = agent_learnings.observations + 1,
                updated_at = now()
            """
        ),
        {"agent_type": agent_type, "task_type": task_type, "pattern": pattern[:120], "fix": fix},
    )


async def record_success(
    session: AsyncSession, *, agent_type: str, task_type: str, pattern: str
) -> None:
    """Credit a pattern whose fix preceded a passing attempt."""
    await session.execute(
        text(
            """
            UPDATE agent_learnings
               SET successes = successes + 1, updated_at = now()
             WHERE agent_type = :agent_type
               AND task_type = :task_type
               AND failure_pattern = :pattern
            """
        ),
        {"agent_type": agent_type, "task_type": task_type, "pattern": pattern[:120]},
    )


async def hints_for(
    session: AsyncSession, *, agent_type: str, task_type: str
) -> list[str]:
    """Trustworthy fixes for this task, most-proven first.

    Never raises: planning must not fail because the learning table is
    unavailable, since the product worked without it for its whole life.
    """
    try:
        rows = await session.execute(
            text(
                """
                SELECT failure_pattern, applied_fix, observations, successes
                  FROM agent_learnings
                 WHERE agent_type = :agent_type AND task_type = :task_type
                   AND is_active
                   AND observations >= :min_observations
                 ORDER BY (successes::float / NULLIF(observations, 0)) DESC NULLS LAST,
                          observations DESC
                 LIMIT :limit
                """
            ),
            {
                "agent_type": agent_type,
                "task_type": task_type,
                "min_observations": MIN_OBSERVATIONS,
                "limit": MAX_HINTS,
            },
        )
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        logger.warning("memory.hints_unavailable err=%s", type(exc).__name__)
        return []

    learnings = [
        Learning(row.failure_pattern, row.applied_fix, row.observations, row.successes)
        for row in rows
    ]
    return [learning.applied_fix for learning in learnings if learning.is_trustworthy]
