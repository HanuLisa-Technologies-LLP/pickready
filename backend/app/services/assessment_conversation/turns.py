"""The conversation engine: open a turn, take one answer, move on.

WHAT THIS REPLACED
------------------
Until 2026-09-24 the whole engine lived inside one route handler,
`api/assessments.respond`, some six hundred lines long, and the answer it took
was whatever arrived next: a client retry after a lost response was filed as
the answer to the NEXT question, the time a recruiter read was a `paused_ms` the
client chose, a reload re-stamped the question clock, and an empty screen at the
deadline was simply a question nobody finished. Appendix B section 3 made all
of that a rule, and the rule needs one engine the route AND the expiry path can
share, so it lives here.

THE TURN
--------
Every prompt the candidate is shown is a TURN: a base question, a follow-up or
a re-ask. Opening one stamps `turn_seq`, `prompt_shown_at` (once, never on a
reload) and the allocation for its kind and format (Appendix B: prose three
minutes, multiple choice and fill-in-the-blank sixty seconds, a follow-up a
hundred seconds, coding twenty minutes). The deadline is the server's
(`timers.turn_clock`), paused only by rows the server wrote (`pauses`).

An answer names the turn it answers. The wrong number is a 409 with nothing
written, so a double click, a replay and a retry after a lost response can never
be filed under the wrong question. An answer that arrives after the deadline
plus the grace is not the candidate's: the SERVER submits what it holds (a
transcribed spoken answer, else the last saved draft, else nothing), and nothing
is an EVIDENCE GAP, recorded as such and never re-asked. The same submission
runs lazily when the candidate comes back to a turn that expired while they
were away.

WHAT MUST NOT MOVE
------------------
The invariants `tests/test_conversation_flow.py` has always pinned:

  * a follow-up or re-ask is answered under its parent's `question_key`, so the
    scorer files it with that question's other answers;
  * a follow-up does NOT advance `next_question_index`, and the index reaching
    the end is what charges the customer;
  * a follow-up outstanding on the last base question holds completion open.

And the new ones:

  * FIXED ORDER, NO SKIPPING, NO EDITING. Only the current turn is answerable;
    past answers are returned read-only and no route writes one.
  * EVERY ITEM IS ASKED. The early close is gone: a conversation ends when its
    written questions are exhausted, and `end_reason` is always
    `prompts_exhausted` for a new row.
  * EVIDENCE PER ANSWER. Every substantive answer is filed in Miti's ledger as
    it arrives (`assessment_pipeline.evidence.record_answer_evidence`, the one
    writer), so the next question's contradiction probing reads a real ledger.
  * The question the candidate reads, the stored prompt and the stored rubric
    belong to ONE question (`ppi_interview.write_question` persists nothing
    it did not accept).
  * Work about a committed row is dispatched AFTER the commit.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.assessment import (
    AssessmentAnswer,
    AssessmentConversation,
    AssessmentMessage,
    CandidateQuestion,
    JobCompetency,
)
from app.models.candidate import JobCandidateLink, Profile
from app.models.job import Job
from app.models.assessment_pause import PAUSE_DEVICE_LOSS, PAUSE_TRANSCRIPTION
from app.models.voice import (
    VOICE_CONSUMED,
    VOICE_FAILED,
    VOICE_IN_FLIGHT,
    VOICE_TRANSCRIBED,
    VoiceAnswer,
)
from app.services import (
    answer_classification,
    assessment_contract,
    conversation_guardrails,
    credit_reconciliation,
    interview_telemetry,
    interviewer,
    ppi_interview,
    telemetry_events,
    vaada_context,
)
from app.services.assessment_conversation import pauses, timers
from app.services.assessment_formats import rendering as format_rendering
from app.services.assessment_formats import scoring as format_scoring
from app.services.assessment_formats import types as question_types
from app.services.assessment_pipeline.evidence import record_answer_evidence
from app.workers.dispatch import dispatch_after_commit

logger = logging.getLogger(__name__)

#: A re-ask after a non-answer, per base question, before the answer is filed
#: as an evidence gap and the interview moves on.
MAX_REASKS_PER_QUESTION = 2

#: The transcript line for a turn that ran out with nothing submitted. Written
#: by the product, so it can never be mistaken for the candidate's words.
TIMED_OUT_TEXT = "(No answer was given before time ran out.)"
#: `assessment_messages.answer_label` for a timed-out answer.
LABEL_TIMED_OUT = "timed_out"

STALE_TURN_DETAIL = (
    "This question was already answered. The assessment has moved on; please "
    "reload to see the question in front of you now."
)
NO_OPEN_TURN_DETAIL = "Please open the assessment first; no question is on screen yet."
COMPLETE_DETAIL = "Conversation is already complete"
PAUSED_DETAIL = (
    "The assessment is paused because your camera or microphone stopped. Turn "
    "it back on to continue; your time is paused until then."
)
TURN_EXPIRED_DETAIL = (
    "The time for this question has run out, so it can no longer be changed."
)
STRUCTURED_NEEDS_PAYLOAD_DETAIL = (
    "This question is answered by choosing or typing into the form shown, not "
    "with a written reply."
)
VOICE_NOT_READY_DETAIL = (
    "Your spoken answer is still being turned into text. It will be submitted "
    "as soon as it is ready."
)
VOICE_FAILED_DETAIL = (
    "Your recording could not be turned into text, so please type this answer "
    "instead."
)
VOICE_NOT_FOR_TURN_DETAIL = "That recording does not belong to the question on screen."


@dataclass(frozen=True)
class Prompt:
    """One question in the candidate's plan, in ask order."""

    aspect: str
    key: str
    row: CandidateQuestion

    @property
    def text(self) -> str:
        """What the candidate reads for this base question: the stored prompt,
        which `ppi_interview.write_question` rewrites only together with its
        rubric."""
        return self.row.prompt


