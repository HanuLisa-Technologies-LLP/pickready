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
from app.services import audit, capabilities as caps

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


# ═══════════════════════════════════════════════════════════════════════════
# THE CANDIDATE DASHBOARD (spec-doc6 §8)
# ═══════════════════════════════════════════════════════════════════════════
#
# The client's daily working surface: eight columns over every candidate the
# caller may see, plus the three panels the row's action columns open (Ready
# Pick Profile, Team Review, Stage), plus the two calibration surfaces D8 and
# spec-doc6 §8.2 require.
#
# EVERY ROUTE HERE IS AUTHORIZED BY DATA, NEVER BY A ROLE NAME
# -------------------------------------------------------------
# `rbac.require_authorized(capability)` runs the whole RBAC §3 chain BEFORE the
# handler: tenant (a cross-tenant hit is 404, never 403, so the refusal is not
# an existence oracle), the §24 ceiling, the grant, per-job assignment scope
# for a SCOPED cell, and resource state. The one thing this module decides for
# itself is which capability each control belongs to, and that mapping is the
# table `DASHBOARD_CONTROLS` below so the conformance test can read it rather
# than restate it.
#
# THE CAPABILITY EACH CONTROL USES, AND WHY
# ------------------------------------------
#   the list, the profile panel  VIEW_CANDIDATE_RATINGS / VIEW_CANDIDATE_REPORTS
#                                 (RBAC §24: YES, YES, scoped x3)
#   stage move                    UPDATE_PIPELINE_STATUS
#                                 (§24: YES, YES, scoped, NO*, NO -- which is
#                                  exactly spec-doc6 §8.2's requirement)
#   team review write             ADD_TEAM_REVIEW_REMARK
#                                 (§24: YES, YES, YES*, YES*, YES)
#   integrity disposition         INTEGRITY_DISPOSITION
#                                 (spec-doc6 C7: HR Manager by right, Super
#                                  Admin by audited override, nobody else)
#
# ONE DELIBERATE COMPOSITION, AND IT IS WORTH READING BEFORE CHANGING
# --------------------------------------------------------------------
# Writing a Team Review requires BOTH `ADD_TEAM_REVIEW_REMARK` and the SCOPED
# `VIEW_CANDIDATE_RATINGS` on the same job. §24 grants the Recruiter and the
# Hiring Manager the remark capability with no scope marker, but it marks
# "View candidates" and "View candidate ratings" as scoped for all three
# non-org-wide roles, and §23 is explicit that holding a role is not owning a
# job. Remarking on a candidate you are not allowed to see is incoherent, so
# the view check is required as well. This is not "restrict more when unsure"
# overriding an affirmative grant: the grant is affirmative about the
# CAPABILITY and silent about the SCOPE, and the scope rule comes from the
# rows that do speak to it.
#
# ONE CAPABILITY IS REUSED, AND IT IS FLAGGED RATHER THAN HIDDEN
# ----------------------------------------------------------------
# The audited calibration view (D8) is "restricted to Super Admin and HR
# Manager", which is exactly the population `INTEGRITY_DISPOSITION` already
# encodes -- ALLOW for the HR Manager, ALLOW_AUDITED_EXCEPTION for the Super
# Admin, DENY for everybody else. It is reused rather than duplicated because a
# second capability with an identical cell set is a second thing to keep in
# step, and `capabilities.py` is owned by other work this phase. A dedicated
# `VIEW_CALIBRATION_INTERNALS` capability is the right long-term shape and is
# reported as such; `test_dashboard_rbac_matrix.py` pins the exact role set
# that reaches the route, so the day the two populations diverge, a test fails
# rather than a screen leaking.

import datetime as dt

from fastapi import Body, HTTPException, Query, Request
from sqlalchemy import text as sql_text

from app.models.hiring import ReviewDisposition
from app.schemas.dashboard import (
    CalibrationInternalsOut,
    DashboardControlsOut,
    DashboardPageOut,
    DashboardRowOut,
    DivergenceListOut,
    DivergenceOut,
    IntegrityDispositionIn,
    OverrideRateOut,
    ReadyPickProfileOut,
    ReadyPickProfileRefOut,
    StageMoveIn,
    StageOptionsOut,
    TeamReviewEntryOut,
    TeamReviewIn,
    TeamReviewPanelOut,
)
from app.services import calibration as calibration_service
from app.services import dashboard as dashboard_service
from app.services import hiring_pipeline, rbac, reference_code, team_review
from app.services.capabilities import Invariant
from app.services.hiring import gates as hiring_gates

