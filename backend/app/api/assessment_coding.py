"""The candidate's coding question: Run the visible samples, read a final answer's state.

Mounted under `/api/v2/assessments` beside the conversation it belongs to,
on the CANDIDATE audience (`get_current_candidate`, `get_candidate_db`). A
candidate has no tenant, so RLS by tenant cannot protect these routes: every
handler resolves the conversation through `_candidate_link`, which checks
that the application is THIS candidate's, and every row it reads afterwards
is filtered by that conversation's id.

RULE 4, AND WHY A RUN IS NOT A DISPATCHED TASK
----------------------------------------------
Slow work is dispatched, never run in a handler. A Run is dispatched too, to
the sandbox's own queue: the POST performs ONE bounded enqueue
(`runs.start_run`, whose connect and read timeouts are settings) and answers
202, and each poll performs ONE bounded fetch (`runs.refresh_run`). Neither
handler ever waits on execution or loops on the sandbox. Routing a Run
through the task Lambda instead would put a cold start in front of an
interactive button inside a twenty-minute question, and buy nothing: the
sandbox queue is already the dispatch. The final answer is different and IS
dispatched: it is stored by the `respond` turn and executed by
`pickready.execute_coding_submission` after the commit
(`coding_assessment.final_answer`).

THE FENCES ON A RUN, IN THE ORDER THEY ARE CHECKED
--------------------------------------------------
1. The Redis rate window (`coding_run_rate_per_minute`). Abuse control only:
   it fails open when Redis is down, by design.
2. Ownership: the conversation is this candidate's (404 otherwise, never 403,
   so a guessed id says nothing about whether it exists).
3. Proctoring is active (`proctoring_gate.require_active`, 409), exactly as
   `respond` requires. Running code is part of answering.
4. The question is the one OPEN now: the conversation is active and
   conversational, and this question is the current base question with no
   follow-up pending (409). A candidate cannot run code against a question
   they have already answered or not yet reached.
5. The service's own fences (`runs.start_run`): an executed question, an
   offered language, a non-empty source, one run per client token, one run
   in flight, and the HARD per-question cap COUNTED IN THE TABLE, which is
   what holds when the Redis window above has failed open.

WHAT NEVER CROSSES THIS BOUNDARY
--------------------------------
No hidden test in any form, no reference solution and no approach notes:
this module imports neither the answer-key module nor anything that returns
one, and the response schemas have no field that could hold one
(`tests/test_coding_key_confinement.py`, `test_hidden_tests_never_leave_server.py`).
No number: a sample's result is a word and the program's own output, and a
final answer's state is one of three words, never how it did.

Every refusal is a sentence the server wrote, rendered verbatim by the
screen, so the screen can never promise something this module then refuses.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.assessment_conversation import _candidate_link, _conversation_prompts
from app.api.deps import CurrentUser, get_candidate_db, get_current_candidate
from app.core.config import get_settings
from app.models.assessment import AssessmentConversation, CandidateQuestion
from app.models.candidate import JobCandidateLink
from app.models.coding import RUN_FAILED, RUN_QUEUED, RUN_UNAVAILABLE, CodingRun, CodingSubmission
from app.models.dual_mode import MODE_CONVERSATIONAL
from app.models.job import Job
from app.schemas.coding import (
    CodingRunIn,
    CodingRunOut,
    CodingRunStartedOut,
    CodingRunTestOut,
    CodingSubmissionStateOut,
)
from app.services.assessment_conversation import turns
from app.services.coding_assessment import runs, submissions
from app.services.proctoring import gate as proctoring_gate
from app.services.rate_limit import rate_limit

router = APIRouter()

__all__ = [
    "router",
    "NOT_FOUND_DETAIL",
    "QUESTION_NOT_OPEN_DETAIL",
    "NO_FINAL_ANSWER_DETAIL",
    "RUN_FAILED_SENTENCE",
    "REFUSAL_STATUS",
]

#: The conversation, the question or the run is not this candidate's, or does
#: not exist. One sentence for all of them, so a guessed id learns nothing.
NOT_FOUND_DETAIL = "This coding question was not found."

QUESTION_NOT_OPEN_DETAIL = (
    "This question is not open. You can run code only on the question in front of you."
)

NO_FINAL_ANSWER_DETAIL = "No final answer has been submitted for this question yet."

#: The sandbox refused the request as malformed: a defect on this side, not
#: the candidate's code, and not an outage. Said as plainly as the outage is.
RUN_FAILED_SENTENCE = (
    "This run could not be checked because of a problem on our side. Your code "
    "is kept; you can run it again, or keep working and submit when ready."
)

#: How each `runs.RunRefused` code answers. 409 is "not now" (the question
#: cannot run, a run is already going), 429 is "no more" (the per-question
#: cap, the same status the rate window answers), 422 is a request the client
#: built wrongly. The body is always the service's own sentence.
REFUSAL_STATUS: dict[str, int] = {
    runs.REFUSAL_NOT_EXECUTABLE: status.HTTP_409_CONFLICT,
    runs.REFUSAL_IN_PROGRESS: status.HTTP_409_CONFLICT,
    runs.REFUSAL_LIMIT: status.HTTP_429_TOO_MANY_REQUESTS,
    runs.REFUSAL_LANGUAGE: status.HTTP_422_UNPROCESSABLE_ENTITY,
    runs.REFUSAL_SOURCE: status.HTTP_422_UNPROCESSABLE_ENTITY,
    runs.REFUSAL_TOKEN: status.HTTP_422_UNPROCESSABLE_ENTITY,
}

#: Conversation states in which a question can be open.
_ACTIVE = "active"

#: The message a run in each closed state carries. A queued or complete run
#: carries none: its state is the answer.
_RUN_MESSAGES: dict[str, str] = {
    RUN_UNAVAILABLE: runs.UNAVAILABLE_SENTENCE,
    RUN_FAILED: RUN_FAILED_SENTENCE,
}

_settings = get_settings()


@dataclass(frozen=True)
class _Owned:
    conversation: AssessmentConversation
    link: JobCandidateLink
    job: Job


async def _owned_conversation(
    session: AsyncSession, user: CurrentUser, conversation_id: uuid.UUID
) -> _Owned:
    """The conversation, when it belongs to the signed-in candidate; 404
    otherwise. `_candidate_link` is the one ownership check every candidate
    assessment route uses (it also answers 409 while the job's assessment
    is not open)."""
    conversation = await session.get(AssessmentConversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_DETAIL)
    link, job = await _candidate_link(session, user, conversation.job_candidate_link_id)
    return _Owned(conversation=conversation, link=link, job=job)


async def _conversation_question(
    session: AsyncSession, conversation: AssessmentConversation, question_id: uuid.UUID
) -> CandidateQuestion:
    """A question issued on THIS conversation's application, or 404."""
    question = await session.get(CandidateQuestion, question_id)
    if question is None or question.job_candidate_link_id != conversation.job_candidate_link_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_DETAIL)
    return question


