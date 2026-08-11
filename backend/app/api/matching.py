"""AI matching pipeline endpoints (FR-4.2/4.5). Matching itself always runs
as the `pickready.run_matching` Celery task — never inline (ESD §17)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_tenant_db, require_capability
from app.models.candidate import Candidate, JobCandidateLink, source_type_label
from app.models.job import Job
from app.schemas.candidates import CandidateOut
from app.schemas.matching import (
    MatchingTaskStatusOut,
    MatchResultOut,
    MatchResultsOut,
    RunMatchingOut,
)
from app.services import capabilities as caps
from app.services.audit import audit
from app.services.matching import client_breakdown, ranking_payload
from app.workers.celery_app import celery_app

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
            detail="Finalize the technical question bank before running the full assessment pipeline",
        )
    candidate_count = (
        await session.execute(
            select(func.count(JobCandidateLink.id)).where(
                JobCandidateLink.job_id == job.id
            )
        )
    ).scalar_one()
    task = celery_app.send_task("pickready.run_matching", args=[str(job.id)])
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="matching_triggered", target_type="job", target_id=job.id)
    return RunMatchingOut(
        job_id=job.id,
        task_id=task.id,
        candidate_count=candidate_count,
    )


@router.get("/tasks/{task_id}", response_model=MatchingTaskStatusOut)
async def matching_task_status(
    task_id: str,
    _user: CurrentUser = Depends(require_capability(caps.TRIGGER_MATCHING)),
) -> MatchingTaskStatusOut:
    result = celery_app.AsyncResult(task_id)
    return MatchingTaskStatusOut(
        task_id=task_id,
        state=result.state,
        done=result.ready(),
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
