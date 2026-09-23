"""The candidate's side of the assessment: the invitation link, the conversation, mode and consent.

Carved out of `api/assessments.py` on 2026-09-24 (PLAN-p3 WP0) as a PURE MOVE:
identical URLs under the same `/api/v2/assessments` prefix, identical
dependencies, identical behaviour. `api/assessments.py` keeps the staff side
(job setup review, the Tatva matrix, the Job SWOT, reports and transcripts);
`api/assessment_recording.py` holds the video and session-media routes.
"""
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentUser,
    get_candidate_db,
    get_current_candidate,
    get_optional_candidate,
    get_public_db,
)
from app.models.assessment import (
    AssessmentAnswer,
    AssessmentConversation,
    AssessmentMessage,
    CandidateQuestion,
    # LEGACY, read-only: a transcript written before Draft v4 keyed its
    # technical exchanges on this table, and the recruiter's transcript view
    # still has to resolve those keys to a label.
    JobCompetency,
)
from app.models.candidate import Candidate, JobCandidateLink, Profile
from app.models.job import Job
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.assessments import (
    AssessmentConsentIn,
    AssessmentModeIn,
    ConsentTermsOut,
    ConversationAnswerEditIn,
    ConversationMessageIn,
    ConversationOut,
    ModeStateOut,
    InvitationResolveOut,
    QuestionOut,
)
# The proctored session's recording start. It lives beside the client-portal
# video schemas rather than with the assessment ones because it is the
# candidate-side half of the SAME artifact those schemas deliver, and both
# halves are bound by the same rule: no bucket name and no object key.
from app.services.assessment_questions import budget as question_budget
from app.services import (
    answer_classification,
    assessment_consent,
    assessment_invite,
    consent_catalog,
    conversation_guardrails,
    cost_telemetry,
    credit_reconciliation,
    hiring_pipeline,
    interview_telemetry,
    interviewer,
    job_posting,
    ppi_interview,
    retake,
    telemetry_events,
)
# DUAL-MODE ASSESSMENT (2026-09-05 spec). The video package is reached ONLY by
# these routes and the processing task, never by a scorer; the models carry the
# consent audit and the recording lifecycle.
from app.models.dual_mode import (
    MODE_CONVERSATIONAL,
    VideoRecording,
)
from app.services.assessment_formats import rendering as format_rendering
from app.services.assessment_formats import scoring as format_scoring
from app.services.assessment_formats import types as question_types
# PROCTORING IS MANDATORY (proctoring-spec-doc.md, principle P4). The gate is
# this module's only import-time dependency on the proctoring package; the
# behaviour recorder and the report loader are reached inside the handlers
# that need them, so the scoring isolation the package promises stays
# visible in the import graph as gate-plus-report-join and nothing else.
from app.services.proctoring import gate as proctoring_gate
from app.workers.dispatch import dispatch
from app.services.rate_limit import rate_limit

from app.api.assessments import READY_FOR_CANDIDATES

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_REASKS_PER_QUESTION = 2


# ── The assessment invitation link (2026-08-11) ──────────────────────────────
#
# The email now carries a signed token rather than a raw application id, and it
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
    "not_invited": (
        "This assessment is not open to you yet. The hiring team invites "
        "candidates individually, and you will be emailed if they invite you."
    ),
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
    "something went wrong" -- which is the generic-error failure this codebase
    keeps having to fix. The refusal is in the `state` field, and no state
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
    # `next`, which is the whole point of the change: the candidate comes back
    # HERE after signing in, not to the jobs board.
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

    # The six-month classification. Under PPI nothing is portable between jobs
    # (services/retake), so this never skips the assessment -- it only supplies
    # the sentence explaining why the candidate is answering questions again.
    recent_prior = False
    try:
        decision = await retake.decide(session, link.candidate_id, link.job_id)
        recent_prior = decision.decision == retake.DECISION_REUSE
    except Exception:  # pragma: no cover - classification is never load-bearing
        logger.exception("invitation.retake_classification_failed link_id=%s", link.id)

    started = conversation.started_at is not None
    return _invite_out(
        "in_progress" if started else "ready",
        redirect_to=f"/portal/assessments/{link.id}",
        recent_prior_report=recent_prior,
        **context,
    )


# ── The unified candidate conversation (spec §8) ─────────────────────────────


async def _candidate_link(session: AsyncSession, user: CurrentUser, link_id: uuid.UUID) -> tuple[JobCandidateLink, Job]:
    candidate = (
        await session.execute(select(Candidate).where(Candidate.user_id == user.user_id))
    ).scalars().first()
    link = await session.get(JobCandidateLink, link_id)
    if candidate is None or link is None or link.candidate_id != candidate.id:
        raise HTTPException(status_code=404, detail="Application not found")
    job = await session.get(Job, link.job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    # The review gate (spec §5, amended 2026-08-04): no candidate enters the
    # conversation until the recruiter has saved the PPI FRAMEWORK. The
    # technical bank stopped gating anything in the same change, so it is no
    # longer named here or in the message the candidate reads.
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


# REMOVED 2026-08-05: `_CONNECTORS`, eight canned openers ("Great.",
# "Understood.", "Thanks for that.", "Appreciate the detail." ...) prepended to
# every question by POSITION.
#
# They were the literal thing the brief forbids: templated acknowledgment
# strings, chosen by `position % 8` and therefore blind to what the candidate
# had just said. An answer of "I do not know" was met with "Appreciate the
# detail." That is worse than saying nothing, because it tells the candidate
# the interviewer is not reading -- and it is the single most visible reason
# the assessment read as a form rather than a conversation.
#
# The job they were doing (making the blended technical/PPI sequence read as
# ONE conversation, spec §8) is now done properly by
# `interviewer.compose_next_question`, which writes the transition against the
# actual transcript, or writes nothing when there is no real connection to
# draw. A bare question is always better than a false acknowledgment.


async def _dimension_coverage(
    session: AsyncSession, link: JobCandidateLink
) -> tuple[int, int]:
    """(matrix items with substantive evidence, matrix items probed at all).

    This is what Vaada's stopping rule reads. Two properties are doing the work.

    COUNTED PER COMPETENCY, NOT PER QUESTION. A matrix item can be probed by
    more than one question, and a follow-up is filed under its parent question's
    key. Counting questions would let three questions against one competency
    look like three covered dimensions, and the conversation would close having
    asked about a third of the matrix.

    ONLY A SUBSTANTIVE ANSWER COUNTS. `answer_label` is the classifier's own
    verdict (services/answer_classification), and empty, gibberish, off-topic and
    evasive replies are all excluded. This is the direction that matters: if a
    non-answer counted as coverage, a candidate could end their own assessment
    early by not answering, which is precisely backwards. A NULL label is
    treated as substantive, because every degradation path in the classifier
    returns "substantive" by design and a provider outage must not silently
    start withholding coverage.
    """
    row = (
        await session.execute(
            text(
                """
                SELECT
                    COUNT(DISTINCT q.competency_id) FILTER (
                        WHERE m.id IS NOT NULL
                          AND COALESCE(m.answer_label, 'substantive') = 'substantive'
                    ) AS covered,
                    COUNT(DISTINCT q.competency_id) AS total
                FROM candidate_questions q
                LEFT JOIN assessment_messages m
                       ON m.question_key = CAST(q.id AS text)
                      AND m.speaker = 'candidate'
                WHERE q.job_candidate_link_id = :link_id
                """
            ),
            {"link_id": str(link.id)},
        )
    ).first()
    if row is None:
        return 0, 0
    return int(row[0] or 0), int(row[1] or 0)


async def _coverage_rows(
    session: AsyncSession, link: JobCandidateLink
) -> list[dict[str, Any]]:
    """Per-matrix-item evidence counts, for Vaada's explicit conversation state.

    A SECOND READ BESIDE `_dimension_coverage`, AND DELIBERATELY SO. That
    function is the stopping rule's own input and its exact SQL is pinned by
    `tests/test_conversation_key_contract.py` -- the join key, the DISTINCT
    competency count and the substantive filter are each pinned because getting
    any of them wrong grades every candidate on the job Not Matching with no
    error anywhere. Folding it into this richer read would put the product's
    only comparability guarantee behind a refactor whose breakage is silent.
    Two small indexed aggregates on one turn is the cheaper mistake.

    Grouped by competency, never by question: several questions can probe one
    matrix item and a follow-up is filed under its parent's key, so counting
    questions would let a third of the matrix look like full coverage.
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT c.name AS dimension,
                       COUNT(m.id) AS answers,
                       COUNT(m.id) FILTER (
                           WHERE COALESCE(m.answer_label, 'substantive') = 'substantive'
                       ) AS substantive,
                       COUNT(m.id) FILTER (WHERE m.evidence_gap) AS gaps
                  FROM candidate_questions q
                  JOIN job_competencies c ON c.id = q.competency_id
                  LEFT JOIN assessment_messages m
                         ON m.question_key = CAST(q.id AS text)
                        AND m.speaker = 'candidate'
                 WHERE q.job_candidate_link_id = :link_id
                 GROUP BY c.id, c.name, c.category, c.ordinal
                 ORDER BY c.category, c.ordinal
                """
            ),
            {"link_id": str(link.id)},
        )
    ).all()
    return [
        {
            "dimension": str(row[0]),
            "answers": int(row[1] or 0),
            "substantive": int(row[2] or 0),
            "gaps": int(row[3] or 0),
        }
        for row in rows
    ]


