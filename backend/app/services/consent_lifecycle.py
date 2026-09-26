"""Consent renewal and the inactivity rule, as pure functions.

`VIVEKIUM_SPRINT_FEATURES.md` feature 8. Every value here is DERIVED from
timestamps on the candidate row and never stored, the same discipline
`job_posting.posting_status`, `profile_age` and `bgv_workflow.derive_status`
already follow: a stored status is a second copy of something the row already
determines, and the two can disagree.

WHY THIS MODULE HOLDS NO DELETION AND NO EMAIL
-----------------------------------------------
It answers one question, `stage_for`, and the sweep decides what to do about
the answer. That split is what makes the dangerous half testable without a
database, a mail provider or a scheduler, and it is why the thresholds can be
checked at their exact boundaries rather than inferred from behaviour.

THE BRIEF CONTRADICTS ITSELF BY ONE STEP, AND THIS IS THE RESOLUTION
---------------------------------------------------------------------
Feature 8 says a reminder goes out at six months, the grace period is fifteen
days, and then: "If no response in 15 days: second email sent, states that the
profile will be permanently deleted automatically if no action taken", and
separately "Auto-deletion trigger: after 15-day grace period with no consent
renewal".

Read literally, those two put the final warning and the deletion at the same
instant, which would send somebody a letter warning them of something that had
already happened. ASSUMPTION, per claude.md section 8: the final warning opens
a SECOND window of the same length, and deletion happens at the end of it. The
letter is then true when it is sent, which is the only reading under which the
brief's own sentence means anything.

THE CLOCKS ARE INDEPENDENT, WHICH IS THE POINT OF C6
------------------------------------------------------
Consent expiry and inactivity are two different facts about a person and are
evaluated separately. The obvious implementation, one sweep with an OR, deletes
a candidate who renewed last week because some engagement column was never
written. `stage_for` returns the CONSENT stage; `is_dormant` answers the
inactivity question; the caller that deletes records WHICH of the two applied,
so an erasure receipt can always say why.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

#: Every stage a candidate's consent can be in, ordered by how far through the
#: lifecycle they are so a reader can see the sequence.
STAGE_ACTIVE = "active"
STAGE_REMINDER_DUE = "reminder_due"
STAGE_FINAL_WARNING_DUE = "final_warning_due"
STAGE_DELETION_DUE = "deletion_due"

ALL_STAGES: frozenset[str] = frozenset(
    {STAGE_ACTIVE, STAGE_REMINDER_DUE, STAGE_FINAL_WARNING_DUE, STAGE_DELETION_DUE}
)

#: Why a candidate is being erased. Recorded on the audit row, because
#: "deleted" with no reason is a fact nobody can answer a complaint with.
REASON_CONSENT_EXPIRED = "consent_not_renewed"
REASON_DORMANT = "inactive_for_the_retention_period"


@dataclass(frozen=True)
class Thresholds:
    """The three numbers, passed in rather than read from settings here.

    Injected so the boundary tests can state a threshold and assert exactly on
    it, and so this module imports no configuration and can be reasoned about
    on its own.
    """

    renewal_months: int
    grace_days: int
    inactivity_months: int

    def __post_init__(self) -> None:
        # A zero or negative window would put every candidate past every
        # threshold the moment they registered. Refused loudly here rather than
        # silently erasing the entire databank on the next sweep.
        for name in ("renewal_months", "grace_days", "inactivity_months"):
            if getattr(self, name) <= 0:
                raise ValueError(
                    f"{name} must be positive; a non-positive window would put "
                    "every candidate past its threshold immediately"
                )


def _months(count: int) -> timedelta:
    """Calendar months are not a timedelta, so this states the approximation.

    30 days, deliberately crude and deliberately visible. The alternative is
    `dateutil.relativedelta`, and the extra precision would buy nothing: every
    threshold here is measured in months for human readability rather than to
    land on a particular calendar date, and a candidate is never worse off by
    more than a few days either way. Stated as a function so the choice lives
    in one place rather than being repeated as a literal.
    """
    return timedelta(days=30 * count)


def renewal_due_at(*, consented_at: datetime, thresholds: Thresholds) -> datetime:
    """When this candidate's consent needs renewing."""
    return consented_at + _months(thresholds.renewal_months)


def stage_for(
    *,
    now: datetime,
    consented_at: datetime,
    reminder_sent_at: datetime | None,
    final_warning_sent_at: datetime | None,
    thresholds: Thresholds,
) -> str:
    """Where this candidate is in the renewal cycle.

    `consented_at` is the later of registration and the last renewal, resolved
    by the caller, so this function never has to know which it was given.

    THE STAGES ARE GATED ON THE LETTERS HAVING ACTUALLY BEEN SENT, not only on
    elapsed time, and that is the property that makes the sweep safe to run
    late. If the scheduler is down for a month, a candidate does not skip from
    active straight to deletion_due: the reminder is still owed, it is sent on
    the next sweep, and the grace window starts from THAT moment. A time-only
    rule would erase people for not answering a letter nobody sent.
    """
    if now < renewal_due_at(consented_at=consented_at, thresholds=thresholds):
        return STAGE_ACTIVE
    if reminder_sent_at is None:
        return STAGE_REMINDER_DUE

    grace = timedelta(days=thresholds.grace_days)
    if now < reminder_sent_at + grace:
        return STAGE_ACTIVE
    if final_warning_sent_at is None:
        return STAGE_FINAL_WARNING_DUE

    # The second window, per the ASSUMPTION in the module docstring: the final
    # warning has to be true when it is sent, so deletion waits out another
    # grace period after it.
    if now < final_warning_sent_at + grace:
        return STAGE_ACTIVE
    return STAGE_DELETION_DUE


