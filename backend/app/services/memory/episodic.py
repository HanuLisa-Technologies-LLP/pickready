"""Reading the trace table: what has this agent been doing lately.

Writing is `observability.trace.persist`; this is the query side, and it exists
so an operator question ("is the ranking agent degrading for this tenant") is a
function call rather than hand-written SQL in a notebook that nobody keeps.

PROVENANCE HERE WAS ALREADY MOSTLY PRESENT (RPN-AI-UP-001 W3.5)
-----------------------------------------------------------------
`agent_execution_traces` has carried `tenant_id`, `agent_type`, `task_type` and
a per-run `request_id` since migration 0055, and an RLS policy has scoped it
since the same migration. What was missing was on the READ side: `health` took
no tenant at all, so the only summary this module could produce was a
platform-wide one, and a tenant-scoped caller asking "how is my hiring going"
got a number computed over everybody's runs and silently narrowed by RLS to
their own. `tenant_id` is now a required argument, and passing None is a
platform-wide question somebody had to type, not one they fell into.

The trace table gains no `source` or `trust_level` column, and that is
deliberate rather than an omission: a trace records what a run DID, and the
questions those columns answer belong to what a run left behind. Traces are
also W4's surface, and two workstreams editing one table's shape is how the
second one silently loses.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

#: Traces are kept for a year (migration 0055's intent). Anything longer is an
#: audit trail, and the audit trail is `audit_log`.
RETENTION_DAYS = 365


async def recent(
    session: AsyncSession,
    *,
    agent_type: str | None = None,
    tenant_id: uuid.UUID | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    clauses, params = [], {"limit": max(1, min(500, limit))}
    if agent_type:
        clauses.append("agent_type = :agent_type")
        params["agent_type"] = agent_type
    if tenant_id:
        clauses.append("tenant_id = :tenant_id")
        params["tenant_id"] = str(tenant_id)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = await session.execute(
        text(
            f"""
            SELECT request_id, agent_type, task_type, status, complexity,
                   fast_path, attempts, degraded, duration_ms, cost_usd,
                   failure_category, created_at
              FROM agent_execution_traces
              {where}
             ORDER BY created_at DESC
             LIMIT :limit
            """
        ),
        params,
    )
    return [dict(row._mapping) for row in rows]


async def health(
    session: AsyncSession,
    *,
    agent_type: str,
    tenant_id: uuid.UUID | None,
    days: int = 7,
) -> dict[str, Any]:
    """The operational summary. Asks the TABLE, which is the whole point.

    Every health check this product has regretted asked a timestamp instead.

    `tenant_id` has no default. None means "every tenant", which is a real
    question a platform operator asks and a question a customer must never be
    handed by accident, so it has to be typed rather than fallen into.
    """
    clauses = [
        "agent_type = :agent_type",
        "created_at > now() - CAST(:days || ' days' AS interval)",
    ]
    params: dict[str, Any] = {"agent_type": agent_type, "days": days}
    if tenant_id is not None:
        clauses.append("tenant_id = :tenant_id")
        params["tenant_id"] = str(tenant_id)

    row = (
        await session.execute(
            text(
                """
                SELECT COUNT(*) AS runs,
                       COUNT(*) FILTER (WHERE status = 'success') AS successes,
                       COUNT(*) FILTER (WHERE degraded) AS degraded,
                       COALESCE(AVG(duration_ms), 0)::int AS avg_ms,
                       COALESCE(SUM(cost_usd), 0) AS cost_usd
                  FROM agent_execution_traces
                 WHERE """
                + " AND ".join(clauses)
            ),
            params,
        )
    ).first()
    runs = int(row.runs or 0)
    return {
        "agent_type": agent_type,
        "tenant_id": str(tenant_id) if tenant_id else None,
        "days": days,
        "runs": runs,
        "success_rate": round((row.successes or 0) / runs, 4) if runs else 0.0,
        "degraded_rate": round((row.degraded or 0) / runs, 4) if runs else 0.0,
        "avg_duration_ms": int(row.avg_ms or 0),
        "cost_usd": float(row.cost_usd or 0),
    }


