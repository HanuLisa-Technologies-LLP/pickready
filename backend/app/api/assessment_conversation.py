"""The candidate's side of the assessment: the invitation link, consent, turns, voice.

Carved out of `api/assessments.py` on 2026-09-24 (PLAN-p3 WP0), then rebuilt the
same day (PLAN-p3 WP3) around the conversation ENGINE in
`services/assessment_conversation/`: the routes resolve who is asking and which
turn they name, and the engine takes the answer. `api/assessments.py` keeps the
staff side; `api/assessment_recording.py` holds the session recording routes.

WHAT CHANGED, IN ONE PLACE
--------------------------
- ONE MODE. The mode routes are gone; consent is one text (`GET/POST
  .../consent`).
- THE SERVER KEEPS THE TIME (Appendix B section 3). Every answer names its
  `turn_seq`; the deadline is computed from rows the server wrote; a late
  answer is replaced by what the server holds.
- NO EDITING. The answer-edit route is deleted; past exchanges come back
  read-only on every response.
- THE FIRST START re-checks credit, locks the contract in the same transaction
  that stamps `started_at` (`assessment_contract.lock_contract`) and logs the
  `stage=vaada` digest; questions written against a different contract are
  regenerated before anything is asked.
- SPOKEN ANSWERS, transcribed server-side with the clock paused, and final.
- Work about a committed row is dispatched AFTER the commit.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.api.assessments import READY_FOR_CANDIDATES
from app.api.deps import (
    CurrentUser,
    get_candidate_db,
    get_current_candidate,
    get_optional_candidate,
    get_public_db,
)
from app.core.config import get_settings
from app.models.assessment import AssessmentConversation, CandidateQuestion
from app.models.candidate import JobCandidateLink
from app.models.dual_mode import MODE_CONVERSATIONAL
from app.models.job import Job
from app.models.tenant import Tenant
from app.models.user import User
from app.models.assessment_pause import PAUSE_TRANSCRIPTION
from app.models.voice import (
    VOICE_CONSUMED,
    VOICE_FAILED,
    VOICE_RECORDING,
    VOICE_TRANSCRIBED,
    VOICE_TRANSCRIBING,
    VOICE_UPLOADED,
    VoiceAnswer,
)
from app.schemas.assessment_conversation import (
    AssessmentConsentIn,
    ConsentStateOut,
    ConsentTermsOut,
    ConversationMessageIn,
    ConversationOut,
    DraftIn,
    HistoryEntryOut,
    InvitationResolveOut,
    TurnOut,
    VoiceAnswerOut,
    VoiceBeginIn,
)
from app.schemas.assessments import QuestionOut
from app.services import (
    assessment_consent,
    assessment_contract,
    assessment_invite,
    candidate_identity,
    consent_catalog,
    cost_telemetry,
    credits,
    hiring_pipeline,
    job_posting,
    locks,
    object_storage,
)
from app.services.assessment_conversation import pauses, timers, turns
from app.services.assessment_formats import types as question_types
# PROCTORING IS MANDATORY (proctoring-spec-doc.md, principle P4). The gate is
# this module's only import-time dependency on the proctoring package, so the
# scoring isolation the package promises stays visible in the import graph.
from app.services.proctoring import gate as proctoring_gate
from app.services.rate_limit import rate_limit
# The sanctioned media package: a spoken answer is stored and transcribed
# there, and nothing here reads media back.
from app.services.video import transcribe, voice
from app.workers.dispatch import dispatch, dispatch_after_commit

logger = logging.getLogger(__name__)

router = APIRouter()

STATUS_PREPARING = "preparing"
STATUS_PAUSED = "paused"
PREPARING_LABEL = "Preparing your assessment"

NOT_INVITED_DETAIL = (
    "This assessment is not open to you yet. The hiring team invites "
    "candidates individually, and you will be emailed if they invite you."
)
#: What a candidate reads when the employer's credit cannot pay for a NEW
#: assessment. It names no billing term: the balance is the employer's
#: business, and the employer sees the refusal on their own screens.
START_CREDIT_DETAIL = (
    "This assessment cannot be started right now. Please try again later, and "
    "contact the hiring team if this continues."
)
RETIRED_MODE_DETAIL = (
    "This assessment was begun as a video interview, which is no longer "
    "offered, so it cannot continue here. Please contact the hiring team."
)
VOICE_UNAVAILABLE_DETAIL = (
    "Speaking an answer is not available for this assessment. Please type it."
)
VOICE_NOT_PROSE_DETAIL = "This question is not answered by speaking."
VOICE_NOT_FAILED_DETAIL = "That recording has not failed, so there is nothing to acknowledge."


# ── The assessment invitation link (2026-08-11) ──────────────────────────────
#
# The email carries a signed token rather than a raw application id, and it
# lands on a PUBLIC page whose only job is to route. This endpoint is what that
# page asks. It is deliberately the one place that knows the order the checks
# run in, because the order is the whole design:
#
#   token validity -> is the application still real -> is anyone signed in ->
#   is it the RIGHT person -> is the posting window still open -> has this
#   already been submitted -> has the recruiter actually invited them
#
# Signature before world, identity before state. Checking "already submitted"
# before "wrong account" would tell a stranger holding the link whether a
# particular candidate had finished their assessment.

_INVITE_STATE_MESSAGES = {
    "expired": (
        "This assessment link has expired. Ask the hiring team to send a new "
        "invitation and it will work straight away."
    ),
    "invalid": (
        "This assessment link could not be read. It may have been broken by "
        "your email client, so try copying the whole address from the message."
    ),
    "window_closed": (
        "This job posting has closed, so the assessment is no longer open. "
        "Nothing you have already sent is lost."
    ),
    "not_invited": NOT_INVITED_DETAIL,
    "needs_auth": (
        "Sign in to your candidate account to open this assessment."
    ),
    "ready": "Your assessment is ready.",
    "in_progress": (
        "You have already started this assessment. Your saved answers are "
        "still there and you will pick up where you left off."
    ),
    "completed": (
        "You have already submitted this assessment. There is nothing more to "
        "answer."
    ),
    "gone": (
        "That application is no longer available. If you believe this is a "
        "mistake, reply to the invitation email and the hiring team can check."
    ),
}


def _invite_out(
    state: str,
    *,
    redirect_to: str | None = None,
    message: str | None = None,
    **extra: Any,
) -> InvitationResolveOut:
    return InvitationResolveOut(
        state=state,
        redirect_to=redirect_to,
        message=message or _INVITE_STATE_MESSAGES.get(state, "This link could not be opened."),
        **extra,
    )


# Abuse control, not authorization (services/rate_limit). This endpoint is
# unauthenticated and does real database work per call. 30/min is far above
# anyone clicking a link from an email, and well below what probing for valid
# tokens would need -- which is the only other reason to call it in volume.
@router.get("/invitations/{token}", response_model=InvitationResolveOut,
    dependencies=[Depends(rate_limit("assessment_invitation", limit=30, window=60))],
)
async def resolve_invitation(
    token: str,
    user: CurrentUser | None = Depends(get_optional_candidate),
    session: AsyncSession = Depends(get_public_db),
) -> InvitationResolveOut:
    """Resolve an emailed assessment link into exactly one next step.

    Answers 200 for every outcome, including the refusals. That is deliberate:
    the caller is a landing page that must render a specific explanation for
    each state, and a 401/404/410 would collapse five different situations into
    "something went wrong". The refusal is in the `state` field, and no state
    carries a `redirect_to` it has not earned.
    """
    try:
        payload = assessment_invite.verify(token)
    except assessment_invite.InviteTokenError as exc:
        return _invite_out(exc.reason)

    link_id = uuid.UUID(str(payload["link_id"]))
    invited_email = str(payload.get("email") or "")

    link = await session.get(JobCandidateLink, link_id)
    if link is None:
        return _invite_out("gone")
    job = await session.get(Job, link.job_id)
    if job is None:
        return _invite_out("gone")
    tenant = await session.get(Tenant, link.tenant_id)
    context = {
        "job_title": job.title,
        "company_name": tenant.name if tenant else None,
    }

    # Signed out. The page sends them to /login carrying this same URL as
    # `next`, so the candidate comes back HERE after signing in.
    if user is None:
        return _invite_out(
            "needs_auth",
            invited_email_masked=assessment_invite.mask_email(invited_email),
            **context,
        )

    account = await session.get(User, user.user_id)
    signed_in_email = account.email if account else None
    if not assessment_invite.emails_match(invited_email, signed_in_email):
        # Never silently attach the assessment to whoever happens to be signed
        # in. Both addresses are reported (one masked) because "wrong account"
        # with no way to tell which account is right is an unactionable error.
        return _invite_out(
            "wrong_account",
            invited_email_masked=assessment_invite.mask_email(invited_email),
            signed_in_email=signed_in_email,
            message=(
                "This invitation was sent to a different email address. Sign "
                "out and sign back in with the account it was sent to."
            ),
            **context,
        )

    conversation = (
        await session.execute(
            select(AssessmentConversation).where(
                AssessmentConversation.job_candidate_link_id == link.id
            )
        )
    ).scalars().first()

    # Already submitted wins over the window: a candidate who finished on the
    # last day should be shown their submission, not told the job has closed.
    if conversation is not None and conversation.completed_at is not None:
        return _invite_out(
            "completed",
            redirect_to=f"/portal/applications?application={link.id}",
            **context,
        )

    if not job_posting.can_edit_application(
        applied_at=link.created_at,
        posting_start=job.posting_start_date,
        posting_end_date=job.posting_end_date,
        grace_period_end_date=job.grace_period_end_date,
    ):
        return _invite_out("window_closed", **context)

    if conversation is None or conversation.invitation_sent_at is None:
        return _invite_out("not_invited", **context)

    # REMOVED 2026-09-24: the six-month `retake.decide` classification that fed
    # `recent_prior_report`. Nothing is portable between jobs, so it only ever
    # supplied a sentence, and its `except Exception` swallowed every failure.
    started = conversation.started_at is not None
    return _invite_out(
        "in_progress" if started else "ready",
        redirect_to=f"/portal/assessments/{link.id}",
        **context,
    )


# ── The unified candidate conversation ───────────────────────────────────────


async def _candidate_link(
    session: AsyncSession, user: CurrentUser, link_id: uuid.UUID
) -> tuple[JobCandidateLink, Job]:
    """The signed-in candidate's own application, on a job that is ready.

    The candidate is resolved by `candidate_identity`, the one resolver
    (`candidates.user_id`, never an email match at request time).
    """
    candidate_id = await candidate_identity.resolve_candidate_id(session, user.user_id)
    link = await session.get(JobCandidateLink, link_id)
    if candidate_id is None or link is None or link.candidate_id != candidate_id:
        raise HTTPException(status_code=404, detail="Application not found")
    job = await session.get(Job, link.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    # The review gate: no candidate enters the conversation until the
    # recruiter has saved the skills.
    if job.assessment_status != READY_FOR_CANDIDATES:
        raise HTTPException(
            status_code=409,
            detail=(
                "This assessment is not open yet. The hiring team is still "
                "confirming how this role will be evaluated, and you will be "
                "emailed as soon as it is ready."
            ),
        )
    return link, job


async def _conversation_prompts(
    session: AsyncSession, job: Job, link: JobCandidateLink
) -> list[tuple[str, str, str, CandidateQuestion]]:
    """This candidate's questions in ask order, from the engine."""
    return await turns.conversation_prompts(session, job, link)


