"""Hiring pipeline endpoints (spec §3, §4, §7.3).

Assessment selection, status transitions, and interview scheduling. The rule
that shapes all of it: **not every applicant is assessed**. Applicants are
ranked on their resume and profile form, the recruiter picks who is worth
assessing, and only those candidates ever get an assessment — and therefore a
PPI Assessment Report.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentUser, get_tenant_db, require_capability
from app.services import assessment_invitations, capabilities as caps
from app.services import email_outbox
from app.services import hiring_pipeline as pipeline
from app.services import telemetry_events
from app.services.audit import audit

router = APIRouter()


# ── Schemas ──────────────────────────────────────────────────────────────────

class SelectCandidatesIn(BaseModel):
    """Which applicants to invite to the assessment."""
    link_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class SelectCandidatesOut(BaseModel):
    invited: int
    #: Applications that could not be invited, each with the reason. Never
    #: silently dropped — a recruiter who ticked 20 boxes needs to know which
    #: 3 did not go out and why.
    skipped: list[dict] = []


class ChangeStatusIn(BaseModel):
    status: str
    #: Internal note. Recorded on the history row; never shown to the candidate.
    remarks: str | None = None
    #: Set false to record the decision without mailing the candidate — useful
    #: when the conversation already happened by phone.
    send_email: bool = True


class TransitionOptionOut(BaseModel):
    """One entry of the Decision / "Move to" dropdown."""

    status: str
    label: str


class ApplicationStatusOut(BaseModel):
    link_id: uuid.UUID
    status: str
    stage_label: str
    status_updated_at: datetime | None = None
    #: The MANUAL move set. `shortlisted` is deliberately absent (client
    #: decision, 2026-07-28) while remaining a live stage in the FSM: see
    #: services/hiring_pipeline.MANUAL_TRANSITION_EXCLUDED.
    allowed_transitions: list[str] = []
    allowed_transition_options: list[TransitionOptionOut] = []
    timeline: list[dict] = []
    email_queued: bool = False


class TransitionsOut(BaseModel):
    """Everything the "Move to" dropdown needs for one application."""

    link_id: uuid.UUID
    status: str
    stage_label: str
    #: True when the application is at a terminal stage and nothing may be
    #: picked. The UI disables the control rather than showing an empty menu.
    is_terminal: bool = False
    options: list[TransitionOptionOut] = []


class ScheduleInterviewIn(BaseModel):
    scheduled_at: datetime
    stage_name: str = Field(default="Interview", max_length=100)
    interviewer_id: uuid.UUID | None = None
    notes: str | None = None


class InterviewStageOut(BaseModel):
    id: uuid.UUID
    stage_number: int
    stage_name: str
    scheduled_at: datetime
    completed_at: datetime | None = None
    status: str
    feedback: str | None = None


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _link_or_404(session: AsyncSession, user: CurrentUser, link_id: uuid.UUID):
    row = (
        await session.execute(
            text(
                """
                SELECT l.id, l.tenant_id, l.job_id, l.candidate_id, l.status,
                       l.status_updated_at, j.title, j.correlation_id,
                       c.full_name, c.email
                FROM job_candidate_links l
                JOIN jobs j ON j.id = l.job_id
                JOIN candidates c ON c.id = l.candidate_id
                WHERE l.id = :lid
                """
            ),
            {"lid": str(link_id)},
        )
    ).mappings().first()
    # Explicit tenant check is defense in depth; RLS is the boundary.
    if row is None or str(row["tenant_id"]) != str(user.tenant_id):
        raise HTTPException(status_code=404, detail="Application not found")
    return row


# ── Assessment selection (spec §3.1) ─────────────────────────────────────────

@router.post(
    "/jobs/{job_id}/select-candidates",
    response_model=SelectCandidatesOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def select_candidates_for_assessment(
    job_id: uuid.UUID,
    body: SelectCandidatesIn,
    user: CurrentUser = Depends(require_capability(caps.SEND_OUTREACH)),
    session: AsyncSession = Depends(get_tenant_db),
) -> SelectCandidatesOut:
    """Invite selected applicants to the assessment.

    This is the gate the whole pipeline turns on: an application that is never
    selected here never gets an assessment, and therefore never gets a PRISM
    Report. The conversation row IS the invitation, so an uninvited candidate
    cannot reach the questions by guessing a URL.

    `services/assessment_invitations.invite_batch` owns the three passes (read
    and lock, one credit question for the whole batch, write). The invitation
    email is drafted by a dispatched task after the commit, never here.
    """
    try:
        result = await assessment_invitations.invite_batch(
            session,
            tenant_id=uuid.UUID(str(user.tenant_id)),
            job_id=job_id,
            link_ids=list(body.link_ids),
            actor_user_id=user.user_id,
        )
    except assessment_invitations.InvitationRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return SelectCandidatesOut(
        invited=len(result.invited), skipped=list(result.skipped)
    )


# ── Status transitions (spec §3.3 / §7.3) ────────────────────────────────────

@router.post("/applications/{link_id}/change-status", response_model=ApplicationStatusOut)
async def change_status(
    link_id: uuid.UUID,
    body: ChangeStatusIn,
    user: CurrentUser = Depends(require_capability(caps.DECIDE_PROFILE)),
    session: AsyncSession = Depends(get_tenant_db),
) -> ApplicationStatusOut:
    """Move one application along the pipeline, mailing the candidate.

    Illegal moves are refused with 409 and an explanation naming what IS
    available, rather than a bare rejection — the recruiter should be able to
    act on the message without reading the spec.
    """
    row = await _link_or_404(session, user, link_id)
    if pipeline.normalize(body.status) in pipeline.SYSTEM_ONLY_TARGETS:
        raise HTTPException(status_code=409, detail=pipeline.SYSTEM_ONLY_REFUSAL)
    if pipeline.normalize(body.status) == pipeline.ASSESSMENT_INVITED:
        # "Move to: Assessment invitation sent" IS an invitation, so it goes
        # through the one invitation path. Applied directly it wrote the stage
        # with no `assessment_conversations` row (which IS the invitation):
        # the candidate was mailed a link the start route refused, no credit
        # was asked about, and `select-candidates` could never invite them
        # afterwards because they were no longer at `applied` (PLAN-p3 WP1).
        # The invitation email is always sent, by a worker after the commit,
        # because it carries the candidate's only way in; `send_email` cannot
        # withhold it, and `email_queued` says so. Inviting takes the
        # invitation's own capability as well as this route's: a person who
        # may decide on a profile but not invite must not invite by this door.
        # (`assessment_invitations.invite_by_stage_move`, shared with the
        # dashboard's stage control.)
        try:
            result = await assessment_invitations.invite_by_stage_move(
                session,
                tenant_id=uuid.UUID(str(user.tenant_id)),
                job_id=uuid.UUID(str(row["job_id"])),
                link_id=link_id,
                actor_user_id=user.user_id,
                actor_role=user.role,
                remarks=body.remarks,
            )
        except assessment_invitations.InvitationRefused as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        queued = True
    else:
        try:
            result = await pipeline.apply_transition(
                session,
                link_id=link_id,
                tenant_id=uuid.UUID(str(user.tenant_id)),
                target=body.status,
                actor_user_id=user.user_id,
                remarks=body.remarks,
            )
        except pipeline.InvalidTransition as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        queued = False

    # Master Directive Part 2 section 5.1: EV_HM_DECISION, one row per explicit
    # pipeline decision. Feeds SLA_PR / PRL (services/metrics.py).
    await telemetry_events.emit(
        session,
        tenant_id=user.tenant_id,
        event_code=telemetry_events.EV_HM_DECISION,
        job_id=row["job_id"],
        candidate_id=row["candidate_id"],
        job_candidate_link_id=link_id,
        actor_user_id=user.user_id,
        correlation_id=row["correlation_id"],
        payload={"from_status": result.previous, "to_status": result.status},
    )

    if (
        body.send_email
        and result.email_type
        and result.status != pipeline.ASSESSMENT_INVITED
    ):
        # Drafted here, in the request: a single stage change is one draft,
        # and the recruiter is told whether it was queued. The send itself is
        # dispatched after this request commits (`email_outbox`). The
        # invitation is the exception above: a worker drafts it.
        queued = (
            await email_outbox.queue_transition_email(
                session,
                link_id=link_id,
                email_type=result.email_type,
                sent_by=user.user_id,
            )
        ) is not None

    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="application_status_changed",
        target_type="job_candidate_link",
        target_id=link_id,
        metadata={
            "from": result.previous, "to": result.status, "email_queued": queued,
        },
    )
    return ApplicationStatusOut(
        link_id=link_id,
        status=result.status,
        stage_label=result.stage_label,
        status_updated_at=result.changed_at,
        allowed_transitions=sorted(pipeline.manual_transitions(result.status)),
        allowed_transition_options=[
            TransitionOptionOut(**opt)
            for opt in pipeline.transition_options(result.status)
        ],
        timeline=await pipeline.timeline(session, link_id),
        email_queued=queued,
    )


@router.get("/applications/{link_id}/status", response_model=ApplicationStatusOut)
async def application_status(
    link_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_REVIEW_SCREEN)),
    session: AsyncSession = Depends(get_tenant_db),
) -> ApplicationStatusOut:
    """Current stage plus the full history."""
    row = await _link_or_404(session, user, link_id)
    current = pipeline.normalize(row["status"])
    return ApplicationStatusOut(
        link_id=link_id,
        status=current,
        stage_label=pipeline.STAGE_LABELS.get(current, current),
        status_updated_at=row["status_updated_at"],
        allowed_transitions=sorted(pipeline.manual_transitions(current)),
        allowed_transition_options=[
            TransitionOptionOut(**opt) for opt in pipeline.transition_options(current)
        ],
        timeline=await pipeline.timeline(session, link_id),
    )


@router.get("/applications/{link_id}/transitions", response_model=TransitionsOut)
async def application_transitions(
    link_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_REVIEW_SCREEN)),
    session: AsyncSession = Depends(get_tenant_db),
) -> TransitionsOut:
    """The Decision / "Move to" options for one application.

    The UI must not carry its own copy of the stage list: the moment it does,
    a change here (such as withdrawing "Shortlisted" as a manual choice on
    2026-07-28) has to be made twice and one of the two will be forgotten.
    This endpoint is the single answer to "what may I pick right now".
    """
    row = await _link_or_404(session, user, link_id)
    current = pipeline.normalize(row["status"])
    return TransitionsOut(
        link_id=link_id,
        status=current,
        stage_label=pipeline.STAGE_LABELS.get(current, current),
        is_terminal=current in pipeline.TERMINAL,
        options=[
            TransitionOptionOut(**opt) for opt in pipeline.transition_options(current)
        ],
    )


# ── Interview rounds (spec §6.1 interview_stages) ────────────────────────────

@router.post(
    "/applications/{link_id}/schedule-interview",
    response_model=InterviewStageOut,
    status_code=status.HTTP_201_CREATED,
)
async def schedule_interview(
    link_id: uuid.UUID,
    body: ScheduleInterviewIn,
    user: CurrentUser = Depends(require_capability(caps.SCHEDULE_INTERVIEWS)),
    session: AsyncSession = Depends(get_tenant_db),
) -> InterviewStageOut:
    """Book an interview round, numbering it automatically.

    Multiple rounds per application are the norm, so `stage_number` is derived
    from what already exists rather than asked for — a recruiter should not
    have to remember whether this is round two or three.
    """
    row = await _link_or_404(session, user, link_id)
    next_number = (
        await session.execute(
            text(
                "SELECT COALESCE(MAX(stage_number), 0) + 1 FROM interviews "
                "WHERE job_candidate_link_id = :lid"
            ),
            {"lid": str(link_id)},
        )
    ).scalar_one()

    interview_id = uuid.uuid4()
    await session.execute(
        text(
            """
            INSERT INTO interviews
                (id, tenant_id, job_candidate_link_id, scheduled_at, stage_number,
                 stage_name, interviewer_id, notes, status, created_at)
            VALUES
                (:iid, :tid, :lid, :at, :num, :name, :interviewer, :notes,
                 'scheduled', now())
            """
        ),
        {
            "iid": str(interview_id), "tid": str(user.tenant_id), "lid": str(link_id),
            "at": body.scheduled_at, "num": next_number, "name": body.stage_name,
            "interviewer": str(body.interviewer_id) if body.interviewer_id else None,
            "notes": body.notes,
        },
    )

    # Advance the pipeline alongside the booking, but only when that is a legal
    # move — booking a second round while one is already scheduled leaves the
    # status where it is, and must not fail because of that.
    if pipeline.can_transition(row["status"], pipeline.INTERVIEW_SCHEDULED):
        result = await pipeline.apply_transition(
            session,
            link_id=link_id,
            tenant_id=uuid.UUID(str(user.tenant_id)),
            target=pipeline.INTERVIEW_SCHEDULED,
            actor_user_id=user.user_id,
            remarks=f"Round {next_number}: {body.stage_name}",
        )
        if result.email_type:
            # The date and time are passed through for the prompt to repeat
            # VERBATIM. Reformatting or converting a timezone here is how
            # someone ends up missing their interview.
            await email_outbox.queue_transition_email(
                session,
                link_id=link_id,
                email_type=result.email_type,
                sent_by=user.user_id,
                extra_context={
                    "stage_name": body.stage_name,
                    "scheduled_at": body.scheduled_at.strftime(
                        "%A %d %B %Y at %H:%M UTC"
                    ),
                },
            )

    await audit(
        session,
        tenant_id=user.tenant_id,
        actor_user_id=user.user_id,
        action="interview_scheduled",
        target_type="job_candidate_link",
        target_id=link_id,
        metadata={"round": next_number, "stage_name": body.stage_name},
    )
    return InterviewStageOut(
        id=interview_id,
        stage_number=next_number,
        stage_name=body.stage_name,
        scheduled_at=body.scheduled_at,
        status="scheduled",
    )


@router.get("/jobs/{job_id}/candidate-pipeline")
async def candidate_pipeline(
    job_id: uuid.UUID,
    user: CurrentUser = Depends(require_capability(caps.VIEW_REVIEW_SCREEN)),
    session: AsyncSession = Depends(get_tenant_db),
) -> dict:
    """Applicant counts grouped by stage, in pipeline order.

    Every stage is present even at zero, so the funnel does not visually
    reshape itself as candidates move — an empty column is information.
    """
    # An all-zero funnel for a job that does not exist (mistyped id, or another
    # tenant's job) is indistinguishable from a real job nobody has applied to,
    # so the UI renders a confident empty state for a job the caller cannot
    # see. Every sibling endpoint 404s here; this one silently did not.
    exists = (
        await session.execute(
            text("SELECT 1 FROM jobs WHERE id = :jid AND tenant_id = :tid"),
            {"jid": str(job_id), "tid": str(user.tenant_id)},
        )
    ).first()
    if exists is None:
        raise HTTPException(status_code=404, detail="Job not found")

    rows = (
        await session.execute(
            text(
                """
                SELECT l.status, count(*) AS n
                FROM job_candidate_links l
                JOIN jobs j ON j.id = l.job_id
                WHERE l.job_id = :jid AND j.tenant_id = :tid
                  AND l.archived_at IS NULL
                GROUP BY l.status
                """
            ),
            {"jid": str(job_id), "tid": str(user.tenant_id)},
        )
    ).mappings().all()

    counts: dict[str, int] = {stage: 0 for stage in pipeline.PIPELINE_ORDER}
    for row in rows:
        counts[pipeline.normalize(row["status"])] = (
            counts.get(pipeline.normalize(row["status"]), 0) + row["n"]
        )
    return {
        "job_id": str(job_id),
        "stages": [
            {
                "status": stage,
                "label": pipeline.STAGE_LABELS.get(stage, stage),
                "count": counts.get(stage, 0),
            }
            for stage in pipeline.PIPELINE_ORDER
        ],
        "total": sum(counts.values()),
    }
