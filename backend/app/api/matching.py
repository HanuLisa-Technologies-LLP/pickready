"""AI matching pipeline endpoints (FR-4.2/4.5). Matching itself always runs
as the dispatched `pickready.run_matching` task — never inline (ESD §17)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_tenant_db, require_capability
from app.models.candidate import Candidate, JobCandidateLink, source_type_label
from app.models.job import Job
from app.schemas.candidates import CandidateOut
from app.schemas.matching import (
    ActivityOut,
    MatchingStageOut,
    MatchingTaskStatusOut,
    MatchResultOut,
    MatchResultsOut,
    RunMatchingOut,
)
from app.services import capabilities as caps
from app.services import matching_progress
from app.services.audit import audit
from app.services.matching import client_breakdown, ranking_payload
from app.workers import status as task_status
from app.workers.dispatch import dispatch

router = APIRouter()


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
    job = await _get_job(session, user, job_id)
    if job.ratified_at is None:
        raise HTTPException(status_code=409, detail="Matching runs once the job is ratified (FR-3.4)")
    if job.archived_at is not None:
        raise HTTPException(status_code=409, detail="Restore this job before running matching")
    if job.assessment_status != "ready_for_candidates":
        raise HTTPException(
            status_code=409,
            detail=(
                "Finish the job setup review before running matching. The PPI "
                "matrix and the matching categories both need to be saved."
            ),
        )
    candidate_count = (
        await session.execute(
            select(func.count(JobCandidateLink.id)).where(
                JobCandidateLink.job_id == job.id
            )
        )
    ).scalar_one()
    task = dispatch("pickready.run_matching", args=[str(job.id)])
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="matching_triggered", target_type="job", target_id=job.id)
    return RunMatchingOut(
        job_id=job.id,
        task_id=task.id,
        candidate_count=candidate_count,
    )


# ── The Matching Categories editor is DELETED (Vivekium release) ────────────
#
# The list, add, rename, remove and finalise routes for a job's categories
# lived here. Yukti scores a FIXED structure held in backend configuration (owner
# decision D2) and nobody edits categories, so there is nothing to list, add,
# rename, remove or finalise. The generator and its prompt went with them.


@router.get("/tasks/{task_id}", response_model=MatchingTaskStatusOut)
async def matching_task_status(
    task_id: str,
    _user: CurrentUser = Depends(require_capability(caps.TRIGGER_MATCHING)),
) -> MatchingTaskStatusOut:
    result = await task_status.read(task_id)
    # The stage list the job page renders inline while the run is under way.
    #
    # `result.payload` is what the run last published. It is only a stage
    # payload while the run is in the PROGRESS state -- on SUCCESS it is the
    # return value, and on FAILURE it is empty with the exception CLASS NAME
    # recorded beside it, because rendering an exception as stages would put a
    # traceback on a recruiter's screen. A run nothing has picked up yet
    # returns the full list in `pending`, so the page draws the plan
    # immediately rather than discovering it one row at a time.
    info = (
        result.payload if result.state == matching_progress.STATE_PROGRESS else None
    )
    payload = (
        info
        if isinstance(info, dict) and isinstance(info.get("stages"), list)
        else matching_progress.empty_payload(operation_id=task_id)
    )
    activity = payload.get("activity")
    return MatchingTaskStatusOut(
        task_id=task_id,
        state=result.state,
        done=result.done,
        stages=[MatchingStageOut(**stage) for stage in payload["stages"]],
        candidate_count=int(payload.get("candidate_count") or 0),
        scored_count=int(payload.get("scored_count") or 0),
        # The activity block travels in the SAME payload as the stages, so the
        # two can never be read a poll apart from each other and show a
        # sentence describing a stage the list has already moved past.
        activity=ActivityOut(**activity) if isinstance(activity, dict) else None,
    )


@router.get("/jobs/{job_id}/results", response_model=MatchResultsOut)
async def matching_results(
    job_id: uuid.UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, ge=1, le=100),
    user: CurrentUser = Depends(require_capability(caps.VIEW_DATABANK)),
    session: AsyncSession = Depends(get_tenant_db),
) -> MatchResultsOut:
    job = await _get_job(session, user, job_id)
    total = (
        await session.execute(
            select(func.count())
            .select_from(JobCandidateLink)
            .where(JobCandidateLink.job_id == job.id)
        )
    ).scalar_one()
    # One joined query, not one query per candidate. The ORDER BY carries an
    # explicit `id` tiebreak so the page boundary is stable: without a total
    # order, two links with the same score can swap places between requests and
    # one of them disappears from the paginated result entirely.
    rows = (
        await session.execute(
            select(JobCandidateLink, Candidate)
            .join(Candidate, Candidate.id == JobCandidateLink.candidate_id)
            .where(JobCandidateLink.job_id == job.id)
            .order_by(
                JobCandidateLink.match_score.desc().nulls_last(),
                JobCandidateLink.id,
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    results: list[MatchResultOut] = []
    for link, candidate in rows:
        results.append(MatchResultOut(
            link_id=link.id,
            candidate=CandidateOut.model_validate(candidate),
            source=link.source,
            source_type=link.source_type,
            source_type_label=source_type_label(link.source_type),
            tier=link.tier,
            rationale=link.match_rationale,
            # Numeric parameter scores stay internal (claude.md).
            breakdown=client_breakdown(link.match_breakdown_json),
            # Comments-only projection (always present; see services.matching).
            **ranking_payload(link.match_breakdown_json),
        ))
    total_pages = max(1, (int(total) + page_size - 1) // page_size)
    return MatchResultsOut(
        job_id=job.id,
        results=results,
        total=int(total),
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_next=page < total_pages,
        has_previous=page > 1,
    )