#: control -> the capability that decides it. Read by the conformance test, so
#: the test cannot drift from the routes by restating a mapping.
DASHBOARD_CONTROLS: dict[str, str] = {
    "list": caps.VIEW_CANDIDATE_RATINGS,
    "profile": caps.VIEW_CANDIDATE_REPORTS,
    "team_review": caps.ADD_TEAM_REVIEW_REMARK,
    "stage_move": caps.UPDATE_PIPELINE_STATUS,
    "integrity_disposition": caps.INTEGRITY_DISPOSITION,
    "calibration": caps.INTEGRITY_DISPOSITION,
}

#: What the tooltip says when column 8's control is disabled. The
#: specification asks for an explanation on hover rather than a greyed control
#: with no reason, and the reason is a product fact rather than an apology.
STAGE_DISABLED_REASON = (
    "Moving a candidate through the pipeline is not part of this role. "
    "Ask the assigned Recruiter or an HR Manager."
)
STAGE_DISABLED_UNDER_REVIEW = "Pending integrity review, HR Manager only"
TEAM_REVIEW_DISABLED_REASON = (
    "Adding a Team Review remark is not part of this role."
)


async def _may(
    session: AsyncSession,
    user: CurrentUser,
    capability: str,
    resource: rbac.Resource | None = None,
) -> bool:
    """Whether this caller may exercise one control. Never an enforcement.

    Used only to fill `DashboardControlsOut`, so the UI does not render a
    control the server would refuse. RBAC §3: frontend visibility is not a
    security boundary, and every control this describes is refused again at its
    own route.
    """
    principal = rbac.Principal(
        user_id=user.user_id, tenant_id=user.tenant_id, role=user.role
    )
    decision = await rbac.authorize(session, principal, capability, resource)
    return decision.allowed


def _is_scoped(user: CurrentUser, capability: str) -> bool:
    """Whether this caller's §24 cell narrows them to assigned jobs.

    Reads `capabilities.invariant_for`, which is the matrix as data. A role
    branch here would be exactly the thing claude.md rule 3 forbids, and it
    would also silently stop tracking the matrix.
    """
    return caps.invariant_for(user.role, capability) is Invariant.SCOPED


async def _controls(session: AsyncSession, user: CurrentUser) -> DashboardControlsOut:
    can_stage = await _may(session, user, caps.UPDATE_PIPELINE_STATUS)
    can_review = await _may(session, user, caps.ADD_TEAM_REVIEW_REMARK)
    can_disposition = await _may(session, user, caps.INTEGRITY_DISPOSITION)
    return DashboardControlsOut(
        can_move_stage=can_stage,
        stage_disabled_reason=None if can_stage else STAGE_DISABLED_REASON,
        can_team_review=can_review,
        team_review_disabled_reason=None if can_review else TEAM_REVIEW_DISABLED_REASON,
        can_disposition_integrity=can_disposition,
        can_view_calibration=can_disposition,
        scoped_to_assignments=_is_scoped(user, caps.VIEW_CANDIDATE_RATINGS),
    )


# NOTHING IN THIS MODULE COMMITS. `deps.get_tenant_db` opens the request's
# transaction with `session.begin()` and commits on a clean return, so a
# handler that committed would close the transaction the dependency is still
# holding and the request would fail on the way out. Writes `flush()` when
# they need a generated id and let the dependency finish the job -- which is
# also what makes an audit row and the mutation it describes atomic.