def _question_out(row: CandidateQuestion) -> QuestionOut:
    """The prompt on screen as the candidate may see it: the format and the
    CANDIDATE VIEW of the payload. The answer key never crosses here;
    `candidate_view` is an allowlist per type."""
    return QuestionOut(
        id=row.id,
        question_type=row.question_type,
        payload=question_types.candidate_view(row.id, row.question_type, row.payload_json),
        time_allocation_seconds=row.time_allocation_seconds,
    )


async def _ensure_conversation_ready(
    session: AsyncSession, job: Job, link: JobCandidateLink
) -> None:
    """The video interview start's readiness check, kept only for that route.

    RETIRING WITH IT: `api/assessment_recording.start_video_interview` is the
    last caller, and the video interview mode is deleted by the recording work
    package (PLAN-p3 WP5). The conversation's own start no longer calls this:
    it dispatched and then RAISED, so the transaction rolled back after the task
    had already been sent. `start_conversation` returns `preparing` instead and
    dispatches after the commit.
    """
    has_questions = (
        await session.execute(
            select(func.count())
            .select_from(CandidateQuestion)
            .where(CandidateQuestion.job_candidate_link_id == link.id)
        )
    ).scalar_one()
    if has_questions:
        return
    dispatch("pickready.generate_candidate_questions", args=[str(link.id)])
    raise HTTPException(
        status_code=409,
        detail="We are preparing your assessment. Please try again in a moment.",
    )