async def _ledger_dimension_flags(
    session: AsyncSession, job: Job, link: JobCandidateLink
) -> tuple[set[str], set[str]]:
    """(dimensions the ledger contradicts, dimensions it only inferred).

    THE MITI SIDE OF THE LOOP. Whether two readings of a dimension disagree is a
    question about the evidence ledger, not about the transcript, so it is read
    from the ledger rather than guessed at from what the candidate typed. That
    is what makes this a loop rather than two agents talking past each other:
    Miti records what it read, and Vaada asks about what does not add up while
    there is still a candidate to ask.

    NEVER LOAD-BEARING. A ledger that is unavailable, empty, or has not been
    written for this application yet returns two empty sets, and the
    conversation behaves exactly as it did before the ledger existed. The
    opposite direction would be far worse: a ledger outage that made every
    conversation refuse to close would strand every candidate in the product
    mid-assessment.
    """
    from app.services.evidence import ledger

    try:
        claims = await ledger.load_claims(
            session, tenant_id=job.tenant_id, job_id=job.id, link_id=link.id
        )
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.info("assessments.evidence_unavailable link_id=%s", link.id)
        return set(), set()
    conflicting = {
        claim.dimension
        for claim in claims
        if claim.status == ledger.CLAIM_CONTRADICTED
    }
    # `inferred_only` is the product agreeing with itself: everything behind the
    # dimension was concluded rather than stated or confirmed. That is exactly
    # the shape a conversation can fix, by asking.
    inferred = {
        claim.dimension
        for claim in claims
        if claim.status == ledger.CLAIM_INFERRED_ONLY
    }
    return conflicting, inferred


async def _conversation_state(
    session: AsyncSession,
    job: Job,
    link: JobCandidateLink,
    conversation: AssessmentConversation,
    total_written: int,
) -> "interviewer.ConversationState":
    """Where this conversation has got to, as one explicit value.

    INTERNAL ONLY. It is logged for an operator and read by the stop decision;
    no field of it reaches a response schema, and the no-numbers rule covers its
    counts exactly as it covers a score.
    """
    rows = await _coverage_rows(session, link)
    conflicting, inferred = await _ledger_dimension_flags(session, job, link)
    return interviewer.conversation_state(
        dimensions=[
            interviewer.DimensionEvidence(
                dimension=row["dimension"],
                answers=row["answers"],
                substantive=row["substantive"],
                gaps=row["gaps"],
                conflicting=row["dimension"] in conflicting,
                weak=row["dimension"] in inferred,
            )
            for row in rows
        ],
        asked=conversation.next_question_index,
        total_written=total_written,
        floor=question_budget.min_questions(job.assessment_grade, job.role_classification),
        probe_outstanding=conversation.pending_prompt is not None,
    )


async def _conversation_prompts(
    session: AsyncSession, job: Job, link: JobCandidateLink
) -> list[tuple[str, str, str, CandidateQuestion]]:
    """This candidate's questions, in one sequence (spec §7).

    ONE LIST, from ONE table. Until Draft v4 this function round-robin
    interleaved two: a technical slot list and a PPI question list, produced by
    two generators and consumed by two scorers. There is one PPI matrix now and
    technical depth lives inside its Must-have items, so there is one stream and
    no seam to hide -- the candidate never interacts with separate technical and
    behavioural bots and never sees the scoring methods behind the conversation.

    The order is the matrix's own: Must-have, Nice-to-have, Behavioural, which
    is `assessment_questions.generate.generate_candidate_questions`' allocation order, held in `ordinal`.
    That is deterministic per job, which is what keeps two candidates' reports
    comparable.

    `question_key` carries the JobCompetency id, because that is what the scorer
    files an answer under. The DOMAIN carries the aspect, so the recruiter's
    transcript view can say which part of the matrix an exchange belonged to
    without the candidate ever having been told.

    The fourth element is the ROW, because since the question formats landed
    a slot's format, payload and time allocation decide how the turn is
    handled, and a second query per turn to fetch what this one already
    loaded would be the N+1 this function exists to avoid.
    """
    rows = (
        await session.execute(
            select(CandidateQuestion, JobCompetency)
            .join(JobCompetency, JobCompetency.id == CandidateQuestion.competency_id)
            .where(CandidateQuestion.job_candidate_link_id == link.id)
            .order_by(CandidateQuestion.ordinal)
        )
    ).all()

    # Returned BARE. The conversational join between one question and the next
    # is written per turn by the question writer against the real transcript, so
    # it can only claim a connection that actually exists. Anything canned here
    # would be prepended before the candidate has said anything for it to
    # respond to.
    return [
        (competency.category, str(question.id), question.prompt, question)
        for question, competency in rows
    ]


def _question_out(row: CandidateQuestion) -> QuestionOut:
    """The prompt on screen as the candidate may see it: the format, the
    CANDIDATE VIEW of the payload, the suggested time. The answer key never
    crosses here; `candidate_view` is an allowlist per type."""
    return QuestionOut(
        id=row.id,
        question_type=row.question_type,
        payload=question_types.candidate_view(row.id, row.question_type, row.payload_json),
        time_allocation_seconds=row.time_allocation_seconds,
    )


def _time_spent(conversation: AssessmentConversation, now: datetime, paused_ms: int) -> int | None:
    """Seconds between the prompt being shown and this answer, less any time
    a blocking proctoring warning held the screen, bounded by the elapsed
    time so a client cannot pause its way to zero. None when the prompt's
    display was never stamped."""
    shown = conversation.prompt_shown_at
    if shown is None:
        return None
    elapsed = max(0.0, (now - shown).total_seconds())
    paused = min(max(0, int(paused_ms)) / 1000.0, elapsed)
    return int(elapsed - paused)


def _question_uuid(key: str | None) -> uuid.UUID | None:
    """The question row a message key names, or None for a legacy key."""
    try:
        return uuid.UUID(str(key))
    except (ValueError, TypeError):
        return None


async def _write_answer_record(
    session: AsyncSession,
    job: Job,
    conversation: AssessmentConversation,
    question_id: uuid.UUID,
    message: AssessmentMessage,
    *,
    answer_json: dict[str, Any],
    auto_score: float | None,
    evaluation: dict[str, Any] | None,
    now: datetime,
    paused_ms: int,
    question_type: str,
) -> None:
    """One `assessment_answers` row per (conversation, question).

    A follow-up or a re-ask lands under its parent's key, and the transcript
    already holds it; here it is a revision of the base answer, so the row's
    count goes up and its time accumulates rather than a second row being
    written. The unique constraint is what makes that the only choice.
    """
    record = (
        await session.execute(
            select(AssessmentAnswer).where(
                AssessmentAnswer.conversation_id == conversation.id,
                AssessmentAnswer.question_id == question_id,
            )
        )
    ).scalars().first()
    spent = _time_spent(conversation, now, paused_ms)
    if record is None:
        session.add(
            AssessmentAnswer(
                tenant_id=job.tenant_id,
                conversation_id=conversation.id,
                question_id=question_id,
                message_id=message.id,
                question_type=question_type,
                answer_json=answer_json,
                started_at=conversation.prompt_shown_at,
                submitted_at=now,
                time_spent_seconds=spent,
                auto_score=auto_score,
                ai_evaluation_json=evaluation,
            )
        )
    else:
        record.revision_count += 1
        record.submitted_at = now
        if spent is not None:
            record.time_spent_seconds = (record.time_spent_seconds or 0) + spent
        if auto_score is not None:
            record.answer_json = answer_json
            record.auto_score = auto_score
            record.ai_evaluation_json = evaluation
            record.message_id = message.id
    await session.flush()


