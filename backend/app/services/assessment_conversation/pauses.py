"""The intervals the turn clock does not count (`assessment_pauses`).

OWNERSHIP: PLAN-p3 WP4 (proctoring) owns this module in the release; the
conversation engine imports it. This is the interface PLAN-p3 section 3.4
fixes, written so the engine is testable on its own branch:

    open_pause(session, conversation_id, reason, *, at, ref_id=None)
    close_pause(session, conversation_id, reason, *, at, ref_id=None)
    pauses_for_turn(session, conversation_id, *, since)

Three reasons, three writers: `device_loss` (proctoring, a camera or a
microphone stopped), `transcription` (the voice path, a spoken answer is being
transcribed or its failure is on screen), `warning` (proctoring, a blocking
warning is on screen). Every pause is a row the SERVER wrote; the client's
`paused_ms` no longer exists (Appendix B section 3).

Imports nothing from `services/proctoring`, so proctoring can import it
without a cycle, and nothing here reads or writes media.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import AssessmentConversation
from app.models.voice import PAUSE_REASONS, AssessmentPause
from app.services.assessment_conversation.timers import Pause


def _check_reason(reason: str) -> None:
    if reason not in PAUSE_REASONS:
        raise ValueError(f"unknown pause reason {reason!r}; expected one of {PAUSE_REASONS}")


async def open_pause(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    reason: str,
    *,
    at: datetime,
    ref_id: uuid.UUID | None = None,
) -> AssessmentPause:
    """Open a pause, or return the one already open for the same reason and
    reference. Idempotent, so a redelivered event cannot stack two pauses."""
    _check_reason(reason)
    existing = (
        await session.execute(
            select(AssessmentPause).where(
                AssessmentPause.conversation_id == conversation_id,
                AssessmentPause.reason == reason,
                AssessmentPause.ended_at.is_(None),
                (
                    AssessmentPause.ref_id.is_(None)
                    if ref_id is None
                    else AssessmentPause.ref_id == ref_id
                ),
            )
        )
    ).scalars().first()
    if existing is not None:
        return existing
    conversation = await session.get(AssessmentConversation, conversation_id)
    if conversation is None:
        raise LookupError(f"assessment conversation {conversation_id} not found")
    pause = AssessmentPause(
        tenant_id=conversation.tenant_id,
        conversation_id=conversation_id,
        reason=reason,
        ref_id=ref_id,
        started_at=at,
    )
    session.add(pause)
    await session.flush()
    return pause


async def close_pause(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    reason: str,
    *,
    at: datetime,
    ref_id: uuid.UUID | None = None,
) -> int:
    """Make sure every matching pause has ended by `at`. Returns how many
    rows changed.

    An open pause ends at `at`. A pause already scheduled to end LATER than
    `at` is brought forward to `at`, which is what acknowledging a failed
    transcription does. A pause that ended earlier is left alone, and `at` is
    never set before a pause's own start (the interval CHECK).
    """
    _check_reason(reason)
    conditions = [
        AssessmentPause.conversation_id == conversation_id,
        AssessmentPause.reason == reason,
        AssessmentPause.started_at <= at,
        or_(AssessmentPause.ended_at.is_(None), AssessmentPause.ended_at > at),
    ]
    if ref_id is not None:
        conditions.append(AssessmentPause.ref_id == ref_id)
    result = await session.execute(
        update(AssessmentPause)
        .where(*conditions)
        .values(ended_at=at)
        .execution_options(synchronize_session=False)
    )
    await session.flush()
    return int(result.rowcount or 0)


async def pauses_for_turn(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    since: datetime,
) -> list[Pause]:
    """Every pause that can overlap a turn opened at `since`: those still open
    or scheduled, and those that ended after it. Oldest first."""
    rows = (
        await session.execute(
            select(AssessmentPause.reason, AssessmentPause.started_at, AssessmentPause.ended_at)
            .where(
                AssessmentPause.conversation_id == conversation_id,
                or_(AssessmentPause.ended_at.is_(None), AssessmentPause.ended_at > since),
            )
            .order_by(AssessmentPause.started_at)
        )
    ).all()
    return [Pause(reason=reason, start=start, end=end) for reason, start, end in rows]
