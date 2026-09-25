"""The Run button: the candidate's current code against the VISIBLE sample tests.

INTERACTIVE, SO NOTHING HERE WAITS ON EXECUTION
-----------------------------------------------
Rule 4 says slow work is dispatched. A Run is dispatched too, to the sandbox's
own queue: `start_run` performs ONE bounded enqueue (`provider.submit`, whose
connect and read timeouts are settings) and returns a `queued` row, and each
poll performs ONE bounded `collect` (`refresh_run`). Neither ever loops on the
sandbox. Routing a Run through the task Lambda would put a cold start in front
of an interactive button inside a twenty-minute timer, and buy nothing: the
sandbox queue is already the dispatch.

THE FENCES, IN THE ORDER THEY ARE CHECKED
-----------------------------------------
1. The question is an executed (v2) coding question and offers the language.
2. The source is non-empty and within `CODE_SOURCE_MAX_CHARS`.
3. The same `client_token` returns the SAME run: a double click or a retried
   request is one run.
4. One run in flight per question: a second press while one is queued is
   refused rather than stacked.
5. A HARD CAP per question, counted in `coding_runs`
   (`coding_run_max_per_question`). The Redis rate window in front of this
   (the API's, 4C) fails open by design, so this count is the fence that
   holds when Redis is down. Runs the sandbox never took (`unavailable`) do
   not count: an outage must not spend a candidate's runs.
Checks 3 to 5 run under a transaction-scoped advisory lock on the
(conversation, question) pair, so two concurrent presses cannot both pass the
cap or both see nothing in flight.

WHAT A RUN KEEPS. Visible tests are public, so the program's stdout, stderr
and compiler output are kept (truncated) and shown back. No timing and no
number is kept for the candidate: they read words ("Passed", "Wrong answer").
A Run is never graded; it is also the only server-side copy of what the
candidate had typed, which is what an auto-submit falls back to.

An outage is a STATE the candidate is told about in one server sentence
(`UNAVAILABLE_SENTENCE`), never a silent spinner and never a fake result.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.assessment import AssessmentConversation, CandidateQuestion
from app.models.coding import (
    CODE_SOURCE_MAX_CHARS,
    RUN_COMPLETE,
    RUN_FAILED,
    RUN_QUEUED,
    RUN_UNAVAILABLE,
    CodingRun,
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
from app.services.coding_assessment import execution, phrasing
from app.services.coding_assessment import payload as coding_payload

logger = logging.getLogger(__name__)

__all__ = [
    "LOCK_NAMESPACE",
    "CLIENT_TOKEN_MAX_CHARS",
    "REFUSAL_NOT_EXECUTABLE",
    "REFUSAL_LANGUAGE",
    "REFUSAL_SOURCE",
    "REFUSAL_TOKEN",
    "REFUSAL_IN_PROGRESS",
    "REFUSAL_LIMIT",
    "REFUSAL_SENTENCES",
    "UNAVAILABLE_SENTENCE",
    "RunRefused",
    "start_run",
    "refresh_run",
    "candidate_results",
]

LOCK_NAMESPACE = "coding.run"
CLIENT_TOKEN_MAX_CHARS = 64

REFUSAL_NOT_EXECUTABLE = "not_executable"
REFUSAL_LANGUAGE = "language_not_offered"
REFUSAL_SOURCE = "source_invalid"
REFUSAL_TOKEN = "client_token_invalid"
REFUSAL_IN_PROGRESS = "run_in_progress"
REFUSAL_LIMIT = "run_limit_reached"

#: The server's sentence for each refusal. The API renders it verbatim, so
#: the screen can never promise something this module then refuses.
REFUSAL_SENTENCES: dict[str, str] = {
    REFUSAL_NOT_EXECUTABLE: "This question cannot be run.",
    REFUSAL_LANGUAGE: "That language is not offered for this question.",
    REFUSAL_SOURCE: "There is no code to run, or the code is too long.",
    REFUSAL_TOKEN: "This run request could not be identified. Please press Run again.",
    REFUSAL_IN_PROGRESS: "A run is already in progress.",
    REFUSAL_LIMIT: (
        "You have used every run for this question. Your code is kept; you can "
        "keep working and submit when ready."
    ),
}

UNAVAILABLE_SENTENCE = (
    "The code runner is not available right now. Your code is kept; you can "
    "keep working and submit when ready."
)

#: Runs that count against the per-question cap: the sandbox took them.
_COUNTED_STATUSES = (RUN_QUEUED, RUN_COMPLETE, RUN_FAILED)


class RunRefused(Exception):
    """A Run the fences refused. `code` is one of the REFUSAL_* words and
    `sentence` is what the candidate reads."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
        self.sentence = REFUSAL_SENTENCES[code]