async def _locked_conversation(
    session: AsyncSession, conversation_id: uuid.UUID
) -> AssessmentConversation:
    """The conversation, row-locked for this request.

    Every route that changes the turn takes the lock, so two requests naming
    the same turn (a double click, a retry racing the original) run one after
    the other and the second finds the turn already moved on.
    """
    conversation = (
        await session.execute(
            select(AssessmentConversation)
            .where(AssessmentConversation.id == conversation_id)
            .with_for_update()
        )
    ).scalars().first()
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conversation


async def _invited_conversation(
    session: AsyncSession, link: JobCandidateLink, *, lock: bool
) -> AssessmentConversation:
    """The conversation behind `link`, which IS the invitation, or a 403."""
    query = select(AssessmentConversation).where(
        AssessmentConversation.job_candidate_link_id == link.id
    )
    if lock:
        query = query.with_for_update()
    conversation = (await session.execute(query)).scalars().first()
    if conversation is None or conversation.invitation_sent_at is None:
        raise HTTPException(status_code=403, detail=NOT_INVITED_DETAIL)
    return conversation


async def _candidate_conversation(
    session: AsyncSession, user: CurrentUser, link_id: uuid.UUID
) -> tuple[JobCandidateLink, Job, AssessmentConversation]:
    """The invited conversation behind a candidate's link, or the refusal the
    start gives: the conversation row IS the invitation."""
    link, job = await _candidate_link(session, user, link_id)
    conversation = await _invited_conversation(session, link, lock=False)
    return link, job, conversation


def _refuse_retired_mode(conversation: AssessmentConversation) -> None:
    """A session BEGUN in the retired video interview mode cannot continue as a
    conversation: its records belong to the other mode. Migration 0123 moved
    every unstarted one; a started one can only be a row from before it."""
    if conversation.mode != MODE_CONVERSATIONAL:
        raise HTTPException(status_code=409, detail=RETIRED_MODE_DETAIL)


