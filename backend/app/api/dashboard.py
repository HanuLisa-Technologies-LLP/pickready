"""HR/Recruiter dashboard (FR-10.x).

# ASSUMPTION: ESD §14 computes these metrics from materialized views refreshed
# by Celery beat; the views live in Track B's migrations. Until they exist,
# this endpoint aggregates live over the base tables (data volumes are small
# pre-launch); the query shape maps 1:1 onto the future views.
# ASSUMPTION: "scoped to the logged-in HR/Recruiter's assignments" — staff are
# assigned per tenant (PRD §4) and no per-job assignment table exists, so the
# scope is the caller's tenant (enforced by RLS).
"""
import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_tenant_db, require_capability
from app.models.candidate import JobCandidateLink, PipelineStatusEntry
from app.models.enums import LinkSource, PipelineStatus
from app.models.job import Job
from app.schemas.dashboard import DashboardSummaryOut, JobMetricsOut
from app.services import capabilities as caps

router = APIRouter()


@router.get("/summary", response_model=DashboardSummaryOut)
async def dashboard_summary(
    user: CurrentUser = Depends(require_capability(caps.VIEW_DASHBOARD)),
    session: AsyncSession = Depends(get_tenant_db),
) -> DashboardSummaryOut:
    jobs = (
        await session.execute(
            select(Job).where(
                Job.tenant_id == user.tenant_id, Job.ratified_at.isnot(None)
            ).order_by(Job.created_at.desc())
        )
    ).scalars().all()
    job_ids = [j.id for j in jobs]

    links = []
    if job_ids:
        links = (
            await session.execute(
                select(JobCandidateLink).where(JobCandidateLink.job_id.in_(job_ids))
            )
        ).scalars().all()

    link_job: dict[uuid.UUID, uuid.UUID] = {l.id: l.job_id for l in links}
    databank: dict[uuid.UUID, int] = defaultdict(int)
    fresh: dict[uuid.UUID, int] = defaultdict(int)
    for link in links:
        if link.source == LinkSource.databank:
            databank[link.job_id] += 1
        else:
            fresh[link.job_id] += 1

    # Latest pipeline status per link (history table; last write wins).
    latest: dict[uuid.UUID, PipelineStatus] = {}
    if link_job:
        entries = (
            await session.execute(
                select(PipelineStatusEntry)
                .where(PipelineStatusEntry.job_candidate_link_id.in_(list(link_job)))
                .order_by(PipelineStatusEntry.at)
            )
        ).scalars().all()
        for entry in entries:
            latest[entry.job_candidate_link_id] = entry.status

    by_status: dict[uuid.UUID, dict[PipelineStatus, int]] = defaultdict(lambda: defaultdict(int))
    for link_id, status_value in latest.items():
        by_status[link_job[link_id]][status_value] += 1

    return DashboardSummaryOut(
        jobs=[
            JobMetricsOut(
                job_id=j.id,
                title=j.title,
                databank_matched=databank[j.id],
                fresh_sourced=fresh[j.id],
                shortlisted=by_status[j.id][PipelineStatus.shortlisted],
                # Both spellings. Migration 0018 renamed this stage to
                # `offer_extended` but kept `offered` valid rather than
                # rewriting history, so a tenant can hold rows of either and
                # counting only one silently reports a smaller funnel.
                offered=(
                    by_status[j.id][PipelineStatus.offered]
                    + by_status[j.id][PipelineStatus.offer_extended]
                ),
                joined=by_status[j.id][PipelineStatus.joined],
            )
            for j in jobs
        ],
        total_jobs_worked=len(jobs),
    )


# ── The AI Dashboard: REMOVED from the customer portal ───────────────────────
#
# `GET /dashboard/ai-insights` lived here and is DELETED, not deprecated
# (spec 30, client instruction: "Remove the AI Dashboard feature completely from
# Readypick platform in customer's portal"). The page, its component and its
# response schema went in the same change.
#
# Deleted rather than left returning an empty payload: a route that answers is a
# route a client keeps calling, and a 404 from an unregistered path is the
# honest answer to a request for a feature that does not exist.
