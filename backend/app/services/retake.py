"""The six-month window, and the Portable plus Job Specific split it now carries.

~~REUSE IS RETIRED (2026-07-30).~~ **SUPERSEDED 2026-09-22, OWNER RULING:
change request 23, the full Portable plus Job Specific intelligence split.**
The supersession is marked rather than deleted, because the retired rule was
RIGHT about the thing it was protecting and the new rule only works by
continuing to protect it.

WHAT THE 2026-07-30 RULE SAID, AND WHY IT WAS CORRECT
-------------------------------------------------------
Reuse made sense under PFI, where the behavioural dimension set was FIXED for
the whole product: a candidate graded on "Learning agility" for one job had
been graded on exactly the criterion the next job would use, so copying the
result asserted nothing that had not been assessed.

PPI generates a fresh evaluation framework FROM EACH JOB'S OWN JD (spec §6.2).
Job A's Must-haves, Nice-to-haves and Behavioural Competencies are not job B's.
Copying a report across would state a grade against criteria the candidate was
never assessed on, the identical error that has always kept the MATCHING
section from travelling, now true of the whole report.

WHAT CHANGED, AND WHAT DID NOT
--------------------------------
The owner ruled on 2026-09-22 that the product carries two layers:

    PORTABLE INTELLIGENCE      career history, education, employer-confirmed
                               employment, factual profile information,
                               resume-derived core skills. Reusable.
    JOB SPECIFIC INTELLIGENCE  role-specific capability as THIS matrix defines
                               it, every behavioural dimension, leadership
                               requirements, scenario responses, and this
                               employer's own strategic alignment. Always
                               freshly assessed.

The reversal is narrow and it is exactly the distinction the retired rule was
missing: **what travels is the EVIDENCE, never the VERDICT.** A prior job's
score or grade is still never reused, for anything, and the paragraph above is
still the reason. What travels is the raw fact underneath, which the new job's
matrix then judges for itself with its own required level and its own rubric.

So `PORTABLE_CATEGORIES` is STILL an empty frozenset and `copy_report` still
copies nothing. No report SECTION travels; that half of the 2026-07-30 ruling
is untouched, and `app/scripts/eval_report.py` still gates on it. The store
that does travel is `portable_evidence_items`, which has no column a grade
could be written into. See `services/portable_evidence.py`.

THE WINDOW IS NOW LOAD-BEARING RATHER THAN EXPLANATORY
--------------------------------------------------------
The retired docstring said the window "is the natural place to reinstate reuse
if the product ever regains a product-wide dimension set". That is what has
happened, one layer lower than expected: the product has regained a
product-wide EVIDENCE set rather than a dimension set. So the same boundary
that decides whether a candidate is told "your previous assessment is old" now
decides whether a portable fact is fresh enough to stand in for asking.

    prior report completed  < 6 months ago  -> fresh assessment, and say why
    prior report completed >= 6 months ago  -> fresh assessment
    no prior report at all                  -> fresh assessment

Every application still runs its own assessment. What the split changes is
which QUESTIONS it asks, never whether it happens.

Boundary: exactly six months old is a RETAKE, and the window is the
strictly-less-than side, so the rule never silently extends itself.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import FunctionalSkillsReport, JobCompetency
from app.models.candidate import Candidate, JobCandidateLink
from app.services import consent_catalog, portable_evidence

logger = logging.getLogger(__name__)

#: The window, in days. 183 = half of 365, rounded up, so "six months" does not
#: drift by a day or two depending on which months the period happens to span.
RETAKE_WINDOW_DAYS = 183

DECISION_REUSE = "reuse"
DECISION_RETAKE = "retake"
DECISION_FIRST_ASSESSMENT = "first_assessment"

#: NO REPORT SECTION TRAVELS, AND THE 2026-09-22 RULING DOES NOT REOPEN THIS.
#: Under PPI every section of a report is a GRADE scoped to the job it was
#: written for. The Portable layer carries evidence, which is a different
#: thing, and it carries it in a different table with no column a grade could
#: occupy. Kept as an explicit EMPTY set rather than deleted, so `copy_report`
#: refuses loudly instead of a future caller rediscovering section reuse by
#: accident, and `app/scripts/eval_report.py` still gates CI on its emptiness.
PORTABLE_CATEGORIES: frozenset[str] = frozenset()


@dataclass
class RetakeDecision:
    """The outcome of the rule, plus everything the UI needs to explain it."""
    decision: str                       # reuse | retake | first_assessment
    source_report_id: uuid.UUID | None = None
    source_job_id: uuid.UUID | None = None
    assessed_at: datetime | None = None
    age_days: int | None = None
    #: The Portable plus Job Specific split for THIS job, or None when it could
    #: not be computed. None is a real and distinct state from an empty
    #: coverage: empty means the record establishes nothing, None means we did
    #: not manage to look, and the two must not produce the same sentence.
    coverage: portable_evidence.Coverage | None = None
    #: Why coverage is None, when it is. Recorded rather than swallowed: a
    #: degradation nobody can see is the silent fallback rule 6 forbids.
    coverage_unavailable_reason: str = ""

    @property
    def requires_new_assessment(self) -> bool:
        # Always true, before and after the 2026-09-22 ruling. The split
        # changes which QUESTIONS an assessment asks; it never removes the
        # assessment. The property is kept because callers read it rather than
        # the string.
        return True

    @property
    def age_months(self) -> int | None:
        """Whole months since the prior assessment, for candidate-facing copy."""
        if self.age_days is None:
            return None
        return self.age_days * 12 // 365

    def message(self) -> str | None:
        """The sentence shown to the candidate. None when there is nothing to
        explain (a first assessment with nothing established needs no preamble).

        THE COVERAGE SENTENCE LEADS, when there is one. It is the concrete
        half: it names what the platform already holds, which the candidate can
        check and correct. The age sentence that follows is the explanation for
        why they are still answering questions, and it reads as an excuse
        rather than a courtesy if it arrives before the good news.

        NAMES, NEVER COUNTS, in both halves. "3 of 12 criteria covered" is a
        number reaching a client, and it is also the less useful sentence: a
        name tells them what we think we know.
        """
        coverage_line = (
            portable_evidence.coverage_sentence(self.coverage)
            if self.coverage is not None
            else None
        )
        if self.decision == DECISION_FIRST_ASSESSMENT:
            return coverage_line
        if self.decision == DECISION_REUSE:
            age_line = (
                "You completed an assessment recently. The questions are "
                "written for each specific role, so this one has its own."
            )
        else:
            months = self.age_months
            age = f"{months} months old" if months else "from a while ago"
            age_line = (
                f"Your previous assessment is {age}, so you'll complete a fresh "
                "one for this role."
            )
        return f"{coverage_line} {age_line}" if coverage_line else age_line


def classify_age(assessed_at: datetime | None, now: datetime) -> tuple[str, int | None]:
    """Pure rule: (decision, age_in_days) for a prior assessment timestamp.

    Naive timestamps are read as UTC, because a stored value without a timezone is
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