def _request_generation(
    session: AsyncSession,
    conversation: AssessmentConversation,
    link: JobCandidateLink,
    *,
    now: datetime,
    force: bool,
) -> None:
    """Ask for this candidate's questions, AFTER the commit, at most once per
    `assessment_question_redispatch_seconds` unless `force`.

    Generation is a Fargate task, and a start polled while it runs must not
    start another each time. A lost invoke is repaired by the next start past
    the window.
    """
    last = conversation.questions_requested_at
    window = get_settings().assessment_question_redispatch_seconds
    if not force and last is not None and (now - last).total_seconds() < window:
        return
    conversation.questions_requested_at = now
    dispatch_after_commit(session, "pickready.generate_candidate_questions", args=[str(link.id)])


async def _regenerate(
    session: AsyncSession,
    conversation: AssessmentConversation,
    link: JobCandidateLink,
    *,
    now: datetime,
) -> None:
    """Throw away questions written against a contract that is not the one
    this start would lock, and ask for them again.

    SAFE BECAUSE NOTHING HAS BEEN ASKED: this runs only while `started_at` is
    NULL, so there is no answer, no transcript and no turn. A NULL digest
    counts as a mismatch, which is what catches a question set generated
    before the digest existed (or for an application that was never invited
    under the current skills).

    Only a STAMPED mismatch re-dispatches at once: those questions were
    written against another contract, so the skills really changed. An
    UNSTAMPED set waits out the redispatch window like any other request,
    because a generator that stopped stamping would otherwise be sent again
    on every poll of this route, one Fargate task each time.
    """
    stamped = conversation.questions_contract_digest is not None
    await session.execute(
        delete(CandidateQuestion).where(CandidateQuestion.job_candidate_link_id == link.id)
    )
    conversation.next_question_index = 0
    conversation.pending_prompt = None
    conversation.pending_question_key = None
    conversation.pending_domain = None
    conversation.pending_kind = None
    conversation.delivered_prompt = None
    conversation.reasks_used = 0
    conversation.follow_ups_used = 0
    conversation.questions_contract_digest = None
    conversation.questions_contract_version = None
    conversation.composition_json = None
    await session.flush()
    logger.info(
        "assessment_start.questions_regenerated conversation_id=%s stamped=%s",
        conversation.id, stamped,
    )
    _request_generation(session, conversation, link, now=now, force=stamped)


async def _lock_and_start(
    session: AsyncSession,
    job: Job,
    link: JobCandidateLink,
    conversation: AssessmentConversation,
    *,
    now: datetime,
) -> bool:
    """Lock the contract and stamp the start, or regenerate. Returns whether
    the conversation started.

    Under the SKILLS advisory lock a skills edit also takes, so the contract
    read here is the contract `lock_contract` freezes: the comparison and the
    lock cannot be split by an edit. The digest the questions carry is compared
    with it BEFORE locking, so a start that has to regenerate locks nothing
    (D5: skills lock when a candidate actually starts).
    """
    await locks.advisory_xact_lock(session, locks.SKILLS, job.id)
    current = await assessment_contract.load_contract(session, job.id)
    if conversation.questions_contract_digest != current.digest:
        await _regenerate(session, conversation, link, now=now)
        return False
    try:
        contract = await assessment_contract.lock_contract(session, job.id, conversation.id)
    except assessment_contract.ContractNotReady as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    conversation.started_at = now
    await session.flush()
    if hiring_pipeline.can_transition(link.status, hiring_pipeline.ASSESSMENT_IN_PROGRESS):
        await hiring_pipeline.apply_transition(
            session,
            link_id=link.id,
            tenant_id=link.tenant_id,
            target=hiring_pipeline.ASSESSMENT_IN_PROGRESS,
        )
    # VAADA'S HALF OF THE DIGEST PAIR. Miti logs `stage=miti` over the same
    # bound snapshot at grading, and a test proves the two are identical.
    assessment_contract.log_digest(assessment_contract.STAGE_VAADA, conversation.id, contract)
    return True


def _preparing(conversation: AssessmentConversation) -> ConversationOut:
    return ConversationOut(
        conversation_id=conversation.id,
        status=STATUS_PREPARING,
        prompt=None,
        progress_label=PREPARING_LABEL,
        answered_questions=0,
        total_questions=0,
    )