#: Where a candidate is on the INACTIVITY clock. Named apart from the consent
#: stages on purpose: the two clocks are independent (C6) and a shared
#: vocabulary would invite a caller to compare one against the other.
DORMANCY_ACTIVE = "dormancy_active"
DORMANCY_WARNING_DUE = "dormancy_warning_due"
DORMANCY_DELETION_DUE = "dormancy_deletion_due"

ALL_DORMANCY_STAGES: frozenset[str] = frozenset(
    {DORMANCY_ACTIVE, DORMANCY_WARNING_DUE, DORMANCY_DELETION_DUE}
)


def dormancy_stage_for(
    *,
    now: datetime,
    last_engagement_at: datetime,
    warning_sent_at: datetime | None,
    thresholds: Thresholds,
) -> str:
    """Where this candidate is on the inactivity clock.

    WHAT THIS REPLACES, AND WHY IT HAD TO. `is_dormant` answers one question
    and the sweep used to erase on the strength of it alone: dormant, deleted,
    with no letter, no warning and no window. The specification asks for a
    deletion warning, then a grace period, then deletion, and the shape below
    is the consent path's, for the reason that path already records.

    THE GRACE RUNS FROM THE LETTER, NOT FROM THE DUE DATE. Gating the stage on
    `warning_sent_at` rather than on elapsed time is what makes the sweep safe
    to run late: if the scheduler is down for a month, a candidate does not
    skip from active straight to deletion. The warning is still owed, it goes
    out on the next sweep, and the window starts from THAT moment. A time-only
    rule erases people for not answering a letter nobody sent, which is the one
    failure this feature cannot recover from.

    ONE WINDOW HERE, TWO ON THE CONSENT CLOCK, and the asymmetry is the brief
    rather than an oversight. Feature 8 names two consent letters, a reminder
    and then a final warning, so `stage_for` opens a second window to keep the
    final warning's own sentence true when it is sent. The inactivity rule
    names ONE letter, so there is one window. Adding a second here would
    silently double the time a dormant profile is retained, which is a
    retention decision and not a symmetry decision.

    A WARNING OLDER THAN THE LAST ENGAGEMENT IS SPENT. `dormancy_warning_sent_at`
    is a latch and nothing clears it, so a candidate who was warned, came back,
    and then went quiet again would otherwise be read as already warned and
    erased with no second letter at all. Comparing the two timestamps settles
    that here rather than requiring every writer of `last_engagement_at` to
    remember to clear a column it has no other reason to know about.
    """
    if not is_dormant(
        now=now, last_engagement_at=last_engagement_at, thresholds=thresholds
    ):
        return DORMANCY_ACTIVE
    if warning_sent_at is None or warning_sent_at <= last_engagement_at:
        return DORMANCY_WARNING_DUE
    if now < warning_sent_at + timedelta(days=thresholds.grace_days):
        return DORMANCY_ACTIVE
    return DORMANCY_DELETION_DUE


def is_dormant(
    *, now: datetime, last_engagement_at: datetime, thresholds: Thresholds
) -> bool:
    """Whether this candidate has been inactive long enough to erase.

    Evaluated INDEPENDENTLY of the consent stage, per C6. A candidate who
    renews every six months and never otherwise touches the platform is still
    dormant at the inactivity threshold, and a candidate who applied yesterday
    is not dormant however overdue their consent is.

    The brief counts a job-matching email the candidate RECEIVES as engagement,
    which is unusual and deliberate: it resets the clock for somebody the
    platform is still actively putting in front of employers. Recording that is
    the caller's job; this function only compares the timestamp it is given.
    """
    return now >= last_engagement_at + _months(thresholds.inactivity_months)


def consented_at_for(*, created_at: datetime, renewed_at: datetime | None) -> datetime:
    """The moment the current consent period began.

    NULL `renewed_at` means never renewed, which is the ordinary state of every
    candidate who registered less than a renewal cycle ago, so it reads through
    to registration rather than being treated as missing data.
    """
    return renewed_at or created_at


def engagement_at_for(
    *, created_at: datetime, last_engagement_at: datetime | None
) -> datetime:
    """The moment this candidate last did, or received, anything.

    Same reading-through rule: a candidate who registered and has done nothing
    since is dormant measured from REGISTRATION, not exempt for want of a
    timestamp. The alternative, treating NULL as "never dormant", would make
    the inactivity rule silently inapplicable to exactly the accounts it exists
    for.
    """
    return last_engagement_at or created_at


def utcnow() -> datetime:
    """One clock, so a test can monkeypatch a single name."""
    return datetime.now(timezone.utc)