async def load_coverage(
    session: AsyncSession,
    candidate_id: uuid.UUID,
    job_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> portable_evidence.Coverage:
    """The Portable plus Job Specific split of THIS job's matrix for THIS person.

    CONSENT IS CHECKED FIRST, AND A REFUSAL IS AN EMPTY COVERAGE RATHER THAN AN
    ERROR. `consent_catalog.cross_employer_reuse_allowed` is the one authority
    and answers from the catalogue row whose wording actually names this
    purpose; a missing row refuses, so a candidate who declined the optional
    item and one who has not been asked are treated alike. Either way they get
    the product's behaviour from before this feature existed, which is every
    criterion assessed fresh. That is never wrong, only slower, so there is
    nothing to raise about.

    Reads the job's ACTIVE matrix rows. Deliberately not the frozen scorecard:
    this runs at apply time, which can precede the freeze, and what the
    candidate is being told is which areas they will be asked about. The
    scoring path re-derives coverage from the same function against whatever
    the matrix holds when questions are generated, so nothing here is relied on
    as a decision.
    """
    if not await consent_catalog.cross_employer_reuse_allowed(session, candidate_id):
        return portable_evidence.Coverage()

    criteria = (
        await session.execute(
            select(JobCompetency)
            .where(
                JobCompetency.job_id == job_id,
                JobCompetency.is_active.is_(True),
            )
            .order_by(JobCompetency.category, JobCompetency.ordinal)
        )
    ).scalars().all()
    if not criteria:
        return portable_evidence.Coverage()

    facts = await portable_evidence.load_for_candidate(session, candidate_id)
    return portable_evidence.coverage(
        list(criteria), facts, max_age_days=RETAKE_WINDOW_DAYS, now=now
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
    A report already existing for THIS job is ignored, because that is a re-entry into
    an assessment already in progress, not a retake question.

    THE REPORT ROW IS READ FOR ITS TIMESTAMP AND NOTHING ELSE. `synthesized_at`
    and the two ids are the only fields touched, and no score, grade or
    dimension from it reaches the returned decision or the coverage split: the
    coverage is computed from `portable_evidence_items`, which has no column a
    grade could occupy. That separation is the 2026-09-22 ruling's whole
    safety property and `tests/test_portable_evidence.py` pins it.
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

    # Computed for every decision, including a first assessment: a candidate
    # who has never been assessed here can still have a portable record, from
    # their own profile and from an employer confirmation, and telling them
    # what it establishes is the point of the feature.
    #
    # A FAILURE HERE IS RECORDED AND NEVER RAISED. One of the two callers
    # (`portal.apply`) is not wrapped, and this runs after the application row
    # has been written; letting a coverage read cost somebody their submission
    # would be the wrong trade by a wide margin. The decision then carries
    # `coverage=None`, which produces no coverage sentence at all rather than
    # a sentence claiming nothing was established, and the reason is logged.
    coverage: portable_evidence.Coverage | None = None
    reason = ""
    try:
        coverage = await load_coverage(session, candidate_id, job_id, now=now)
    except Exception as exc:  # noqa: BLE001 - recorded, never silent
        reason = type(exc).__name__
        logger.warning(
            "retake.coverage_unavailable candidate_id=%s job_id=%s reason=%s",
            candidate_id, job_id, reason, exc_info=True,
        )

    if row is None:
        return RetakeDecision(
            decision=DECISION_FIRST_ASSESSMENT,
            coverage=coverage,
            coverage_unavailable_reason=reason,
        )

    decision, age_days = classify_age(row.synthesized_at, now)
    return RetakeDecision(
        decision=decision,
        source_report_id=row.id,
        source_job_id=row.job_id,
        assessed_at=row.synthesized_at,
        age_days=age_days,
        coverage=coverage,
        coverage_unavailable_reason=reason,
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