def _row_out(row: dashboard_service.DashboardRow) -> DashboardRowOut:
    return DashboardRowOut(
        link_id=row.link_id,
        candidate_id=row.candidate_id,
        full_name=row.full_name,
        system_id=row.system_id,
        job_id=row.job_id,
        job_title=row.job_title,
        source_type=row.source_type,
        source_label=row.source_label,
        pre_screen_grade=row.pre_screen_grade,
        pre_screen_label=row.pre_screen_label,
        ready_pick_score=row.ready_pick_score,
        band=row.band,
        band_label=row.band_label,
        band_screen_reader_label=row.band_screen_reader_label,
        confidence=row.confidence,
        confidence_indicator=row.confidence_indicator,
        confidence_label=row.confidence_label,
        score_range=row.score_range,
        score_range_note=row.score_range_note,
        note=row.note,
        note_is_pending=row.note_is_pending,
        profile=(
            None
            if row.profile is None
            else ReadyPickProfileRefOut(evaluation_id=row.profile.evaluation_id)
        ),
        profile_pending_reason=row.profile_pending_reason,
        team_review_count=row.team_review_count,
        own_verdict=row.own_verdict,
        own_verdict_at=row.own_verdict_at,
        stage=row.stage,
        stage_label=row.stage_label,
        stage_on_hold=row.stage_on_hold,
        stored_status=row.stored_status,
        under_integrity_review=row.under_integrity_review,
        archived=row.archived,
    )