async def _require_open(session: AsyncSession, owned: _Owned, question: CandidateQuestion) -> None:
    """409 unless `question` is the one the candidate is answering now.

    The current base question is `next_question_index` in the conversation's
    own sequence (`_conversation_prompts`, the one ordering `respond` walks),
    and no follow-up may be pending, because a pending prompt means the turn
    in front of the candidate is that follow-up, not this question.
    """
    conversation = owned.conversation
    if (
        conversation.status != _ACTIVE
        or conversation.mode != MODE_CONVERSATIONAL
        or conversation.pending_prompt
    ):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=QUESTION_NOT_OPEN_DETAIL)
    prompts = await _conversation_prompts(session, owned.job, owned.link)
    index = conversation.next_question_index
    if index >= len(prompts) or prompts[index][3].id != question.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=QUESTION_NOT_OPEN_DETAIL)
    # The SERVER's turn clock (p4-4c hunk 2, applied at the stage 2
    # integration): a Run is refused while the turn is paused and once its
    # time, plus the grace, has run out. The same clock `respond` reads, so a
    # candidate cannot keep running samples on a question the server has
    # already closed.
    clock = await turns.turn_clock(session, conversation, now=datetime.now(timezone.utc))
    if clock is None or clock.expired:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=QUESTION_NOT_OPEN_DETAIL)
    if clock.paused:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=proctoring_gate.PAUSED_DETAIL)


