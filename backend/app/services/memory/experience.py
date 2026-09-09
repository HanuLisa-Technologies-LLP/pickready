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

EVERY LEARNING BELONGS TO ONE TENANT (RPN-AI-UP-001 W3.5)
-----------------------------------------------------------
`services/memory/provenance.py` carries the decision and its reasoning. What it
means here is mechanical and worth stating in both places: `tenant_id` is a
required argument of every function below, the unique key includes it, the
retrieval query filters on it, and an RLS policy on the table makes a
cross-tenant read impossible even from a query somebody wrote wrong. A pattern
extracted from a run whose prompt carried a candidate's own words cannot reach
another customer's grading, because there is no row it could be read out of.

THE FILTERS ARE IN THE QUERY, NOT IN THE DATACLASS
----------------------------------------------------
Tenant and staleness are both applied in SQL. `Learning` is what a row BECOMES
after it has been read, and a filter applied there has already read the row it
was meant to exclude. Same ordering rule the tool executor follows: the refusal
comes before the read, not after it.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.memory.provenance import (
    TRUST_CANDIDATE_AUTHORED,
    TRUST_RANK,
    Provenance,
)

logger = logging.getLogger(__name__)

MIN_OBSERVATIONS = 3
MIN_SUCCESS_RATE = 0.5
MAX_HINTS = 3

#: The pattern column is varchar(120). Truncation happens once, here, so the
#: INSERT and the UPDATE that credits it cannot disagree about what the key is.
_PATTERN_WIDTH = 120


@dataclass(frozen=True)
class Learning:
    """One stored lesson, as it comes back out of the table.

    The provenance fields carry defaults so a caller reasoning about
    trustworthiness alone can build one, and the defaults are the LEAST
    trusting available: no tenant, and the trust level of text a stranger
    wrote. A default that assumed platform provenance would make an unlabelled
    learning look like the safest kind there is.
    """

    failure_pattern: str
    applied_fix: str
    observations: int
    successes: int
    tenant_id: uuid.UUID | str | None = None
    source: str = ""
    trust_level: str = TRUST_CANDIDATE_AUTHORED
    revalidate_after: datetime | None = None
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)

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
    session: AsyncSession,
    *,
    agent_type: str,
    task_type: str,
    pattern: str,
    fix: str,
    provenance: Provenance,
) -> None:
    """Count one occurrence of a failure pattern and the fix that was tried.

    `provenance` is required and has no default. A learning that cannot say
    which tenant it came from is exactly the learning the tenant scope exists to
    stop, so the write is impossible rather than merely discouraged: there is no
    call shape that produces an unattributed row.

    A repeat observation refreshes `revalidate_after`. Seeing the same pattern
    again IS the revalidation, so a learning that keeps happening keeps its
    life, and one that stopped happening quietly expires without anybody having
    to run a sweep.
    """
    if not pattern.strip() or not fix.strip():
        return
    params = {
        "agent_type": agent_type,
        "task_type": task_type,
        "pattern": pattern[:_PATTERN_WIDTH],
        "fix": fix,
        **provenance.as_row(),
    }
    # jsonb, not a Postgres ARRAY. asyncpg maps a Python list onto an array,
    # and the cast in the statement is what makes the column and the parameter
    # agree; the encoding happens once, here.
    params["evidence_ids"] = json.dumps([str(v) for v in provenance.evidence_ids])
    await session.execute(
        text(
            """
            INSERT INTO agent_learnings (
                tenant_id, agent_type, task_type, failure_pattern, applied_fix,
                source, source_version, created_by, trust_level, evidence_ids,
                revalidate_after
            )
            VALUES (
                :tenant_id, :agent_type, :task_type, :pattern, :fix,
                :source, :source_version, :created_by, :trust_level,
                CAST(:evidence_ids AS jsonb), :revalidate_after
            )
            ON CONFLICT (tenant_id, agent_type, task_type, failure_pattern)
            DO UPDATE SET
                observations = agent_learnings.observations + 1,
                revalidate_after = EXCLUDED.revalidate_after,
                updated_at = now()
            """
        ),
        params,
    )