@dataclass
class Submission:
    """One answer to the current turn, from the route or from the expiry path."""

    turn_seq: int
    answer: str = ""
    answer_payload: dict[str, Any] | None = None
    voice_answer_id: uuid.UUID | None = None
    #: The client said its countdown reached zero.
    timed_out: bool = False
    behaviour: Any = None


@dataclass
class TurnContext:
    session: AsyncSession
    job: Job
    link: JobCandidateLink
    conversation: AssessmentConversation
    prompts: list[Prompt]
    proctoring_session: Any = None
    #: How many answers this request took (0 or 1), for the cost record.
    submissions: int = field(default=0)


# ── The plan ─────────────────────────────────────────────────────────────────


async def conversation_prompts(
    session: AsyncSession, job: Job, link: JobCandidateLink
) -> list[tuple[str, str, str, CandidateQuestion]]:
    """This candidate's questions, in ask order, as (aspect, key, text, row).

    ONE list from ONE table, ordered by `ordinal`, which is the plan's own
    deterministic order: two candidates on one job are asked about the same
    skills in the same order. `question_key` is the question row's id, which
    is what the scorer files an answer under.
    """
    rows = (
        await session.execute(
            select(CandidateQuestion, JobCompetency)
            .join(JobCompetency, JobCompetency.id == CandidateQuestion.competency_id)
            .where(CandidateQuestion.job_candidate_link_id == link.id)
            .order_by(CandidateQuestion.ordinal)
        )
    ).all()
    return [
        (competency.category, str(question.id), question.prompt, question)
        for question, competency in rows
    ]


def as_prompts(rows: list[tuple[str, str, str, CandidateQuestion]]) -> list[Prompt]:
    return [Prompt(aspect=aspect, key=key, row=row) for aspect, key, _text, row in rows]


# ── The turn ─────────────────────────────────────────────────────────────────


def turn_kind(conversation: AssessmentConversation) -> str:
    """base | probe | reask. Rows from before migration 0045 carry a NULL kind
    on a pending prompt; every one of those was a probe."""
    if conversation.pending_prompt is None:
        return timers.TURN_BASE
    return (
        timers.TURN_REASK if conversation.pending_kind == "reask" else timers.TURN_PROBE
    )


def is_finished(conversation: AssessmentConversation, prompts: list[Prompt]) -> bool:
    return conversation.completed_at is not None or (
        conversation.pending_prompt is None
        and conversation.next_question_index >= len(prompts)
    )


def current_row(
    conversation: AssessmentConversation, prompts: list[Prompt]
) -> CandidateQuestion | None:
    """The question row the current turn is about: the pending prompt's parent
    for a follow-up or re-ask, else the next base question."""
    if conversation.pending_prompt is not None:
        key = conversation.pending_question_key or ""
        return next((prompt.row for prompt in prompts if prompt.key == key), None)
    index = conversation.next_question_index
    return prompts[index].row if index < len(prompts) else None


def current_prompt_text(
    conversation: AssessmentConversation, prompts: list[Prompt]
) -> str | None:
    """What is on screen for the current turn, or None once finished."""
    if conversation.pending_prompt is not None:
        return conversation.pending_prompt
    index = conversation.next_question_index
    if index >= len(prompts):
        return None
    return prompts[index].text


def is_prose_turn(conversation: AssessmentConversation, prompts: list[Prompt]) -> bool:
    """A follow-up and a re-ask are always prose; a base question is prose when
    its format is one of the text formats."""
    if conversation.pending_prompt is not None:
        return True
    row = current_row(conversation, prompts)
    return row is not None and row.question_type in question_types.TEXT_TYPES


def allocation_for(conversation: AssessmentConversation, prompts: list[Prompt]) -> int:
    settings = get_settings()
    row = current_row(conversation, prompts)
    return timers.allocation_seconds(
        kind=turn_kind(conversation),
        question_type=row.question_type if row is not None else None,
        prose_seconds=settings.assessment_time_prose_seconds,
        objective_seconds=settings.assessment_time_objective_seconds,
        coding_seconds=settings.assessment_time_coding_seconds,
        follow_up_seconds=settings.assessment_time_follow_up_seconds,
        objective_types=question_types.OBJECTIVE_TYPES,
        coding_type=question_types.CODING,
    )