async def _view(
    ctx: turns.TurnContext, *, answer_message_id: uuid.UUID | None = None
) -> ConversationOut:
    """The conversation as the candidate sees it NOW: the prompt on screen,
    its clock, and every past exchange read-only."""
    session = ctx.session
    conversation = ctx.conversation
    prompts = ctx.prompts
    now = datetime.now(timezone.utc)
    finished = turns.is_finished(conversation, prompts)
    clock = (
        await turns.turn_clock(session, conversation, now=now)
        if turns.has_open_turn(conversation, prompts)
        else None
    )
    if conversation.status in ("completed", "terminated"):
        view_status = conversation.status
    elif clock is not None and clock.paused:
        view_status = STATUS_PAUSED
    else:
        view_status = "active"
    index = conversation.next_question_index
    total = len(prompts)
    open_turn = clock is not None and conversation.status == "active"
    return ConversationOut(
        conversation_id=conversation.id,
        status=view_status,
        prompt=None if finished else turns.current_prompt_text(conversation, prompts),
        progress_label=(
            f"Question {index + 1} of {total}" if index < total else "Conversation complete"
        ),
        answered_questions=index,
        total_questions=total,
        is_reask=conversation.pending_kind == "reask",
        answer_message_id=answer_message_id,
        question=(
            _question_out(prompts[index].row)
            if not finished and conversation.pending_prompt is None and index < total
            else None
        ),
        turn_seq=conversation.turn_seq if open_turn else 0,
        turn=(
            TurnOut(
                kind=turns.turn_kind(conversation),
                allocation_seconds=clock.allocation_seconds,
                deadline_at=clock.deadline_at,
                server_now=now,
                paused=clock.paused,
                pause_reason=clock.pause_reason,
            )
            if open_turn and clock is not None
            else None
        ),
        voice_input_available=bool(
            open_turn and transcribe.is_available() and turns.is_prose_turn(conversation, prompts)
        ),
        history=[
            HistoryEntryOut(question=question, answer=answer)
            for question, answer in await turns.history(session, conversation.id)
        ],
    )


async def _flush_cost(ctx: turns.TurnContext, tally: Any) -> None:
    """Everything this request spent, onto the application's cost record."""
    await cost_telemetry.flush(
        ctx.session,
        scope=cost_telemetry.AssessmentScope(
            tenant_id=ctx.job.tenant_id, job_id=ctx.job.id, link_id=ctx.link.id
        ),
        tally=tally,
        conversation_turns=ctx.submissions,
        questions_asked=ctx.conversation.next_question_index if ctx.submissions else None,
    )


