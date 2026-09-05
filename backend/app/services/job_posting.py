"""The fixed 30-day job posting window and its visibility rules (spec §2).

Every job is live for EXACTLY 30 days, then allows 5 more days in which people
who already applied can still edit. The window is not configurable and a
recruiter cannot move it: `jobs.posting_end_date` and
`jobs.grace_period_end_date` are GENERATED columns (migration 0018), so the
database itself rejects an UPDATE against them.

    day 1-30   active        visible, applications open, public link works
    day 31-35  grace_period  hidden from new candidates, public link 404s,
                             existing applicants may still edit
    day 36+    expired       gone for candidates; recruiters keep read access

CLOSURE CUTS THE WINDOW SHORT, AND IT IS A FIFTH STATE
--------------------------------------------------------
The 30 days are the LONGEST a posting runs, never the shortest. A client whose
requirement is filled on day 18 closes the job then (`jobs.closed_at`), and
`closed` dominates every date-derived state: the public link stops working, no
new application is accepted, and the job leaves every candidate's board. It is
a separate state rather than a back-dated `posting_start_date`, because moving
the start would silently rewrite which candidates count as Old Profiles and
which applications were made "in window", and both of those are read as
history.

Closure is terminal for CANDIDATES and changes nothing for the hiring TEAM:
every application already in the pipeline stays visible, rankable and
progressable. The five-day edit tail is not offered after a closure, because
the tail exists so an applicant can improve a submission still under
consideration, and a filled requirement is not.

WHY EVERYTHING HERE IS A PURE FUNCTION
--------------------------------------
These rules decide whether a person can see a job at all, so getting one
boundary wrong is invisible in testing and severe in production — a candidate
silently loses access, or keeps it when they should not. Every rule below is a
pure function of (timestamps, now) with no I/O, so each boundary is asserted
directly in tests/test_job_posting.py rather than inferred from an endpoint.

The three-state calculation is duplicated in SQL as the `job_posting_state`
view for queries that filter on it. The two MUST agree; `posting_status` and
the view are written from the same four-branch shape for that reason.

BOUNDARIES ARE INCLUSIVE AT THE END OF EACH WINDOW
--------------------------------------------------
An instant exactly ON `posting_end_date` is still ACTIVE, and an instant
exactly on `grace_period_end_date` is still in GRACE. Ties go to the candidate:
someone hitting Apply at the last second gets in. This matches claude.md rule 8
(tier boundaries are inclusive upward) — the codebase resolves boundary ties
one consistent way.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

#: The fixed window. Business constants, deliberately NOT configuration —
#: the spec is explicit that no recruiter can vary them. Mirrored by
#: migration 0018's generated columns.
POSTING_DAYS = 30
GRACE_DAYS = 5

STATUS_SCHEDULED = "scheduled"
STATUS_ACTIVE = "active"
STATUS_GRACE = "grace_period"
STATUS_EXPIRED = "expired"
#: Closed early by the client because the hiring requirement was met
#: (`jobs.closed_at`). Dominates the four date-derived states.
STATUS_CLOSED = "closed"


def _aware(value: datetime | None) -> datetime | None:
    """Read a naive timestamp as UTC.

    Every timestamp in this database is UTC; treating a naive one as local time
    would move a boundary by hours, which on a 30-day window is the difference
    between a candidate getting in and being refused.
    """
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def posting_end(posting_start: datetime) -> datetime:
    """The end of the active window. Mirrors the generated column."""
    return _aware(posting_start) + timedelta(days=POSTING_DAYS)


def grace_end(posting_start: datetime) -> datetime:
    """The end of the grace period. Mirrors the generated column."""
    return _aware(posting_start) + timedelta(days=POSTING_DAYS + GRACE_DAYS)


def posting_status(
    posting_start: datetime | None,
    posting_end_date: datetime | None = None,
    grace_period_end_date: datetime | None = None,
    now: datetime | None = None,
    closed_at: datetime | None = None,
) -> str:
    """Which of the five lifecycle states this job is in right now.

    `posting_end_date` / `grace_period_end_date` are accepted so a caller that
    already loaded the generated columns does not recompute them; when omitted
    they are derived from `posting_start`.

    A job with no `posting_start_date` at all reads as EXPIRED rather than
    active — an unknown window must fail closed, not grant access.

    `closed_at` is checked FIRST and wins outright. A closure is a decision a
    person made about this requisition, and no arithmetic over dates may
    reinstate it: checking it after the window would leave a job closed on day
    18 reading as `active` for twelve more days.
    """
    now = _aware(now) or datetime.now(timezone.utc)
    closed = _aware(closed_at)
    if closed is not None and now >= closed:
        return STATUS_CLOSED
    start = _aware(posting_start)
    if start is None:
        return STATUS_EXPIRED
    end = _aware(posting_end_date) or posting_end(start)
    grace = _aware(grace_period_end_date) or grace_end(start)

    if now < start:
        return STATUS_SCHEDULED
    if now <= end:
        return STATUS_ACTIVE
    if now <= grace:
        return STATUS_GRACE
    return STATUS_EXPIRED


def is_active(*args: Any, **kwargs: Any) -> bool:
    """True only during the 30-day active window."""
    return posting_status(*args, **kwargs) == STATUS_ACTIVE


def days_remaining(end: datetime | None, now: datetime | None = None) -> int:
    """Whole days from `now` until `end`, floored at 0.

    Used for the "Edit window closes in X days" copy. Rounded UP so a window
    closing in six hours reads as "1 day" rather than "0 days" — telling
    someone they have zero days left while they still have time to act is the
    worse of the two errors.
    """
    end = _aware(end)
    if end is None:
        return 0
    now = _aware(now) or datetime.now(timezone.utc)
    seconds = (end - now).total_seconds()
    if seconds <= 0:
        return 0
    return max(1, -(-int(seconds) // 86400))


# ── Candidate visibility (spec §2.2) ─────────────────────────────────────────

def candidate_registered_in_time(
    candidate_created_at: datetime | None,
    posting_end_date: datetime | None,
) -> bool:
    """Spec Rule 3: someone who registered after the active window closed can
    NEVER see this job — not in their board, not in search, not by direct URL.

    A candidate with no `created_at` is treated as eligible: refusing access
    because a timestamp is missing would lock a real person out over a data
    defect, and the active-window check still applies on top of this.
    """
    created = _aware(candidate_created_at)
    end = _aware(posting_end_date)
    if created is None or end is None:
        return True
    return created <= end


def can_view_job(
    *,
    posting_start: datetime | None,
    posting_end_date: datetime | None = None,
    grace_period_end_date: datetime | None = None,
    candidate_created_at: datetime | None = None,
    has_applied: bool = False,
    now: datetime | None = None,
    closed_at: datetime | None = None,
) -> bool:
    """Whether a signed-in candidate may see this job at all.

    Two independent gates, both of which must pass:

      * they registered on or before the day the active window closed
        (spec Rule 3 — a late registrant never sees the job, even during the
        grace period), and
      * the job is currently active, OR they already applied and it is still
        within the grace period.

    An applicant keeps read access through the grace period so they can act on
    the edit window; after it, the job disappears for everyone.
    """
    status = posting_status(
        posting_start, posting_end_date, grace_period_end_date, now, closed_at
    )
    end = _aware(posting_end_date) or (
        posting_end(posting_start) if posting_start else None
    )
    if not candidate_registered_in_time(candidate_created_at, end):
        return False
    if status == STATUS_ACTIVE:
        return True
    if status == STATUS_GRACE:
        return has_applied
    return False


def can_apply(
    *,
    posting_start: datetime | None,
    posting_end_date: datetime | None = None,
    grace_period_end_date: datetime | None = None,
    candidate_created_at: datetime | None = None,
    now: datetime | None = None,
    closed_at: datetime | None = None,
) -> bool:
    """Whether a NEW application may be submitted.

    Strictly the 30-day active window, and never after a closure. The grace
    period is for editing an application that already exists, never for
    creating one.
    """
    end = _aware(posting_end_date) or (
        posting_end(posting_start) if posting_start else None
    )
    if not candidate_registered_in_time(candidate_created_at, end):
        return False
    return (
        posting_status(
            posting_start, posting_end_date, grace_period_end_date, now, closed_at
        )
        == STATUS_ACTIVE
    )


def public_link_active(
    posting_start: datetime | None,
    posting_end_date: datetime | None = None,
    now: datetime | None = None,
    closed_at: datetime | None = None,
) -> bool:
    """Spec Rule 2: an externally shared link works during the 30-day window
    and 404s afterwards — including throughout the grace period, which is for
    existing applicants only and grants nothing to an anonymous visitor. A
    closed job's link 404s from the moment it is closed, which is the whole
    point of closing one early."""
    return (
        posting_status(posting_start, posting_end_date, None, now, closed_at)
        == STATUS_ACTIVE
    )


def can_edit_application(
    *,
    applied_at: datetime | None,
    posting_start: datetime | None,
    posting_end_date: datetime | None = None,
    grace_period_end_date: datetime | None = None,
    now: datetime | None = None,
) -> bool:
    """Spec Rule 4 / §5.1: the 5-day edit window.

    THIS ONE DELIBERATELY DOES NOT TAKE `closed_at`, unlike every other rule in
    this module. Closure stops INTAKE: no new applications, no public link, the
    job off every board. It does not evict the candidates already in the
    pipeline, and this predicate is what lets one of them finish an assessment
    they were already invited to and correct an application the team is still
    reading. Cutting that off would destroy work the client has already been
    charged for, on the day they told us the requisition went well.

    Editable while the job is ACTIVE (the application is still open) and
    throughout the GRACE period, provided the application was actually made
    during the active window. An application created outside that window
    should not exist, and if one somehow does it does not earn an edit right.
    """
    applied = _aware(applied_at)
    end = _aware(posting_end_date) or (
        posting_end(posting_start) if posting_start else None
    )
    if applied is None or end is None:
        return False
    if applied > end:
        return False
    return posting_status(
        posting_start, posting_end_date, grace_period_end_date, now
    ) in (STATUS_ACTIVE, STATUS_GRACE)


@dataclass
class PostingWindow:
    """The lifecycle facts about one job, ready to serialise to a client."""
    posting_start_date: datetime | None
    posting_end_date: datetime | None
    grace_period_end_date: datetime | None
    posting_status: str
    days_until_posting_ends: int
    days_until_grace_ends: int
    #: When the client closed this job early because the requirement was met.
    closed_at: datetime | None = None
    #: What they said about why, verbatim. Never interpreted, never scored.
    closed_reason: str | None = None

    @property
    def is_active(self) -> bool:
        return self.posting_status == STATUS_ACTIVE

    @property
    def is_closed(self) -> bool:
        return self.posting_status == STATUS_CLOSED

    @property
    def accepts_applications(self) -> bool:
        return self.is_active

    def summary(self) -> str:
        """The one-line description shown on the recruiter's job page."""
        if self.posting_status == STATUS_CLOSED:
            return (
                "Closed. The requirement was met, so no new applications are "
                "accepted. Your team keeps every candidate already in the "
                "pipeline."
            )
        if self.posting_status == STATUS_ACTIVE:
            return (
                f"Live for {self.days_until_posting_ends} more "
                f"{'day' if self.days_until_posting_ends == 1 else 'days'}. "
                "Applicants can edit for 5 days after that."
            )
        if self.posting_status == STATUS_GRACE:
            return (
                "Posting closed, no new applications. Existing applicants can "
                f"edit for {self.days_until_grace_ends} more "
                f"{'day' if self.days_until_grace_ends == 1 else 'days'}."
            )
        if self.posting_status == STATUS_SCHEDULED:
            return "Not yet live."
        return "Expired. Visible to your team only; candidates can no longer see it."


def describe(job: Any, now: datetime | None = None) -> PostingWindow:
    """Build the PostingWindow for a job row (ORM object or mapping)."""
    def field(name: str) -> Any:
        if isinstance(job, dict):
            return job.get(name)
        return getattr(job, name, None)

    start = _aware(field("posting_start_date"))
    end = _aware(field("posting_end_date")) or (posting_end(start) if start else None)
    grace = _aware(field("grace_period_end_date")) or (
        grace_end(start) if start else None
    )
    closed = _aware(field("closed_at"))
    return PostingWindow(
        posting_start_date=start,
        posting_end_date=end,
        grace_period_end_date=grace,
        posting_status=posting_status(start, end, grace, now, closed),
        days_until_posting_ends=days_remaining(end, now),
        days_until_grace_ends=days_remaining(grace, now),
        closed_at=closed,
        closed_reason=field("closed_reason"),
    )
