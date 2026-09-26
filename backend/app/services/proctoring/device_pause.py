"""The device pause: a camera or microphone stops, the assessment waits.

THE RULE (master prompt, Phase 3, Proctoring; read to the candidate before
they start by `phrasing.candidate_rules`)
-------------------------------------------------------------------------
Camera or microphone loss triggers a PAUSE, an explicit warning and a grace
period (`device_grace_seconds`, two minutes) to restore the device. At most
`device_max_pauses` (two) pauses per session. The next loss after that, or a
pause that is not recovered inside its grace, ends the session. Both endings
are `technical_failure`, never an integrity outcome: a camera that died and a
candidate who switched it off look the same from here, and the report names
which device stopped instead of guessing why.

A LOSS SHORTER THAN THE GLITCH WINDOW PAUSES NOTHING. The browser holds a loss
back for `device_glitch_seconds` before it reports one; a report that arrives
anyway with a measured duration under the window is recorded as an
interruption (`ingestion.classify`) and costs no pause.

THE GRACE BOUNDARY IS INCLUSIVE. A recovery received exactly at the end of the
grace resumes the assessment; the session ends only once the grace has been
EXCEEDED. Ties go to the candidate, as every boundary in this product does.

THE STATE IS THE PAUSE ROW. `assessment_pauses` with reason `device_loss` and
no `ended_at` IS "paused"; the number of such rows ever opened IS the number of
pauses used. There is no flag beside it to disagree with it
(`models/assessment_pause.py`). Every decision that WRITES reads that record
under a ROW LOCK on the proctoring session, so two batches from one browser,
one carrying a loss and one a recovery, are decided one after the other and
never both against the same stale picture.

TIMES ARE THE SERVER'S. The pause opens when the server receives the loss and
closes when it receives the recovery. The browser's own timestamps are stored
on the event for the activity log and decide nothing, because a client clock is
a number the client chose.

WHAT THIS MODULE DOES NOT DO. It stores no event and ends no session: it
DECIDES, and `ingestion.py`, which owns the event stream and the termination
path, records and acts. That keeps one writer for proctoring events and one
path to an ended session, and it is why nothing here imports `ingestion`.
"""
from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment_pause import AssessmentPause
from app.models.proctoring import ENDED_OUTCOMES, ProctoringEvent, ProctoringSession
from app.services.assessment_conversation import pauses
from app.services.proctoring.config import ProctoringConfig

__all__ = [
    "Decision",
    "PauseState",
    "lock",
    "begin",
    "recover",
    "expired_pause",
    "state",
    "grace_deadline",
]


class Decision(enum.Enum):
    #: A new pause opened; the clock stopped.
    PAUSED = "paused"
    #: A pause is already open; a second loss is logged and changes nothing.
    ALREADY_PAUSED = "already_paused"
    #: Every pause allowed has been used; the session ends.
    LIMIT_EXCEEDED = "limit_exceeded"
    #: The open pause closed inside the grace; the clock runs again.
    RESUMED = "resumed"
    #: Nothing was paused; a recovery is logged and changes nothing.
    NOT_PAUSED = "not_paused"
    #: The grace ran out before the device came back; the session ends.
    TIMED_OUT = "timed_out"
    #: A concurrent request ended the session while this one waited for the
    #: lock; nothing is decided here.
    SESSION_ENDED = "session_ended"


@dataclass(frozen=True)
class PauseState:
    """What the candidate's screen needs to know about the device pause."""

    paused: bool
    grace_deadline_at: datetime | None
    pauses_used: int
    max_pauses: int
    #: The catalog type of the loss that opened the open pause, so the screen
    #: can say WHICH device to fix. None when nothing is paused.
    opened_by: str | None = None


def grace_deadline(row: AssessmentPause, config: ProctoringConfig) -> datetime:
    """The last instant a recovery is accepted for this pause.

    Read from the row's own `expires_at` when it has one, because that is the
    deadline the candidate was shown when the pause opened; a setting changed
    mid-pause must not move it.
    """
    if row.expires_at is not None:
        return row.expires_at
    return row.started_at + timedelta(seconds=config.device_grace_seconds)


def _exceeded(row: AssessmentPause, now: datetime, config: ProctoringConfig) -> bool:
    return now > grace_deadline(row, config)


