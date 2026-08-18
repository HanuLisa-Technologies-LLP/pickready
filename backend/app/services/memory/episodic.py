"""Reading the trace table: what has this agent been doing lately.

Writing is `observability.trace.persist`; this is the query side, and it exists
so an operator question ("is the ranking agent degrading for this tenant") is a
function call rather than hand-written SQL in a notebook that nobody keeps.
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


async def health(session: AsyncSession, *, agent_type: str, days: int = 7) -> dict[str, Any]:
    """The operational summary. Asks the TABLE, which is the whole point.

    Every health check this product has regretted asked a timestamp instead.
    """
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
                 WHERE agent_type = :agent_type
                   AND created_at > now() - CAST(:days || ' days' AS interval)
                """
            ),
            {"agent_type": agent_type, "days": days},
        )
    ).first()
    runs = int(row.runs or 0)
    return {
        "agent_type": agent_type,
        "days": days,
        "runs": runs,
        "success_rate": round((row.successes or 0) / runs, 4) if runs else 0.0,
        "degraded_rate": round((row.degraded or 0) / runs, 4) if runs else 0.0,
        "avg_duration_ms": int(row.avg_ms or 0),
        "cost_usd": float(row.cost_usd or 0),
    }


async def failure_breakdown(session: AsyncSession, *, days: int = 7) -> dict[str, int]:
    """Failures grouped by root cause, which is how "70% of failures are
    retrieval quality" stops being a guess."""
    rows = await session.execute(
        text(
            """
            SELECT COALESCE(failure_category, 'unknown') AS category, COUNT(*) AS n
              FROM agent_execution_traces
             WHERE status <> 'success'
               AND created_at > now() - CAST(:days || ' days' AS interval)
             GROUP BY 1 ORDER BY 2 DESC
            """
        ),
        {"days": days},
    )
    return {row.category: int(row.n) for row in rows}
