"""The turn clock, as a table: allocations, pauses, the grace boundary, time spent.

`services/assessment_conversation/timers` is pure arithmetic over server
timestamps, so every rule Appendix B section 3 states is a row here: the
allocation per kind and format, paused time as a UNION (an overlap counts
once), a pause that has not ended stopping the clock however long it lasts, a
pause SCHEDULED to end later still stopping it now, the grace boundary
inclusive (ties go to the candidate, CLAUDE.md rule 8), and the time a
recruiter reads never more than the allocation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.assessment_conversation.pauses import Interval
from app.services.assessment_conversation.timers import (
    TURN_BASE,
    TURN_PROBE,
    TURN_REASK,
    allocation_seconds,
    time_spent_seconds,
    turn_clock,
)

T0 = datetime(2026, 9, 24, 10, 0, 0, tzinfo=timezone.utc)
OBJECTIVE = frozenset({"mcq_single", "mcq_multi", "fill_blank"})


def _at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def _clock(now: float, *pauses: Interval, allocation: int = 180, grace: int = 5):
    return turn_clock(
        shown_at=T0, allocation_seconds=allocation, pauses=pauses, now=_at(now),
        grace_seconds=grace,
    )


@pytest.mark.parametrize(
    ("kind", "question_type", "expected"),
    [
        (TURN_BASE, "evidence_based", 180),
        (TURN_BASE, "short_answer", 180),
        (TURN_BASE, "mcq_single", 60),
        (TURN_BASE, "mcq_multi", 60),
        (TURN_BASE, "fill_blank", 60),
        (TURN_BASE, "coding", 1200),
        (TURN_PROBE, "evidence_based", 100),
        (TURN_REASK, "short_answer", 100),
        # A structured question is never probed, but if a kind says follow-up
        # the follow-up allocation wins whatever the base format was.
        (TURN_PROBE, "coding", 100),
    ],
)
def test_the_appendix_b_allocation_table(kind, question_type, expected) -> None:
    assert allocation_seconds(
        kind=kind, question_type=question_type, prose_seconds=180, objective_seconds=60,
        coding_seconds=1200, follow_up_seconds=100, objective_types=OBJECTIVE,
        coding_type="coding",
    ) == expected


def test_an_unknown_kind_is_refused() -> None:
    with pytest.raises(ValueError):
        allocation_seconds(
            kind="bonus", question_type="short_answer", prose_seconds=180,
            objective_seconds=60, coding_seconds=1200, follow_up_seconds=100,
            objective_types=OBJECTIVE, coding_type="coding",
        )


def test_an_unpaused_turn_expires_after_the_allocation_plus_the_grace() -> None:
    assert not _clock(185).expired, "the grace boundary is inclusive"
    assert _clock(185.001).expired
    clock = _clock(100)
    assert clock.deadline_at == _at(180)
    assert clock.remaining_ms == 80_000
    assert not clock.paused


def test_a_closed_pause_moves_the_deadline_by_its_length() -> None:
    clock = _clock(200, Interval(reason="transcription", start=_at(30), end=_at(60)))
    assert clock.paused_seconds == 30
    assert clock.deadline_at == _at(210)
    assert not clock.expired and not clock.paused


def test_overlapping_pauses_count_once() -> None:
    clock = _clock(
        300,
        Interval(reason="device_loss", start=_at(30), end=_at(90)),
        Interval(reason="warning", start=_at(60), end=_at(120)),
    )
    assert clock.paused_seconds == 90


def test_an_open_pause_stops_the_clock_and_never_expires() -> None:
    clock = _clock(10_000, Interval(reason="device_loss", start=_at(100), end=None))
    assert clock.paused
    assert clock.pause_reason == "device_loss"
    assert not clock.expired


def test_a_pause_scheduled_to_end_later_is_still_stopping_the_clock() -> None:
    clock = _clock(400, Interval(reason="transcription", start=_at(350), end=_at(420)))
    assert clock.paused and not clock.expired
    assert clock.paused_seconds == 50


def test_the_lost_device_is_reported_first_when_pauses_overlap() -> None:
    clock = _clock(
        100,
        Interval(reason="transcription", start=_at(50), end=None),
        Interval(reason="device_loss", start=_at(80), end=None),
    )
    assert clock.pause_reason == "device_loss"


def test_a_pause_before_the_turn_opened_costs_nothing() -> None:
    clock = _clock(100, Interval(reason="warning", start=_at(-60), end=_at(-30)))
    assert clock.paused_seconds == 0


def test_time_spent_is_elapsed_minus_paused_and_never_more_than_the_allocation() -> None:
    paused = Interval(reason="transcription", start=_at(10), end=_at(40))
    assert time_spent_seconds(_clock(100, paused), now=_at(100)) == 70
    assert time_spent_seconds(_clock(500), now=_at(500)) == 180
    assert time_spent_seconds(_clock(0), now=_at(0)) == 0


def test_a_non_positive_allocation_is_refused() -> None:
    with pytest.raises(ValueError):
        _clock(10, allocation=0)


@pytest.mark.parametrize(
    "overrides",
    [
        {"assessment_time_prose_seconds": 0},
        {"assessment_time_follow_up_seconds": -1},
        {"assessment_submit_grace_seconds": -1},
        {"assessment_voice_transcribe_poll_seconds": 120},
    ],
)
def test_a_clock_that_cannot_work_is_refused_at_boot(overrides) -> None:
    """A zero allocation would expire every question the moment it opened; a
    poll as long as its timeout never polls twice. Both are refused when the
    settings load, never discovered on a candidate's turn."""
    from pydantic import ValidationError

    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(**overrides)