@router.get("/candidates", response_model=DashboardPageOut)
async def dashboard_candidates(
    job_id: uuid.UUID | None = Query(default=None),
    source_type: list[str] | None = Query(default=None),
    stage: list[str] | None = Query(default=None),
    pre_screen_grade: list[str] | None = Query(default=None),
    search: str | None = Query(default=None, max_length=120),
    include_archived: bool = Query(default=False),
    sort: str | None = Query(default=None),
    direction: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(
        default=dashboard_service.PAGE_SIZE, ge=1, le=dashboard_service.MAX_PAGE_SIZE
    ),
    user: CurrentUser = Depends(require_capability(caps.VIEW_CANDIDATE_RATINGS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> DashboardPageOut:
    """One page of the eight-column dashboard.

    Filtered, sorted and counted in SQL BEFORE the page is cut, because
    filtering a fetched page in the browser makes the match count depend on
    which page happened to be loaded. The order carries a trailing
    `created_at, id` so it is TOTAL: without that, two candidates sharing a
    score can swap between two fetches and one of them appears twice or not at
    all.

    Scope comes from the §24 cell, not from a role name. A Recruiter, a Hiring
    Manager and an Interview Manager see the candidates on the jobs they are
    ASSIGNED to (`job_assignments`, RBAC §9.2 and §23); the HR Manager and the
    Super Admin see the whole tenant.
    """
    # An unknown filter value is refused rather than ignored. Silently dropping
    # it would answer a narrower question than the one that was asked while
    # reporting a total for the wider one.
    for value in source_type or ():
        if value not in dashboard_service.SOURCE_TYPES:
            raise HTTPException(status_code=422, detail=f"Unknown source {value!r}")
    for value in pre_screen_grade or ():
        if value not in dashboard_service.PRE_SCREEN_GRADES:
            raise HTTPException(
                status_code=422, detail=f"Unknown pre-screen grade {value!r}"
            )
    stage_values = {s.value for s in hiring_pipeline.CandidatePipelineStage}
    for value in stage or ():
        if value not in stage_values:
            raise HTTPException(status_code=422, detail=f"Unknown stage {value!r}")
    if sort is not None and sort not in dashboard_service.SORT_KEYS:
        raise HTTPException(status_code=422, detail=f"Unknown sort {sort!r}")

    page_result = await dashboard_service.candidates_page(
        session,
        tenant_id=user.tenant_id,
        viewer_id=user.user_id,
        scoped_to_assignments=_is_scoped(user, caps.VIEW_CANDIDATE_RATINGS),
        job_id=job_id,
        source_types=source_type,
        stages=stage,
        pre_screen_grades=pre_screen_grade,
        search=search,
        include_archived=include_archived,
        sort=sort,
        direction=direction,
        page=page,
        page_size=page_size,
    )
    return DashboardPageOut(
        rows=[_row_out(row) for row in page_result.rows],
        total=page_result.total,
        page=page_result.page,
        page_size=page_result.page_size,
        controls=await _controls(session, user),
        stages=[s.value for s in hiring_pipeline.CandidatePipelineStage],
    )


async def _link_or_404(
    session: AsyncSession,
    job_id: uuid.UUID,
    link_id: uuid.UUID,
    tenant_id,
) -> dict:
    """One application, or the same 404 a cross-tenant hit produces.

    The link must belong to the job named in the path. Without that check the
    job in the URL would be decorative: authorization ran against a job the
    caller owns while the row came from one they do not, which is a scope
    bypass that looks correct in a diff.
    """
    row = (
        await session.execute(
            sql_text(
                "SELECT link.id, link.tenant_id, link.job_id, link.candidate_id, "
                "       link.status, cand.full_name "
                "FROM job_candidate_links link "
                "JOIN candidates cand ON cand.id = link.candidate_id "
                "WHERE link.id = :lid"
            ),
            {"lid": str(link_id)},
        )
    ).mappings().first()
    if (
        row is None
        or str(row["tenant_id"]) != str(tenant_id)
        or str(row["job_id"]) != str(job_id)
    ):
        raise HTTPException(status_code=404, detail="Not found")
    return dict(row)


async def _latest_evaluation(session: AsyncSession, link_id: uuid.UUID) -> dict | None:
    row = (
        await session.execute(
            sql_text(
                "SELECT id, aggregate_json, dimension_scores, competency_scores, "
                "       triangulation_json, gate_results_json, confidence, "
                "       needs_human_review, scorecard_version, company_dna_version, "
                "       situation_type, scoring_mode, completed_at "
                "FROM evaluations WHERE link_id = :lid "
                "ORDER BY created_at DESC, id DESC LIMIT 1"
            ),
            {"lid": str(link_id)},
        )
    ).mappings().first()
    return None if row is None else dict(row)


async def _under_integrity_review(session: AsyncSession, evaluation: dict | None) -> bool:
    """G3 recorded as failed, with no human disposition against it.

    Both halves. A failed gate is a finding; a recorded decision is what closes
    it. No flag auto-clears and no flag rejects anybody: this locks the stage
    control and nothing else.
    """
    if evaluation is None:
        return False
    failed = any(
        result.get("gate") == hiring_gates.G3 and result.get("passed") is False
        for result in (evaluation.get("gate_results_json") or [])
    )
    if not failed:
        return False
    disposed = (
        await session.execute(
            sql_text(
                "SELECT 1 FROM review_dispositions WHERE evaluation_id = :eid LIMIT 1"
            ),
            {"eid": str(evaluation["id"])},
        )
    ).first()
    return disposed is None


@router.get(
    "/jobs/{job_id}/candidates/{link_id}/profile",
    response_model=ReadyPickProfileOut,
)
async def ready_pick_profile(
    job_id: uuid.UUID,
    link_id: uuid.UUID,
    user: CurrentUser = Depends(
        rbac.require_authorized(caps.VIEW_CANDIDATE_REPORTS, job_id_param="job_id")
    ),
    session: AsyncSession = Depends(get_tenant_db),
) -> ReadyPickProfileOut:
    """Column 6's slide-over panel: the evidence behind the score.

    NAMED per-dimension ratings, never raw D1-D5 numbers (spec-doc6 D8 / C2).
    The raw numbers are `/calibration` below, which two roles reach and every
    read of which is logged.

    404 when no Ready Pick Profile has been written. Not an empty panel: a
    panel with five blank dimensions is indistinguishable from a candidate the
    evaluators found nothing on, and the row's disabled button has already said
    the honest thing.
    """
    link = await _link_or_404(session, job_id, link_id, user.tenant_id)
    evaluation = await _latest_evaluation(session, link_id)
    if evaluation is None:
        raise HTTPException(
            status_code=404,
            detail=dashboard_service.PROFILE_PENDING_REASON,
        )
    payload = dashboard_service.profile_panel(
        evaluation=evaluation,
        candidate_name=link["full_name"],
        system_id=reference_code.reference_code(
            user.tenant_id, job_id, link["candidate_id"]
        ),
        under_integrity_review=await _under_integrity_review(session, evaluation),
    )
    return ReadyPickProfileOut(evaluation_id=evaluation["id"], **{
        key: value for key, value in payload.items() if key != "artifact"
    })


@router.get(
    "/jobs/{job_id}/candidates/{link_id}/calibration",
    response_model=CalibrationInternalsOut,
)
async def calibration_internals(
    request: Request,
    job_id: uuid.UUID,
    link_id: uuid.UUID,
    user: CurrentUser = Depends(
        rbac.require_authorized(caps.INTEGRITY_DISPOSITION, job_id_param="job_id")
    ),
    session: AsyncSession = Depends(get_tenant_db),
) -> CalibrationInternalsOut:
    """Raw D1-D5 numbers, evaluator outputs and aggregation internals.

    spec-doc6 D8: internal engine state, not a product surface. Restricted to
    Super Admin and HR Manager, and ALWAYS LOGGED WHEN VIEWED -- the audit row
    is written before the payload is built, and `audit.record_action` raises on
    failure so a read that could not be recorded does not commit.
    """
    link = await _link_or_404(session, job_id, link_id, user.tenant_id)
    evaluation = await _latest_evaluation(session, link_id)
    if evaluation is None:
        raise HTTPException(status_code=404, detail="Not found")
    await calibration_service.log_calibration_view(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        actor_role=getattr(user.role, "value", user.role),
        evaluation_id=evaluation["id"],
        job_id=job_id,
        link_id=link_id,
        candidate_id=link["candidate_id"],
        # RBAC §7.5: the Super Admin's reach into another role's surface is an
        # override, and an override is recorded AS one.
        exceptional=caps.invariant_for(user.role, caps.INTEGRITY_DISPOSITION)
        is Invariant.ALLOW_AUDITED_EXCEPTION,
    )
    payload = calibration_service.calibration_view(evaluation)
    payload.pop("artifact", None)
    return CalibrationInternalsOut(**payload)


# ── Column 7: Team Review ────────────────────────────────────────────────────


def _verdict_entry(row, viewer_id) -> TeamReviewEntryOut:
    return TeamReviewEntryOut(
        id=row["id"],
        reviewer_user_id=row["reviewer_user_id"],
        reviewer_email=row.get("reviewer_email"),
        reviewer_role=row.get("reviewer_role"),
        verdict=row["rating"],
        verdict_label=team_review.VERDICT_LABELS.get(row["rating"], row["rating"]),
        remarks=row["remarks"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        # RBAC §29: nobody may silently alter another interviewer's remark.
        # Enforced at the write path, which only ever touches the caller's own
        # row; this flag exists so the panel does not offer a control that
        # would be refused.
        editable=str(row["reviewer_user_id"]) == str(viewer_id),
    )


async def _team_review_panel(
    session: AsyncSession,
    *,
    link: dict,
    job_id: uuid.UUID,
    tenant_id,
    viewer_id,
    can_write: bool,
) -> TeamReviewPanelOut:
    rows = (
        await session.execute(
            sql_text(
                "SELECT tr.id, tr.reviewer_user_id, tr.rating, tr.remarks, "
                "       tr.created_at, tr.updated_at, "
                "       u.email AS reviewer_email, u.role AS reviewer_role "
                "FROM candidate_team_reviews tr "
                "LEFT JOIN users u ON u.id = tr.reviewer_user_id "
                "WHERE tr.job_candidate_link_id = :lid "
                "ORDER BY tr.updated_at DESC, tr.id"
            ),
            {"lid": str(link["id"])},
        )
    ).mappings().all()
    return TeamReviewPanelOut(
        link_id=link["id"],
        candidate_name=link["full_name"],
        system_id=reference_code.reference_code(
            tenant_id, job_id, link["candidate_id"]
        ),
        verdicts=list(team_review.VERDICTS),
        verdict_labels=dict(team_review.VERDICT_LABELS),
        entries=[_verdict_entry(row, viewer_id) for row in rows],
        can_write=can_write,
    )


@router.get(
    "/jobs/{job_id}/candidates/{link_id}/team-review",
    response_model=TeamReviewPanelOut,
)
async def team_review_panel(
    job_id: uuid.UUID,
    link_id: uuid.UUID,
    user: CurrentUser = Depends(
        rbac.require_authorized(caps.VIEW_CANDIDATE_RATINGS, job_id_param="job_id")
    ),
    session: AsyncSession = Depends(get_tenant_db),
) -> TeamReviewPanelOut:
    """Every reviewer's verdict, with its author and timestamp (RBAC §29).

    Read is gated on seeing the candidate, not on being able to write: an
    Interview Manager's whole purpose is to read the panel and add to it, and a
    person who may not write still needs to see what the panel says.
    """
    link = await _link_or_404(session, job_id, link_id, user.tenant_id)
    return await _team_review_panel(
        session,
        link=link,
        job_id=job_id,
        tenant_id=user.tenant_id,
        viewer_id=user.user_id,
        can_write=await _may(session, user, caps.ADD_TEAM_REVIEW_REMARK),
    )


@router.put(
    "/jobs/{job_id}/candidates/{link_id}/team-review",
    response_model=TeamReviewPanelOut,
)
async def upsert_team_review(
    job_id: uuid.UUID,
    link_id: uuid.UUID,
    payload: TeamReviewIn = Body(...),
    user: CurrentUser = Depends(
        rbac.require_authorized(caps.ADD_TEAM_REVIEW_REMARK, job_id_param="job_id")
    ),
    session: AsyncSession = Depends(get_tenant_db),
) -> TeamReviewPanelOut:
    """The caller's OWN verdict. Never anybody else's (RBAC §29).

    The row is keyed on (link, reviewer) and the statement can only ever touch
    the caller's own: there is no reviewer id in the request body, so editing
    somebody else's remark is not refused, it is unexpressible.

    THIS IS ALSO THE DIVERGENCE ROUTING POINT (spec-doc6 §8.2). When the
    verdict disagrees with the Ready Pick Score a `CalibrationRecord` is
    raised and audited, which is what puts it in the Super Admin activity view.
    Nothing about that reaches the reviewer: no warning, no confirmation step,
    no second-guessing prompt, no different response. Measure, never nudge.
    """
    if payload.verdict not in team_review.VERDICTS:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown verdict; expected one of {list(team_review.VERDICTS)}",
        )
    # A remark on a candidate this caller may not see is incoherent. §24 marks
    # candidate visibility scoped for the three per-job roles even where it
    # leaves the remark capability unscoped, so both are required. See the
    # module header.
    resource = await rbac.load_job_resource(session, job_id)
    if resource is None or not await _may(
        session, user, caps.VIEW_CANDIDATE_RATINGS, resource
    ):
        raise HTTPException(status_code=404, detail="Not found")

    link = await _link_or_404(session, job_id, link_id, user.tenant_id)
    review_id = (
        await session.execute(
            sql_text(
                """
                INSERT INTO candidate_team_reviews
                    (id, tenant_id, job_candidate_link_id, reviewer_user_id,
                     rating, remarks, updated_at)
                VALUES (:new_id, :tenant_id, :link_id, :reviewer, :rating,
                        :remarks, now())
                ON CONFLICT (job_candidate_link_id, reviewer_user_id)
                DO UPDATE SET rating = EXCLUDED.rating,
                              remarks = EXCLUDED.remarks,
                              updated_at = now()
                RETURNING id
                """
            ),
            {
                # `candidate_team_reviews` carries no database-side default for
                # its primary key (the ORM mixin generates it), so the id is
                # supplied here. On CONFLICT this value is discarded and
                # RETURNING gives back the EXISTING row's id, which is what
                # keeps a refined verdict pointing at the same divergence
                # record instead of minting a second one.
                "new_id": str(uuid.uuid4()),
                "tenant_id": str(user.tenant_id),
                "link_id": str(link_id),
                "reviewer": str(user.user_id),
                "rating": payload.verdict,
                "remarks": payload.remarks.strip(),
            },
        )
    ).scalar_one()

    await audit.record_action(
        session,
        action=audit.TEAM_REVIEW_REMARK_ADDED,
        actor_user_id=user.user_id,
        actor_role=getattr(user.role, "value", user.role),
        tenant_id=user.tenant_id,
        resource_type="candidate_team_review",
        resource_id=review_id,
        job_id=job_id,
        application_id=link_id,
        candidate_id=link["candidate_id"],
        new_state={"verdict": payload.verdict},
    )

    evaluation = await _latest_evaluation(session, link_id)
    aggregate = (evaluation or {}).get("aggregate_json") or {}
    await calibration_service.raise_divergence(
        session,
        tenant_id=user.tenant_id,
        job_id=job_id,
        evaluation_id=None if evaluation is None else evaluation["id"],
        team_review_id=review_id,
        reviewer_user_id=user.user_id,
        reviewer_role=getattr(user.role, "value", user.role),
        verdict=payload.verdict,
        machine_grade=aggregate.get("overall_grade") or None,
        machine_confidence=(evaluation or {}).get("confidence"),
        link_id=link_id,
        candidate_id=link["candidate_id"],
    )

    return await _team_review_panel(
        session,
        link=link,
        job_id=job_id,
        tenant_id=user.tenant_id,
        viewer_id=user.user_id,
        can_write=True,
    )


# ── Column 8: Stage ──────────────────────────────────────────────────────────


def _stage_options(status: str, *, can_move: bool, reason: str | None) -> StageOptionsOut:
    stage = hiring_pipeline.dashboard_stage(status)
    return StageOptionsOut(
        stage=None if stage is None else stage.value,
        stage_label=(
            f"On hold, paused at {status.replace('_', ' ')}"
            if stage is None
            else stage.value
        ),
        stored_status=status,
        # From the server, always. The UI hardcodes no stage list: the FSM is
        # the only thing that knows which moves are legal from here.
        allowed_transitions=hiring_pipeline.transition_options(status)
        if can_move
        else [],
        can_move=can_move,
        disabled_reason=reason,
    )


@router.get(
    "/jobs/{job_id}/candidates/{link_id}/stage", response_model=StageOptionsOut
)
async def stage_options(
    job_id: uuid.UUID,
    link_id: uuid.UUID,
    user: CurrentUser = Depends(
        rbac.require_authorized(caps.VIEW_CANDIDATE_RATINGS, job_id_param="job_id")
    ),
    session: AsyncSession = Depends(get_tenant_db),
) -> StageOptionsOut:
    """The current stage and the moves this caller may make from it.

    Read is gated on seeing the candidate; the MOVE is gated separately, and a
    caller who may look but not move gets the stage with an empty option list
    and the reason why.
    """
    link = await _link_or_404(session, job_id, link_id, user.tenant_id)
    evaluation = await _latest_evaluation(session, link_id)
    under_review = await _under_integrity_review(session, evaluation)
    can_move = await _may(session, user, caps.UPDATE_PIPELINE_STATUS)
    reason: str | None = None
    if under_review:
        can_move = False
        reason = STAGE_DISABLED_UNDER_REVIEW
    elif not can_move:
        reason = STAGE_DISABLED_REASON
    return _stage_options(
        hiring_pipeline.normalize(link["status"]), can_move=can_move, reason=reason
    )


@router.post(
    "/jobs/{job_id}/candidates/{link_id}/stage", response_model=StageOptionsOut
)
async def move_stage(
    job_id: uuid.UUID,
    link_id: uuid.UUID,
    payload: StageMoveIn = Body(...),
    user: CurrentUser = Depends(
        rbac.require_authorized(caps.UPDATE_PIPELINE_STATUS, job_id_param="job_id")
    ),
    session: AsyncSession = Depends(get_tenant_db),
) -> StageOptionsOut:
    """Move one candidate through the pipeline.

    Two independent locks, and neither is decorative:

      * `rbac.require_authorized` refuses the Hiring Manager and the Interview
        Manager outright (§24) and refuses a Recruiter on a job they are not
        assigned to (§9.2, §23). It ALSO refuses anybody while
        `under_integrity_review` is true, through `rbac._state_rules`.
      * `hiring_pipeline.assert_transition` refuses an illegal move. Each stage
        carries a promise -- `assessment_completed` means a report exists -- and
        the transition emails reference it.

    The integrity lock is re-checked here rather than trusted from the row
    loader, because `require_authorized` loads a JOB resource and the finding
    is on the APPLICATION.
    """
    link = await _link_or_404(session, job_id, link_id, user.tenant_id)
    evaluation = await _latest_evaluation(session, link_id)
    if await _under_integrity_review(session, evaluation):
        raise HTTPException(status_code=403, detail=STAGE_DISABLED_UNDER_REVIEW)

    previous = hiring_pipeline.normalize(link["status"])
    try:
        result = await hiring_pipeline.apply_transition(
            session,
            link_id=link_id,
            tenant_id=user.tenant_id,
            target=payload.status,
            actor_user_id=user.user_id,
            remarks=payload.remarks,
        )
    except hiring_pipeline.InvalidTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Not found") from exc

    await audit.record_action(
        session,
        action=audit.CANDIDATE_STAGE_MOVED,
        actor_user_id=user.user_id,
        actor_role=getattr(user.role, "value", user.role),
        tenant_id=user.tenant_id,
        resource_type="job_candidate_link",
        resource_id=link_id,
        job_id=job_id,
        application_id=link_id,
        candidate_id=link["candidate_id"],
        previous_state={"status": previous},
        new_state={"status": result.status},
    )
    return _stage_options(result.status, can_move=True, reason=None)


# ── The integrity disposition (spec-doc6 C7) ─────────────────────────────────


@router.post("/jobs/{job_id}/candidates/{link_id}/integrity-disposition")
async def integrity_disposition(
    job_id: uuid.UUID,
    link_id: uuid.UUID,
    payload: IntegrityDispositionIn = Body(...),
    user: CurrentUser = Depends(
        rbac.require_authorized(caps.INTEGRITY_DISPOSITION, job_id_param="job_id")
    ),
    session: AsyncSession = Depends(get_tenant_db),
) -> dict:
    """A person looked at an integrity finding and decided something.

    Workflow 2's third step. HR Manager by right, Super Admin as an audited
    override, nobody else.

    Note what is recorded: that a person DECIDED, not that they approved. All
    four dispositions are accepted, including `rejected`, and there is no
    `auto_cleared` and never will be. A disposition the pipeline could write
    would satisfy the gate without a human, which is the entire thing the gate
    exists to prevent.
    """
    if payload.disposition not in hiring_gates.DISPOSITIONS:
        raise HTTPException(
            status_code=422,
            detail=(
                "Unknown disposition; expected one of "
                f"{sorted(hiring_gates.DISPOSITIONS)}"
            ),
        )
    link = await _link_or_404(session, job_id, link_id, user.tenant_id)
    evaluation = await _latest_evaluation(session, link_id)
    if evaluation is None:
        raise HTTPException(status_code=404, detail="Not found")

    flags = [
        result
        for result in (evaluation.get("gate_results_json") or [])
        if result.get("passed") is False
    ]
    row = ReviewDisposition(
        tenant_id=user.tenant_id,
        evaluation_id=evaluation["id"],
        evaluation_ref=evaluation["id"],
        job_id=job_id,
        link_id=link_id,
        disposition=payload.disposition,
        decided_by=user.user_id,
        # COPIED, not joined. A later rescore must not silently change what the
        # reviewer is recorded as having seen.
        flags_json=flags,
        note=payload.note,
    )
    session.add(row)
    await session.flush()
    await audit.record_action(
        session,
        action=audit.INTEGRITY_DISPOSITION_RECORDED,
        actor_user_id=user.user_id,
        actor_role=getattr(user.role, "value", user.role),
        tenant_id=user.tenant_id,
        resource_type="review_disposition",
        resource_id=row.id,
        job_id=job_id,
        application_id=link_id,
        candidate_id=link["candidate_id"],
        new_state={"disposition": payload.disposition},
        exceptional=caps.invariant_for(user.role, caps.INTEGRITY_DISPOSITION)
        is Invariant.ALLOW_AUDITED_EXCEPTION,
    )
    # The row re-evaluates itself: the finding is now disposed of, so the stage
    # control unlocks. Reported back rather than left to a refetch, so
    # workflow 2's last step is one round trip.
    return {
        "disposition": payload.disposition,
        "under_integrity_review": False,
        "stage": _stage_options(
            hiring_pipeline.normalize(link["status"]), can_move=True, reason=None
        ).model_dump(),
    }


# ── Divergence routing and the override rate (spec-doc6 §8.2) ────────────────


@router.get("/calibration/divergences", response_model=DivergenceListOut)
async def calibration_divergences(
    job_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: CurrentUser = Depends(require_capability(caps.INTEGRITY_DISPOSITION)),
    session: AsyncSession = Depends(get_tenant_db),
) -> DivergenceListOut:
    """The Standards Board's queue: Team Review verdicts that disagreed.

    MEASURE, NEVER NUDGE. This is a list and a rate, for the people who
    maintain the scorecard. It carries no target, no threshold and no verdict
    about any reviewer, and it is reachable only by the two roles that can act
    on a calibration problem. A recruiter never sees their own override rate,
    because a recruiter shown a deviation figure stops deviating and the signal
    dies.

    The reviewer's REMARK is deliberately absent. It belongs to its author and
    is read on the Team Review panel, with their name attached.
    """
    rows = await calibration_service.divergences(
        session, tenant_id=user.tenant_id, job_id=job_id, limit=limit, offset=offset
    )
    rate = await calibration_service.override_rate(
        session, tenant_id=user.tenant_id, job_id=job_id
    )
    return DivergenceListOut(
        divergences=[DivergenceOut(**row) for row in rows],
        override_rate=OverrideRateOut(**rate.as_dict()),
    )
