"""AI matching pipeline endpoints (FR-4.2/4.5). Matching itself always runs
as the `pickready.run_matching` Celery task — never inline (ESD §17)."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_tenant_db, require_capability
from app.models.candidate import Candidate, JobCandidateLink
from app.models.job import Job
from app.schemas.candidates import CandidateOut
from app.schemas.matching import MatchResultOut, MatchResultsOut, RunMatchingOut
from app.services import capabilities as caps
from app.services.audit import audit
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
    celery_app.send_task("pickready.run_matching", args=[str(job.id)])
    await audit(session, tenant_id=user.tenant_id, actor_user_id=user.user_id,
                action="matching_triggered", target_type="job", target_id=job.id)
    return RunMatchingOut(job_id=job.id)


@router.get("/jobs/{job_id}/results", response_model=MatchResultsOut)
async def matching_results(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_DATABANK)),
    session: AsyncSession = Depends(get_tenant_db),
) -> MatchResultsOut:
    job = await _get_job(session, user, job_id)
    links = (
        await session.execute(
            select(JobCandidateLink)
            .where(JobCandidateLink.job_id == job.id)
            .order_by(JobCandidateLink.match_score.desc().nulls_last())
        )
    ).scalars().all()
    results: list[MatchResultOut] = []
    for link in links:
        candidate = await session.get(Candidate, link.candidate_id)
        results.append(MatchResultOut(
            link_id=link.id,
            candidate=CandidateOut.model_validate(candidate),
            source=link.source,
            match_score=link.match_score,
            tier=link.tier,
            rationale=link.match_rationale,
        ))
    return MatchResultsOut(job_id=job.id, results=results)