async def record_success(
    session: AsyncSession,
    *,
    agent_type: str,
    task_type: str,
    pattern: str,
    tenant_id: uuid.UUID | str,
) -> None:
    """Credit a pattern whose fix preceded a passing attempt.

    `tenant_id` is required for the same reason the filter exists on the read:
    without it this UPDATE would credit every tenant's row carrying the same
    pattern, which is a cross-tenant write dressed as a counter.
    """
    await session.execute(
        text(
            """
            UPDATE agent_learnings
               SET successes = successes + 1, updated_at = now()
             WHERE tenant_id = :tenant_id
               AND agent_type = :agent_type
               AND task_type = :task_type
               AND failure_pattern = :pattern
            """
        ),
        {
            "tenant_id": str(tenant_id),
            "agent_type": agent_type,
            "task_type": task_type,
            "pattern": pattern[:_PATTERN_WIDTH],
        },
    )


async def hints_for(
    session: AsyncSession,
    *,
    agent_type: str,
    task_type: str,
    tenant_id: uuid.UUID | str,
) -> list[str]:
    """Trustworthy fixes for this task, in this tenant, most-proven first.

    Four filters, each doing separate work: the tenant (nothing another
    customer's input produced), `is_active` (nothing revoked), the observation
    floor (nothing anecdotal), and `revalidate_after` (nothing whose evidence
    has aged out). The ordering breaks a tie on trust level, so between two
    equally proven fixes the one drawn from text this platform generated wins
    over one drawn from text a stranger uploaded.

    Never raises: planning must not fail because the learning table is
    unavailable, since the product worked without it for its whole life.
    """
    try:
        rows = await session.execute(
            text(
                """
                SELECT failure_pattern, applied_fix, observations, successes,
                       tenant_id, source, trust_level, revalidate_after
                  FROM agent_learnings
                 WHERE tenant_id = :tenant_id
                   AND agent_type = :agent_type
                   AND task_type = :task_type
                   AND is_active
                   AND observations >= :min_observations
                   AND revalidate_after > now()
                 ORDER BY (successes::float / NULLIF(observations, 0)) DESC NULLS LAST,
                          observations DESC
                 LIMIT :limit
                """
            ),
            {
                "tenant_id": str(tenant_id),
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
        Learning(
            failure_pattern=row.failure_pattern,
            applied_fix=row.applied_fix,
            observations=row.observations,
            successes=row.successes,
            tenant_id=row.tenant_id,
            source=row.source,
            trust_level=row.trust_level,
            revalidate_after=row.revalidate_after,
        )
        for row in rows
    ]
    learnings.sort(key=lambda learning: TRUST_RANK.get(learning.trust_level, len(TRUST_RANK)))
    return [learning.applied_fix for learning in learnings if learning.is_trustworthy]


async def revoke_learnings_from_source(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    source: str,
    source_version: str | None = None,
) -> int:
    """Stop applying every learning traceable to one source. Returns the count.

    THIS IS WHAT PROVENANCE IS FOR. A source found to be compromised -- a
    poisoned document, a prompt version that taught the wrong lesson, a parser
    that misread a whole batch -- is identifiable only because every row names
    what produced it. Without `source` the only available remedy is emptying
    the table.

    DEACTIVATED, NEVER DELETED. `is_active = false` stops the row being
    retrieved, which is the whole requirement, and keeps the observation counts
    that are the evidence of what happened. A DELETE would make the revocation
    itself unauditable and unrecoverable, and a revocation issued in error is
    exactly the case that needs to be recoverable.

    SCOPED TO ONE TENANT, always. A revocation is a change to how that
    customer's future generations behave, and a call that could reach across
    tenants would be the cross-tenant influence channel this design removed,
    running backwards.

    `source_version` narrows it further when only one version of a producer was
    at fault. Omitted, every version of that source in this tenant is revoked,
    which is the safe direction: the cost of revoking a good learning is that it
    is relearned, and the cost of leaving a poisoned one is that it keeps
    shaping prompts.
    """
    clauses = ["tenant_id = :tenant_id", "source = :source", "is_active"]
    params: dict[str, object] = {"tenant_id": str(tenant_id), "source": source}
    if source_version is not None:
        clauses.append("source_version = :source_version")
        params["source_version"] = source_version

    result = await session.execute(
        text(
            "UPDATE agent_learnings SET is_active = false, updated_at = now() "
            "WHERE " + " AND ".join(clauses)
        ),
        params,
    )
    revoked = int(result.rowcount or 0)
    logger.info(
        "memory.learnings_revoked tenant=%s source=%s version=%s count=%d",
        tenant_id,
        source,
        source_version,
        revoked,
    )
    return revoked
