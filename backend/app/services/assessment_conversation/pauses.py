"""The pause record: when an assessment's clock was stopped, and why.

    open_pause(session, conversation_id, reason, *, at, ref_id=None, max_seconds=None)
    close_pause(session, conversation_id, reason, *, at, ref_id=None)
    close_all_open(session, conversation_id, *, at)
    current_pause(session, conversation_id, reason, *, at)
    unclosed_pause(session, conversation_id, reason)
    pauses_for_turn(session, conversation_id, *, since, until)
    paused_seconds(intervals, *, start, end)
    count_pauses(session, conversation_id, reason)

WHO CALLS WHAT (PLAN-p3 3.4 and 3.7)
------------------------------------
Proctoring opens and closes `device_loss` pauses when a camera or microphone
stops and comes back (`services/proctoring/device_pause.py`), and `warning`
pauses while a blocking warning is on the candidate's screen. The voice answer
route opens and closes `transcription` pauses. The turn timer only READS, via
`pauses_for_turn`, and subtracts the union of what it gets back.

THIS MODULE IMPORTS NOTHING FROM PROCTORING, and the dependency runs one way on
purpose: the conversation engine needs the pause record to measure a turn, and
must not thereby acquire the proctoring package, which no module that decides
what a candidate is asked or scored may reach
(`tests/test_proctoring_scoring_isolation.py`).

THE CAP IS APPLIED HERE, ONCE. A row's `expires_at` is the latest instant it
can count for. `Interval.end` is `min(ended_at, expires_at)`, so a warning
nobody acknowledged, or a device that never came back, stops the clock for a
bounded time whether or not any request ever returns to close it. Every reader
goes through `_interval`, so no reader can forget the cap.

AN EXPIRED ROW IS CLOSED BEFORE THE NEXT ONE OPENS. The partial unique index
`uq_assessment_pauses_open` allows one row with `ended_at IS NULL` per reason
per conversation; `open_pause` stamps an expired row's `ended_at` with its own
`expires_at` first, which is the instant it stopped counting, never "now".

TIMES ARE THE SERVER'S. Every `at` a caller passes is the server's clock at the
moment it decided; a browser's own timestamp never opens, closes or sizes a
pause, because a client-supplied duration is a number the client chose (the
`paused_ms` field this replaces was exactly that).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import AssessmentConversation
from app.models.assessment_pause import (
    PAUSE_DEVICE_LOSS,
    PAUSE_REASONS,
    PAUSE_TRANSCRIPTION,
    PAUSE_WARNING,
    AssessmentPause,
)

__all__ = [
    "PAUSE_DEVICE_LOSS",
    "PAUSE_TRANSCRIPTION",
    "PAUSE_WARNING",
    "PAUSE_REASONS",
    "Interval",
    "UnknownPauseReason",
    "open_pause",
    "close_pause",
    "current_pause",
    "unclosed_pause",
    "pauses_for_turn",
    "count_pauses",
    "paused_seconds",
    "close_all_open",
]


class UnknownPauseReason(ValueError):
    """A reason outside `PAUSE_REASONS`. Refused before the database's CHECK
    would refuse it, so the error names the reason rather than a constraint."""


@dataclass(frozen=True)
class Interval:
    """One pause as the clock sees it. `end` is None while it still counts."""

    reason: str
    start: datetime
    end: datetime | None


def _require_reason(reason: str) -> None:
    if reason not in PAUSE_REASONS:
        raise UnknownPauseReason(f"{reason!r} is not a pause reason; expected one of {PAUSE_REASONS}")


def _effective_end(row: AssessmentPause) -> datetime | None:
    ends = [moment for moment in (row.ended_at, row.expires_at) if moment is not None]
    return min(ends) if ends else None


def _interval(row: AssessmentPause, *, at: datetime) -> Interval:
    """The row as the clock sees it at `at`: an end that is still in the
    future is not an end yet."""
    end = _effective_end(row)
    if end is not None and end > at:
        end = None
    return Interval(reason=row.reason, start=row.started_at, end=end)


def _counts_at(row: AssessmentPause, at: datetime) -> bool:
    """True when the row is still stopping the clock at `at`."""
    if row.started_at > at:
        return False
    end = _effective_end(row)
    return end is None or end > at


async def unclosed_pause(
    session: AsyncSession, conversation_id: uuid.UUID, reason: str
) -> AssessmentPause | None:
    """The row of this reason nobody has closed yet, INCLUDING one whose cap
    has passed. The clock ignores such a row after its cap (`current_pause`);
    its owner may still need to act on it, which is why this is separate: an
    unrecovered device pause past its grace ends the session."""
    # populate_existing: a caller deciding under a lock must see the row as
    # committed, not as this session first loaded it. Autoflush has already
    # written this session's own pending changes before the SELECT runs.
    return (
        await session.execute(
            select(AssessmentPause)
            .where(
                AssessmentPause.conversation_id == conversation_id,
                AssessmentPause.reason == reason,
                AssessmentPause.ended_at.is_(None),
            )
            .execution_options(populate_existing=True)
        )
    ).scalars().first()


async def current_pause(
    session: AsyncSession, conversation_id: uuid.UUID, reason: str, *, at: datetime
) -> AssessmentPause | None:
    """The pause of this reason that is stopping the clock at `at`, or None.

    A row left open past its `expires_at` is NOT current: it stopped counting
    at `expires_at`, and whoever owns the reason decides what that means (for a
    device loss, that the session ends).
    """
    _require_reason(reason)
    row = await unclosed_pause(session, conversation_id, reason)
    if row is None or not _counts_at(row, at):
        return None
    return row


async def open_pause(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    reason: str,
    *,
    at: datetime,
    ref_id: uuid.UUID | None = None,
    max_seconds: float | None = None,
) -> AssessmentPause:
    """Open a pause of `reason` at `at`, or return the one already open.

    Idempotent per reason: a second open while the first still counts returns
    the first unchanged, so a retried request cannot stack two pauses and stop
    the clock twice for one event. An open row whose cap has passed is closed
    at its cap first, then a new one opens.
    """
    _require_reason(reason)
    if max_seconds is not None and max_seconds <= 0:
        raise ValueError("a pause cap must be positive")
    existing = await unclosed_pause(session, conversation_id, reason)
    if existing is not None:
        cap = _effective_end(existing)
        if cap is None or cap > at:
            # Still stopping the clock (or opened by another task a moment
            # ahead of this one's clock): the same pause, not a second one.
            return existing
        existing.ended_at = cap
        await session.flush()
    conversation = await session.get(AssessmentConversation, conversation_id)
    if conversation is None:
        raise LookupError(f"assessment conversation {conversation_id} does not exist")
    expires_at = at + timedelta(seconds=max_seconds) if max_seconds is not None else None
    statement = (
        insert(AssessmentPause)
        .values(
            id=uuid.uuid4(),
            tenant_id=conversation.tenant_id,
            conversation_id=conversation_id,
            reason=reason,
            started_at=at,
            expires_at=expires_at,
            ref_id=ref_id,
        )
        .on_conflict_do_nothing(
            index_elements=["conversation_id", "reason"],
            index_where=AssessmentPause.ended_at.is_(None),
        )
    )
    await session.execute(statement)
    row = await unclosed_pause(session, conversation_id, reason)
    if row is None:  # pragma: no cover - the insert or the conflict left one
        raise RuntimeError(f"no open {reason} pause after opening one on {conversation_id}")
    return row


async def close_pause(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    reason: str,
    *,
    at: datetime,
    ref_id: uuid.UUID | None = None,
) -> AssessmentPause | None:
    """Close the open pause of `reason`, or return None when there is none.

    With `ref_id`, only the pause that `ref_id` opened is closed: an
    acknowledgement of one voice answer's failure must not close a pause a
    later voice answer opened. The close is stamped at `at`, or at the cap if
    the cap came first, because the pause stopped counting then.
    """
    _require_reason(reason)
    row = await unclosed_pause(session, conversation_id, reason)
    if row is None:
        return None
    if ref_id is not None and row.ref_id != ref_id:
        return None
    cap = row.expires_at
    row.ended_at = max(row.started_at, min(at, cap) if cap is not None else at)
    await session.flush()
    return row


async def pauses_for_turn(
    session: AsyncSession,
    conversation_id: uuid.UUID,
    *,
    since: datetime,
    until: datetime,
) -> tuple[Interval, ...]:
    """Every pause that overlaps `[since, until]`, capped, oldest first.

    Not clipped to the window and not merged: the turn timer intersects and
    unions them itself (`paused_seconds` below is that arithmetic, for callers
    that only need the total), because a warning pause and a device pause can
    overlap and the overlap must count once.
    """
    rows = (
        await session.execute(
            select(AssessmentPause)
            .where(
                AssessmentPause.conversation_id == conversation_id,
                AssessmentPause.started_at <= until,
            )
            .order_by(AssessmentPause.started_at, AssessmentPause.id)
        )
    ).scalars().all()
    intervals: list[Interval] = []
    for row in rows:
        interval = _interval(row, at=until)
        if interval.end is not None and interval.end < since:
            continue
        intervals.append(interval)
    return tuple(intervals)


def paused_seconds(
    intervals: tuple[Interval, ...] | list[Interval], *, start: datetime, end: datetime
) -> float:
    """Seconds of `[start, end]` covered by the UNION of `intervals`.

    Pure. An interval with no end runs to `end`. Overlaps count once, so two
    reasons stopping the clock at the same time never give the candidate the
    same second twice.
    """
    if end <= start:
        return 0.0
    spans = sorted(
        (max(interval.start, start), min(interval.end or end, end))
        for interval in intervals
    )
    total = 0.0
    cursor = start
    for lo, hi in spans:
        if hi <= lo:
            continue
        lo = max(lo, cursor)
        if hi > lo:
            total += (hi - lo).total_seconds()
            cursor = hi
    return total


async def count_pauses(
    session: AsyncSession, conversation_id: uuid.UUID, reason: str
) -> int:
    """How many pauses of `reason` this conversation has ever opened."""
    _require_reason(reason)
    return int(
        (
            await session.execute(
                select(func.count(AssessmentPause.id)).where(
                    AssessmentPause.conversation_id == conversation_id,
                    AssessmentPause.reason == reason,
                )
            )
        ).scalar_one()
    )


async def close_all_open(
    session: AsyncSession, conversation_id: uuid.UUID, *, at: datetime
) -> int:
    """Close every pause still open on a conversation that has ended.

    Stamped at `at` or at each row's cap, whichever came first. Returns the
    number closed. Called when proctoring ends a session, so an ended
    assessment does not leave a pause that reads as still running.
    """
    rows = (
        await session.execute(
            select(AssessmentPause).where(
                AssessmentPause.conversation_id == conversation_id,
                AssessmentPause.ended_at.is_(None),
            )
        )
    ).scalars().all()
    for row in rows:
        cap = row.expires_at
        row.ended_at = max(row.started_at, min(at, cap) if cap is not None else at)
    if rows:
        await session.flush()
    return len(rows)