def has_open_turn(conversation: AssessmentConversation, prompts: list[Prompt]) -> bool:
    return (
        conversation.turn_seq > 0
        and conversation.prompt_shown_at is not None
        and conversation.turn_allocation_seconds is not None
        and not is_finished(conversation, prompts)
    )


async def open_turn(
    session: AsyncSession, conversation: AssessmentConversation, prompts: list[Prompt]
) -> None:
    """Put the next prompt on screen: a new turn, its clock stamped NOW.

    Called only after the question text is written, so the time a model took
    to write it is never counted against the candidate. The draft belongs to
    one turn and is cleared.
    """
    conversation.turn_seq = int(conversation.turn_seq or 0) + 1
    conversation.prompt_shown_at = datetime.now(timezone.utc)
    conversation.turn_allocation_seconds = allocation_for(conversation, prompts)
    conversation.draft_answer_json = None
    conversation.draft_turn_seq = None
    conversation.draft_saved_at = None
    await session.flush()


async def turn_clock(
    session: AsyncSession, conversation: AssessmentConversation, *, now: datetime
) -> timers.TurnClock | None:
    """The open turn's clock, or None when no turn is open."""
    if (
        conversation.turn_seq <= 0
        or conversation.prompt_shown_at is None
        or conversation.turn_allocation_seconds is None
    ):
        return None
    shown = conversation.prompt_shown_at
    pause_rows = await pauses.pauses_for_turn(
        session, conversation.id, since=shown, until=now
    )
    return timers.turn_clock(
        shown_at=shown,
        allocation_seconds=int(conversation.turn_allocation_seconds),
        pauses=pause_rows,
        now=now,
        grace_seconds=get_settings().assessment_submit_grace_seconds,
    )


def require_turn(conversation: AssessmentConversation, turn_seq: int) -> None:
    """The request must name the turn on screen, or nothing is written."""
    if conversation.turn_seq <= 0:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NO_OPEN_TURN_DETAIL)
    if int(turn_seq) != int(conversation.turn_seq):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=STALE_TURN_DETAIL)


async def require_not_device_paused(
    session: AsyncSession, conversation: AssessmentConversation
) -> None:
    """A lost camera or microphone pauses the assessment; nothing is answered,
    drafted or recorded until it is back.

    The pause is proctoring's (`services/proctoring/device_pause`), and so is
    the decision about one left open past its grace (the session ends). Any
    UNCLOSED device pause refuses here, including one past its cap: an answer
    taken in that moment would be an answer given with the camera off. This is
    the same question `proctoring.gate.require_answerable` asks, and the
    routes switch to that one call when both work packages are integrated.
    """
    open_pause = await pauses.unclosed_pause(session, conversation.id, PAUSE_DEVICE_LOSS)
    if open_pause is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=PAUSED_DETAIL)


# ── Voice answers on the current turn ────────────────────────────────────────


async def voice_for_turn(
    session: AsyncSession, conversation: AssessmentConversation, *, statuses: tuple[str, ...]
) -> VoiceAnswer | None:
    return (
        await session.execute(
            select(VoiceAnswer)
            .where(
                VoiceAnswer.conversation_id == conversation.id,
                VoiceAnswer.turn_seq == conversation.turn_seq,
                VoiceAnswer.status.in_(statuses),
            )
            .order_by(VoiceAnswer.created_at.desc())
            .limit(1)
        )
    ).scalars().first()


def transcription_budget_seconds() -> int:
    """How long a spoken answer may take to come back from Transcribe before
    it is treated as failed: the Transcribe budget plus a margin for the
    task's own start and the upload's commit. It is also the CAP on the
    transcription pause (`pauses.open_pause(max_seconds=...)`), so a lost task
    can never stop a candidate's clock for longer than this, whether or not
    anybody reads the row again."""
    return int(get_settings().assessment_voice_transcribe_timeout_seconds) + 60


async def open_transcription_pause(
    session: AsyncSession, row: VoiceAnswer, *, now: datetime
) -> None:
    """Stop the clock while `row` is transcribed. Capped, never open-ended."""
    await pauses.open_pause(
        session,
        row.conversation_id,
        PAUSE_TRANSCRIPTION,
        at=now,
        ref_id=row.id,
        max_seconds=transcription_budget_seconds(),
    )


async def hold_after_failure(
    session: AsyncSession, row: VoiceAnswer, *, now: datetime
) -> None:
    """A transcription FAILED at `now`: keep the clock stopped for a short,
    capped window so the candidate can read what happened and switch to
    typing (`assessment_voice_failure_pause_seconds`).

    The transcription pause ends at `now` and a failure pause of the same
    reason, opened by the same row, runs for the window. Acknowledging closes
    it sooner (`close_pause` with this row's `ref_id`); nothing needs a sweep
    to end it, because the cap is on the row.
    """
    await pauses.close_pause(
        session, row.conversation_id, PAUSE_TRANSCRIPTION, at=now, ref_id=row.id
    )
    await pauses.open_pause(
        session,
        row.conversation_id,
        PAUSE_TRANSCRIPTION,
        at=now,
        ref_id=row.id,
        max_seconds=get_settings().assessment_voice_failure_pause_seconds,
    )