def _run_out(run: CodingRun, question: CandidateQuestion) -> CodingRunOut:
    return CodingRunOut(
        run_id=run.id,
        status=run.status,
        tests=[CodingRunTestOut(**row) for row in runs.candidate_results(run, question)],
        message=_RUN_MESSAGES.get(run.status),
    )


@router.post(
    "/conversations/{conversation_id}/coding/{question_id}/runs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=CodingRunStartedOut,
    dependencies=[
        Depends(rate_limit("coding_run", limit=_settings.coding_run_rate_per_minute, window=60))
    ],
)
async def start_coding_run(
    conversation_id: uuid.UUID,
    question_id: uuid.UUID,
    body: CodingRunIn,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
):
    """Run the candidate's code against the question's VISIBLE samples.

    Answers 202 with the run to poll. A sandbox that cannot take the run is
    an OUTAGE, answered 503 with the server's sentence; the `unavailable` row
    is COMMITTED rather than rolled back (the response is returned, not
    raised), so the outage is on record and, because the sandbox never took
    it, does not count against the candidate's runs. A replay of the same
    client token answers the run that token already started.
    """
    owned = await _owned_conversation(session, user, conversation_id)
    conversation = owned.conversation
    await proctoring_gate.require_active(session, conversation)
    question = await _conversation_question(session, conversation, question_id)
    await _require_open(session, owned, question)
    try:
        run = await runs.start_run(
            session,
            conversation=conversation,
            question=question,
            language=body.language,
            source=body.source,
            client_token=body.client_token,
        )
    except runs.RunRefused as refused:
        raise HTTPException(status_code=REFUSAL_STATUS[refused.code], detail=refused.sentence) from refused
    if run.status == RUN_UNAVAILABLE:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": runs.UNAVAILABLE_SENTENCE},
        )
    return CodingRunStartedOut(run_id=run.id, status=run.status)


@router.get(
    "/conversations/{conversation_id}/coding/{question_id}/runs/{run_id}",
    response_model=CodingRunOut,
    dependencies=[
        Depends(
            rate_limit("coding_run_poll", limit=_settings.coding_run_poll_rate_per_minute, window=60)
        )
    ],
)
async def get_coding_run(
    conversation_id: uuid.UUID,
    question_id: uuid.UUID,
    run_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> CodingRunOut:
    """Where a Run stands, and its sample results once it has finished.

    A queued run is refreshed with ONE bounded fetch from the sandbox under
    `FOR UPDATE SKIP LOCKED` (a concurrent poll returns the row as it stands).
    Ownership only, no open-question check: a result arriving after the
    candidate moved on is theirs to read and says nothing hidden.
    """
    conversation = (await _owned_conversation(session, user, conversation_id)).conversation
    question = await _conversation_question(session, conversation, question_id)
    run = (
        await session.execute(
            select(CodingRun).where(
                CodingRun.id == run_id,
                CodingRun.conversation_id == conversation.id,
                CodingRun.question_id == question.id,
            )
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NOT_FOUND_DETAIL)
    if run.status == RUN_QUEUED:
        run = await runs.refresh_run(session, run=run, question=question)
    return _run_out(run, question)


@router.get(
    "/conversations/{conversation_id}/coding/{question_id}/submission",
    response_model=CodingSubmissionStateOut,
    dependencies=[
        Depends(
            rate_limit(
                "coding_submission_state",
                limit=_settings.coding_submission_state_rate_per_minute,
                window=60,
            )
        )
    ],
)
async def get_coding_submission_state(
    conversation_id: uuid.UUID,
    question_id: uuid.UUID,
    user: CurrentUser = Depends(get_current_candidate),
    session: AsyncSession = Depends(get_candidate_db),
) -> CodingSubmissionStateOut:
    """The state of the candidate's own final answer, in one word: never a
    result, because the hidden tests stay hidden, including how the program
    did on them."""
    conversation = (await _owned_conversation(session, user, conversation_id)).conversation
    question = await _conversation_question(session, conversation, question_id)
    submission = (
        await session.execute(
            select(CodingSubmission).where(
                CodingSubmission.conversation_id == conversation.id,
                CodingSubmission.question_id == question.id,
            )
        )
    ).scalar_one_or_none()
    if submission is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=NO_FINAL_ANSWER_DETAIL)
    return CodingSubmissionStateOut(state_word=submissions.candidate_state_word(submission))
