"""A final coding answer: accepted, executed against the hidden tests, reviewed.

THE ORDER IS THE DESIGN
-----------------------
1. ACCEPT (`accept_final`, inside the candidate's `respond` transaction). One
   `coding_submissions` row per answer, `INSERT ... ON CONFLICT (answer_id) DO
   NOTHING`, so a replayed submit is one row and one dispatch. An empty answer
   is `no_code`: an evidence gap, never executed, never reviewed, never a zero.
   Otherwise the row is `pending` and `pickready.execute_coding_submission` is
   dispatched AFTER COMMIT (`dispatch_after_commit`), so the task can never
   start before the row it reads exists, and a rolled-back submit dispatches
   nothing.
2. SUBMIT (the task). The program and its inputs go to the sandbox and the
   ticket is COMMITTED before anybody polls. A worker killed while polling
   leaves `submitted` with a ticket, and the next attempt COLLECTS that run
   instead of executing the candidate's code a second time.
3. COLLECT. Poll to a predictive deadline, then judge every hidden test with
   `keys.AnswerKey.passed` and keep only outcome words and resource figures
   (`execution.summarise_hidden`). Hidden stdout and stderr are never
   persisted and never logged. The sample (visible) tests run in the same
   batch, and the first runtime error message among them is kept, because a
   public input cannot carry the answer key.
4. REVIEW. The code-quality review (`review.review_code_quality`) runs once
   execution is complete. A failed review is `review_status='failed'` and the
   sweep asks again; it is never replaced by a template.
5. FILE AND HAND OVER. The answer is filed in the evidence ledger through
   `assessment_pipeline.evidence.record_answer_evidence`, the one ledger
   writer. When the conversation is complete, nothing on it is still open and
   no report exists, scoring is dispatched after commit.

WHAT HAPPENS WHEN THE SANDBOX MISBEHAVES
----------------------------------------
* Unreachable, queue full, timed out, deadline passed: the attempt is recorded
  (`execution_attempts`, `last_error_class`), committed, and the error is
  RE-RAISED so the task's retry loop and the platform's error metric see it.
  The ticket, if there is one, is kept: the run may still finish.
* `ExecutionTicketLost`: the host was replaced or pruned the run. Collecting
  harder will not find it, so the ticket is CLEARED, the row goes back to
  `pending`, and the code is submitted again in the same invocation (once).
  This is the one case where resubmitting is correct: the result is a pure
  function of code and input, so a second run cannot double anything.
* A sandbox fault on any test (`INTERNAL_ERROR`, a result set that does not
  cover every input): the run says nothing about the candidate, so it is
  discarded and resubmitted rather than graded.
* The sandbox refused OUR request, the answer key failed its digest, or the
  stored code no longer matches the digest it was accepted under: a defect on
  this side. Recorded and raised as `SubmissionDefect`, which the task turns
  into a permanent failure rather than a retry storm. The row stays open, so
  after `coding_execution_max_wait_hours` the answer reads "Not assessed" and
  goes to a person.

No agent-action ledger entry is written for the sandbox call, deliberately: a
duplicate execution after an UNKNOWN submit is harmless (it is a pure function
of code and input, and the host prunes orphans hourly), which is the property
the action ledger exists to establish for side effects that are not.

LOCKS. Every write stage takes `pg_try_advisory_xact_lock` on the submission
(`services/locks`, BLAKE2b key) and commits at the end of the stage. A second
worker that finds the lock taken returns; a worker that finds the stage
already done by another moves on. Nothing holds a lock across a sandbox poll
or a model call.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.assessment import (
    AssessmentAnswer,
    AssessmentConversation,
    AssessmentMessage,
    CandidateQuestion,
    FunctionalSkillsReport,
    JobCompetency,
)
from app.models.candidate import JobCandidateLink
from app.models.coding import (
    EXECUTION_COMPLETE,
    EXECUTION_NO_CODE,
    EXECUTION_PENDING,
    EXECUTION_SUBMITTED,
    REVIEW_COMPLETE,
    REVIEW_FAILED,
    REVIEW_NOT_APPLICABLE,
    REVIEW_PENDING,
    CodingRun,
    CodingSubmission,
    submission_source_digest,
)
from app.services import code_execution, locks
from app.services.assessment_formats import types
from app.services.code_execution import (
    CodeExecutionProvider,
    ExecutionNotConfigured,
    ExecutionRejected,
    ExecutionTicket,
    ExecutionTicketLost,
    ExecutionUnavailable,
)
from app.services.coding_assessment import execution, keys, phrasing, review
from app.services.coding_assessment import payload as coding_payload

logger = logging.getLogger(__name__)

__all__ = [
    "LOCK_NAMESPACE",
    "TASK_NAME",
    "SCORING_TASK_NAME",
    "CONVERSATION_COMPLETED",
    "OPEN_EXECUTION",
    "OPEN_REVIEW",
    "STATE_WORD_SUBMITTED",
    "STATE_WORD_CHECKING",
    "STATE_WORD_CHECKED",
    "SubmissionDefect",
    "DraftSource",
    "ScoringHold",
    "ExecutionReport",
    "accept_final",
    "latest_draft",
    "pending_for_conversation",
    "scoring_hold",
    "candidate_state_word",
    "execute_submission",
]

LOCK_NAMESPACE = "coding.submission"
TASK_NAME = "pickready.execute_coding_submission"
SCORING_TASK_NAME = "pickready.run_functional_assessment"
#: `assessment_conversations.status` once the candidate has finished.
CONVERSATION_COMPLETED = "completed"

#: The execution and review states that still owe work.
OPEN_EXECUTION: tuple[str, ...] = (EXECUTION_PENDING, EXECUTION_SUBMITTED)
OPEN_REVIEW: tuple[str, ...] = (REVIEW_PENDING, REVIEW_FAILED)

#: What a candidate is told about their own final answer. Never a result: the
#: hidden tests stay hidden, including how the program did on them.
STATE_WORD_SUBMITTED = "Submitted"
STATE_WORD_CHECKING = "Being checked"
STATE_WORD_CHECKED = "Checked"

#: One resubmission per invocation after a lost ticket. A second loss inside
#: the same invocation means the host is being replaced under us; the task's
#: retry loop and the sweep take it from there.
_RESUBMITS_PER_INVOCATION = 1


class SubmissionDefect(RuntimeError):
    """A defect on this side (a refused request, a key that fails its digest,
    code that no longer matches what was accepted). Not retryable."""


@dataclass(frozen=True)
class DraftSource:
    """The last code the server holds for a question: the latest Run."""

    run_id: uuid.UUID
    language: str
    source: str = field(repr=False)
    created_at: datetime


@dataclass(frozen=True)
class ScoringHold:
    """Whether scoring should wait for coding work on a conversation.

    `hold` is True while any submission is open AND younger than the maximum
    wait. Past the window the answer is graded "Not assessed" instead.
    """

    hold: bool
    open_submissions: int
    expired_submissions: int


@dataclass(frozen=True)
class ExecutionReport:
    """What one task invocation did, in words and counts safe to log."""

    submission_id: str
    outcome: str
    execution_status: str | None = None
    review_status: str | None = None
    scoring_dispatched: bool = False


# ── Accepting ───────────────────────────────────────────────────────────────


def _code_of(answer: AssessmentAnswer) -> tuple[str, str]:
    body = answer.answer_json if isinstance(answer.answer_json, dict) else {}
    language = body.get("language")
    code = body.get("code")
    if not isinstance(language, str) or not isinstance(code, (str, type(None))):
        raise ValueError("a coding answer carries a language and code")
    return language, code or ""


async def accept_final(
    session: AsyncSession,
    *,
    conversation: AssessmentConversation,
    question: CandidateQuestion,
    answer: AssessmentAnswer,
    auto_submitted: bool = False,
) -> CodingSubmission:
    """Store a final v2 coding answer and hand it to execution after commit.

    Idempotent on the answer: a second call returns the existing row and
    dispatches nothing. Raises ValueError for a caller defect (not a v2 coding
    question, an answer that belongs to another question, a language the
    question does not offer), because those are composition or routing bugs.
    Runs in the CALLER's transaction and never commits.
    """
    if question.question_type != types.CODING or not coding_payload.is_v2(question.payload_json):
        raise ValueError("accept_final is for executed (v2) coding questions only")
    if answer.question_id != question.id or answer.conversation_id != conversation.id:
        raise ValueError("the answer does not belong to this question and conversation")
    if answer.id is None:
        raise ValueError("the answer must be flushed before it is accepted")
    parsed = coding_payload.parse(question.payload_json)
    language, code = _code_of(answer)
    if language not in parsed.languages:
        raise ValueError(f"this coding question does not offer {language!r}")
    empty = not code.strip()
    statement = (
        pg_insert(CodingSubmission)
        .values(
            id=uuid.uuid4(),
            tenant_id=answer.tenant_id,
            answer_id=answer.id,
            conversation_id=conversation.id,
            question_id=question.id,
            auto_submitted=auto_submitted,
            source_sha256=submission_source_digest(language, code),
            execution_status=EXECUTION_NO_CODE if empty else EXECUTION_PENDING,
            review_status=REVIEW_NOT_APPLICABLE if empty else REVIEW_PENDING,
            execution_attempts=0,
            created_at=datetime.now(timezone.utc),
        )
        .on_conflict_do_nothing(index_elements=["answer_id"])
        .returning(CodingSubmission.id)
    )
    inserted = (await session.execute(statement)).scalar_one_or_none()
    row = (
        await session.execute(
            select(CodingSubmission)
            .where(CodingSubmission.answer_id == answer.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    if inserted is None:
        logger.info("coding_submission.already_accepted submission_id=%s", row.id)
        return row
    if empty:
        logger.info("coding_submission.no_code submission_id=%s auto=%s", row.id, auto_submitted)
        return row
    from app.workers.dispatch import dispatch_after_commit

    dispatch_after_commit(session, TASK_NAME, args=[str(row.id)])
    logger.info("coding_submission.accepted submission_id=%s auto=%s", row.id, auto_submitted)
    return row


async def latest_draft(
    session: AsyncSession, *, conversation_id: uuid.UUID, question_id: uuid.UUID
) -> DraftSource | None:
    """The code of the most recent Run on a question, or None.

    The only server-side copy of what a candidate had typed. An auto-submit on
    timeout with no client submission uses it (ASSUMPTION A1 of the phase
    plan); None means an empty answer, which is an evidence gap.
    """
    row = (
        await session.execute(
            select(CodingRun)
            .where(CodingRun.conversation_id == conversation_id, CodingRun.question_id == question_id)
            .order_by(CodingRun.created_at.desc(), CodingRun.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return DraftSource(run_id=row.id, language=row.language, source=row.source, created_at=row.created_at)


def _open_clause():
    return CodingSubmission.execution_status.in_(OPEN_EXECUTION) | CodingSubmission.review_status.in_(
        OPEN_REVIEW
    )


async def pending_for_conversation(session: AsyncSession, conversation_id: uuid.UUID) -> int:
    """How many coding submissions on a conversation still owe work."""
    return int(
        (
            await session.execute(
                select(func.count())
                .select_from(CodingSubmission)
                .where(CodingSubmission.conversation_id == conversation_id, _open_clause())
            )
        ).scalar_one()
    )


async def scoring_hold(
    session: AsyncSession, *, conversation_id: uuid.UUID, now: datetime | None = None
) -> ScoringHold:
    """Whether the scoring task must wait for coding work on this conversation.

    Scoring calls this before it spends anything. It holds while a submission
    is open and younger than `coding_execution_max_wait_hours`; once every
    open one is older, it proceeds and the evidence reads `unavailable`, which
    the grader turns into "Not assessed" with a person in the loop.
    """
    moment = now or datetime.now(timezone.utc)
    cutoff = moment - timedelta(hours=get_settings().coding_execution_max_wait_hours)
    rows = (
        await session.execute(
            select(CodingSubmission.created_at).where(
                CodingSubmission.conversation_id == conversation_id, _open_clause()
            )
        )
    ).scalars().all()
    expired = sum(1 for created in rows if created <= cutoff)
    return ScoringHold(hold=len(rows) > expired, open_submissions=len(rows), expired_submissions=expired)


def candidate_state_word(submission: CodingSubmission) -> str:
    """What the candidate reads about their own final answer. Never a result."""
    if submission.execution_status == EXECUTION_NO_CODE:
        return STATE_WORD_SUBMITTED
    if submission.execution_status == EXECUTION_COMPLETE and submission.review_status == REVIEW_COMPLETE:
        return STATE_WORD_CHECKED
    if submission.execution_status == EXECUTION_PENDING:
        return STATE_WORD_SUBMITTED
    return STATE_WORD_CHECKING


# ── Executing ───────────────────────────────────────────────────────────────


async def _claim(session: AsyncSession, submission_id: uuid.UUID) -> CodingSubmission | None | bool:
    """Take the submission's lock and read it fresh. False when another
    worker holds the lock; None when the row is gone (erased)."""
    if not await locks.try_advisory_lock(session, LOCK_NAMESPACE, submission_id):
        await session.rollback()
        return False
    return (
        await session.execute(
            select(CodingSubmission)
            .where(CodingSubmission.id == submission_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def _record_attempt(session: AsyncSession, submission_id: uuid.UUID, error_class: str) -> None:
    """Count a failed attempt and name it. Class name only, never a message."""
    await session.rollback()
    await session.execute(
        update(CodingSubmission)
        .where(CodingSubmission.id == submission_id)
        .values(
            execution_attempts=CodingSubmission.execution_attempts + 1,
            last_error_class=error_class[:80],
        )
    )
    await session.commit()


def _ticket_json(ticket: ExecutionTicket) -> dict[str, Any]:
    return {"provider": ticket.provider, "refs": list(ticket.refs), "keys": list(ticket.keys)}


def _ticket_of(row: CodingSubmission) -> ExecutionTicket:
    stored = row.provider_ref_json or {}
    return ExecutionTicket(
        provider=str(stored.get("provider") or ""),
        refs=tuple(str(ref) for ref in stored.get("refs") or ()),
        keys=tuple(str(key) for key in stored.get("keys") or ()),
    )


@dataclass(frozen=True)
class _Material:
    """What executing one submission needs, read once per stage."""

    question: CandidateQuestion
    payload: coding_payload.CodingPayloadV2
    language: str
    code: str = field(repr=False)
    answer_key: keys.AnswerKey = field(repr=False)


async def _material(session: AsyncSession, row: CodingSubmission) -> _Material:
    """The question, the code and the answer key, each verified. Any mismatch
    is a `SubmissionDefect`: grading against a changed key or changed code
    would report a result for something nobody submitted."""
    question = await session.get(CandidateQuestion, row.question_id)
    answer = await session.get(AssessmentAnswer, row.answer_id)
    if question is None or answer is None:
        raise SubmissionDefect("the submission's question or answer is gone")
    try:
        parsed = coding_payload.parse(question.payload_json)
        language, code = _code_of(answer)
        answer_key = await keys.load_answer_key(session, question.id)
    except (ValueError, keys.KeyNotFound, keys.KeyIntegrityError) as exc:
        raise SubmissionDefect(type(exc).__name__) from exc
    if submission_source_digest(language, code) != row.source_sha256:
        raise SubmissionDefect("the stored code does not match the digest it was accepted under")
    if language not in parsed.languages:
        raise SubmissionDefect("the answer's language is not offered by its question")
    return _Material(question=question, payload=parsed, language=language, code=code, answer_key=answer_key)


def _inputs(material: _Material) -> tuple[code_execution.TestInput, ...]:
    return coding_payload.visible_inputs(material.payload) + material.answer_key.test_inputs()


async def _submit(
    session: AsyncSession, row: CodingSubmission, provider: CodeExecutionProvider
) -> None:
    """Hand the program to the sandbox and COMMIT the ticket before anything polls."""
    material = await _material(session, row)
    ticket = await provider.submit(
        language=material.language,
        source=material.code,
        tests=_inputs(material),
        limits=coding_payload.limits_for(material.payload, material.language),
    )
    row.provider_ref_json = _ticket_json(ticket)
    row.execution_status = EXECUTION_SUBMITTED
    await session.commit()
    logger.info(
        "coding_submission.submitted submission_id=%s tests=%d", row.id, len(ticket.keys)
    )


async def _collect(
    provider: CodeExecutionProvider,
    ticket: ExecutionTicket,
    *,
    deadline_seconds: float,
    poll_seconds: float,
    sleep: Callable[[float], Awaitable[Any]],
    clock: Callable[[], float],
) -> list[code_execution.TestExecution]:
    """Poll to a PREDICTIVE deadline: another poll starts only if it can finish."""
    started = clock()
    while True:
        results = await provider.collect(ticket)
        if results is not None:
            return results
        if clock() - started + poll_seconds >= deadline_seconds:
            raise ExecutionUnavailable("the sandbox did not finish inside the poll deadline", reason="deadline")
        await sleep(poll_seconds)


async def _record_evidence(session: AsyncSession, row: CodingSubmission, question: CandidateQuestion) -> None:
    """File the answer in the evidence ledger through its one writer.

    Idempotent there (the ledger returns an existing row for the same
    message), and a ledger failure never fails the caller: that is the
    writer's own rule 1.
    """
    from app.services.assessment_pipeline.evidence import record_answer_evidence

    answer = await session.get(AssessmentAnswer, row.answer_id)
    if answer is None or answer.message_id is None:
        logger.warning("coding_submission.evidence_unlocated submission_id=%s", row.id)
        return
    message = await session.get(AssessmentMessage, answer.message_id)
    conversation = await session.get(AssessmentConversation, row.conversation_id)
    skill = await session.get(JobCompetency, question.competency_id)
    # One column, not the ORM row: the ledger needs the candidate and nothing
    # else about the application.
    candidate_id = (
        (
            await session.execute(
                select(JobCandidateLink.candidate_id).where(
                    JobCandidateLink.id == conversation.job_candidate_link_id
                )
            )
        ).scalar_one_or_none()
        if conversation is not None
        else None
    )
    if message is None or conversation is None or skill is None or candidate_id is None:
        logger.warning("coding_submission.evidence_unlocated submission_id=%s", row.id)
        return
    await record_answer_evidence(
        session,
        tenant_id=row.tenant_id,
        job_id=conversation.job_id,
        link_id=conversation.job_candidate_link_id,
        candidate_id=candidate_id,
        skill_id=skill.id,
        skill_name=skill.name,
        skill_bucket=skill.category,
        question_id=question.id,
        message_id=message.id,
        turn=int(message.ordinal or 0),
        text=message.content,
        answered_at=message.created_at,
    )


async def _complete_execution(
    session: AsyncSession,
    submission_id: uuid.UUID,
    ticket: ExecutionTicket,
    results: list[code_execution.TestExecution],
) -> bool:
    """Write the judged results if the row is still waiting on THIS ticket.
    Returns False when another worker already finished it."""
    row = await _claim(session, submission_id)
    if not row or row.execution_status != EXECUTION_SUBMITTED or _ticket_of(row) != ticket:
        await session.rollback()
        return False
    material = await _material(session, row)
    visible_keys = [test.id for test in material.payload.visible_tests]
    visible = [result for result in results if result.key in set(visible_keys)]
    hidden = [result for result in results if result.key not in set(visible_keys)]
    summary = execution.summarise_hidden(hidden, material.answer_key)
    visible_rows = execution.visible_results(
        visible, {test.id: test.expected_stdout for test in material.payload.visible_tests}
    )
    row.tests_total = summary.tests_total
    row.tests_passed = summary.tests_passed
    row.test_results_json = list(summary.results)
    row.compile_output = summary.compile_output
    row.visible_error_output = execution.first_visible_error(visible_rows)
    row.executed_at = datetime.now(timezone.utc)
    row.execution_status = EXECUTION_COMPLETE
    row.last_error_class = None
    await _record_evidence(session, row, material.question)
    await session.commit()
    logger.info(
        "coding_submission.executed submission_id=%s tests=%d outcomes=%s",
        submission_id, summary.tests_total, sorted(summary.outcome_counts),
    )
    return True


async def _reset_for_resubmit(
    session: AsyncSession, submission_id: uuid.UUID, ticket: ExecutionTicket, error_class: str
) -> None:
    row = await _claim(session, submission_id)
    if not row or row.execution_status != EXECUTION_SUBMITTED or _ticket_of(row) != ticket:
        await session.rollback()
        return
    row.provider_ref_json = None
    row.execution_status = EXECUTION_PENDING
    row.execution_attempts = row.execution_attempts + 1
    row.last_error_class = error_class
    await session.commit()


async def _execution_stage(
    session: AsyncSession,
    submission_id: uuid.UUID,
    provider: CodeExecutionProvider,
    *,
    sleep: Callable[[float], Awaitable[Any]],
    clock: Callable[[], float],
) -> str | None:
    """Drive execution to `complete`. Returns the execution status it left,
    or None when another worker holds the lock or the row is gone."""
    settings = get_settings()
    resubmits = 0
    while True:
        row = await _claim(session, submission_id)
        if row is False:
            return None
        if row is None:
            await session.rollback()
            logger.info("coding_submission.gone submission_id=%s", submission_id)
            return None
        if row.execution_status in (EXECUTION_COMPLETE, EXECUTION_NO_CODE):
            status = row.execution_status
            await session.commit()
            return status
        if row.execution_status == EXECUTION_PENDING:
            try:
                await _submit(session, row, provider)
            except (ExecutionUnavailable, ExecutionNotConfigured) as exc:
                await _record_attempt(session, submission_id, type(exc).__name__)
                logger.warning(
                    "coding_submission.submit_unavailable submission_id=%s reason=%s",
                    submission_id, exc.reason,
                )
                if isinstance(exc, ExecutionNotConfigured):
                    raise SubmissionDefect(f"code execution is not configured: {exc.reason}") from exc
                raise
            except (ExecutionRejected, SubmissionDefect) as exc:
                await _record_attempt(session, submission_id, type(exc).__name__)
                logger.error(
                    "coding_submission.defect submission_id=%s error=%s", submission_id, type(exc).__name__
                )
                if isinstance(exc, SubmissionDefect):
                    raise
                raise SubmissionDefect("the sandbox refused the submission request") from exc
            continue
        # SUBMITTED: the ticket is durable. End the transaction before polling.
        ticket = _ticket_of(row)
        await session.commit()
        try:
            results = await _collect(
                provider,
                ticket,
                deadline_seconds=settings.coding_submission_poll_deadline_seconds,
                poll_seconds=settings.code_execution_poll_seconds,
                sleep=sleep,
                clock=clock,
            )
            done = await _complete_execution(session, submission_id, ticket, results)
        except ExecutionTicketLost:
            await _reset_for_resubmit(session, submission_id, ticket, "ExecutionTicketLost")
            logger.warning("coding_submission.ticket_lost submission_id=%s resubmitting", submission_id)
            if resubmits >= _RESUBMITS_PER_INVOCATION:
                raise
            resubmits += 1
            continue
        except execution.SandboxFault as fault:
            await session.rollback()
            await provider.discard(ticket)
            await _reset_for_resubmit(session, submission_id, ticket, "SandboxFault")
            logger.warning(
                "coding_submission.sandbox_fault submission_id=%s reason=%s", submission_id, fault.reason
            )
            raise ExecutionUnavailable("the sandbox faulted on this run", reason=fault.reason) from fault
        except ExecutionUnavailable as exc:
            await _record_attempt(session, submission_id, type(exc).__name__)
            logger.warning(
                "coding_submission.collect_unavailable submission_id=%s reason=%s",
                submission_id, exc.reason,
            )
            raise
        except SubmissionDefect as exc:
            await _record_attempt(session, submission_id, type(exc).__name__)
            logger.error(
                "coding_submission.defect submission_id=%s error=%s", submission_id, type(exc).__name__
            )
            raise
        await provider.discard(ticket)
        if not done:
            logger.info("coding_submission.finished_elsewhere submission_id=%s", submission_id)
        continue


async def _review_stage(session: AsyncSession, submission_id: uuid.UUID) -> str | None:
    """Write the code-quality review if execution is complete and it is owed."""
    row = (
        await session.execute(
            select(CodingSubmission)
            .where(CodingSubmission.id == submission_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if row is None:
        await session.rollback()
        return None
    if row.execution_status != EXECUTION_COMPLETE or row.review_status not in OPEN_REVIEW:
        status = row.review_status
        await session.commit()
        return status
    material = await _material(session, row)
    notes = await keys.review_notes(session, material.question.id)
    outcome_sentence = phrasing.outcome_sentence(
        state="complete",
        tests_total=row.tests_total,
        tests_passed=row.tests_passed,
        outcome_counts=execution.outcome_counts(row.test_results_json or ()),
    )
    review_input = review.ReviewInput(
        statement=material.question.prompt,
        input_format=material.payload.input_format,
        output_format=material.payload.output_format,
        constraints=material.payload.constraints,
        language=material.language,
        approach_notes=notes,
        outcome_sentence=outcome_sentence,
        code=material.code,
    )
    # No transaction is held across the model call.
    await session.commit()
    outcome = await review.review_code_quality(session, review_input)
    claimed = await _claim(session, submission_id)
    if not claimed or claimed.review_status not in OPEN_REVIEW:
        status = claimed.review_status if claimed else None
        await session.commit()
        return status
    if outcome.record is None:
        claimed.review_status = REVIEW_FAILED
        logger.warning(
            "coding_submission.review_failed submission_id=%s reasons=%d",
            submission_id, len(outcome.reasons),
        )
    else:
        claimed.review_status = REVIEW_COMPLETE
        claimed.review_json = outcome.record
        claimed.review_model_id = outcome.model_id
        claimed.review_prompt_version = outcome.prompt_version
        claimed.reviewed_at = datetime.now(timezone.utc)
    status = claimed.review_status
    await session.commit()
    return status


async def _dispatch_scoring_if_ready(session: AsyncSession, conversation_id: uuid.UUID) -> bool:
    """Hand the application to scoring when this was the last coding work.

    Only for a COMPLETED conversation with nothing open and no report yet;
    scoring's own advisory lock makes a concurrent dispatch a no-op.
    """
    conversation = await session.get(AssessmentConversation, conversation_id)
    if conversation is None or conversation.status != CONVERSATION_COMPLETED:
        await session.commit()
        return False
    if await pending_for_conversation(session, conversation_id):
        await session.commit()
        return False
    reported = (
        await session.execute(
            select(func.count())
            .select_from(FunctionalSkillsReport)
            .where(FunctionalSkillsReport.job_candidate_link_id == conversation.job_candidate_link_id)
        )
    ).scalar_one()
    if reported:
        await session.commit()
        return False
    from app.workers.dispatch import dispatch_after_commit

    dispatch_after_commit(session, SCORING_TASK_NAME, args=[str(conversation.job_candidate_link_id)])
    await session.commit()
    logger.info("coding_submission.scoring_dispatched conversation_id=%s", conversation_id)
    return True


async def execute_submission(
    session: AsyncSession,
    submission_id: uuid.UUID | str,
    *,
    provider: CodeExecutionProvider | None = None,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> ExecutionReport:
    """The body of `pickready.execute_coding_submission`. See the module docstring.

    Raises `ExecutionUnavailable` (retryable) and `SubmissionDefect` (not);
    returns an `ExecutionReport` otherwise, including when another worker
    holds the lock.
    """
    sid = uuid.UUID(str(submission_id))
    if provider is None:
        try:
            provider = code_execution.get_provider()
        except ExecutionNotConfigured as exc:
            await _record_attempt(session, sid, type(exc).__name__)
            raise SubmissionDefect(f"code execution is not configured: {exc.reason}") from exc
    execution_status = await _execution_stage(session, sid, provider, sleep=sleep, clock=clock)
    if execution_status is None:
        return ExecutionReport(submission_id=str(sid), outcome="busy_or_gone")
    review_status = await _review_stage(session, sid)
    row = await session.get(CodingSubmission, sid)
    if row is None:
        return ExecutionReport(submission_id=str(sid), outcome="gone")
    dispatched = await _dispatch_scoring_if_ready(session, row.conversation_id)
    return ExecutionReport(
        submission_id=str(sid),
        outcome="done",
        execution_status=execution_status,
        review_status=review_status,
        scoring_dispatched=dispatched,
    )