# Abuse control and COST control, not authorization (services/rate_limit).
# Every turn invokes a model, so these are the most expensive candidate
# surfaces in the product; the ceilings sit well above a real assessment and
# well below a flood, per candidate.
@router.post(
    "/conversations/links/{link_id}/start",
    response_model=ConversationOut,
    dependencies=[Depends(rate_limit("assessment_start", limit=10, window=60))],
)
async def start_conversation(
    link_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> ConversationOut:
    """Open the assessment, or come back to it.

    In order: the invitation (the row IS the invitation), proctoring, the one
    mode, consent; on the FIRST start the credit re-check, then questions
    (missing: `preparing`, dispatched after the commit), then the contract
    (lock and stamp `started_at` in one transaction, or regenerate when the
    questions were written against a different contract). A turn that expired
    while the candidate was away is submitted by the server; the first turn is
    opened once. A reload returns the SAME turn and the SAME clock.
    """
    tally = cost_telemetry.begin()
    link, job = await _candidate_link(session, user, link_id)
    conversation = await _invited_conversation(session, link, lock=True)
    proctoring_session = await proctoring_gate.require_active(session, conversation)
    _refuse_retired_mode(conversation)
    await assessment_consent.require_consent(session, conversation)
    now = datetime.now(timezone.utc)

    if conversation.started_at is None:
        # A running conversation always finishes; a NEW one must be payable.
        allowed, _required, _balance = await credits.can_start_assessment(
            session, job.tenant_id, role_classification=job.role_classification
        )
        if not allowed:
            logger.warning(
                "assessment_start.refused_credit tenant_id=%s job_id=%s link_id=%s",
                job.tenant_id, job.id, link.id,
            )
            raise HTTPException(status_code=402, detail=START_CREDIT_DETAIL)

    rows = await turns.conversation_prompts(session, job, link)
    if not rows:
        _request_generation(session, conversation, link, now=now, force=False)
        return _preparing(conversation)
    if conversation.started_at is None:
        if not await _lock_and_start(session, job, link, conversation, now=now):
            return _preparing(conversation)

    ctx = turns.TurnContext(
        session=session, job=job, link=link, conversation=conversation,
        prompts=turns.as_prompts(rows), proctoring_session=proctoring_session,
    )
    if turns.has_open_turn(conversation, ctx.prompts):
        clock = await turns.turn_clock(session, conversation, now=now)
        if clock is not None and clock.expired:
            await turns.submit_turn(
                ctx, await turns.expiry_submission(ctx, clock), clock=clock,
                server_timeout=True,
            )
    elif conversation.turn_seq == 0 and not turns.is_finished(conversation, ctx.prompts):
        if conversation.pending_prompt is None:
            await turns.write_next_question(ctx)
        await turns.open_turn(session, conversation, ctx.prompts)
    await _flush_cost(ctx, tally)
    return await _view(ctx)


@router.post(
    "/conversations/{conversation_id}/respond",
    response_model=ConversationOut,
    dependencies=[Depends(rate_limit("assessment_turn", limit=40, window=60))],
)
async def respond(
    conversation_id: uuid.UUID,
    body: ConversationMessageIn,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> ConversationOut:
    """Answer the turn on screen, and only that turn.

    A body naming any other turn is a 409 with nothing written. An answer
    arriving after the deadline plus the grace is IGNORED: the server submits
    what it holds for the turn (a transcribed spoken answer, else the draft
    saved in time, else nothing, which is an evidence gap).
    """
    tally = cost_telemetry.begin()
    conversation = await _locked_conversation(session, conversation_id)
    link, job = await _candidate_link(session, user, conversation.job_candidate_link_id)
    if conversation.status == "terminated":
        raise HTTPException(status_code=409, detail=proctoring_gate.SESSION_ENDED_DETAIL)
    _refuse_retired_mode(conversation)
    proctoring_session = await proctoring_gate.require_active(session, conversation)
    prompts = turns.as_prompts(await turns.conversation_prompts(session, job, link))
    if turns.is_finished(conversation, prompts):
        raise HTTPException(status_code=409, detail=turns.COMPLETE_DETAIL)
    turns.require_turn(conversation, body.turn_seq)
    now = datetime.now(timezone.utc)
    clock = await turns.turn_clock(session, conversation, now=now)
    if clock is None:
        raise HTTPException(status_code=409, detail=turns.NO_OPEN_TURN_DETAIL)
    await turns.require_not_device_paused(session, conversation)
    ctx = turns.TurnContext(
        session=session, job=job, link=link, conversation=conversation,
        prompts=prompts, proctoring_session=proctoring_session,
    )
    if clock.expired:
        submission = await turns.expiry_submission(ctx, clock)
    else:
        in_flight = await turns.voice_for_turn(
            session, conversation, statuses=(VOICE_UPLOADED, VOICE_TRANSCRIBING)
        )
        if in_flight is not None and not await turns.fail_stale_voice(session, in_flight, now=now):
            raise HTTPException(status_code=409, detail=turns.VOICE_NOT_READY_DETAIL)
        submission = turns.Submission(
            turn_seq=body.turn_seq,
            answer=body.answer,
            answer_payload=body.answer_payload,
            voice_answer_id=body.voice_answer_id,
            timed_out=body.timed_out,
            behaviour=body.behaviour,
        )
    message = await turns.submit_turn(ctx, submission, clock=clock, server_timeout=clock.expired)
    await _flush_cost(ctx, tally)
    return await _view(ctx, answer_message_id=message.id)


# REMOVED 2026-09-24: the route that rewrote the latest answer. Past answers
# are viewable and never editable (Appendix B section 3); `history` on every
# response is the read-only view.


@router.put(
    "/conversations/{conversation_id}/draft",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(rate_limit("assessment_draft", limit=20, window=60))],
)
async def save_draft(
    conversation_id: uuid.UUID,
    body: DraftIn,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> Response:
    """Keep the candidate's unsent answer for the turn on screen.

    Only for the current turn and only while it is still open: a draft saved
    after the deadline plus the grace is refused, so the one the server
    submits at expiry is one the candidate wrote in time.
    """
    conversation = await _locked_conversation(session, conversation_id)
    link, job = await _candidate_link(session, user, conversation.job_candidate_link_id)
    if conversation.status != "active":
        raise HTTPException(status_code=409, detail=turns.COMPLETE_DETAIL)
    await proctoring_gate.require_active(session, conversation)
    prompts = turns.as_prompts(await turns.conversation_prompts(session, job, link))
    if turns.is_finished(conversation, prompts):
        raise HTTPException(status_code=409, detail=turns.COMPLETE_DETAIL)
    turns.require_turn(conversation, body.turn_seq)
    now = datetime.now(timezone.utc)
    clock = await turns.turn_clock(session, conversation, now=now)
    if clock is None:
        raise HTTPException(status_code=409, detail=turns.NO_OPEN_TURN_DETAIL)
    await turns.require_not_device_paused(session, conversation)
    if clock.expired:
        raise HTTPException(status_code=409, detail=turns.TURN_EXPIRED_DETAIL)
    conversation.draft_answer_json = {
        "answer": body.answer,
        "answer_payload": body.answer_payload,
    }
    conversation.draft_turn_seq = body.turn_seq
    conversation.draft_saved_at = now
    await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Spoken answers ───────────────────────────────────────────────────────────


def _voice_out(row: VoiceAnswer) -> VoiceAnswerOut:
    return VoiceAnswerOut(
        id=row.id,
        status=row.status,
        transcript=row.transcript_text if row.status in (VOICE_TRANSCRIBED, VOICE_CONSUMED) else None,
        message=turns.VOICE_FAILED_DETAIL if row.status == VOICE_FAILED else None,
        max_seconds=get_settings().assessment_voice_max_seconds,
    )


async def _open_voice_turn(
    session: AsyncSession,
    user: CurrentUser,
    conversation_id: uuid.UUID,
    turn_seq: int | None,
) -> tuple[turns.TurnContext, timers.TurnClock]:
    """The conversation, its open turn and its clock, for a voice route. The
    same gates as an answer: invitation, proctoring, the turn, the device."""
    conversation = await _locked_conversation(session, conversation_id)
    link, job = await _candidate_link(session, user, conversation.job_candidate_link_id)
    if conversation.status != "active":
        raise HTTPException(status_code=409, detail=turns.COMPLETE_DETAIL)
    proctoring_session = await proctoring_gate.require_active(session, conversation)
    prompts = turns.as_prompts(await turns.conversation_prompts(session, job, link))
    if turns.is_finished(conversation, prompts):
        raise HTTPException(status_code=409, detail=turns.COMPLETE_DETAIL)
    if turn_seq is not None:
        turns.require_turn(conversation, turn_seq)
    clock = await turns.turn_clock(session, conversation, now=datetime.now(timezone.utc))
    if clock is None:
        raise HTTPException(status_code=409, detail=turns.NO_OPEN_TURN_DETAIL)
    await turns.require_not_device_paused(session, conversation)
    ctx = turns.TurnContext(
        session=session, job=job, link=link, conversation=conversation,
        prompts=prompts, proctoring_session=proctoring_session,
    )
    return ctx, clock


async def _voice_row(
    session: AsyncSession, conversation: AssessmentConversation, voice_id: uuid.UUID
) -> VoiceAnswer:
    row = await session.get(VoiceAnswer, voice_id)
    if row is None or row.conversation_id != conversation.id:
        raise HTTPException(status_code=404, detail="Recording not found")
    return row


@router.post(
    "/conversations/{conversation_id}/voice/begin",
    response_model=VoiceAnswerOut,
    dependencies=[Depends(rate_limit("assessment_voice", limit=10, window=60))],
)
async def begin_voice_answer(
    conversation_id: uuid.UUID,
    body: VoiceBeginIn,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> VoiceAnswerOut:
    """Start speaking the answer to the prose turn on screen.

    One spoken answer per turn: a second begin returns the one in flight, a
    transcript already made is final, and after a FAILED transcription the
    answer is typed.
    """
    ctx, clock = await _open_voice_turn(session, user, conversation_id, body.turn_seq)
    conversation = ctx.conversation
    if clock.expired:
        raise HTTPException(status_code=409, detail=turns.TURN_EXPIRED_DETAIL)
    if not transcribe.is_available():
        raise HTTPException(status_code=409, detail=VOICE_UNAVAILABLE_DETAIL)
    if not turns.is_prose_turn(conversation, ctx.prompts):
        raise HTTPException(status_code=409, detail=VOICE_NOT_PROSE_DETAIL)
    now = datetime.now(timezone.utc)
    existing = await turns.voice_for_turn(
        session, conversation,
        statuses=(VOICE_RECORDING, VOICE_UPLOADED, VOICE_TRANSCRIBING, VOICE_TRANSCRIBED,
                  VOICE_CONSUMED, VOICE_FAILED),
    )
    if existing is not None:
        await turns.fail_stale_voice(session, existing, now=now)
        if existing.status == VOICE_FAILED:
            raise HTTPException(status_code=409, detail=turns.VOICE_FAILED_DETAIL)
        return _voice_out(existing)
    row_for_turn = turns.current_row(conversation, ctx.prompts)
    if row_for_turn is None:
        raise HTTPException(status_code=409, detail=turns.NO_OPEN_TURN_DETAIL)
    row = VoiceAnswer(
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.id,
        question_id=row_for_turn.id,
        turn_seq=conversation.turn_seq,
        status=VOICE_RECORDING,
        capture_started_at=now,
    )
    session.add(row)
    await session.flush()
    return _voice_out(row)


@router.post(
    "/conversations/{conversation_id}/voice/{voice_id}/audio",
    response_model=VoiceAnswerOut,
    dependencies=[Depends(rate_limit("assessment_voice", limit=10, window=60))],
)
async def upload_voice_audio(
    conversation_id: uuid.UUID,
    voice_id: uuid.UUID,
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> VoiceAnswerOut:
    """The recording of one spoken answer.

    Read with a ceiling one byte above `assessment_voice_max_bytes`, so an
    oversized upload is refused without ever being held whole. Stored
    encrypted under `voice-answers/`, the clock is PAUSED while Transcribe
    runs, and the transcription is dispatched after the commit.
    """
    ctx, clock = await _open_voice_turn(session, user, conversation_id, None)
    conversation = ctx.conversation
    row = await _voice_row(session, conversation, voice_id)
    if row.turn_seq != conversation.turn_seq:
        raise HTTPException(status_code=409, detail=turns.VOICE_NOT_FOR_TURN_DETAIL)
    if row.status != VOICE_RECORDING:
        return _voice_out(row)
    if clock.expired:
        raise HTTPException(status_code=409, detail=turns.TURN_EXPIRED_DETAIL)
    ceiling = int(get_settings().assessment_voice_max_bytes)
    data = await file.read(ceiling + 1)
    try:
        extension = voice.validate_audio(data, file.content_type)
    except voice.VoiceUploadRefused as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    key = voice.audio_key(conversation.id, row.id, extension)
    now = datetime.now(timezone.utc)
    try:
        size = await run_in_threadpool(
            voice.store_audio, key=key, data=data, content_type=file.content_type or ""
        )
    except object_storage.ObjectStorageError as exc:
        # Nothing to transcribe, so this answer is typed. A FAILED row is the
        # honest record; there is no pause to hold, because nothing is running.
        logger.warning(
            "assessment_voice.store_failed voice_id=%s error=%s", row.id, type(exc).__name__
        )
        row.status = VOICE_FAILED
        row.failure_reason = f"The recording could not be stored: {type(exc).__name__}"
        row.failed_at = now
        await session.flush()
        return _voice_out(row)
    row.status = VOICE_UPLOADED
    row.s3_key = key
    row.content_type = voice.media_type(file.content_type)
    row.size_bytes = size
    row.uploaded_at = now
    await turns.open_transcription_pause(session, row, now=now)
    await session.flush()
    # AFTER THE COMMIT: the task reads this row. The pause is CAPPED at the
    # Transcribe budget, so a lost invoke can never leave a candidate paused
    # for ever, and the row is failed on read past the same budget
    # (`turns.fail_stale_voice`) so they are told to type.
    dispatch_after_commit(session, "pickready.transcribe_voice_answer", args=[str(row.id)])
    return _voice_out(row)


@router.get(
    "/conversations/{conversation_id}/voice/{voice_id}",
    response_model=VoiceAnswerOut,
    dependencies=[Depends(rate_limit("assessment_voice_status", limit=60, window=60))],
)
async def voice_answer_status(
    conversation_id: uuid.UUID,
    voice_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> VoiceAnswerOut:
    """Where a spoken answer stands; the FINAL transcript once it exists."""
    conversation = await _locked_conversation(session, conversation_id)
    await _candidate_link(session, user, conversation.job_candidate_link_id)
    row = await _voice_row(session, conversation, voice_id)
    await turns.fail_stale_voice(session, row, now=datetime.now(timezone.utc))
    await session.flush()
    return _voice_out(row)


@router.post(
    "/conversations/{conversation_id}/voice/{voice_id}/acknowledge",
    response_model=VoiceAnswerOut,
    dependencies=[Depends(rate_limit("assessment_voice", limit=10, window=60))],
)
async def acknowledge_voice_failure(
    conversation_id: uuid.UUID,
    voice_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> VoiceAnswerOut:
    """The candidate has read that the transcription failed: the clock resumes
    now rather than at the end of the failure window, and they type."""
    conversation = await _locked_conversation(session, conversation_id)
    await _candidate_link(session, user, conversation.job_candidate_link_id)
    row = await _voice_row(session, conversation, voice_id)
    if row.status != VOICE_FAILED:
        raise HTTPException(status_code=409, detail=VOICE_NOT_FAILED_DETAIL)
    now = datetime.now(timezone.utc)
    if row.acknowledged_at is None:
        row.acknowledged_at = now
        await pauses.close_pause(
            session, conversation.id, PAUSE_TRANSCRIPTION, at=now, ref_id=row.id
        )
    await session.flush()
    return _voice_out(row)


# ── Consent: one text, one mode ──────────────────────────────────────────────
#
# REMOVED 2026-09-24 with the video interview mode (Appendix B section 1):
# `GET/POST /conversations/links/{id}/mode`, `_mode_frozen` and `_mode_state`.
# There is one assessment mode and one consent text.


def _consent_state(consented: bool) -> ConsentStateOut:
    terms = assessment_consent.terms_for()
    return ConsentStateOut(
        consented=consented,
        consent=ConsentTermsOut(
            text=terms.text,
            consent_version=terms.consent_version,
            privacy_policy_version=terms.privacy_policy_version,
            terms_version=terms.terms_version,
            # Stage B items (vivekium feature 6), served so the screen never
            # authors consent copy of its own.
            items=consent_catalog.stage_payload(consent_catalog.STAGE_ASSESSMENT),
        ),
    )


@router.get("/conversations/links/{link_id}/consent", response_model=ConsentStateOut)
async def get_assessment_consent(
    link_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> ConsentStateOut:
    """The consent terms exactly as the server will stamp them, and whether
    this session has accepted them (spec 3.2: the wording is configuration, so
    the client renders what it is given and holds no copy of its own)."""
    _link, _job, conversation = await _candidate_conversation(session, user, link_id)
    consent = await assessment_consent.find_consent(session, conversation.id)
    return _consent_state(consent is not None)


@router.post("/conversations/links/{link_id}/consent", response_model=ConsentStateOut)
async def accept_assessment_consent(
    link_id: uuid.UUID,
    body: AssessmentConsentIn,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> ConsentStateOut:
    """Record the candidate's acceptance (3.4). Only acceptance is recorded;
    a decline stores nothing. Idempotent per session. Every Stage B item must
    be ticked (vivekium feature 6)."""
    link, _job, conversation = await _candidate_conversation(session, user, link_id)
    await assessment_consent.record_consent(
        session,
        conversation,
        candidate_id=link.candidate_id,
        consent_keys=body.consent_keys,
    )
    return _consent_state(True)
