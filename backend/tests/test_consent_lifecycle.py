"""The consent clock, checked ON its boundaries rather than near them.

WHY THIS TEST IS WRITTEN BEFORE THE SWEEP THAT USES IT
--------------------------------------------------------
The thing this logic ultimately authorises is the permanent, irreversible
erasure of a real person's profile. A test that only exercised the sweep would
prove that some candidate got deleted and would say nothing about WHEN the
threshold falls, which is the part that decides whether the right person was
deleted. `services/consent_lifecycle` holds no deletion and no email precisely
so the decision can be interrogated here, with no database and no clock.

Every assertion below is at an exact boundary or one second either side of it.
An off-by-one in a window measured in months is invisible to a test that checks
"a year ago is overdue", and it is exactly the kind of error that erases
somebody fifteen days early.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import consent_lifecycle as cl

T = cl.Thresholds(renewal_months=6, grace_days=15, inactivity_months=24)

#: A fixed instant. Real `utcnow` never appears in this file: a test whose
#: result depends on when it runs is a test that fails one day a year.
REGISTERED = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
SECOND = timedelta(seconds=1)


def _stage(now, *, consented_at=REGISTERED, reminder=None, final=None):
    return cl.stage_for(
        now=now,
        consented_at=consented_at,
        reminder_sent_at=reminder,
        final_warning_sent_at=final,
        thresholds=T,
    )


def test_a_non_positive_window_is_refused_loudly() -> None:
    """The failure this prevents erases the entire databank on one sweep.

    A window of zero puts every candidate past every threshold the instant they
    register. It has to raise where it is CONSTRUCTED, because by the time the
    sweep is iterating rows the mistake is already authorising deletions.
    """
    for bad in (
        {"renewal_months": 0, "grace_days": 15, "inactivity_months": 24},
        {"renewal_months": 6, "grace_days": 0, "inactivity_months": 24},
        {"renewal_months": 6, "grace_days": 15, "inactivity_months": -1},
    ):
        with pytest.raises(ValueError):
            cl.Thresholds(**bad)


def test_consent_is_active_right_up_to_the_renewal_instant() -> None:
    due = cl.renewal_due_at(consented_at=REGISTERED, thresholds=T)
    assert _stage(due - SECOND) == cl.STAGE_ACTIVE
    assert _stage(due) == cl.STAGE_REMINDER_DUE


def test_the_grace_window_runs_from_the_REMINDER_not_from_the_due_date() -> None:
    """The property that makes the sweep safe to run late.

    If the scheduler is down for a month, the reminder is still owed. A
    candidate must not skip from active to deletion because time passed while
    nobody was writing to them: the window starts when the letter was actually
    sent.
    """
    due = cl.renewal_due_at(consented_at=REGISTERED, thresholds=T)
    very_late = due + timedelta(days=90)

    # Nothing sent, and three months overdue: still only the REMINDER is owed.
    assert _stage(very_late) == cl.STAGE_REMINDER_DUE

    # Sent at that late moment; the grace period starts there, not at `due`.
    sent = very_late
    assert _stage(sent + timedelta(days=14), reminder=sent) == cl.STAGE_ACTIVE
    assert (
        _stage(sent + timedelta(days=15), reminder=sent)
        == cl.STAGE_FINAL_WARNING_DUE
    )


def test_deletion_waits_out_a_SECOND_window_after_the_final_warning() -> None:
    """The brief's own contradiction, resolved so the letter is true.

    Read literally, feature 8 puts the final warning and the deletion at the
    same instant, which would warn somebody about something that had already
    happened. The final warning opens another window of the same length.
    """
    due = cl.renewal_due_at(consented_at=REGISTERED, thresholds=T)
    reminder = due
    final = reminder + timedelta(days=15)

    assert _stage(final, reminder=reminder) == cl.STAGE_FINAL_WARNING_DUE
    # Immediately after the final warning, nothing is due yet.
    assert _stage(final, reminder=reminder, final=final) == cl.STAGE_ACTIVE
    assert (
        _stage(final + timedelta(days=15) - SECOND, reminder=reminder, final=final)
        == cl.STAGE_ACTIVE
    )
    assert (
        _stage(final + timedelta(days=15), reminder=reminder, final=final)
        == cl.STAGE_DELETION_DUE
    )


def test_renewing_restarts_the_whole_cycle() -> None:
    """A renewal is a new `consented_at`, so stamps from the last cycle cannot
    drag somebody who has just renewed towards deletion."""
    renewed = REGISTERED + timedelta(days=200)
    old_reminder = REGISTERED + timedelta(days=180)
    old_final = old_reminder + timedelta(days=15)

    assert (
        _stage(
            renewed + timedelta(days=1),
            consented_at=renewed,
            reminder=old_reminder,
            final=old_final,
        )
        == cl.STAGE_ACTIVE
    )


def test_dormancy_is_measured_on_its_own_clock() -> None:
    """C6: two independent facts, never one sweep with an OR.

    A candidate who renewed yesterday, and is therefore nowhere near consent
    expiry, is STILL dormant if they have not engaged for the inactivity
    period, and a candidate who engaged yesterday is not dormant however
    overdue their consent is. A rule that mixed the two would erase an active
    candidate whose engagement column was never written.
    """
    engaged = REGISTERED
    threshold = engaged + timedelta(days=30 * 24)

    assert not cl.is_dormant(
        now=threshold - SECOND, last_engagement_at=engaged, thresholds=T
    )
    assert cl.is_dormant(now=threshold, last_engagement_at=engaged, thresholds=T)

    recently_engaged = threshold - timedelta(days=1)
    assert not cl.is_dormant(
        now=threshold, last_engagement_at=recently_engaged, thresholds=T
    )


def test_null_timestamps_read_through_to_registration() -> None:
    """The direction of this default is the whole point.

    Treating a NULL engagement stamp as "never dormant" would make the
    inactivity rule silently inapplicable to exactly the accounts it exists
    for: the ones that registered and did nothing. It reads through to
    registration instead, so those accounts are IN scope.
    """
    assert cl.consented_at_for(created_at=REGISTERED, renewed_at=None) == REGISTERED
    later = REGISTERED + timedelta(days=200)
    assert cl.consented_at_for(created_at=REGISTERED, renewed_at=later) == later

    assert (
        cl.engagement_at_for(created_at=REGISTERED, last_engagement_at=None)
        == REGISTERED
    )
    assert (
        cl.engagement_at_for(created_at=REGISTERED, last_engagement_at=later) == later
    )


def test_every_declared_stage_is_reachable_and_nothing_else_is() -> None:
    """A stage nobody declared is a branch the sweep will not handle, and the
    sweep's fallthrough is 'do nothing', which would silently stop the whole
    lifecycle rather than failing. A DECLARED stage that is unreachable is dead
    code pretending to be a rule, so this asserts set equality."""
    due = cl.renewal_due_at(consented_at=REGISTERED, thresholds=T)
    reminder = due
    final = reminder + timedelta(days=15)
    moments = [
        REGISTERED,
        due - SECOND,
        due,
        reminder + timedelta(days=15),
        final + timedelta(days=15),
    ]
    seen = {_stage(moment, reminder=reminder, final=final) for moment in moments}
    seen |= {_stage(moment) for moment in moments}
    # The final-warning stage is only reachable while `final` is still NULL,
    # which is the whole shape of the machine: once the letter is sent, that
    # branch can never be taken again for this cycle. Generating the set
    # without this case is how the first draft of this test convinced itself a
    # live stage was dead.
    seen |= {_stage(moment, reminder=reminder) for moment in moments}
    assert seen == cl.ALL_STAGES
