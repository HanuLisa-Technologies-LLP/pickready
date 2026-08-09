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
from app.models.assessment import (
    AssessmentConversation,
    FunctionalSkillsReport,
    JobCompetency,
    ReportDimension,
)
from app.models.candidate import JobCandidateLink, PipelineStatusEntry
from app.models.enums import LinkSource, PipelineStatus
from app.models.job import Job
from app.schemas.dashboard import (
    AIDashboardOut,
    AssessmentFunnelOut,
    DashboardSummaryOut,
    FrameworkHealthOut,
    GradeCountOut,
    JobMetricsOut,
)
from app.services import capabilities as caps
from app.services import rating
from app.services.functional_assessment import CATEGORY_MATCHING

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


@router.get("/ai-insights", response_model=AIDashboardOut)
async def ai_dashboard(
    user: CurrentUser = Depends(require_capability(caps.VIEW_DASHBOARD)),
    session: AsyncSession = Depends(get_tenant_db),
) -> AIDashboardOut:
    """The AI Dashboard: what the AI actually did for THIS customer.

    Added to the Customer Portal on 2026-08-09. Scope is the caller's tenant,
    enforced by RLS on the session (CLAUDE.md rule 1); the explicit tenant
    predicates below are defence in depth, not the boundary.

    Three things about the numbers here, because it is the obvious place to
    break a standing rule:

    * Every figure that LEAVES this handler is a COUNT OF THINGS (jobs,
      candidates, assessments, reports). That is what the existing dashboard
      already reports and is outside the no-numbers rule, which covers a score,
      percentage, rank or band for an assessment or a match. Scores ARE read
      here, to decide which grade a candidate falls under, and not one of them
      reaches the response: what is returned is the word and a headcount.
    * The grade breakdown is keyed by the four WORD labels of `services.rating`
      and every grade is present even at zero. Omitting the empty ones would
      read as "nobody landed there" rather than "nobody has been assessed".
    * Framework health is measured against `job_competencies` ROWS, never
      against `framework_generated_at`. A timestamp is not evidence that work
      happened, and the specific failure it hides -- a stamped framework with
      no rows, so the job is stuck and no candidate on it can ever be assessed
      -- is the one a customer most needs to see. `reconcile_job_setup` repairs
      these; this dashboard is where they become visible in the meantime.
    """
    jobs = (
        await session.execute(
            select(Job.id, Job.assessment_status, Job.framework_approved_at)
            .where(Job.tenant_id == user.tenant_id)
        )
    ).all()
    job_ids = [row.id for row in jobs]

    # Jobs that actually HAVE framework rows. Asked of the table, deliberately.
    jobs_with_rows: set[uuid.UUID] = set()
    if job_ids:
        jobs_with_rows = {
            row[0]
            for row in (
                await session.execute(
                    select(JobCompetency.job_id)
                    .where(JobCompetency.job_id.in_(job_ids))
                    .distinct()
                )
            ).all()
        }

    ready = awaiting = pending = 0
    for row in jobs:
        if row.id not in jobs_with_rows:
            # No competencies: generation has not succeeded, whatever any
            # timestamp on the row claims.
            pending += 1
        elif row.framework_approved_at is not None:
            ready += 1
        else:
            awaiting += 1

    invited = started = completed = 0
    if job_ids:
        conversations = (
            await session.execute(
                select(
                    AssessmentConversation.started_at,
                    AssessmentConversation.completed_at,
                ).where(AssessmentConversation.job_id.in_(job_ids))
            )
        ).all()
        invited = len(conversations)
        started = sum(1 for row in conversations if row.started_at is not None)
        completed = sum(1 for row in conversations if row.completed_at is not None)

    reports: list = []
    if job_ids:
        reports = (
            await session.execute(
                select(
                    FunctionalSkillsReport.id,
                    FunctionalSkillsReport.overall_score,
                    FunctionalSkillsReport.scoring_mode,
                    FunctionalSkillsReport.synthesized_at,
                ).where(FunctionalSkillsReport.job_id.in_(job_ids))
            )
        ).all()

    # One grade per CANDIDATE, from the report's own overall. "11 candidates are
    # Matching" is the question being asked; counting report DIMENSIONS would
    # answer a different question with a bigger number.
    #
    # `overall_score` is null on reports written before migration 0030, which
    # recompute it from their dimensions on read. That recomputation is
    # reproduced here rather than skipped, because a dashboard that silently
    # omits every older report would undercount the customer's own history and
    # look like data loss. It is the SAME rule the report GET applies (mean of
    # every dimension except the AI Score's matching rows), so a candidate
    # cannot be counted under one grade here and shown another on their report.
    grade_counts: dict[str, int] = {grade: 0 for grade in rating.GRADES}
    legacy_ids = [row.id for row in reports if row.overall_score is None]
    recomputed: dict[uuid.UUID, int] = {}
    if legacy_ids:
        dimension_rows = (
            await session.execute(
                select(ReportDimension.report_id, ReportDimension.score).where(
                    ReportDimension.report_id.in_(legacy_ids),
                    ReportDimension.category != CATEGORY_MATCHING,
                )
            )
        ).all()
        totals: dict[uuid.UUID, list[int]] = defaultdict(list)
        for row in dimension_rows:
            totals[row.report_id].append(row.score)
        recomputed = {
            report_id: round(sum(scores) / len(scores))
            for report_id, scores in totals.items()
            if scores
        }

    for row in reports:
        if row.synthesized_at is None:
            continue  # Not finished; it has no grade to report yet.
        overall = row.overall_score
        if overall is None:
            overall = recomputed.get(row.id)
        grade = rating.grade_for_percent(overall)
        if grade is not None:
            grade_counts[grade] += 1

    return AIDashboardOut(
        jobs_with_ai_framework=len(jobs_with_rows),
        framework=FrameworkHealthOut(
            ready_for_candidates=ready,
            awaiting_approval=awaiting,
            pending_generation=pending,
        ),
        assessments=AssessmentFunnelOut(
            invited=invited,
            started=started,
            completed=completed,
            reports_ready=sum(
                1 for row in reports if row.synthesized_at is not None
            ),
        ),
        grades=[
            GradeCountOut(grade=grade, candidates=grade_counts[grade])
            for grade in rating.GRADES
        ],
        # The exact mode `functional_assessment` writes when every provider was
        # down. Matched literally rather than as "anything other than
        # llm_rubric", because `no_transcript` is a different fact (the
        # candidate answered nothing) and counting it here would tell a
        # customer the AI failed when it did not.
        reports_on_fallback=sum(
            1 for row in reports if row.scoring_mode == "deterministic_fallback"
        ),
        total_reports=len(reports),
    )
