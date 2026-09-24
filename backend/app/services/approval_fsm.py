"""Job publication record (ESD 7 / FR-3.2..3.4): the one transition left.

A job publishes DIRECTLY: `POST /jobs/{id}/publish` stamps `ratified` and
`ratified_at` and logs every level of the old four-level chain
(Requested -> Recommended -> Approved -> Ratified) as an explicit `skipped`
`job_approvals` row, so the bypass is auditable and never silent (ESD 7).
Every "is this job live / HR-visible?" check reads `ratified_at IS NOT NULL`,
which is why the terminal stamp survives the chain it used to end.

WHAT WENT, AND WHY (Vivekium release, PLAN-p7 WP-B6)
----------------------------------------------------
The multi-level approval chain is DELETED, not dormant: the pure planner
(`plan_submit`, `validate_transition`, `next_active_level`), its persistence
wrappers (`apply_submit`, `apply_transition`), its typed errors, the company
route that configured the levels and the two capabilities only that machinery
read (their `role_permissions` rows are deleted by migration
`0129_route_scrap`, which names them). Phase 1 had already deleted the job-side routes
(submit, approve, the approvals list, the hand-off to the Hiring Manager).
Nothing reached any of it: every job has published directly since the flat
staff model of 2026-07-24, and "the FSM is dormant, not deleted" was the
sentence that kept a second, unreachable publication path alive for two
months.

`companies.approval_levels_config` is left in place as a column: it holds
customer-entered data and dropping it is not this change's call.
"""
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import APPROVAL_CHAIN, ApprovalDecision, JobStatus


@dataclass(frozen=True)
class ApprovalRow:
    """One job_approvals row to write."""
    level: JobStatus
    decision: ApprovalDecision
    approver_user_id: uuid.UUID | None = None
    remarks: str | None = None


@dataclass(frozen=True)
class TransitionResult:
    new_status: JobStatus
    rows: list[ApprovalRow] = field(default_factory=list)
    ratified: bool = False


def plan_direct_publish() -> TransitionResult:
    """Every level logged as an explicit `skipped` row (never silently
    auto-approved, ESD 7), and the job lands terminal at `ratified`, so every
    downstream `ratified_at IS NOT NULL` visibility check keeps working."""
    rows = [
        ApprovalRow(level=level, decision=ApprovalDecision.skipped,
                    remarks="approval chain bypassed (direct publish)")
        for level in APPROVAL_CHAIN
    ]
    return TransitionResult(new_status=JobStatus.ratified, rows=rows, ratified=True)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _persist(session: AsyncSession, job, result: TransitionResult) -> None:
    from app.models.job import JobApproval  # local import keeps the planner DB-free

    for row in result.rows:
        session.add(JobApproval(
            tenant_id=job.tenant_id,
            job_id=job.id,
            level=row.level,
            approver_user_id=row.approver_user_id,
            decision=row.decision,
            remarks=row.remarks,
        ))
    job.status = result.new_status
    if result.ratified:
        job.ratified_at = _now()
    await session.flush()


async def apply_direct_publish(session: AsyncSession, job) -> TransitionResult:
    """Persist a direct publish: stamp `ratified` + `ratified_at` and log the
    four bypassed levels as skipped rows."""
    result = plan_direct_publish()
    await _persist(session, job, result)
    return result
