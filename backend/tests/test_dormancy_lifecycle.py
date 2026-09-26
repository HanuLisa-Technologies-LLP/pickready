"""The inactivity clock now has a letter and a window, and this pins both.

WHAT IT REPLACES
------------------
The dormancy rule was two lines in the sweep: if `is_dormant`, erase. No
warning email, no grace period, no analogue of `stage_for`, and nothing a
candidate could have done in between because they were never told. The
specification asks for a deletion warning, then a fifteen day grace, then
deletion.

WHY THE BOUNDARIES ARE ASSERTED EXACTLY
-----------------------------------------
Because the consequence of a boundary being one comparison out is a profile
deleted a day early with the letter still in flight. The thresholds are
injected rather than read from settings, so each case states the number it is
testing instead of inheriting one.

THE CASE THAT IS NOT OBVIOUS is
`test_a_warning_older_than_the_last_engagement_is_spent`. The stamp is a latch
and nothing clears it, so a candidate who was warned, came back, and drifted
away again would read as already warned and be erased with no second letter at
all. That is the failure mode of every latch in this product, and it is settled
inside the pure function rather than left to every writer of
`last_engagement_at` to remember.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services import consent_lifecycle as cl

THRESHOLDS = cl.Thresholds(renewal_months=6, grace_days=15, inactivity_months=24)
NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


def _months(count: int) -> timedelta:
    return timedelta(days=30 * count)


def test_an_active_candidate_is_never_warned() -> None:
    assert (
        cl.dormancy_stage_for(
            now=NOW,
            last_engagement_at=NOW - _months(23),
            warning_sent_at=None,
            thresholds=THRESHOLDS,
        )
        == cl.DORMANCY_ACTIVE
    )


def test_the_warning_falls_exactly_at_the_inactivity_threshold() -> None:
    """Inclusive, the direction every window in this product resolves ties.

    One second earlier is active; the instant itself is due.
    """
    due = NOW - _months(24)
    assert (
        cl.dormancy_stage_for(
            now=NOW,
            last_engagement_at=due,
            warning_sent_at=None,
            thresholds=THRESHOLDS,
        )
        == cl.DORMANCY_WARNING_DUE
    )
    assert (
        cl.dormancy_stage_for(
            now=NOW,
            last_engagement_at=due + timedelta(seconds=1),
            warning_sent_at=None,
            thresholds=THRESHOLDS,
        )
        == cl.DORMANCY_ACTIVE
    )


def test_the_grace_runs_from_the_letter_and_not_from_the_due_date() -> None:
    """The property that makes the sweep safe to run late.

    A candidate who has been dormant for years and was written to YESTERDAY is
    inside their window. A time-only rule would erase them for not answering a
    letter that had barely arrived, and would erase everybody at once the first
    time the scheduler came back from an outage.
    """
    assert (
        cl.dormancy_stage_for(
            now=NOW,
            last_engagement_at=NOW - _months(48),
            warning_sent_at=NOW - timedelta(days=1),
            thresholds=THRESHOLDS,
        )
        == cl.DORMANCY_ACTIVE
    )


def test_deletion_falls_exactly_at_the_end_of_the_grace_period() -> None:
    warned = NOW - timedelta(days=15)
    assert (
        cl.dormancy_stage_for(
            now=NOW,
            last_engagement_at=NOW - _months(30),
            warning_sent_at=warned,
            thresholds=THRESHOLDS,
        )
        == cl.DORMANCY_DELETION_DUE
    )
    assert (
        cl.dormancy_stage_for(
            now=NOW,
            last_engagement_at=NOW - _months(30),
            warning_sent_at=warned + timedelta(seconds=1),
            thresholds=THRESHOLDS,
        )
        == cl.DORMANCY_ACTIVE
    )


def test_coming_back_cancels_the_deletion_outright() -> None:
    """Signing in is the act the letter asks for, and it has to be enough."""
    assert (
        cl.dormancy_stage_for(
            now=NOW,
            last_engagement_at=NOW - timedelta(days=1),
            warning_sent_at=NOW - timedelta(days=40),
            thresholds=THRESHOLDS,
        )
        == cl.DORMANCY_ACTIVE
    )


def test_a_warning_older_than_the_last_engagement_is_spent() -> None:
    """Warned, returned, drifted away again: a SECOND letter is owed.

    Without the timestamp comparison the stale latch reads as "already warned"
    and the next sweep past the grace window erases somebody who was never
    told this time round.
    """
    assert (
        cl.dormancy_stage_for(
            now=NOW,
            last_engagement_at=NOW - _months(25),
            warning_sent_at=NOW - _months(30),
            thresholds=THRESHOLDS,
        )
        == cl.DORMANCY_WARNING_DUE
    )


def test_the_two_clocks_do_not_borrow_each_other_s_vocabulary() -> None:
    """Consent and inactivity are different facts about a person (C6).

    Sharing stage names would invite a caller to compare one against the other,
    and the comparison that reads most naturally, "is this candidate at the
    deletion stage", would then be true for a reason the erasure did not
    record.
    """
    assert not (cl.ALL_STAGES & cl.ALL_DORMANCY_STAGES)
