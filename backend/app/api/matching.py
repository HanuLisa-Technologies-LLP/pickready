"""AI Matching endpoints. The run itself is always the dispatched
`pickready.run_matching` task, never inline (claude.md rule 4).

Two routes, and what was deleted (Vivekium release, Phase 2)
------------------------------------------------------------
* `POST /matching/jobs/{job_id}/run` starts a run and answers its task id.
* `GET /matching/jobs/{job_id}/tasks/{task_id}` reports that run's progress.

`GET /matching/tasks/{task_id}` is DELETED, because it had no tenant check at
all: the run-status record in Redis is keyed by the task id alone
(`workers/status`), so any holder of `trigger_matching` in any tenant could read
any other tenant's run by its id. The replacement is keyed by the job AND
proves the task was started for that job in the caller's tenant, by reading the
`matching_triggered` audit row the run route writes in the same transaction as
the dispatch. `GET /matching/jobs/{job_id}/results` is DELETED too: nothing
called it, it ordered by the retired `match_score`, and it serialized the
retired per-category comments. The ranked table is `GET /jobs/{job_id}/candidates`.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_tenant_db, require_capability
from app.models.candidate import JobCandidateLink
from app.models.job import Job
from app.models.tenant import AuditLog
from app.schemas.matching import (
    ActivityOut,
    MatchingStageOut,
    MatchingTaskStatusOut,
    RunMatchingOut,
)
from app.services import assessment_contract
from app.services import capabilities as caps
from app.services import matching_progress
from app.services.audit import audit
from app.workers import status as task_status
from app.workers.dispatch import dispatch_after_commit

router = APIRouter()

#: The audit action the run route writes, and the task-status route reads back
#: to prove a task id belongs to this job in this tenant.
ACTION_MATCHING_TRIGGERED = "matching_triggered"

#: The refusals, server-authored so the job page renders them verbatim.
DETAIL_NOT_PUBLISHED = "AI Matching runs once the job is published."
DETAIL_ARCHIVED = "Restore this job before running AI Matching."
DETAIL_CLOSED = "This job is closed, so AI Matching no longer runs on it."
DETAIL_SKILLS_NOT_SAVED = "Save the skills on this job before running AI Matching."
DETAIL_TASK_NOT_FOUND = "No AI Matching run with that id was started for this job."


async def _get_job(session: AsyncSession, user: CurrentUser, job_id: uuid.UUID) -> Job:
    job = await session.get(Job, job_id)
    if job is None or job.tenant_id != user.tenant_id:  # defense in depth; RLS is the boundary
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.post(
    "/jobs/{job_id}/run",
    response_model=RunMatchingOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_matching(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.TRIGGER_MATCHING)),
    session: AsyncSession = Depends(get_tenant_db),
) -> RunMatchingOut:
    """Start AI Matching for one job.

    Refused, with a sentence the page shows as written, for a job that is not
    published, archived or closed, and for a job whose Skills step has not been
    saved: Yukti reads a resume against the SAVED skills
    (`assessment_contract.skills_saved`), so a run before the save would rank
    every candidate against nothing and call it a result. This replaces the
    old gate on `assessment_status`, which asked for the retired matching
    categories as well.

    The dispatch happens AFTER this transaction commits
    (`dispatch_after_commit`): a run started before the audit row existed could
    be polled before the task-status route could prove who started it, and a
    request that rolls back must start nothing. The handle's id is returned now
    and is also the audit row's `task_id`, which is what the status route
    reads.
    """
    job = await _get_job(session, user, job_id)
    if job.ratified_at is None:
        raise HTTPException(status_code=409, detail=DETAIL_NOT_PUBLISHED)
    if job.archived_at is not None:
        raise HTTPException(status_code=409, detail=DETAIL_ARCHIVED)
    if job.closed_at is not None:
        raise HTTPException(status_code=409, detail=DETAIL_CLOSED)
    if not await assessment_contract.skills_saved(session, job.id):
        raise HTTPException(status_code=409, detail=DETAIL_SKILLS_NOT_SAVED)
    candidate_count = (
        await session.execute(
            select(func.count(JobCandidateLink.id)).where(
                JobCandidateLink.job_id == job.id
            )
        )
    ).scalar_one()
    task = dispatch_after_commit(session, "pickready.run_matching", args=[str(job.id)])
    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action=ACTION_MATCHING_TRIGGERED,
        target_type="job",
        target_id=job.id,
        metadata={"task_id": task.id},
    )
    return RunMatchingOut(
        job_id=job.id,
        task_id=task.id,
        candidate_count=candidate_count,
    )


async def _task_was_started_for(
    session: AsyncSession, user: CurrentUser, job_id: uuid.UUID, task_id: str
) -> bool:
    """Whether THIS tenant's run route started `task_id` for `job_id`.

    Read from the audit row written in the same transaction as the dispatch,
    filtered on the tenant explicitly as well as by RLS (defence in depth), so
    a task id from another tenant or another job answers False.
    """
    found = (
        await session.execute(
            select(AuditLog.id)
            .where(
                AuditLog.tenant_id == user.tenant_id,
                AuditLog.action == ACTION_MATCHING_TRIGGERED,
                AuditLog.target_type == "job",
                AuditLog.target_id == str(job_id),
                AuditLog.metadata_json["task_id"].astext == task_id,
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return found is not None


@router.get("/jobs/{job_id}/tasks/{task_id}", response_model=MatchingTaskStatusOut)
async def matching_task_status(
    job_id: uuid.UUID,
    task_id: str,
    user: CurrentUser = Depends(require_capability(caps.TRIGGER_MATCHING)),
    session: AsyncSession = Depends(get_tenant_db),
) -> MatchingTaskStatusOut:
    """The stage list the job page renders inline while a run is under way.

    404 unless the job is visible to the caller AND the run route recorded
    starting `task_id` for it in the caller's tenant. Never 403: a refusal
    that confirmed the id exists would itself tell one tenant about another's
    run.

    `result.payload` is what the run last published. While the run is in the
    PROGRESS state it is the live stage payload; on SUCCESS it is the task's
    return value, which `pickready.run_matching` makes its FINAL stage payload
    so a finished run keeps its stages and its degraded flag rather than
    redrawing as an all-pending plan. On FAILURE it is empty with the
    exception CLASS NAME recorded beside it, because rendering an exception as
    stages would put a traceback on a recruiter's screen. A run nothing has
    picked up yet returns the full list in `pending`, so the page draws the
    plan immediately.
    """
    await _get_job(session, user, job_id)
    if not await _task_was_started_for(session, user, job_id, task_id):
        raise HTTPException(status_code=404, detail=DETAIL_TASK_NOT_FOUND)
    result = await task_status.read(task_id)
    info = (
        result.payload
        if result.state in (task_status.STATE_PROGRESS, task_status.STATE_SUCCESS)
        else None
    )
    payload = (
        info
        if isinstance(info, dict) and isinstance(info.get("stages"), list)
        else matching_progress.empty_payload(operation_id=task_id)
    )
    activity = payload.get("activity")
    reasons = payload.get("degraded_reasons")
    return MatchingTaskStatusOut(
        task_id=task_id,
        state=result.state,
        done=result.done,
        stages=[MatchingStageOut(**stage) for stage in payload["stages"]],
        candidate_count=int(payload.get("candidate_count") or 0),
        scored_count=int(payload.get("scored_count") or 0),
        # The activity block travels in the SAME payload as the stages, so the
        # two can never be read a poll apart from each other.
        activity=ActivityOut(**activity) if isinstance(activity, dict) else None,
        # Server-written sentences saying what this run could not do (the
        # embedding service was down, some candidates could not be assessed).
        degraded=bool(payload.get("degraded")),
        degraded_reasons=[str(r) for r in reasons] if isinstance(reasons, list) else [],
    )