async def fail_stale_voice(
    session: AsyncSession, row: VoiceAnswer, *, now: datetime
) -> bool:
    """A transcription that never reported back becomes a FAILED one.

    The task can be lost (an invoke that failed after the commit, a worker
    killed mid-job). Its pause stops counting at its cap whatever happens;
    this marks the ROW failed on read past the same budget, so the candidate
    is told to type rather than waiting on a transcript that is not coming.
    Returns whether the row changed.
    """
    if row.status not in VOICE_IN_FLIGHT or row.uploaded_at is None:
        return False
    if (now - row.uploaded_at).total_seconds() <= transcription_budget_seconds():
        return False
    row.status = VOICE_FAILED
    row.failure_reason = "The transcription did not report back within its time budget."
    row.failed_at = now
    await hold_after_failure(session, row, now=now)
    logger.warning(
        "assessment_turns.voice_timed_out voice_id=%s conversation_id=%s",
        row.id, row.conversation_id,
    )
    return True


# ── Reads the writer and the log line need ───────────────────────────────────


async def transcript_rows(
    session: AsyncSession, conversation_id: uuid.UUID
) -> list[dict[str, Any]]:
    """The conversation so far, oldest first: the agent's MEMORY, read back from
    `assessment_messages` because every turn is one stateless request."""
    rows = (
        await session.execute(
            select(AssessmentMessage.speaker, AssessmentMessage.content)
            .where(AssessmentMessage.conversation_id == conversation_id)
            .order_by(AssessmentMessage.ordinal)
        )
    ).all()
    return [{"speaker": speaker, "content": content} for speaker, content in rows]


async def history(
    session: AsyncSession, conversation_id: uuid.UUID
) -> list[tuple[str, str]]:
    """Every answered exchange as (question, answer), oldest first. The agent
    line is written together with the answer, so an unanswered turn is never
    in here."""
    rows = (
        await session.execute(
            select(AssessmentMessage.speaker, AssessmentMessage.content)
            .where(AssessmentMessage.conversation_id == conversation_id)
            .order_by(AssessmentMessage.ordinal)
        )
    ).all()
    pairs: list[tuple[str, str]] = []
    question: str | None = None
    for speaker, content in rows:
        if speaker == "agent":
            question = content
        elif speaker == "candidate" and question is not None:
            pairs.append((question, content))
            question = None
    return pairs


async def resume_excerpt(session: AsyncSession, link: JobCandidateLink) -> str:
    """This application's resume text, or empty, read per turn so a question is
    grounded in what the application carries now."""
    if link.profile_id is None:
        return ""
    return (
        await session.execute(select(Profile.resume_text).where(Profile.id == link.profile_id))
    ).scalar_one_or_none() or ""


async def ledger_dimension_flags(
    session: AsyncSession, job: Job, link: JobCandidateLink
) -> tuple[set[str], set[str]]:
    """(skills the ledger contradicts, skills it only inferred), by name.

    THE MITI SIDE OF THE LOOP, and since 2026-09-24 it reads a ledger that is
    written per answer while the conversation runs. A database failure here is
    logged and read as "no flags", inside a SAVEPOINT so the turn's own writes
    survive it: the flags shape the NEXT question, and a ledger outage must
    cost that question its contradiction probe and nothing else.
    """
    from app.services.evidence import ledger  # noqa: PLC0415 -- import cycle

    try:
        async with session.begin_nested():
            claims = await ledger.load_claims(
                session, tenant_id=job.tenant_id, job_id=job.id, link_id=link.id
            )
    except SQLAlchemyError:
        logger.warning("assessment_turns.ledger_unavailable link_id=%s", link.id)
        return set(), set()
    conflicting = {c.dimension for c in claims if c.status == ledger.CLAIM_CONTRADICTED}
    inferred = {c.dimension for c in claims if c.status == ledger.CLAIM_INFERRED_ONLY}
    return conflicting, inferred


async def coverage_rows(session: AsyncSession, link: JobCandidateLink) -> list[dict[str, Any]]:
    """Per-skill answer counts for the operator's state line. Grouped by
    SKILL, never by question: several questions can probe one skill and a
    follow-up is filed under its parent's key."""
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


# ── Writing the next question ────────────────────────────────────────────────


