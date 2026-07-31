"""The fixed 30-day posting window and its visibility rules (spec §2).

Every boundary is asserted directly. These rules decide whether a person can
see a job at all, so an off-by-one here is invisible in manual testing and
severe in production — a candidate silently loses access, or keeps it when the
posting has closed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services import job_posting as jp

START = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
END = START + timedelta(days=30)            # 2026-01-31 10:00
GRACE = START + timedelta(days=35)          # 2026-02-05 10:00


def at(**delta) -> datetime:
    """A moment relative to the posting start."""
    return START + timedelta(**delta)


# ── The window is fixed ──────────────────────────────────────────────────────

def test_window_is_exactly_thirty_days_plus_five() -> None:
    assert jp.POSTING_DAYS == 30
    assert jp.GRACE_DAYS == 5
    assert jp.posting_end(START) == END
    assert jp.grace_end(START) == GRACE
    assert jp.posting_end(START) - START == timedelta(days=30)
    assert jp.grace_end(START) - jp.posting_end(START) == timedelta(days=5)


def test_derived_dates_match_the_generated_columns() -> None:
    """Python and the database must agree, or a boundary moves depending on
    which one the caller happened to consult."""
    assert jp.posting_end(START) == START + timedelta(days=jp.POSTING_DAYS)
    assert jp.grace_end(START) == START + timedelta(
        days=jp.POSTING_DAYS + jp.GRACE_DAYS
    )


# ── The four states ──────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "when,expected",
    [
        (at(days=-1), jp.STATUS_SCHEDULED),
        (START, jp.STATUS_ACTIVE),
        (at(days=1), jp.STATUS_ACTIVE),
        (at(days=29, hours=23), jp.STATUS_ACTIVE),
        (END, jp.STATUS_ACTIVE),                    # day 30 boundary: still open
        (END + timedelta(seconds=1), jp.STATUS_GRACE),
        (at(days=31), jp.STATUS_GRACE),
        (at(days=34, hours=23), jp.STATUS_GRACE),
        (GRACE, jp.STATUS_GRACE),                   # day 35 boundary: still grace
        (GRACE + timedelta(seconds=1), jp.STATUS_EXPIRED),
        (at(days=36), jp.STATUS_EXPIRED),
        (at(days=400), jp.STATUS_EXPIRED),
    ],
)
def test_posting_status_across_the_whole_lifecycle(when, expected) -> None:
    assert jp.posting_status(START, END, GRACE, now=when) == expected


def test_boundaries_are_inclusive_so_ties_go_to_the_candidate() -> None:
    """Exactly ON the deadline is still inside it — someone hitting Apply at
    the last second gets in (claude.md rule 8: boundaries resolve upward)."""
    assert jp.posting_status(START, END, GRACE, now=END) == jp.STATUS_ACTIVE
    assert jp.posting_status(START, END, GRACE, now=GRACE) == jp.STATUS_GRACE


def test_a_job_with_no_start_date_fails_closed() -> None:
    """An unknown window must deny access, never grant it."""
    assert jp.posting_status(None) == jp.STATUS_EXPIRED
    assert jp.can_apply(posting_start=None) is False
    assert jp.can_view_job(posting_start=None) is False
    assert jp.public_link_active(None) is False


def test_status_derives_the_dates_when_they_are_not_supplied() -> None:
    assert jp.posting_status(START, now=at(days=15)) == jp.STATUS_ACTIVE
    assert jp.posting_status(START, now=at(days=32)) == jp.STATUS_GRACE
    assert jp.posting_status(START, now=at(days=40)) == jp.STATUS_EXPIRED


def test_naive_timestamps_are_read_as_utc() -> None:
    """Reading a naive timestamp as local time would move the boundary by
    hours — on a 30-day window that decides real access."""
    naive_start = START.replace(tzinfo=None)
    assert jp.posting_status(naive_start, now=at(days=10)) == jp.STATUS_ACTIVE
    assert jp.posting_status(naive_start, now=at(days=31)) == jp.STATUS_GRACE


# ── Rule 3: registered too late, never sees the job ──────────────────────────

def test_candidate_registered_within_the_window_is_eligible() -> None:
    assert jp.candidate_registered_in_time(at(days=1), END) is True
    assert jp.candidate_registered_in_time(at(days=29), END) is True
    assert jp.candidate_registered_in_time(END, END) is True          # boundary


def test_candidate_registered_after_the_window_is_never_eligible() -> None:
    assert jp.candidate_registered_in_time(END + timedelta(seconds=1), END) is False
    assert jp.candidate_registered_in_time(at(days=31), END) is False


def test_a_late_registrant_cannot_see_the_job_even_during_grace() -> None:
    """Spec Rule 3 is absolute: not in the board, not in search, not by URL —
    and the grace period does not create a back door."""
    assert (
        jp.can_view_job(
            posting_start=START, posting_end_date=END, grace_period_end_date=GRACE,
            candidate_created_at=at(days=31), has_applied=False, now=at(days=32),
        )
        is False
    )
    # Not even if they somehow hold an application.
    assert (
        jp.can_view_job(
            posting_start=START, posting_end_date=END, grace_period_end_date=GRACE,
            candidate_created_at=at(days=31), has_applied=True, now=at(days=32),
        )
        is False
    )


def test_a_missing_registration_date_does_not_lock_someone_out() -> None:
    """A data defect must not cost a real person their access; the
    active-window check still applies on top."""
    assert jp.candidate_registered_in_time(None, END) is True
    assert (
        jp.can_view_job(
            posting_start=START, posting_end_date=END, grace_period_end_date=GRACE,
            candidate_created_at=None, now=at(days=10),
        )
        is True
    )


# ── Visibility matrix (spec §"Visibility Rules") ─────────────────────────────

def _view(*, created, applied, when) -> bool:
    return jp.can_view_job(
        posting_start=START, posting_end_date=END, grace_period_end_date=GRACE,
        candidate_created_at=created, has_applied=applied, now=when,
    )


def test_registered_in_window_checking_during_window_can_see() -> None:
    assert _view(created=at(days=2), applied=False, when=at(days=10)) is True


def test_applicant_keeps_visibility_through_the_grace_period() -> None:
    assert _view(created=at(days=2), applied=True, when=at(days=33)) is True


def test_non_applicant_loses_visibility_the_moment_the_window_closes() -> None:
    """The grace period exists to let existing applicants finish editing. It is
    not an extension of the browsing window for everyone else."""
    assert _view(created=at(days=2), applied=False, when=at(days=33)) is False


def test_nobody_sees_the_job_after_the_grace_period() -> None:
    assert _view(created=at(days=2), applied=True, when=at(days=36)) is False
    assert _view(created=at(days=2), applied=False, when=at(days=36)) is False


# ── Applying ─────────────────────────────────────────────────────────────────

def test_can_apply_only_inside_the_active_window() -> None:
    common = dict(
        posting_start=START, posting_end_date=END, grace_period_end_date=GRACE,
        candidate_created_at=at(days=1),
    )
    assert jp.can_apply(**common, now=START) is True
    assert jp.can_apply(**common, now=at(days=29)) is True
    assert jp.can_apply(**common, now=END) is True                 # boundary
    assert jp.can_apply(**common, now=END + timedelta(seconds=1)) is False


def test_the_grace_period_never_allows_a_new_application() -> None:
    """Editing an existing application and creating a new one are different
    rights; only the first survives into the grace period."""
    assert (
        jp.can_apply(
            posting_start=START, posting_end_date=END, grace_period_end_date=GRACE,
            candidate_created_at=at(days=1), now=at(days=32),
        )
        is False
    )


def test_a_late_registrant_cannot_apply_even_while_the_job_is_active() -> None:
    """Impossible in practice, but the gate is asserted independently so a
    future change to can_view_job cannot quietly open this path."""
    assert (
        jp.can_apply(
            posting_start=START, posting_end_date=END,
            candidate_created_at=at(days=31), now=at(days=20),
        )
        is False
    )


# ── Rule 2: the external/public link ─────────────────────────────────────────

def test_public_link_works_through_the_active_window_then_404s() -> None:
    assert jp.public_link_active(START, END, now=START) is True
    assert jp.public_link_active(START, END, now=at(days=15)) is True
    assert jp.public_link_active(START, END, now=END) is True      # boundary
    assert jp.public_link_active(START, END, now=at(days=31)) is False


def test_the_public_link_is_dead_during_the_grace_period() -> None:
    """The grace period grants nothing to an anonymous visitor — it is scoped
    to people who already applied."""
    assert jp.public_link_active(START, END, now=at(days=33)) is False
    assert jp.public_link_active(START, END, now=at(days=36)) is False


# ── The 5-day edit window (spec §5.1) ────────────────────────────────────────

def _edit(*, applied, when) -> bool:
    return jp.can_edit_application(
        applied_at=applied, posting_start=START, posting_end_date=END,
        grace_period_end_date=GRACE, now=when,
    )


def test_an_applicant_can_edit_during_the_active_window() -> None:
    assert _edit(applied=at(days=5), when=at(days=10)) is True


def test_an_applicant_can_edit_throughout_the_grace_period() -> None:
    assert _edit(applied=at(days=28), when=at(days=31)) is True
    assert _edit(applied=at(days=28), when=at(days=34)) is True
    assert _edit(applied=at(days=28), when=GRACE) is True          # boundary


def test_editing_stops_the_instant_the_grace_period_ends() -> None:
    assert _edit(applied=at(days=28), when=GRACE + timedelta(seconds=1)) is False
    assert _edit(applied=at(days=28), when=at(days=36)) is False


def test_an_application_made_after_the_window_earns_no_edit_right() -> None:
    """Such an application should not exist; if one somehow does, it does not
    inherit the grace period."""
    assert _edit(applied=at(days=32), when=at(days=33)) is False


def test_edit_requires_both_timestamps() -> None:
    assert jp.can_edit_application(applied_at=None, posting_start=START) is False
    assert jp.can_edit_application(applied_at=at(days=2), posting_start=None) is False


# ── Countdown copy ───────────────────────────────────────────────────────────

def test_days_remaining_rounds_up_so_a_part_day_is_never_zero() -> None:
    """Telling someone they have zero days left while they still have hours to
    act is the worse of the two rounding errors."""
    assert jp.days_remaining(END, now=at(days=29)) == 1
    assert jp.days_remaining(END, now=END - timedelta(hours=6)) == 1
    assert jp.days_remaining(END, now=at(days=25)) == 5


def test_days_remaining_is_zero_once_the_deadline_has_passed() -> None:
    assert jp.days_remaining(END, now=END) == 0
    assert jp.days_remaining(END, now=at(days=31)) == 0
    assert jp.days_remaining(None) == 0


# ── The serialised view ──────────────────────────────────────────────────────

def test_describe_reads_an_orm_row_or_a_mapping_identically() -> None:
    from types import SimpleNamespace

    row = {
        "posting_start_date": START,
        "posting_end_date": END,
        "grace_period_end_date": GRACE,
    }
    from_mapping = jp.describe(row, now=at(days=10))
    from_object = jp.describe(SimpleNamespace(**row), now=at(days=10))
    assert from_mapping == from_object
    assert from_mapping.posting_status == jp.STATUS_ACTIVE
    assert from_mapping.is_active is True
    assert from_mapping.accepts_applications is True


def test_describe_derives_missing_generated_columns() -> None:
    window = jp.describe({"posting_start_date": START}, now=at(days=10))
    assert window.posting_end_date == END
    assert window.grace_period_end_date == GRACE


def test_summary_copy_matches_the_state() -> None:
    active = jp.describe({"posting_start_date": START}, now=at(days=25))
    assert "Live for 5 more days" in active.summary()

    grace = jp.describe({"posting_start_date": START}, now=at(days=32))
    assert "no new applications" in grace.summary()
    assert "3 more days" in grace.summary()

    expired = jp.describe({"posting_start_date": START}, now=at(days=40))
    assert "Expired" in expired.summary()

    # Singular/plural is handled rather than reading "1 days".
    one_day = jp.describe({"posting_start_date": START}, now=at(days=29, hours=12))
    assert "1 more day." in one_day.summary()
