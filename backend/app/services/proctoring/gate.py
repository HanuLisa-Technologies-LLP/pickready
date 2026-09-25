"""The questions the assessment API asks proctoring (principle P4).

    require_active(session, conversation)       may this conversation proceed?
    require_answerable(session, conversation)   ...and may it take an answer
                                                right now (not paused)?
    enforce(session, conversation, *, now)      settle a device pause that ran
                                                past its grace; returns the
                                                termination, never raises it
    load_for_conversation(session, id)          the session row, or None

PROCTORING IS MANDATORY. `api/assessments.start_conversation` and
`api/assessments.respond` both call `require_active` before doing anything
else, so a conversation cannot be opened or advanced without a consented,
still-running proctoring session. There is no flag that relaxes this and no
role that bypasses it: a candidate who declines the consent screen does not
take the assessment, which is what "no proctoring means no assessment" has to
mean in code.

Deliberately tiny. It reads rows; it decides nothing about warnings or
terminations, which is `ingestion.py`'s job, and `enforce` only hands over to
it. The API depends on THIS module so that the ingestion pipeline can change
shape without the assessment API noticing.

WHILE A DEVICE PAUSE IS OPEN THE ASSESSMENT TAKES NO ANSWER (master prompt,
Phase 3). `require_answerable` refuses with `PAUSED_DETAIL`, a 409 like every
other step-of-the-flow refusal here. It is what the answer, draft and voice
routes call; the START route calls `require_active` and shows the pause, so a
reload in the middle of a pause lands on the pause screen rather than an error.

`enforce` RETURNS a termination rather than raising one. A raise would roll
back the request's transaction, and with it the ended session, so the next
request would find the same expired pause and end it again. A route that calls
`enforce` and gets a termination back returns a response saying so, and its
transaction commits.
"""
from __future__ import annotations

import uuid

from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import AssessmentConversation
from app.models.proctoring import (
    ENDED_OUTCOMES,
    OUTCOME_ACTIVE,
    ProctoringSession,
)
from app.schemas.proctoring import TerminationOut
from app.services.assessment_conversation import pauses
from app.services.proctoring import ingestion

__all__ = [
    "CONSENT_REQUIRED_DETAIL",
    "SESSION_ENDED_DETAIL",
    "PAUSED_DETAIL",
    "load_for_conversation",
    "require_active",
    "require_answerable",
    "enforce",
]

#: Said to the candidate, so it names what to do. The page shows the consent
#: screen before ever calling `start`, so a candidate reads this only when a
#: session row was lost or a stale tab is retried.
CONSENT_REQUIRED_DETAIL = (
    "This assessment is monitored and cannot begin until you have read and "
    "agreed to the monitoring notice."
)
SESSION_ENDED_DETAIL = (
    "This assessment has ended. Your answers up to that point were saved and "
    "the hiring team has been informed why it ended."
)
#: The assessment is paused because the camera or microphone stopped. The
#: pause screen says which and for how long; this is what a stale tab that
#: tries to answer anyway is told.
PAUSED_DETAIL = (
    "The assessment is paused because your camera or microphone stopped. Fix "
    "it to continue; your answers so far are saved."
)


async def load_for_conversation(
    session: AsyncSession, conversation_id: uuid.UUID
) -> ProctoringSession | None:
    return (
        await session.execute(
            select(ProctoringSession).where(
                ProctoringSession.conversation_id == conversation_id
            )
        )
    ).scalars().first()


async def require_active(
    session: AsyncSession, conversation: AssessmentConversation
) -> ProctoringSession:
    """The proctoring session this conversation runs under, or a 409.

    409 rather than 403: the candidate is who they say they are and is allowed
    to be here; what is missing is a step of the flow. The detail names it.
    """
    proctoring = await load_for_conversation(session, conversation.id)
    if proctoring is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=CONSENT_REQUIRED_DETAIL)
    if proctoring.outcome in ENDED_OUTCOMES:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=SESSION_ENDED_DETAIL)
    if proctoring.outcome != OUTCOME_ACTIVE:  # pragma: no cover - the CHECK forbids it
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=SESSION_ENDED_DETAIL)
    return proctoring


async def require_answerable(
    session: AsyncSession, conversation: AssessmentConversation
) -> ProctoringSession:
    """`require_active`, and refused with a 409 while a device pause is open.

    An open pause past its grace is refused too: its session is about to be
    ended by the next heartbeat, batch or sweep, and an answer taken in the
    meantime would be an answer given with the camera off.
    """
    proctoring = await require_active(session, conversation)
    open_pause = await pauses.unclosed_pause(
        session, conversation.id, pauses.PAUSE_DEVICE_LOSS
    )
    if open_pause is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=PAUSED_DETAIL)
    return proctoring


async def enforce(
    session: AsyncSession, conversation: AssessmentConversation, *, now: datetime
) -> TerminationOut | None:
    """End the session if its device pause has run past the grace, and return
    the termination, or None.

    For an assessment route that wants the answer before it proceeds. It never
    raises the ending, for the reason in the module docstring.
    """
    proctoring = await load_for_conversation(session, conversation.id)
    if proctoring is None or proctoring.outcome in ENDED_OUTCOMES:
        return None
    return await ingestion.enforce_pause(session, proctoring, now=now)