async def write_next_question(ctx: TurnContext) -> None:
    """Write the base question about to be shown, for THIS candidate at THIS
    point, from the conversation's contract (`vaada_context`).

    TEXT QUESTIONS ONLY: a structured question is delivered verbatim, because
    its answer key was written with it. A degraded write leaves the row as it
    was and the stored prompt is what the candidate reads; `delivered_prompt`
    is either that same text or NULL, never a different question.

    Only contract and database failures are absorbed, and each is logged: they
    cost the candidate this question's adaptivity, never their turn. A
    programming error propagates.
    """
    conversation = ctx.conversation
    conversation.delivered_prompt = None
    index = conversation.next_question_index
    if conversation.pending_prompt is not None or index >= len(ctx.prompts):
        return
    prompt = ctx.prompts[index]
    row = prompt.row
    if question_types.is_structured(row.question_type):
        return
    session = ctx.session
    try:
        competency = await session.get(JobCompetency, row.competency_id)
        if competency is None:
            raise LookupError(f"skill {row.competency_id} of question {row.id} is gone")
        context = await vaada_context.build(session, conversation=conversation, question=row)
        memory = await transcript_rows(session, conversation.id)
        conflicting, _inferred = await ledger_dimension_flags(session, ctx.job, ctx.link)
        result = await ppi_interview.write_question(
            session=session,
            job=ctx.job,
            row=row,
            competency=competency,
            context=context,
            resume_excerpt=await resume_excerpt(session, ctx.link),
            transcript=memory,
            asked_before=[m["content"] for m in memory if m.get("speaker") == "agent"],
            conflicting=competency.name in conflicting,
        )
    except (
        assessment_contract.ContractNotBound,
        assessment_contract.ContractIntegrityError,
        LookupError,
        SQLAlchemyError,
    ) as exc:
        logger.warning(
            "assessment_turns.next_question_unavailable conversation_id=%s error=%s",
            conversation.id, type(exc).__name__,
        )
        return
    if not result.degraded:
        conversation.delivered_prompt = row.prompt
    await session.flush()


# ── Taking one answer ────────────────────────────────────────────────────────


async def _next_ordinal(session: AsyncSession, conversation_id: uuid.UUID) -> int:
    return (
        await session.execute(
            select(func.coalesce(func.max(AssessmentMessage.ordinal), 0)).where(
                AssessmentMessage.conversation_id == conversation_id
            )
        )
    ).scalar_one()


async def _write_answer_record(
    ctx: TurnContext,
    question_id: uuid.UUID,
    message: AssessmentMessage,
    *,
    answer_json: dict[str, Any],
    auto_score: float | None,
    evaluation: dict[str, Any] | None,
    now: datetime,
    spent_seconds: int | None,
    question_type: str,
) -> None:
    """One `assessment_answers` row per (conversation, question); a follow-up
    or a re-ask revises it rather than writing a second."""
    session = ctx.session
    conversation = ctx.conversation
    record = (
        await session.execute(
            select(AssessmentAnswer).where(
                AssessmentAnswer.conversation_id == conversation.id,
                AssessmentAnswer.question_id == question_id,
            )
        )
    ).scalars().first()
    if record is None:
        session.add(
            AssessmentAnswer(
                tenant_id=ctx.job.tenant_id,
                conversation_id=conversation.id,
                question_id=question_id,
                message_id=message.id,
                question_type=question_type,
                answer_json=answer_json,
                started_at=conversation.prompt_shown_at,
                submitted_at=now,
                time_spent_seconds=spent_seconds,
                auto_score=auto_score,
                ai_evaluation_json=evaluation,
            )
        )
    else:
        record.revision_count += 1
        record.submitted_at = now
        if spent_seconds is not None:
            record.time_spent_seconds = (record.time_spent_seconds or 0) + spent_seconds
        if auto_score is not None:
            record.answer_json = answer_json
            record.auto_score = auto_score
            record.ai_evaluation_json = evaluation
            record.message_id = message.id
    await session.flush()


async def _record_behaviour(
    ctx: TurnContext, question_id: uuid.UUID | None, behaviour: Any, *, final_length: int
) -> None:
    """Hand the answer field's timings to proctoring. Read by nothing that
    scores; a refusal is logged and the turn continues."""
    if behaviour is None or question_id is None or ctx.proctoring_session is None:
        return
    from app.services.proctoring import behaviour as proctoring_behaviour  # noqa: PLC0415

    try:
        await proctoring_behaviour.record_answer_behaviour(
            ctx.session,
            ctx.proctoring_session,
            ctx.conversation,
            question_id,
            behaviour,
            final_length=final_length,
        )
    except (HTTPException, ValueError) as exc:
        logger.warning(
            "assessment_turns.behaviour_not_recorded conversation_id=%s question_id=%s error=%s",
            ctx.conversation.id, question_id, type(exc).__name__,
        )


async def _record_evidence(
    ctx: TurnContext, row: CandidateQuestion | None, message: AssessmentMessage, *, now: datetime
) -> None:
    """File one answer in Miti's ledger AS IT ARRIVES, through the one writer.

    The writer decides substance itself and never fails the caller (a database
    failure is logged inside its own savepoint).
    """
    if row is None:
        return
    competency = await ctx.session.get(JobCompetency, row.competency_id)
    if competency is None:
        return
    await record_answer_evidence(
        ctx.session,
        tenant_id=ctx.job.tenant_id,
        job_id=ctx.job.id,
        link_id=ctx.link.id,
        candidate_id=ctx.link.candidate_id,
        skill_id=competency.id,
        skill_name=competency.name,
        skill_bucket=competency.category,
        question_id=row.id,
        message_id=message.id,
        turn=int(message.ordinal or 0),
        text=message.content,
        answered_at=now,
    )