async def lock(session: AsyncSession, ps: ProctoringSession) -> None:
    """Take the proctoring session's row lock and refresh `ps` from the row.

    Pending changes are flushed FIRST, because `populate_existing` overwrites
    the in-memory attributes with what the row holds, and an unflushed warning
    count would otherwise be silently undone.
    """
    await session.flush()
    await session.execute(
        select(ProctoringSession)
        .where(ProctoringSession.id == ps.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


async def _unclosed(session: AsyncSession, ps: ProctoringSession) -> AssessmentPause | None:
    return await pauses.unclosed_pause(session, ps.conversation_id, pauses.PAUSE_DEVICE_LOSS)


async def begin(
    session: AsyncSession,
    ps: ProctoringSession,
    *,
    now: datetime,
    ref_id: uuid.UUID | None,
    config: ProctoringConfig,
    started_at: datetime | None = None,
) -> Decision:
    """A camera or microphone loss the classifier did not excuse.

    Under the row lock: an open pause absorbs the loss (a second device failing
    while the first is being fixed is the same interruption); a loss with a
    pause still to spend opens one, capped at the grace; a loss with none left
    ends the session. An open pause already past its grace is the timeout,
    whatever arrived after it.

    `started_at` is for a loss the browser reports only after it ended (its
    batch was held while the network was down): the pause is dated back by
    the measured duration, never by more than the grace, so the candidate's
    clock is credited for the time the device was really gone. `now` is still
    what every limit is judged at.
    """
    await lock(session, ps)
    if ps.outcome in ENDED_OUTCOMES:
        return Decision.SESSION_ENDED
    open_row = await _unclosed(session, ps)
    if open_row is not None:
        if _exceeded(open_row, now, config):
            return Decision.TIMED_OUT
        return Decision.ALREADY_PAUSED
    used = await pauses.count_pauses(session, ps.conversation_id, pauses.PAUSE_DEVICE_LOSS)
    if used >= config.device_max_pauses:
        return Decision.LIMIT_EXCEEDED
    opened = now if started_at is None else max(
        started_at, now - timedelta(seconds=config.device_grace_seconds)
    )
    await pauses.open_pause(
        session,
        ps.conversation_id,
        pauses.PAUSE_DEVICE_LOSS,
        at=opened,
        ref_id=ref_id,
        max_seconds=config.device_grace_seconds,
    )
    return Decision.PAUSED


async def recover(
    session: AsyncSession,
    ps: ProctoringSession,
    *,
    now: datetime,
    config: ProctoringConfig,
) -> tuple[Decision, int | None]:
    """The browser says every monitored device is live again.

    Returns the decision and, when a pause was open, how long it lasted in
    milliseconds (for the activity log; the clock reads the row itself).
    """
    await lock(session, ps)
    if ps.outcome in ENDED_OUTCOMES:
        return Decision.SESSION_ENDED, None
    open_row = await _unclosed(session, ps)
    if open_row is None:
        return Decision.NOT_PAUSED, None
    if _exceeded(open_row, now, config):
        deadline = grace_deadline(open_row, config)
        return Decision.TIMED_OUT, _ms(deadline - open_row.started_at)
    await pauses.close_pause(session, ps.conversation_id, pauses.PAUSE_DEVICE_LOSS, at=now)
    return Decision.RESUMED, _ms(now - open_row.started_at)


async def expired_pause(
    session: AsyncSession,
    ps: ProctoringSession,
    *,
    now: datetime,
    config: ProctoringConfig,
) -> AssessmentPause | None:
    """The open device pause whose grace has been exceeded, or None.

    Read without a lock first, because this runs on every heartbeat and every
    batch and is almost always None. When it is not, the lock is taken and the
    question asked again, so a recovery committed by a concurrent request a
    moment earlier wins and no session is ended twice.
    """
    if ps.outcome in ENDED_OUTCOMES:
        return None
    row = await _unclosed(session, ps)
    if row is None or not _exceeded(row, now, config):
        return None
    await lock(session, ps)
    if ps.outcome in ENDED_OUTCOMES:
        return None
    row = await _unclosed(session, ps)
    if row is None or not _exceeded(row, now, config):
        return None
    return row


async def state(
    session: AsyncSession,
    ps: ProctoringSession,
    *,
    config: ProctoringConfig,
) -> PauseState:
    """The device pause as the candidate's screen shows it."""
    used = await pauses.count_pauses(session, ps.conversation_id, pauses.PAUSE_DEVICE_LOSS)
    row = None if ps.outcome in ENDED_OUTCOMES else await _unclosed(session, ps)
    opened_by = None
    if row is not None and row.ref_id is not None:
        opened_by = (
            await session.execute(
                select(ProctoringEvent.event_type).where(ProctoringEvent.id == row.ref_id)
            )
        ).scalar_one_or_none()
    return PauseState(
        paused=row is not None,
        grace_deadline_at=grace_deadline(row, config) if row is not None else None,
        pauses_used=used,
        max_pauses=config.device_max_pauses,
        opened_by=opened_by,
    )


def _ms(delta: timedelta) -> int:
    return int(delta / timedelta(milliseconds=1))