def _ticket_json(ticket: ExecutionTicket) -> dict[str, Any]:
    return {"provider": ticket.provider, "refs": list(ticket.refs), "keys": list(ticket.keys)}


def _ticket_of(run: CodingRun) -> ExecutionTicket:
    stored = run.provider_ref_json or {}
    return ExecutionTicket(
        provider=str(stored.get("provider") or ""),
        refs=tuple(str(ref) for ref in stored.get("refs") or ()),
        keys=tuple(str(key) for key in stored.get("keys") or ()),
    )


def _executable(question: CandidateQuestion) -> coding_payload.CodingPayloadV2:
    if question.question_type != types.CODING or not coding_payload.is_v2(question.payload_json):
        raise RunRefused(REFUSAL_NOT_EXECUTABLE)
    return coding_payload.parse(question.payload_json)


def _close(run: CodingRun, status: str, error_class: str | None) -> None:
    run.status = status
    run.error_class = error_class
    run.completed_at = datetime.now(timezone.utc)


async def start_run(
    session: AsyncSession,
    *,
    conversation: AssessmentConversation,
    question: CandidateQuestion,
    language: str,
    source: str,
    client_token: str,
    provider: CodeExecutionProvider | None = None,
) -> CodingRun:
    """Record a Run and hand it to the sandbox. Returns the row, never waits.

    The CALLER has already established that this is the candidate's own
    conversation, that proctoring is active and that the question is the open
    one; those are the API's checks (4C). Runs in the caller's transaction and
    flushes; the caller commits. Raises `RunRefused` for a fenced request. A
    sandbox that cannot take the run leaves a row `unavailable` (or `failed`,
    for a request it refused as malformed), which the caller turns into the
    unavailable sentence.
    """
    payload = _executable(question)
    if language not in payload.languages:
        raise RunRefused(REFUSAL_LANGUAGE)
    if not source.strip() or len(source) > CODE_SOURCE_MAX_CHARS:
        raise RunRefused(REFUSAL_SOURCE)
    if not client_token or len(client_token) > CLIENT_TOKEN_MAX_CHARS:
        raise RunRefused(REFUSAL_TOKEN)

    await locks.advisory_xact_lock(session, LOCK_NAMESPACE, f"{conversation.id}:{question.id}")
    existing = (
        await session.execute(
            select(CodingRun).where(
                CodingRun.conversation_id == conversation.id, CodingRun.client_token == client_token
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        if existing.question_id != question.id:
            raise RunRefused(REFUSAL_TOKEN)
        return existing
    in_flight = (
        await session.execute(
            select(func.count())
            .select_from(CodingRun)
            .where(
                CodingRun.conversation_id == conversation.id,
                CodingRun.question_id == question.id,
                CodingRun.status == RUN_QUEUED,
            )
        )
    ).scalar_one()
    if in_flight:
        raise RunRefused(REFUSAL_IN_PROGRESS)
    used = (
        await session.execute(
            select(func.count())
            .select_from(CodingRun)
            .where(
                CodingRun.conversation_id == conversation.id,
                CodingRun.question_id == question.id,
                CodingRun.status.in_(_COUNTED_STATUSES),
            )
        )
    ).scalar_one()
    if used >= get_settings().coding_run_max_per_question:
        raise RunRefused(REFUSAL_LIMIT)

    run = CodingRun(
        id=uuid.uuid4(),
        tenant_id=conversation.tenant_id,
        conversation_id=conversation.id,
        question_id=question.id,
        client_token=client_token,
        language=language,
        source=source,
        status=RUN_QUEUED,
    )
    try:
        sandbox = provider or code_execution.get_provider()
        ticket = await sandbox.submit(
            language=language,
            source=source,
            tests=coding_payload.visible_inputs(payload),
            limits=coding_payload.limits_for(payload, language),
        )
        run.provider_ref_json = _ticket_json(ticket)
    except (ExecutionUnavailable, ExecutionNotConfigured) as exc:
        _close(run, RUN_UNAVAILABLE, type(exc).__name__)
        logger.warning("coding_run.unavailable question_id=%s reason=%s", question.id, exc.reason)
    except ExecutionRejected as exc:
        _close(run, RUN_FAILED, type(exc).__name__)
        logger.error("coding_run.rejected question_id=%s reason=%s", question.id, exc.reason)
    session.add(run)
    await session.flush()
    return run


async def refresh_run(
    session: AsyncSession,
    *,
    run: CodingRun,
    question: CandidateQuestion,
    provider: CodeExecutionProvider | None = None,
) -> CodingRun:
    """One bounded collect for a queued Run. Never loops on the sandbox.

    The row is locked `FOR UPDATE SKIP LOCKED`: when another poll is already
    collecting it, this one returns the row as it stands. A sandbox that stays
    unreachable past `coding_run_deadline_seconds`, or has lost the run,
    closes it `unavailable`; the candidate may press Run again.
    """
    if run.status != RUN_QUEUED:
        return run
    locked = (
        await session.execute(
            select(CodingRun)
            .where(CodingRun.id == run.id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if locked is None or locked.status != RUN_QUEUED:
        return locked or run
    payload = coding_payload.parse(question.payload_json)
    ticket = _ticket_of(locked)
    created = locked.created_at if locked.created_at.tzinfo else locked.created_at.replace(tzinfo=timezone.utc)
    overdue = datetime.now(timezone.utc) - created >= timedelta(
        seconds=get_settings().coding_run_deadline_seconds
    )
    try:
        sandbox = provider or code_execution.get_provider()
        results = await sandbox.collect(ticket)
    except ExecutionTicketLost as exc:
        _close(locked, RUN_UNAVAILABLE, type(exc).__name__)
        await session.flush()
        return locked
    except (ExecutionUnavailable, ExecutionNotConfigured) as exc:
        if overdue:
            _close(locked, RUN_UNAVAILABLE, type(exc).__name__)
            await session.flush()
        logger.warning("coding_run.collect_unavailable run_id=%s reason=%s", locked.id, exc.reason)
        return locked
    if results is None:
        if overdue:
            _close(locked, RUN_UNAVAILABLE, "deadline")
            await sandbox.discard(ticket)
            await session.flush()
        return locked
    try:
        rows = execution.visible_results(
            results, {test.id: test.expected_stdout for test in payload.visible_tests}
        )
    except execution.SandboxFault as fault:
        _close(locked, RUN_UNAVAILABLE, "SandboxFault")
        logger.warning("coding_run.sandbox_fault run_id=%s reason=%s", locked.id, fault.reason)
    else:
        locked.results_json = rows
        _close(locked, RUN_COMPLETE, None)
    await sandbox.discard(ticket)
    await session.flush()
    return locked


@dataclass(frozen=True)
class _Named:
    key: str
    name: str
    stdin: str
    expected_stdout: str


def candidate_results(run: CodingRun, question: CandidateQuestion) -> list[dict[str, str]]:
    """What the candidate sees of a completed Run: per sample test, its name,
    the result WORD, their program's output and the expected output. Words
    and text only; no timing, no count."""
    if run.status != RUN_COMPLETE or not run.results_json:
        return []
    payload = coding_payload.parse(question.payload_json)
    named = {
        test.id: _Named(key=test.id, name=f"Example {phrasing.number_word(i).capitalize()}",
                        stdin=test.stdin, expected_stdout=test.expected_stdout)
        for i, test in enumerate(payload.visible_tests, start=1)
    }
    shown: list[dict[str, str]] = []
    for row in run.results_json:
        test = named.get(str(row.get("key")))
        if test is None:
            continue
        shown.append(
            {
                "name": test.name,
                "result": phrasing.result_word(str(row.get("outcome"))),
                "stdin": test.stdin,
                "stdout": str(row.get("stdout") or ""),
                "expected_stdout": test.expected_stdout,
                "stderr": str(row.get("stderr") or ""),
                "compile_output": str(row.get("compile_output") or ""),
            }
        )
    return shown