async def _consume_voice(
    ctx: TurnContext, submission: Submission
) -> tuple[str | None, VoiceAnswer | None]:
    """The spoken answer for this turn, if there is one: (transcript, row).

    A TRANSCRIBED, unconsumed recording for the turn IS the answer, whatever
    else the request carries, because the transcript is final (Appendix B): a
    typed body sent beside it would be an edit by another route. A request
    naming a recording that is still being transcribed, failed, or belongs to
    another turn is refused.
    """
    session = ctx.session
    conversation = ctx.conversation
    transcribed = await voice_for_turn(session, conversation, statuses=(VOICE_TRANSCRIBED,))
    if submission.voice_answer_id is not None:
        named = await session.get(VoiceAnswer, submission.voice_answer_id)
        if (
            named is None
            or named.conversation_id != conversation.id
            or named.turn_seq != conversation.turn_seq
        ):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=VOICE_NOT_FOR_TURN_DETAIL)
        if named.status in VOICE_IN_FLIGHT:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=VOICE_NOT_READY_DETAIL)
        if named.status == VOICE_FAILED:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=VOICE_FAILED_DETAIL)
        if named.status == VOICE_CONSUMED:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=STALE_TURN_DETAIL)
        transcribed = named
    if transcribed is None:
        return None, None
    return transcribed.transcript_text or "", transcribed


async def expiry_submission(
    ctx: TurnContext, clock: timers.TurnClock
) -> Submission:
    """What the SERVER submits for a turn whose time ran out: a transcribed
    spoken answer, else the draft saved in time, else nothing."""
    conversation = ctx.conversation
    submission = Submission(turn_seq=conversation.turn_seq, timed_out=True)
    transcribed = await voice_for_turn(ctx.session, conversation, statuses=(VOICE_TRANSCRIBED,))
    if transcribed is not None:
        submission.voice_answer_id = transcribed.id
        return submission
    draft = conversation.draft_answer_json
    if (
        isinstance(draft, dict)
        and conversation.draft_turn_seq == conversation.turn_seq
        and conversation.draft_saved_at is not None
        and conversation.draft_saved_at <= clock.grace_deadline_at
    ):
        submission.answer = str(draft.get("answer") or "")
        payload = draft.get("answer_payload")
        submission.answer_payload = payload if isinstance(payload, dict) else None
    return submission


