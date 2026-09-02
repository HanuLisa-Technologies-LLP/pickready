"""The two questions the assessment API asks proctoring (principle P4).

    require_active(session, conversation)   may this conversation proceed?
    load_for_conversation(session, id)      the session row, or None

PROCTORING IS MANDATORY. `api/assessments.start_conversation` and
`api/assessments.respond` both call `require_active` before doing anything
else, so a conversation cannot be opened or advanced without a consented,
still-running proctoring session. There is no flag that relaxes this and no
role that bypasses it: a candidate who declines the consent screen does not
take the assessment, which is what "no proctoring means no assessment" has to
mean in code.

Deliberately tiny and dependency-free. It reads one row; it decides nothing
about warnings or terminations, which is `ingestion.py`'s job. The API depends
on THIS module so that the ingestion pipeline can change shape without the
assessment API noticing.
"""
from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import AssessmentConversation
from app.models.proctoring import (
    ENDED_OUTCOMES,
    OUTCOME_ACTIVE,
    ProctoringSession,
)

__all__ = [
    "CONSENT_REQUIRED_DETAIL",
    "SESSION_ENDED_DETAIL",
    "load_for_conversation",
    "require_active",
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