async def _record_behaviour(
    session: AsyncSession,
    proctoring_session: Any,
    conversation: AssessmentConversation,
    question_id: uuid.UUID | None,
    behaviour: Any,
    *,
    final_length: int,
) -> None:
    """Hand the answer field's keystroke and pointer timings to proctoring.

    Nothing here reads the result: behaviour aggregates go to the proctoring
    tables and nowhere near a score. A refusal from the recorder is logged
    and the turn continues, because a candidate's answer has already been
    saved by the time this runs and losing the turn over telemetry about it
    would be the wrong trade.
    """
    if behaviour is None or question_id is None:
        return
    from app.services.proctoring import behaviour as proctoring_behaviour  # noqa: PLC0415

    try:
        await proctoring_behaviour.record_answer_behaviour(
            session,
            proctoring_session,
            conversation,
            question_id,
            behaviour,
            final_length=final_length,
        )
    except (HTTPException, ValueError) as exc:
        logger.warning(
            "assessments.behaviour_not_recorded conversation_id=%s question_id=%s error=%s",
            conversation.id, question_id, type(exc).__name__,
        )


async def _ensure_conversation_ready(
    session: AsyncSession, job: Job, link: JobCandidateLink
) -> None:
    """Transient prep, never a human gate.

    There used to be two halves here and an asymmetry worth explaining: the
    technical slots were created inline because they came from a pure function
    of the JD, while the PPI questions were dispatched because they were a model
    call. Draft v4 left one half. Every question is generated per candidate
    against the saved matrix, so the whole preparation is a model call, it stays
    in a background task (claude.md rule 4), and the candidate is asked to retry in a
    moment rather than made to wait on a request.
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


# Abuse control and COST control, not authorization (services/rate_limit).
#
# Every turn of an assessment conversation invokes a model, so this pair was the
# most expensive unthrottled surface in the product: an authenticated candidate
# session, or a stolen one, could drive unbounded model spend and exhaust the
# shared per-credential rate limit at the provider, which starves OTHER tenants'
# assessments because `llm_router`'s circuit breaker is keyed by credential.
#
# The numbers are set well above a real assessment and well below a flood. A
# non-managerial assessment is 45 questions answered by a person typing prose,
# so a handful of starts and a few dozen turns a minute cannot be reached by
# somebody doing the assessment, and a candidate who legitimately retries a
# failed send is nowhere near either ceiling. Now that `client_identifier`
# resolves a verified subject, each candidate gets their own bucket rather than
# sharing one with every other candidate behind the same office address.
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
    link, job = await _candidate_link(session, user, link_id)
    conversation = (
        await session.execute(select(AssessmentConversation).where(AssessmentConversation.job_candidate_link_id == link.id))
    ).scalars().first()
    # ── The invitation gate ──────────────────────────────────────────────────
    # Not every applicant is assessed. The conversation row IS the invitation:
    # a recruiter creates it via POST /pipeline/jobs/{id}/select-candidates, and
    # only then may the candidate open the questions. Checked BEFORE any
    # generation is enqueued, so an uninvited candidate cannot make the product
    # do work by hitting the URL.
    if conversation is None or conversation.invitation_sent_at is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "This assessment is not open to you yet. The hiring team invites "
                "candidates individually, and you will be emailed if they invite you."
            ),
        )
    # PROCTORING GATE, before any work. A conversation cannot be opened
    # without a consented, still-running proctoring session (principle P4).
    await proctoring_gate.require_active(session, conversation)
    # MODE GATE (dual-mode spec section 2): this route is the CONVERSATIONAL
    # start. A session set to the video interview begins through the video
    # start route, and answering it here would write typed answers into a
    # session whose consent covers a recording.
    if conversation.mode != MODE_CONVERSATIONAL:
        raise HTTPException(
            status_code=409,
            detail=(
                "You chose the video interview for this assessment. Please "
                "continue in the video interview, or change your mode choice "
                "before starting."
            ),
        )
    # ASSESSMENT CONSENT GATE (dual-mode spec 3.1): a hard prerequisite for
    # starting in EITHER mode, distinct from the proctoring consent above.
    # The gate asks the table, never a stamp.
    await assessment_consent.require_consent(session, conversation)
    await _ensure_conversation_ready(session, job, link)
    # First open of an invited assessment moves it along the pipeline.
    if conversation.started_at is None:
        conversation.started_at = datetime.now(timezone.utc)
        await session.flush()
        if hiring_pipeline.can_transition(
            link.status, hiring_pipeline.ASSESSMENT_IN_PROGRESS
        ):
            await hiring_pipeline.apply_transition(
                session,
                link_id=link.id,
                tenant_id=link.tenant_id,
                target=hiring_pipeline.ASSESSMENT_IN_PROGRESS,
            )
    prompts = await _conversation_prompts(session, job, link)
    # Pre-filled questions are consumed BEFORE anything is served, so the
    # first thing this candidate ever sees is a question the resume could
    # not answer for them (feature 2, C2).
    await _consume_prefilled_questions(session, job, conversation, prompts)
    index = min(conversation.next_question_index, len(prompts))

    # THE FIRST QUESTION IS WRITTEN HERE, and it has to be.
    #
    # Every other base question is written on the request that answers its
    # predecessor, so by the time it is shown `delivered_prompt` is populated.
    # Question one has no predecessor. Before 2026-08-06 that did not matter --
    # the first question was a preset technical string, which is what a preset
    # bank is for -- but the blend puts a technical slot first, and a technical
    # slot now starts life holding the deterministic fallback probe. Without
    # this every candidate's opening question would be the generic
    # "Describe a demanding situation where you applied X", which is precisely
    # the scripted feel the whole change exists to remove.
    #
    # Guarded on `delivered_prompt is None` so re-opening a part-finished
    # assessment does not rewrite the question the candidate is already looking
    # at, and skipped entirely when a probe is outstanding.
    if (
        conversation.pending_prompt is None
        and conversation.delivered_prompt is None
        and index < len(prompts)
    ):
        await _write_next_question(session, job, link, conversation, prompts, index)

    # A pending follow-up outranks the next base question: the candidate left
    # mid-probe and must come back to the probe, not skip past it.
    # `delivered_prompt` is the wording written for this base question; it is
    # NULL only when generation was unavailable, and the stored text is the
    # correct fallback then.
    prompt = conversation.pending_prompt or (
        (conversation.delivered_prompt or prompts[index][2])
        if index < len(prompts)
        else None
    )
    if prompt is not None:
        # The prompt is on screen from now. Re-stamped on every open, including
        # a reload, because the time the recruiter reads is time the candidate
        # spent looking at THIS display of the question.
        conversation.prompt_shown_at = datetime.now(timezone.utc)
        await session.flush()
    return ConversationOut(
        conversation_id=conversation.id,
        status=conversation.status,
        mode=conversation.mode,
        prompt=prompt,
        progress_label=f"Question {index + 1} of {len(prompts)}" if index < len(prompts) else "Conversation complete",
        answered_questions=index,
        total_questions=len(prompts),
        is_reask=conversation.pending_kind == "reask",
        question=(
            _question_out(prompts[index][3])
            if conversation.pending_prompt is None and index < len(prompts)
            else None
        ),
    )



async def _resume_excerpt(session: AsyncSession, link: JobCandidateLink) -> str:
    """This candidate's resume text, or empty.

    Read per turn rather than cached on the conversation: a candidate may
    replace the resume on their profile, and the questions should be grounded in
    what the application actually carries now.
    """
    if link.profile_id is None:
        return ""
    return (
        await session.execute(
            select(Profile.resume_text).where(Profile.id == link.profile_id)
        )
    ).scalar_one_or_none() or ""


async def _consume_prefilled_questions(
    session: AsyncSession,
    job: Job,
    conversation: AssessmentConversation,
    prompts: list[tuple[str, str, str, CandidateQuestion]],
) -> int:
    """Record every consecutive pre-filled question instead of asking it.

    Feature 2 (C2, owner-ruled): a question whose criterion the resume
    already evidences carries `prefilled_answer`, and the conversation
    writes that exchange, agent prompt, then the labelled answer, exactly
    where a typed answer would land (the label names WHICH source, so the
    transcript says where the words came from), advances the index and moves
    on. The scorer reads the same rows it always reads; billing and
    completion fire at the same chokepoints, because the index moves through
    the same gate.

    Since CR 23 (2026-09-22) there are two pre-fill sources: the resume anchor
    and the candidate's PORTABLE record. Both land here identically on purpose,
    because the mechanism is the same one and a second recording path would be
    a second place for billing and completion to disagree.
    """
    from app.services import resume_prefill

    consumed = 0
    while (
        conversation.pending_prompt is None
        and conversation.completed_at is None
        and conversation.next_question_index < len(prompts)
    ):
        aspect, key, prompt_text, row = prompts[conversation.next_question_index]
        prefilled = getattr(row, "prefilled_answer", None)
        if not prefilled:
            break
        ordinal = (
            await session.execute(
                select(func.count()).select_from(AssessmentMessage).where(
                    AssessmentMessage.conversation_id == conversation.id
                )
            )
        ).scalar_one()
        candidate_message = AssessmentMessage(
            tenant_id=job.tenant_id,
            conversation_id=conversation.id,
            ordinal=ordinal + 2,
            speaker="candidate",
            domain=aspect,
            question_key=key,
            content=prefilled,
            # The label says WHICH pre-fill produced this, not merely that one
            # did. Since CR 23 there are two sources (the resume anchor and
            # the candidate's portable record) and reading one as the other
            # would misreport where an answer came from, which is the whole
            # job of a transcript label.
            answer_label=resume_prefill.answer_label_for(row.prefill_source),
        )
        session.add_all([
            AssessmentMessage(
                tenant_id=job.tenant_id, conversation_id=conversation.id,
                ordinal=ordinal + 1, speaker="agent", domain=aspect,
                question_key=key, content=prompt_text,
            ),
            candidate_message,
        ])
        await session.flush()
        await _write_answer_record(
            session, job, conversation, row.id, candidate_message,
            answer_json={"text": prefilled, "source": row.prefill_source or "resume"},
            auto_score=None, evaluation=None,
            now=datetime.now(timezone.utc), paused_ms=0,
            question_type=row.question_type,
        )
        conversation.delivered_prompt = None
        conversation.next_question_index += 1
        consumed += 1
    if consumed:
        await session.flush()
    return consumed


async def _write_next_question(
    session: AsyncSession,
    job: Job,
    link: JobCandidateLink,
    conversation: AssessmentConversation,
    prompts: list[tuple[str, str, str, CandidateQuestion]],
    index: int,
) -> None:
    """Write the base question at `index` for THIS candidate at THIS point, and
    stash it on `conversation.delivered_prompt`.

    TEXT QUESTIONS ONLY. A structured question (an MCQ, a fill-in-the-blank,
    a coding problem) is delivered VERBATIM: its answer key was written with
    it, and a rewritten stem graded against the original key is the exact
    defect the per-question rubric rule exists to prevent.

    BOTH HALVES ARE NOW GENERATED, BY DIFFERENT AGENTS, FOR DIFFERENT REASONS
    -------------------------------------------------------------------------
      ppi        `interviewer.compose_next_question` in MODE_GENERATE. A PPI
                 answer is scored against its COMPETENCY across every answer
                 filed under it, so the question may be written fresh.

      technical  `technical_interview.write_question`. Until 2026-08-06 this was
                 MODE_REWORD -- the phrasing could move but the substance could
                 not -- because the answer was scored against a preset question's
                 stored rubric, and a fresh question would have been graded
                 against a rubric for a question nobody was asked. That objection
                 is answered by generating the RUBRIC WITH THE QUESTION and
                 persisting both before the candidate reads either, which is
                 exactly what `write_question` does. The rubric now always
                 belongs to the question actually asked.

    Both paths degrade to the stored text, which is always a correct thing to
    ask: for PPI it is the question pre-generated for this competency from this
    resume, and for technical it is the deterministic probe `ensure_slots` wrote
    from the job's own skill plan.
    """
    if index >= len(prompts):
        return
    if question_types.is_structured(prompts[index][3].question_type):
        return
    try:
        await _write_next_question_inner(session, job, link, conversation, prompts, index)
    except Exception as exc:  # noqa: BLE001
        # WRITING THE NEXT QUESTION MUST NEVER COST THE CANDIDATE THIS TURN.
        #
        # By the time this runs the candidate's answer is already recorded and
        # the index already advanced. Everything here is an ENHANCEMENT of the
        # question they will read next, and the stored text is always a correct
        # thing to ask, so any failure has exactly one right answer: leave
        # `delivered_prompt` NULL and move on.
        #
        # It is not hypothetical. `llm_router` used to commit the CALLER's
        # transaction when it condemned a key, which closed the request's
        # transaction and made the next read raise -- so a provider outage 500ed
        # a candidate mid-assessment. That root cause is fixed
        # (`llm_router._persist_key_health`), and this stays as the guard that
        # makes the whole step non-load-bearing whatever the next such bug is.
        logger.warning(
            "assessments.next_question_unavailable conversation_id=%s error=%s",
            conversation.id, type(exc).__name__,
        )


async def _write_next_question_inner(
    session: AsyncSession,
    job: Job,
    link: JobCandidateLink,
    conversation: AssessmentConversation,
    prompts: list[tuple[str, str, str, CandidateQuestion]],
    index: int,
) -> None:
    """Write the question at `index` for THIS candidate at THIS point.

    ONE writer for all three aspects (`ppi_interview.write_question`), and the
    method it uses inside varies by aspect rather than by caller: a Must-have or
    Nice-to-have question is written together with the rubric its answer will be
    graded against, and a Behavioural question is written without one because
    there is no single correct answer to weigh a behavioural account against.

    Degrades to the stored text, which is always a correct thing to ask: it is
    the question `assessment_questions.generate.generate_candidate_questions` already wrote for this item
    from this candidate's own resume.
    """
    _aspect, key, stored, _row = prompts[index]
    # Read AFTER the caller's flush so the turn just written is part of the
    # memory this question is conditioned on. Reading a stale transcript would
    # have the interviewer talk as though the last answer had not been given.
    memory = await _transcript_rows(session, conversation.id)
    asked_before = [row["content"] for row in memory if row.get("speaker") == "agent"]
    resume = await _resume_excerpt(session, link)

    row = None
    try:
        row = await session.get(CandidateQuestion, uuid.UUID(str(key)))
    except (ValueError, TypeError):
        row = None
    competency = (
        await session.get(JobCompetency, row.competency_id) if row is not None else None
    )
    if row is None or competency is None:
        # The row vanished under us, which should be impossible. Showing the
        # stored text is the honest degradation; refusing the turn would cost
        # the candidate their assessment over a bookkeeping fault.
        logger.info("assessments.question_row_missing key=%s", key)
        written = stored
    else:
        result = await ppi_interview.write_question(
            session=session,
            job=job,
            row=row,
            competency=competency,
            resume_excerpt=resume,
            transcript=memory,
            asked_before=asked_before,
        )
        written = result.value["question"]
        # LAST CHECK BEFORE THE CANDIDATE READS IT, and deterministic. The
        # writer is told what has already been asked and usually respects it;
        # "usually" is not a guarantee, and the one turn it gets wrong is the
        # turn the candidate notices. Falling back to the stored text costs
        # nothing here: it is the question already written for this item from
        # this candidate's own resume, and it is by construction not one of the
        # lines above.
        if interviewer.is_semantic_repeat(written, asked_before):
            logger.info(
                "assessments.question_rejected reason=repeat conversation_id=%s",
                conversation.id,
            )
            written = stored

    # OUTBOUND GUARD, on the way IN to storage rather than on the way out, so
    # the transcript records exactly the text the candidate will read. A
    # generated question is written by a model that has just been shown a JD, a
    # resume and a criterion -- precisely the context from which a grade or a
    # required level could leak into interviewer speech.
    conversation.delivered_prompt = conversation_guardrails.inspect_agent_output(written)
    await session.flush()


async def _transcript_rows(
    session: AsyncSession, conversation_id: uuid.UUID
) -> list[dict[str, Any]]:
    """The conversation so far, oldest first.

    This is the agent's MEMORY. Without it every turn would be judged on the
    single answer in front of it, which is how the interview came to repeat
    ground the candidate had already covered.

    Read back from `assessment_messages` rather than accumulated in memory
    because `respond` is one stateless HTTP request per turn -- there is no
    process holding the conversation between them.
    """
    rows = (
        await session.execute(
            select(AssessmentMessage.speaker, AssessmentMessage.content)
            .where(AssessmentMessage.conversation_id == conversation_id)
            .order_by(AssessmentMessage.ordinal)
        )
    ).all()
    return [{"speaker": speaker, "content": content} for speaker, content in rows]


# See the note on `start_conversation` above. This is the one that actually
# costs money per call: one model invocation per turn.
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
    # Wall clock for the turn, for the telemetry line at the end. monotonic
    # rather than wall time: this measures a duration, and a clock adjustment
    # mid-request would otherwise produce a negative latency.
    turn_started = time.monotonic()
    conversation = await session.get(AssessmentConversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    link, job = await _candidate_link(session, user, conversation.job_candidate_link_id)
    # THE TURN'S MODEL SPEND IS BILLED TO THIS APPLICATION.
    #
    # Bound here, the moment the application is known and before anything can
    # call a model, and drained by the single `cost_telemetry.flush` near the
    # end of this handler. It is a plain tally rather than a context manager
    # wrapped around four hundred lines: `contextvars` are per asyncio task and
    # FastAPI gives every request its own, so nothing set here can reach
    # another request, and the shape stays readable.
    #
    # A turn that raises after a model call loses that turn's counters, and
    # that is the accepted cost rather than an oversight: this record is an
    # OBSERVATION of work, the same class as `observability.trace`, and a turn
    # that raised has already failed the candidate in front of it. Failing the
    # turn to protect the counter would be exactly backwards.
    turn_usage = cost_telemetry.begin()
    # A terminated conversation takes no further answer. The proctoring gate
    # below refuses too, through the session's outcome; this is the status the
    # ingestion pipeline wrote on the conversation itself, checked first
    # because it is the cheaper and the more direct of the two.
    if conversation.status == "terminated":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=proctoring_gate.SESSION_ENDED_DETAIL,
        )
    # A video-interview session has no typed turns: its answers arrive as one
    # recording and are written by the processing pipeline. Refused here so a
    # hand-built request cannot mix the two modes' records in one session.
    if conversation.mode != MODE_CONVERSATIONAL:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This assessment runs as a video interview; answers are "
                "spoken in the recording rather than typed here."
            ),
        )
    proctoring_session = await proctoring_gate.require_active(session, conversation)
    prompts = await _conversation_prompts(session, job, link)
    index = conversation.next_question_index
    pending = conversation.pending_prompt
    now = datetime.now(timezone.utc)
    # Rows created before 0045 have NULL here; every historical pending prompt
    # was a probe, so that is the safe compatibility meaning.
    pending_kind = (conversation.pending_kind or "probe") if pending else None
    answering_reask = pending_kind == "reask"
    answering_probe = pending_kind == "probe"
    # A pending follow-up means the candidate is answering the PROBE, not the
    # next scripted question. Completion therefore cannot be decided by the
    # index alone: the last base question may still have a follow-up
    # outstanding, and finishing there would charge the customer and start
    # scoring while the candidate is still typing.
    if index >= len(prompts) and not pending:
        raise HTTPException(status_code=409, detail="Conversation is already complete")

    question_row: CandidateQuestion | None = None
    if pending:
        # Answered under the key of the question that PRODUCED the follow-up,
        # so `answers_by_key` files it with that question's other answers and
        # every scorer sees one richer answer rather than an unknown key.
        # The index is deliberately NOT advanced: a follow-up is extra evidence
        # for a question already counted, never an extra question.
        domain = conversation.pending_domain or "technical"
        key = conversation.pending_question_key or ""
        prompt = pending
        conversation.pending_prompt = None
        conversation.pending_question_key = None
        conversation.pending_domain = None
        conversation.pending_kind = None
    else:
        domain, key, prompt, question_row = prompts[index]
        # Log what the candidate actually READ, not what was stored for them.
        # `delivered_prompt` is the wording composed on the previous turn; when
        # it is NULL (first question, or the rewrite was unavailable) the two
        # are the same string. Getting this wrong would make the transcript a
        # record of a conversation that never happened, and the transcript is
        # both the scorers' input and this agent's own memory.
        prompt = conversation.delivered_prompt or prompt
        conversation.delivered_prompt = None

    ordinal = (
        await session.execute(select(func.coalesce(func.max(AssessmentMessage.ordinal), 0)).where(AssessmentMessage.conversation_id == conversation.id))
    ).scalar_one()

    # ── A STRUCTURED QUESTION: delivered verbatim, answered as a structure ──
    #
    # An MCQ, a fill-in-the-blank or a coding problem is validated against the
    # question it answers, scored deterministically on the server where the
    # format is objective, rendered into the transcript line the scorers and
    # the recruiter read, and recorded on `assessment_answers`. None of the
    # conversational machinery applies: no classification, no re-ask, no
    # follow-up, because the answer key was written with the question and a
    # probe would be graded against nothing.
    if question_row is not None and question_types.is_structured(question_row.question_type):
        if body.answer_payload is None:
            raise HTTPException(
                status_code=422,
                detail="This question is answered by choosing or typing into the "
                "form shown, not with a written reply.",
            )
        try:
            parsed = question_types.parse_answer(
                question_row.question_type, question_row.payload_json, body.answer_payload
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        answer_json = parsed.model_dump()
        auto_score: float | None = None
        evaluation: dict[str, Any] | None = None
        if question_row.question_type in question_types.OBJECTIVE_TYPES:
            objective = await format_scoring.score(
                session, question_row.question_type, question_row.payload_json, answer_json
            )
            auto_score = objective.auto_score
            if objective.blank_results:
                evaluation = {"blank_results": list(objective.blank_results)}
        answer_text = format_rendering.transcript_line(
            question_row.question_type, question_row.payload_json, answer_json
        )
        candidate_message = AssessmentMessage(
            tenant_id=job.tenant_id,
            conversation_id=conversation.id,
            ordinal=ordinal + 2,
            speaker="candidate",
            domain=domain,
            question_key=key,
            content=answer_text,
            answer_label="substantive",
        )
        session.add_all([
            AssessmentMessage(tenant_id=job.tenant_id, conversation_id=conversation.id, ordinal=ordinal + 1, speaker="agent", domain=domain, question_key=key, content=prompt),
            candidate_message,
        ])
        await session.flush()
        await _write_answer_record(
            session, job, conversation, question_row.id, candidate_message,
            answer_json=answer_json, auto_score=auto_score, evaluation=evaluation,
            now=now, paused_ms=body.paused_ms, question_type=question_row.question_type,
        )
        await _record_behaviour(
            session, proctoring_session, conversation, question_row.id, body.behaviour,
            final_length=len(answer_text),
        )
        conversation.next_question_index += 1
        conversation.reasks_used = 0
        interview_telemetry.record_turn(
            interview_telemetry.TurnEvent(
                conversation_id=str(conversation.id),
                turn_index=index,
                question_key=key,
                domain=domain,
                answer_label="substantive",
                action="advanced",
                generated=False,
                degraded=False,
                latency_ms=int((time.monotonic() - turn_started) * 1000),
            )
        )

    else:
        # INBOUND GUARD, before this text is stored or reaches any prompt.
        #
        # A candidate's answer is DATA, never instructions to the interviewer, and
        # it goes straight into a model prompt. `sanitized` keeps their real content
        # and defangs attack framing, so an answer that legitimately DISCUSSES
        # prompt injection is still an answer. It also redacts anything shaped like
        # a pasted credential, which protects the candidate rather than us: prompts
        # are traced, and a key pasted by mistake must not be persisted or sent on.
        #
        # Note the contract: `violation is not None` does NOT mean refused. Only
        # `allowed` decides, and it goes False only when the framing was a directive
        # AND nothing resembling an answer is left underneath it.
        guard = conversation_guardrails.inspect_answer(body.answer)
        answer_text = guard.sanitized.strip()
        candidate_message = AssessmentMessage(
            tenant_id=job.tenant_id,
            conversation_id=conversation.id,
            ordinal=ordinal + 2,
            speaker="candidate",
            domain=domain,
            question_key=key,
            content=answer_text,
        )
        session.add_all([
            AssessmentMessage(tenant_id=job.tenant_id, conversation_id=conversation.id, ordinal=ordinal + 1, speaker="agent", domain=domain, question_key=key, content=prompt),
            candidate_message,
        ])
        await session.flush()
        # The structured record beside the transcript line: one row per base
        # question, revised rather than duplicated by a follow-up or a re-ask.
        #
        # The TYPE comes from the question the key names, not from whether this
        # turn happened to be a base question: a follow-up on an evidence question
        # is more evidence for that question, and a record typed `short_answer`
        # because a probe wrote it first would send the scorer down the wrong
        # branch for the whole item.
        question_id = _question_uuid(key)
        if question_id is not None:
            answered_row = question_row or next(
                (row for _aspect, row_key, _prompt, row in prompts if row_key == key), None
            )
            await _write_answer_record(
                session, job, conversation, question_id, candidate_message,
                answer_json={"text": answer_text}, auto_score=None, evaluation=None,
                now=now, paused_ms=body.paused_ms,
                question_type=(
                    answered_row.question_type if answered_row is not None else question_types.SHORT_ANSWER
                ),
            )
        await _record_behaviour(
            session, proctoring_session, conversation, question_id, body.behaviour,
            final_length=len(answer_text),
        )

        # A probe is supplemental evidence for an already accepted base answer, so
        # it never advances the base counter. A base answer and a relevance re-ask
        # are classified: only an accepted one advances the counter.
        if not answering_probe:
            transcript = await _transcript_rows(session, conversation.id)
            reaction = None
            action = "advanced"
            answer_label = "substantive"
            degraded = False
            needs_rechallenge = False

            if not guard.allowed:
                # A refused turn is still transcribed above: the record of what was
                # said is what a dispute is settled from. Classifying it would spend
                # a model call grading an attack, so this short-circuits to the same
                # re-ask mechanism a non-answer uses -- same question_key, no
                # follow-up budget, bounded to one per question.
                answer_label = f"guarded_{guard.violation}"
                needs_rechallenge = True
            else:
                # ONE classification decides what happens next. Gibberish and empty
                # are settled deterministically (no model call, because the model
                # being down is exactly when the guard matters); off_topic and
                # evasive need the model, because they are well-formed prose that
                # simply does not answer the question, and nothing deterministic
                # can see that.
                #
                # Observed live on 2026-08-05: four consecutive keyboard-mash
                # answers were each met with the next scripted question, because
                # the follow-up path deliberately refuses to spend a probe on
                # gibberish. Sound for probing, and the worst behaviour overall --
                # the one case a human interviewer certainly reacts to became the
                # one case this agent never did.
                verdict = await answer_classification.classify(
                    session=session,
                    question=prompt,
                    answer=answer_text,
                    transcript=transcript,
                )
                answer_label = verdict.label
                degraded = verdict.confidence == "low"
                needs_rechallenge = verdict.needs_rechallenge

            candidate_message.answer_label = answer_label

            # Re-asks are bounded per base question. The counter stays still while
            # one is outstanding. Once the cap is exhausted the answer remains in
            # the transcript with evidence_gap=true and the interview moves on, so
            # a candidate cannot be trapped in an infinite loop.
            if (
                needs_rechallenge
                and conversation.reasks_used < MAX_REASKS_PER_QUESTION
            ):
                if not guard.allowed:
                    reaction = guard.candidate_message
                else:
                    reaction = await interviewer.challenge_non_answer(
                        session=session,
                        question=prompt,
                        answer=answer_text,
                        transcript=transcript,
                        label=answer_label,
                    )
                if reaction:
                    conversation.reasks_used += 1
                    action = "rechallenged"

            if reaction is None:
                # Either the answer was relevant, the re-ask cap was reached, or a
                # challenge could not be composed. Every one of those outcomes
                # closes exactly one base slot. Invalid outcomes are explicit gaps.
                conversation.next_question_index += 1
                conversation.reasks_used = 0
                if needs_rechallenge:
                    candidate_message.evidence_gap = True
                    action = "advanced_with_gap"
                elif not answering_reask:
                    reaction = await interviewer.next_follow_up(
                        session=session,
                        question=prompt,
                        answer=answer_text,
                        transcript=transcript,
                        follow_ups_used=conversation.follow_ups_used,
                        already_followed_up=False,
                        # Scaled to this interview's length. A flat five probes
                        # across 45 questions left 89% of the conversation unable
                        # to react to anything the candidate said.
                        budget=interviewer.follow_up_budget(len(prompts)),
                        # Every interviewer line so far, so a probe that merely
                        # re-asks something already asked is dropped rather than
                        # spending a scarce budget to prove nobody was listening.
                        # The check inside is deterministic: it matters most during
                        # an outage, which is when a model cannot answer it.
                        asked_before=[
                            row["content"]
                            for row in transcript
                            if row.get("speaker") == "agent"
                        ],
                    )
                    # Only a real probe draws down the budget. A re-ask does not:
                    # asking someone to actually answer is not a probe, and
                    # spending the budget on it would starve the thin-but-real
                    # answers later in the interview that a probe is worth more on.
                    if reaction:
                        conversation.follow_ups_used += 1
                        action = "followed_up"

            if reaction:
                # OUTBOUND GUARD. Last thing before any interviewer line is stored
                # as what the candidate will read.
                conversation.pending_prompt = conversation_guardrails.inspect_agent_output(reaction)
                conversation.pending_question_key = key
                conversation.pending_domain = domain
                conversation.pending_kind = (
                    "reask" if action == "rechallenged" else "probe"
                )

            # One structured line per turn. Labels, keys and timings only, never
            # answer or question text: an ordinary log is far more widely readable
            # than a LangSmith trace, and prompts carry a real candidate's answers.
            interview_telemetry.record_turn(
                interview_telemetry.TurnEvent(
                    conversation_id=str(conversation.id),
                    turn_index=index,
                    question_key=key,
                    domain=domain,
                    answer_label=answer_label,
                    action=action,
                    generated=conversation.delivered_prompt is not None,
                    degraded=degraded,
                    latency_ms=int((time.monotonic() - turn_started) * 1000),
                )
            )

    # Vaada decides WHERE IN SUTRA'S RANGE this conversation ends.
    #
    # Two ways to finish, and they are not the same event. The first is running
    # out of written questions, which is what this has always done. The second
    # is new on 2026-08-23 and is the specification's own stopping rule: the
    # conversation ends "when Vaada determines sufficient evidence has been
    # gathered across all matrix dimensions". Sutra fixes the RANGE per job so
    # two candidates stay comparable; the candidate's own answer depth decides
    # where inside it they stop.
    #
    # The floor inside `conversation_may_close` is the load-bearing half. Without
    # it a fluent candidate is assessed on fewer criteria than a hesitant one and
    # the two reports stop being comparable, which is the one thing the matrix
    # exists to guarantee. And a dimension is only "covered" by a SUBSTANTIVE
    # answer: an evasive reply is not evidence, and treating it as coverage would
    # let a candidate shorten their own assessment by not answering.
    #
    # A pending follow-up still holds completion open, exactly as before. The
    # candidate is mid-sentence; charging the customer and dispatching scoring
    # there would score an assessment that is still being written.
    evidence_complete = False
    if not conversation.pending_prompt:
        covered, total_dimensions = await _dimension_coverage(session, link)
        # The explicit state, beside the tuple the stopping rule reads. It is
        # what turns "18 of 20 covered" into something an operator can act on:
        # which dimensions are unprobed, which produced nothing usable, and
        # which the evidence ledger already holds two readings of.
        coverage = await _conversation_state(
            session, job, link, conversation, len(prompts)
        )
        logger.info(
            "assessments.conversation_state conversation_id=%s state=%s",
            conversation.id, coverage.as_log(),
        )
        evidence_complete = question_budget.conversation_may_close(
            grade=job.assessment_grade,
            role_classification=job.role_classification,
            asked=conversation.next_question_index,
            total_written=len(prompts),
            covered_dimensions=covered,
            total_dimensions=total_dimensions,
        ) and (
            # AN ADDITIONAL CONDITION, NEVER A REPLACEMENT.
            # `conversation_may_close` stays the only thing that decides whether
            # an assessment may end early, floor included; this can only ever
            # make it stricter. A MATERIAL contradiction obliges `ask_follow_up`
            # while a conversation is still running, and stopping with one
            # outstanding throws away the only chance anyone will get to ask.
            # Completion by exhausting the written questions is untouched below,
            # so this can never strand a candidate in an endless interview.
            interviewer.STOP_NO_CONFLICT_OUTSTANDING in coverage.stop_conditions
        )

    # Consume any pre-filled questions the advance just reached (feature 2,
    # C2): recorded, never asked, and if the tail of the interview is all
    # pre-filled this walks the index to the end so completion and the
    # charge fire in the same block below, on the same request.
    await _consume_prefilled_questions(session, job, conversation, prompts)
    # Read AFTER the consumption above, which can walk the index to the end.
    prompts_exhausted = conversation.next_question_index >= len(prompts)
    if (prompts_exhausted or evidence_complete) and not conversation.pending_prompt:
        conversation.status = "completed"
        conversation.completed_at = datetime.now(timezone.utc)
        # WHICH of the two endings this was, on the row (change request 28B).
        #
        # DERIVED FROM THE BRANCH THAT ALREADY DECIDED, never re-evaluated.
        # `assessment_questions.budget.conversation_may_close` is still the only thing that can end an
        # assessment early; this reads the decision it made rather than asking
        # a second question that could answer differently, which is the
        # duplicate decider `interviewer.stop_conditions` refuses to become.
        #
        # Exhaustion is checked FIRST, and the two are not symmetric. An early
        # close requires `asked < total_written`, so the pair is mutually
        # exclusive at the moment `evidence_complete` is computed; the
        # pre-filled consumption above can then walk the index to the end, and
        # a session that reached its last written question did not stop early
        # whatever was true a few lines earlier. `prompts_exhausted` is the
        # claim that can be checked against the row afterwards, so it wins.
        #
        # `every_dimension_covered` rather than `floor_reached` for the other
        # branch, and the difference is the whole point of the feature: the
        # floor is PERMISSION to stop, not a reason for stopping. Recording
        # the floor would read as "we stopped at the minimum", which is the
        # fail-style early exit this product deliberately does not do.
        conversation.end_reason = (
            interviewer.STOP_PROMPTS_EXHAUSTED
            if prompts_exhausted
            else interviewer.STOP_EVERY_DIMENSION_COVERED
        )
        # The same fact for a reader of the logs, with the three numbers that
        # make it legible. Internal: `interview_telemetry`'s header is what
        # forbids any of this reaching a response schema, and the end reason
        # falls under it for a reason of its own, since how much of the matrix
        # somebody was asked is precisely what a candidate would read as a
        # verdict on their answers.
        interview_telemetry.record_end(
            interview_telemetry.EndEvent(
                conversation_id=str(conversation.id),
                end_reason=conversation.end_reason,
                questions_asked=conversation.next_question_index,
                questions_written=len(prompts),
                floor=question_budget.min_questions(
                    job.assessment_grade, job.role_classification
                ),
            )
        )
        # Master Directive Part 2 section 5.1: EV_INT_COMPLETED, the AI
        # assessment interview session ending. Feeds the TTF evaluation
        # segment (services/metrics.py); never allowed to fail the completion.
        await telemetry_events.emit(
            session,
            tenant_id=job.tenant_id,
            event_code=telemetry_events.EV_INT_COMPLETED,
            job_id=job.id,
            candidate_id=link.candidate_id,
            job_candidate_link_id=link.id,
            correlation_id=job.correlation_id,
            payload={"conversation_id": str(conversation.id)},
        )
        # One full credit, charged at the moment completed_at is set. Idempotent,
        # and deliberately NOT deferred to the scoring task: the customer should
        # see the deduction at the same time they see the report, and a failed
        # scoring run must not make the work free.
        await credit_reconciliation.charge_completed(
            session,
            conversation_id=conversation.id,
            tenant_id=job.tenant_id,
            job_candidate_link_id=link.id,
        )
        dispatch("pickready.run_functional_assessment", args=[str(link.id)])
        # The transcript is final now, so it becomes retrievable evidence
        # (RPN-AI-UP-001 W2.1). Keyed on the LINK, like the recruiter
        # transcript route, because the transcript outlives any one
        # conversation row and every consumer of assessment evidence already
        # holds a link id.
        #
        # AFTER completion and never per turn: a live conversation grows
        # between two reads by design, so indexing mid-assessment would put
        # half a transcript in the index and re-embed it on every answer. Same
        # rule that keeps `extract_assessment` uncached.
        dispatch("pickready.index_document", args=["assessment", str(link.id)])
    await session.flush()
    next_index = conversation.next_question_index

    # Write the next BASE question for THIS candidate at THIS point.
    #
    # Skipped entirely when a probe or re-ask is outstanding: that IS the next
    # thing the candidate sees, and writing a question nobody will read would
    # spend a second sequential model call on a request a candidate is waiting
    # on. Also skipped once the questions run out, for the same reason.
    if (
        not conversation.pending_prompt
        and next_index < len(prompts)
        and conversation.completed_at is None
    ):
        await _write_next_question(
            session, job, link, conversation, prompts, next_index
        )

    # Everything this turn spent, onto the application's cost record. AFTER
    # `_write_next_question`, which is itself a model call and the second most
    # expensive thing in the turn; flushing before it would under-report every
    # interview by one composed question per turn, which is a systematic error
    # rather than a rounding one.
    #
    # `questions_asked` is the conversation's own index, an absolute reading
    # rather than a count of turns. The two genuinely differ: a follow-up and a
    # re-ask are turns that advance no question, and reporting them as
    # questions would make the cost record disagree with the progress label the
    # candidate is looking at.
    await cost_telemetry.flush(
        session,
        scope=cost_telemetry.AssessmentScope(
            tenant_id=job.tenant_id, job_id=job.id, link_id=link.id
        ),
        tally=turn_usage,
        conversation_turns=1,
        questions_asked=conversation.next_question_index,
    )

    # A pending follow-up is what the candidate sees next. The progress label
    # deliberately keeps counting BASE questions, so a probe does not make the
    # interview look longer than it is or push the count past its own total.
    #
    # `prompts[next_index][2]` is deliberately re-read from the ORIGINAL list
    # rather than refetched: for a technical slot `write_question` has just
    # overwritten the row's prompt in place, and the delivered text is what the
    # candidate reads anyway. This branch is only reached when delivery was
    # unavailable, and then the pre-generation text is exactly the right thing
    # to show.
    next_prompt = conversation.pending_prompt or (
        (conversation.delivered_prompt or prompts[next_index][2])
        if next_index < len(prompts)
        else None
    )
    if next_prompt is not None:
        # The next prompt is on screen from the moment this response lands;
        # the next answer's time is measured from here, on the server.
        conversation.prompt_shown_at = datetime.now(timezone.utc)
        await session.flush()
    return ConversationOut(
        conversation_id=conversation.id,
        status=conversation.status,
        prompt=next_prompt,
        progress_label=f"Question {next_index + 1} of {len(prompts)}" if next_index < len(prompts) else "Conversation complete",
        answered_questions=next_index,
        total_questions=len(prompts),
        is_reask=conversation.pending_kind == "reask",
        answer_message_id=candidate_message.id,
        question=(
            _question_out(prompts[next_index][3])
            if conversation.pending_prompt is None
            and next_index < len(prompts)
            and conversation.completed_at is None
            else None
        ),
    )


@router.patch(
    "/conversations/{conversation_id}/answers/{message_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def edit_latest_answer(
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    body: ConversationAnswerEditIn,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> Response:
    """Edit the most recent saved answer while the assessment is active.

    Earlier answers are already inputs to later adaptive questions, so changing
    one would make the transcript disagree with the conversation that occurred.
    The latest answer is safe to correct, but it must still pass the same
    relevance guard that allowed the counter to advance.
    """
    conversation = await session.get(AssessmentConversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if conversation.status != "active":
        raise HTTPException(
            status_code=409,
            detail="Completed assessment responses are permanent and cannot be edited.",
        )
    await _candidate_link(session, user, conversation.job_candidate_link_id)
    message = await session.get(AssessmentMessage, message_id)
    if (
        message is None
        or message.conversation_id != conversation.id
        or message.speaker != "candidate"
    ):
        raise HTTPException(status_code=404, detail="Response not found")
    latest_id = (
        await session.execute(
            select(AssessmentMessage.id)
            .where(
                AssessmentMessage.conversation_id == conversation.id,
                AssessmentMessage.speaker == "candidate",
            )
            .order_by(AssessmentMessage.ordinal.desc())
            .limit(1)
        )
    ).scalar_one()
    if latest_id != message.id:
        raise HTTPException(
            status_code=409,
            detail="Only your most recent response can be edited.",
        )

    guard = conversation_guardrails.inspect_answer(body.answer)
    edited = guard.sanitized.strip()
    if not guard.allowed:
        raise HTTPException(status_code=422, detail=guard.candidate_message)
    question = (
        await session.execute(
            select(AssessmentMessage.content).where(
                AssessmentMessage.conversation_id == conversation.id,
                AssessmentMessage.speaker == "agent",
                AssessmentMessage.ordinal == message.ordinal - 1,
            )
        )
    ).scalar_one_or_none()
    verdict = await answer_classification.classify(
        session=session,
        question=question or "",
        answer=edited,
        transcript=await _transcript_rows(session, conversation.id),
    )
    if verdict.needs_rechallenge:
        detail = {
            "gibberish": "That edit did not come through as an answer.",
            "empty": "Please enter an answer before saving.",
            "off_topic": "That edit answers a different question.",
            "shallow": "That edit still needs the specific example or detail requested.",
            "evasive": "That edit still does not address the specific question.",
        }.get(verdict.label, "That edit does not answer the question.")
        raise HTTPException(status_code=422, detail=detail)

    message.content = edited
    message.answer_label = verdict.label
    message.evidence_gap = False
    # The structured record counts the change (assessment-spec-doc 4,
    # `revision_count`); the text itself is the transcript's.
    record = (
        await session.execute(
            select(AssessmentAnswer).where(AssessmentAnswer.message_id == message.id)
        )
    ).scalars().first()
    if record is not None:
        record.answer_json = {"text": edited}
        record.revision_count += 1
    await session.flush()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ── Dual-mode assessment: mode choice, consent, the video interview ──────────
#
# (2026-09-05 Dual-Mode Assessment Specification, sections 2-7 and 16.)
#
# The flow is mode selection -> consent -> start, and the two starts diverge
# only in HOW answers are collected: the conversational route above types
# them, the video routes below record them, and the processing pipeline
# writes the SAME transcript and answer records so every scorer stays
# mode-blind (spec 21). Proctoring is mandatory in BOTH modes and stores no
# media either way; the recording here is a separately CONSENTED artifact.


async def _candidate_conversation(
    session: AsyncSession, user: CurrentUser, link_id: uuid.UUID
) -> tuple[JobCandidateLink, Job, AssessmentConversation]:
    """The invited conversation behind a candidate's link, or the refusal the
    conversational start already gives: the conversation row IS the
    invitation, and no mode or consent work happens for the uninvited."""
    link, job = await _candidate_link(session, user, link_id)
    conversation = (
        await session.execute(
            select(AssessmentConversation).where(
                AssessmentConversation.job_candidate_link_id == link.id
            )
        )
    ).scalars().first()
    if conversation is None or conversation.invitation_sent_at is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "This assessment is not open to you yet. The hiring team invites "
                "candidates individually, and you will be emailed if they invite you."
            ),
        )
    return link, job, conversation


async def _mode_frozen(
    session: AsyncSession, conversation: AssessmentConversation
) -> bool:
    """Whether the mode can still change. Frozen the moment the assessment has
    BEGUN in either mode: `started_at` is stamped, or a recording row exists.
    The records already written belong to the chosen mode, and switching
    underneath them would leave a session half of each."""
    if conversation.started_at is not None:
        return True
    any_recording = (
        await session.execute(
            select(func.count())
            .select_from(VideoRecording)
            .where(VideoRecording.conversation_id == conversation.id)
        )
    ).scalar_one()
    return bool(any_recording)


async def _mode_state(
    session: AsyncSession, conversation: AssessmentConversation
) -> ModeStateOut:
    terms = assessment_consent.terms_for(conversation.mode)
    consent = await assessment_consent.find_consent(
        session, conversation.id, conversation.mode
    )
    return ModeStateOut(
        mode=conversation.mode,
        mode_frozen=await _mode_frozen(session, conversation),
        consented=consent is not None,
        consent=ConsentTermsOut(
            assessment_mode=terms.assessment_mode,
            text=terms.text,
            consent_version=terms.consent_version,
            privacy_policy_version=terms.privacy_policy_version,
            terms_version=terms.terms_version,
            # Stage B items (vivekium feature 6), served so the screen never
            # authors consent copy of its own.
            items=consent_catalog.stage_payload(consent_catalog.STAGE_ASSESSMENT),
        ),
    )


@router.get("/conversations/links/{link_id}/mode", response_model=ModeStateOut)
async def get_assessment_mode(
    link_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> ModeStateOut:
    """Where this session stands: the chosen mode, whether it can still
    change, and the CURRENT mode's consent terms exactly as the server will
    stamp them (spec 3.2: the wording is configuration, so the client renders
    what it is given and holds no copy of its own)."""
    _link, _job, conversation = await _candidate_conversation(session, user, link_id)
    return await _mode_state(session, conversation)


@router.post("/conversations/links/{link_id}/mode", response_model=ModeStateOut)
async def select_assessment_mode(
    link_id: uuid.UUID,
    body: AssessmentModeIn,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> ModeStateOut:
    """Choose how this assessment collects answers (spec section 2).

    Before consent and before starting, and FROZEN once the assessment has
    begun: a session that has records in one mode cannot be relabelled as the
    other. Consent is per (session, mode), so switching modes here simply
    means the other mode's consent screen is what gates the start.
    """
    _link, _job, conversation = await _candidate_conversation(session, user, link_id)
    if conversation.mode != body.mode and await _mode_frozen(session, conversation):
        raise HTTPException(
            status_code=409,
            detail=(
                "This assessment has already begun in its chosen mode, so the "
                "mode can no longer change."
            ),
        )
    conversation.mode = body.mode
    await session.flush()
    return await _mode_state(session, conversation)


@router.post("/conversations/links/{link_id}/consent", response_model=ModeStateOut)
async def accept_assessment_consent(
    link_id: uuid.UUID,
    body: AssessmentConsentIn,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> ModeStateOut:
    """Record the candidate's acceptance of the CURRENT mode's terms (3.4).

    Only acceptance is recorded; a decline collects nothing and stores
    nothing, the candidate simply does not start. Idempotent per (session,
    mode): the first acceptance is the audit answer.

    The body carries the Stage B items the candidate ticked, and the service
    refuses anything short of the whole set (vivekium feature 6: each item is
    consented individually).
    """
    link, _job, conversation = await _candidate_conversation(session, user, link_id)
    await assessment_consent.record_consent(
        session,
        conversation,
        candidate_id=link.candidate_id,
        consent_keys=body.consent_keys,
    )
    return await _mode_state(session, conversation)