async def submit_turn(
    ctx: TurnContext,
    submission: Submission,
    *,
    clock: timers.TurnClock,
    server_timeout: bool,
) -> AssessmentMessage:
    """Take one answer to the current turn and move the conversation on.

    The caller has already checked the turn number and the device pause.
    `server_timeout` means the deadline passed and `submission` is what the
    server holds (`expiry_submission`), not what a request carried. Returns
    the candidate's message.
    """
    started = time.monotonic()
    session = ctx.session
    conversation = ctx.conversation
    job = ctx.job
    prompts = ctx.prompts
    now = datetime.now(timezone.utc)
    index = conversation.next_question_index
    pending = conversation.pending_prompt
    kind = turn_kind(conversation)
    if index >= len(prompts) and not pending:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=COMPLETE_DETAIL)
    timed_out = server_timeout or submission.timed_out
    spent = timers.time_spent_seconds(clock, now=now)

    row = current_row(conversation, prompts)
    if pending:
        domain = conversation.pending_domain or "ppi"
        key = conversation.pending_question_key or ""
        shown = pending
    else:
        domain, key = prompts[index].aspect, prompts[index].key
        shown = prompts[index].text
    ordinal = await _next_ordinal(session, conversation.id)
    agent_message = AssessmentMessage(
        tenant_id=job.tenant_id, conversation_id=conversation.id, ordinal=ordinal + 1,
        speaker="agent", domain=domain, question_key=key, content=shown,
    )

    # ── A STRUCTURED base question: delivered verbatim, answered as a structure
    if not pending and row is not None and question_types.is_structured(row.question_type):
        parsed_json: dict[str, Any] | None = None
        if submission.answer_payload is not None:
            try:
                parsed_json = question_types.parse_answer(
                    row.question_type, row.payload_json, submission.answer_payload
                ).model_dump()
            except ValueError as exc:
                if not server_timeout:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                # A draft that never became a valid answer is no answer.
                parsed_json = None
        elif not timed_out:
            raise HTTPException(status_code=422, detail=STRUCTURED_NEEDS_PAYLOAD_DETAIL)

        if parsed_json is None:
            message = await _write_timed_out(ctx, agent_message, row, key, domain, now, spent)
        else:
            auto_score: float | None = None
            evaluation: dict[str, Any] | None = None
            if row.question_type in question_types.OBJECTIVE_TYPES:
                objective = await format_scoring.score(
                    session, row.question_type, row.payload_json, parsed_json
                )
                auto_score = objective.auto_score
                if objective.blank_results:
                    evaluation = {"blank_results": list(objective.blank_results)}
            line = format_rendering.transcript_line(row.question_type, row.payload_json, parsed_json)
            if timed_out:
                parsed_json = {**parsed_json, "timed_out": True}
            message = AssessmentMessage(
                tenant_id=job.tenant_id, conversation_id=conversation.id, ordinal=ordinal + 2,
                speaker="candidate", domain=domain, question_key=key, content=line,
                answer_label="substantive",
            )
            session.add_all([agent_message, message])
            await session.flush()
            await _write_answer_record(
                ctx, row.id, message, answer_json=parsed_json, auto_score=auto_score,
                evaluation=evaluation, now=now, spent_seconds=spent,
                question_type=row.question_type,
            )
            await _record_evidence(ctx, row, message, now=now)
        await _record_behaviour(ctx, row.id, submission.behaviour, final_length=len(message.content))
        conversation.next_question_index += 1
        conversation.reasks_used = 0
        _telemetry(conversation, index, key, domain, message.answer_label or "substantive",
                   "timed_out" if parsed_json is None else "advanced", False, started)
        await _finish_turn(ctx)
        return message

    # ── A PROSE turn: a base question, a follow-up or a re-ask ──────────────
    conversation.pending_prompt = None
    conversation.pending_question_key = None
    conversation.pending_domain = None
    conversation.pending_kind = None
    was_written_live = conversation.delivered_prompt is not None
    conversation.delivered_prompt = None

    transcript_text, voice_row = await _consume_voice(ctx, submission)
    raw_text = transcript_text if voice_row is not None else submission.answer
    guard = conversation_guardrails.inspect_answer(raw_text)
    answer_text = guard.sanitized.strip()

    if timed_out and not answer_text:
        message = await _write_timed_out(ctx, agent_message, row, key, domain, now, spent)
        if voice_row is not None:
            voice_row.status = VOICE_CONSUMED
            voice_row.consumed_message_id = message.id
        if kind != timers.TURN_PROBE:
            conversation.next_question_index += 1
            conversation.reasks_used = 0
        _telemetry(conversation, index, key, domain, LABEL_TIMED_OUT, "timed_out",
                   was_written_live, started)
        await _finish_turn(ctx)
        return message

    message = AssessmentMessage(
        tenant_id=job.tenant_id, conversation_id=conversation.id, ordinal=ordinal + 2,
        speaker="candidate", domain=domain, question_key=key, content=answer_text,
    )
    session.add_all([agent_message, message])
    await session.flush()
    answer_json: dict[str, Any] = {"text": answer_text}
    if voice_row is not None:
        answer_json.update({"input": "voice", "voice_answer_id": str(voice_row.id)})
        voice_row.status = VOICE_CONSUMED
        voice_row.consumed_message_id = message.id
    if timed_out:
        answer_json["timed_out"] = True
    if row is not None:
        await _write_answer_record(
            ctx, row.id, message, answer_json=answer_json, auto_score=None, evaluation=None,
            now=now, spent_seconds=spent, question_type=row.question_type,
        )
    await _record_behaviour(
        ctx, row.id if row is not None else None, submission.behaviour,
        final_length=len(answer_text),
    )

    if kind == timers.TURN_PROBE:
        # A probe is supplemental evidence for an accepted base answer: never
        # classified, never re-asked, never advances the base counter.
        await _record_evidence(ctx, row, message, now=now)
        _telemetry(conversation, index, key, domain, "probe_answer", "advanced",
                   was_written_live, started)
        await _finish_turn(ctx)
        return message

    transcript = await transcript_rows(session, conversation.id)
    reaction: str | None = None
    action = "advanced"
    degraded = False
    if not guard.allowed:
        answer_label = f"guarded_{guard.violation}"
        needs_rechallenge = True
    else:
        verdict = await answer_classification.classify(
            session=session, question=shown, answer=answer_text, transcript=transcript
        )
        answer_label = verdict.label
        degraded = verdict.confidence == "low"
        needs_rechallenge = verdict.needs_rechallenge
    message.answer_label = answer_label
    if answer_label == "substantive":
        await _record_evidence(ctx, row, message, now=now)

    # A timed-out answer is never re-asked: the time for this question is over.
    if needs_rechallenge and not timed_out and conversation.reasks_used < MAX_REASKS_PER_QUESTION:
        if not guard.allowed:
            reaction = guard.candidate_message
        else:
            reaction = await interviewer.challenge_non_answer(
                session=session, question=shown, answer=answer_text,
                transcript=transcript, label=answer_label,
            )
        if reaction:
            conversation.reasks_used += 1
            action = "rechallenged"

    if reaction is None:
        conversation.next_question_index += 1
        conversation.reasks_used = 0
        if needs_rechallenge:
            message.evidence_gap = True
            action = "advanced_with_gap"
        elif kind == timers.TURN_BASE and not server_timeout:
            # Nobody is on the other end of a server-submitted turn, so it is
            # not probed; a candidate whose own countdown ran out is.
            reaction = await interviewer.next_follow_up(
                session=session, question=shown, answer=answer_text,
                transcript=transcript, follow_ups_used=conversation.follow_ups_used,
                already_followed_up=False,
                budget=interviewer.follow_up_budget(len(prompts)),
                asked_before=[r["content"] for r in transcript if r.get("speaker") == "agent"],
            )
            if reaction:
                conversation.follow_ups_used += 1
                action = "followed_up"

    if reaction:
        conversation.pending_prompt = conversation_guardrails.inspect_agent_output(reaction)
        conversation.pending_question_key = key
        conversation.pending_domain = domain
        conversation.pending_kind = "reask" if action == "rechallenged" else "probe"

    _telemetry(conversation, index, key, domain, answer_label, action, was_written_live,
               started, degraded=degraded)
    await _finish_turn(ctx)
    return message


