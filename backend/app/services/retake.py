"""The six-month assessment retake rule.

REUSE IS RETIRED (2026-07-30). What survives is the age classification and the
sentence the candidate reads; every application now runs its own assessment.

Why the rule changed
--------------------
Reuse made sense under PFI, where the behavioural dimension set was FIXED for
the whole product: a candidate graded on "Learning agility" for one job had
been graded on exactly the criterion the next job would use, so copying the
result asserted nothing that had not been assessed.

PPI generates a fresh evaluation framework FROM EACH JOB'S OWN JD (spec §6.2).
Job A's Primary Skills, Secondary Skills and Behavioural Competencies are not
job B's. Copying a report across would state a grade against criteria the
candidate was never assessed on — the identical error that has always kept the
MATCHING section from travelling, now true of the whole report.

Technical does not save it either: the technical bank is generated per job from
that job's JD, so a technical grade is equally job-scoped.

The classification still runs, because the candidate is still owed an
explanation for why they are answering questions again, and because the window
is the natural place to reinstate reuse if the product ever regains a
product-wide dimension set.

    prior report completed  < 6 months ago  -> fresh assessment, and say why
    prior report completed >= 6 months ago  -> fresh assessment
    no prior report at all                  -> fresh assessment

Boundary: exactly six months old is a RETAKE — the window is the
strictly-less-than side, so the rule never silently extends itself.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import FunctionalSkillsReport
from app.models.candidate import JobCandidateLink

logger = logging.getLogger(__name__)

#: The window, in days. 183 = half of 365, rounded up, so "six months" does not
#: drift by a day or two depending on which months the period happens to span.
RETAKE_WINDOW_DAYS = 183

DECISION_REUSE = "reuse"
DECISION_RETAKE = "retake"
DECISION_FIRST_ASSESSMENT = "first_assessment"

#: Nothing travels between applications any more: under PPI every section of a
#: report is scoped to the job it was written for. Kept as an explicit EMPTY
#: set rather than deleted, so `copy_report` refuses loudly instead of a future
#: caller rediscovering reuse by accident.
PORTABLE_CATEGORIES: frozenset[str] = frozenset()


@dataclass
class RetakeDecision:
    """The outcome of the rule, plus everything the UI needs to explain it."""
    decision: str                       # reuse | retake | first_assessment
    source_report_id: uuid.UUID | None = None
    source_job_id: uuid.UUID | None = None
    assessed_at: datetime | None = None
    age_days: int | None = None

    @property
    def requires_new_assessment(self) -> bool:
        # Always true now: reuse is retired (see the module docstring). The
        # property is kept because callers read it rather than the string.
        return True

    @property
    def age_months(self) -> int | None:
        """Whole months since the prior assessment, for candidate-facing copy."""
        if self.age_days is None:
            return None
        return self.age_days * 12 // 365

    def message(self) -> str | None:
        """The sentence shown to the candidate. None when there is nothing to
        explain (a first assessment needs no preamble)."""
        if self.decision == DECISION_FIRST_ASSESSMENT:
            return None
        if self.decision == DECISION_REUSE:
            return (
                "You completed an assessment recently, but the questions are "
                "written for each specific role, so this one has its own."
            )
        months = self.age_months
        age = f"{months} months old" if months else "from a while ago"
        return (
            f"Your previous assessment is {age}, so you'll complete a fresh one "
            "for this role."
        )


def classify_age(assessed_at: datetime | None, now: datetime) -> tuple[str, int | None]:
    """Pure rule: (decision, age_in_days) for a prior assessment timestamp.

    Naive timestamps are read as UTC — a stored value without a timezone is
    always UTC in this database, and treating it as local time would shift the
    boundary by hours. Unit-tested in tests/test_retake.py.
    """
    if assessed_at is None:
        return DECISION_FIRST_ASSESSMENT, None
    if assessed_at.tzinfo is None:
        assessed_at = assessed_at.replace(tzinfo=timezone.utc)
    age_days = (now - assessed_at).days
    if age_days < 0:
        # A clock-skewed future timestamp is not evidence of a recent
        # assessment; treat it as fresh (age 0) rather than trusting it.
        age_days = 0
    return (
        DECISION_REUSE if age_days < RETAKE_WINDOW_DAYS else DECISION_RETAKE,
        age_days,
    )


async def decide(
    session: AsyncSession,
    candidate_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> RetakeDecision:
    """Apply the rule for one candidate applying to one job.

    Looks for the candidate's most recent completed report on any OTHER job.
    A report already existing for THIS job is ignored — that is a re-entry into
    an assessment already in progress, not a retake question.
    """
    now = now or datetime.now(timezone.utc)
    row = (
        await session.execute(
            select(FunctionalSkillsReport)
            .join(
                JobCandidateLink,
                JobCandidateLink.id == FunctionalSkillsReport.job_candidate_link_id,
            )
            .where(
                JobCandidateLink.candidate_id == candidate_id,
                FunctionalSkillsReport.job_id != job_id,
                FunctionalSkillsReport.status == "ready",
            )
            .order_by(FunctionalSkillsReport.synthesized_at.desc())
            .limit(1)
        )
    ).scalars().first()

    if row is None:
        return RetakeDecision(decision=DECISION_FIRST_ASSESSMENT)

    decision, age_days = classify_age(row.synthesized_at, now)
    return RetakeDecision(
        decision=decision,
        source_report_id=row.id,
        source_job_id=row.job_id,
        assessed_at=row.synthesized_at,
        age_days=age_days,
    )


async def copy_report(
    session: AsyncSession,
    source_report_id: uuid.UUID,
    target_link: JobCandidateLink,
) -> FunctionalSkillsReport | None:
    """RETIRED (2026-07-30). Never copies a report; returns the target's own
    report if it already has one, otherwise None.

    Under PPI every section of a report is scoped to the job it was written
    for, so there is nothing left that can honestly be carried across (see the
    module docstring). The function is kept, and kept safe to call, because
    removing it would turn a superseded behaviour into an AttributeError in any
    caller that has not been updated yet.
    """
    existing = (
        await session.execute(
            select(FunctionalSkillsReport).where(
                FunctionalSkillsReport.job_candidate_link_id == target_link.id
            )
        )
    ).scalars().first()
    if existing is not None:
        return existing

    logger.info(
        "retake.copy_report_retired source_report_id=%s target_link_id=%s, "
        "a fresh assessment will run instead",
        source_report_id, target_link.id,
    )
    return None
