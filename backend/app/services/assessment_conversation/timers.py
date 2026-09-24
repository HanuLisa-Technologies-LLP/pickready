"""The turn clock, as pure arithmetic over server timestamps.

APPENDIX B SECTION 3 IS A TABLE OF DEADLINES, AND THE SERVER KEEPS THEM
-----------------------------------------------------------------------
Prose three minutes, multiple choice and fill-in-the-blank sixty seconds, a
follow-up a hundred seconds, coding twenty minutes. Before 2026-09-24 the only
clock was the browser's: `respond` subtracted a `paused_ms` the client sent, so
the time a recruiter read was a number the client chose, and a reload re-stamped
`prompt_shown_at` and handed the candidate a fresh question clock.

Now the deadline is computed HERE from three things the server wrote itself:
when the turn opened (`prompt_shown_at`, stamped once), the allocation
snapshotted at that moment, and the pause rows (`assessment_pauses`, read
through `pauses.pauses_for_turn`, which applies each row's cap). Nothing the
client says moves it.

WHAT A PAUSE DOES
-----------------
Paused time is the UNION of the pause intervals intersected with
[shown_at, now]. Overlapping pauses count once: a device loss during a
transcription is one stopped clock, not two. While any pause is open the turn
cannot expire, however long it lasts; the device-loss grace that ENDS such a
pause belongs to proctoring, not to this clock.

THE GRACE BOUNDARY IS INCLUSIVE. An answer that arrives exactly at the
deadline plus the grace is still the candidate's own; one a moment later is
not. Ties go to the candidate, the same direction as every boundary rule in
this product (claude.md rule 8).

Pure, deterministic, no database and no settings read: the caller passes the
pause intervals, the allocation and the grace, so the whole clock is testable
with a table. The UNION arithmetic is `pauses.paused_seconds`, the one
implementation of it; this module only decides what a turn does with it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Sequence

from app.services.assessment_conversation.pauses import Interval, paused_seconds

# ── Turn kinds ────────────────────────────────────────────────────────────────
#: A base question from the candidate's plan.
TURN_BASE = "base"
#: A follow-up probing an accepted prose answer (at most one per question).
TURN_PROBE = "probe"
#: A re-ask after a non-answer. Timed like a follow-up: the candidate is
#: answering the same question again, not a new one.
TURN_REASK = "reask"
TURN_KINDS: tuple[str, ...] = (TURN_BASE, TURN_PROBE, TURN_REASK)

#: Which open pause is REPORTED when several overlap. A lost camera is the one
#: the candidate has to act on, so it is named first.
_REASON_PRIORITY: tuple[str, ...] = ("device_loss", "transcription", "warning")


@dataclass(frozen=True)
class TurnClock:
    """Where one turn's clock stands at `now`. Internal to the server except
    for the fields the countdown needs (`deadline_at`, `paused`)."""

    shown_at: datetime
    allocation_seconds: int
    paused_seconds: float
    deadline_at: datetime
    grace_deadline_at: datetime
    remaining_ms: int
    paused: bool
    pause_reason: str | None
    expired: bool


def _stops_clock_at(pause: Interval, moment: datetime) -> bool:
    return pause.start <= moment and (pause.end is None or pause.end > moment)


def allocation_seconds(
    *,
    kind: str,
    question_type: str | None,
    prose_seconds: int,
    objective_seconds: int,
    coding_seconds: int,
    follow_up_seconds: int,
    objective_types: frozenset[str],
    coding_type: str,
) -> int:
    """The Appendix B allocation for one turn.

    A follow-up and a re-ask take the follow-up allocation whatever the base
    question was: both are prose (a structured question is never probed or
    re-asked). A base question takes its format's allocation, and anything
    that is neither objective nor coding is prose.
    """
    if kind not in TURN_KINDS:
        raise ValueError(f"unknown turn kind {kind!r}; expected one of {TURN_KINDS}")
    if kind != TURN_BASE:
        return int(follow_up_seconds)
    if question_type in objective_types:
        return int(objective_seconds)
    if question_type == coding_type:
        return int(coding_seconds)
    return int(prose_seconds)


def turn_clock(
    *,
    shown_at: datetime,
    allocation_seconds: int,
    pauses: Sequence[Interval],
    now: datetime,
    grace_seconds: int,
) -> TurnClock:
    """The clock of the turn opened at `shown_at`, read at `now`.

    `pauses` are intervals as `pauses.pauses_for_turn(..., until=now)` returns
    them: an `end` of None is a pause still stopping the clock at `now`.
    """
    if allocation_seconds <= 0:
        raise ValueError("a turn allocation must be positive")
    paused_total = paused_seconds(tuple(pauses), start=shown_at, end=now)
    deadline = shown_at + timedelta(seconds=allocation_seconds + paused_total)
    grace_deadline = deadline + timedelta(seconds=max(0, int(grace_seconds)))
    open_reasons = {pause.reason for pause in pauses if _stops_clock_at(pause, now)}
    reason = next((r for r in _REASON_PRIORITY if r in open_reasons), None)
    if reason is None and open_reasons:
        reason = sorted(open_reasons)[0]
    paused = bool(open_reasons)
    remaining = max(0, int((deadline - now).total_seconds() * 1000))
    return TurnClock(
        shown_at=shown_at,
        allocation_seconds=int(allocation_seconds),
        paused_seconds=paused_total,
        deadline_at=deadline,
        grace_deadline_at=grace_deadline,
        remaining_ms=remaining,
        paused=paused,
        pause_reason=reason,
        expired=(not paused) and now > grace_deadline,
    )


def time_spent_seconds(clock: TurnClock, *, now: datetime) -> int:
    """Answering time for the record: elapsed minus paused, never more than
    the allocation and never negative. Server arithmetic only; `clock` must
    have been read at the same `now`."""
    elapsed = max(0.0, (now - clock.shown_at).total_seconds())
    spent = elapsed - clock.paused_seconds
    return int(max(0.0, min(float(clock.allocation_seconds), spent)))