async def _write_timed_out(
    ctx: TurnContext,
    agent_message: AssessmentMessage,
    row: CandidateQuestion | None,
    key: str,
    domain: str,
    now: datetime,
    spent: int,
) -> AssessmentMessage:
    """Record a turn that ran out with nothing submitted: an EVIDENCE GAP,
    written by the product in its own words, never re-asked."""
    message = AssessmentMessage(
        tenant_id=ctx.job.tenant_id, conversation_id=ctx.conversation.id,
        ordinal=agent_message.ordinal + 1, speaker="candidate", domain=domain,
        question_key=key, content=TIMED_OUT_TEXT, answer_label=LABEL_TIMED_OUT,
        evidence_gap=True,
    )
    ctx.session.add_all([agent_message, message])
    await ctx.session.flush()
    if row is not None:
        await _write_answer_record(
            ctx, row.id, message, answer_json={"timed_out": True}, auto_score=None,
            evaluation=None, now=now, spent_seconds=spent, question_type=row.question_type,
        )
    return message


def _telemetry(
    conversation: AssessmentConversation,
    index: int,
    key: str,
    domain: str,
    label: str,
    action: str,
    generated: bool,
    started: float,
    *,
    degraded: bool = False,
) -> None:
    interview_telemetry.record_turn(
        interview_telemetry.TurnEvent(
            conversation_id=str(conversation.id),
            turn_index=index,
            question_key=key,
            domain=domain,
            answer_label=label,
            action=action,
            generated=generated,
            degraded=degraded,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    )


async def _log_state(ctx: TurnContext) -> None:
    """The operator's one line about coverage. Reporting only: nothing reads
    it to decide whether the assessment ends."""
    rows = await coverage_rows(ctx.session, ctx.link)
    conflicting, inferred = await ledger_dimension_flags(ctx.session, ctx.job, ctx.link)
    state = interviewer.conversation_state(
        dimensions=[
            interviewer.DimensionEvidence(
                dimension=r["dimension"], answers=r["answers"], substantive=r["substantive"],
                gaps=r["gaps"], conflicting=r["dimension"] in conflicting,
                weak=r["dimension"] in inferred,
            )
            for r in rows
        ],
        asked=ctx.conversation.next_question_index,
        total_written=len(ctx.prompts),
        # Every item is asked, so the floor IS the whole plan.
        floor=len(ctx.prompts),
        probe_outstanding=ctx.conversation.pending_prompt is not None,
    )
    logger.info(
        "assessments.conversation_state conversation_id=%s state=%s",
        ctx.conversation.id, state.as_log(),
    )


async def _finish_turn(ctx: TurnContext) -> None:
    """After an answer: complete the conversation, or put the next prompt on
    screen as a new turn."""
    session = ctx.session
    conversation = ctx.conversation
    job = ctx.job
    link = ctx.link
    ctx.submissions += 1
    await session.flush()
    if conversation.pending_prompt is None:
        await _log_state(ctx)
    if is_finished(conversation, ctx.prompts):
        conversation.status = "completed"
        conversation.completed_at = datetime.now(timezone.utc)
        # EVERY ITEM IS ASKED, so there is one way to finish.
        conversation.end_reason = interviewer.STOP_PROMPTS_EXHAUSTED
        conversation.turn_allocation_seconds = None
        interview_telemetry.record_end(
            interview_telemetry.EndEvent(
                conversation_id=str(conversation.id),
                end_reason=conversation.end_reason,
                questions_asked=conversation.next_question_index,
                questions_written=len(ctx.prompts),
                floor=len(ctx.prompts),
            )
        )
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
        # One full credit, charged the moment `completed_at` is set; idempotent.
        await credit_reconciliation.charge_completed(
            session,
            conversation_id=conversation.id,
            tenant_id=job.tenant_id,
            job_candidate_link_id=link.id,
        )
        # AFTER THE COMMIT. A scoring run started before this transaction
        # commits reads a conversation that is not yet complete; one sent
        # before a rollback scores a completion that never happened. A lost
        # invoke is repaired by `release_held_assessments` (hourly) and
        # `reconcile_context_index` (hourly).
        dispatch_after_commit(session, "pickready.run_functional_assessment", args=[str(link.id)])
        dispatch_after_commit(session, "pickready.index_document", args=["assessment", str(link.id)])
        await session.flush()
        return
    if conversation.pending_prompt is None:
        await write_next_question(ctx)
    await open_turn(session, conversation, ctx.prompts)
