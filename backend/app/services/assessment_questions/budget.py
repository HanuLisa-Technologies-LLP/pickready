"""How many questions a candidate is asked, and of which kind.

PURE AND DETERMINISTIC. Nothing here reads the database, calls a model or
reads a clock, so two candidates on one job are given the same budget and the
same mix, which is what keeps their reports comparable, and a provider outage
cannot change either.

HOW MANY (`question_budget`)
----------------------------
One question per skill in the job's contract, and never fewer than the grade's
floor (`assessment_question_floor_<grade>`). The skills store caps a bucket at
five, so a budget is 8 to 15. This SUPERSEDES the per-grade ranges this module
held until 2026-09-25 (12 to 38 questions by grade and STEM flag) and the
early close that let a conversation stop inside the range: every item in the
plan is asked, so the mix below is what a candidate is actually served.

WHAT KIND (`mix`)
-----------------
Counted in QUESTIONS, never in time or weight. For a coding role the shares
are prose 0.7, coding 0.2 and objective 0.1 (multiple choice and
fill-in-the-blank); for any other role the coding share joins prose. The
budget is apportioned by LARGEST REMAINDER, and a tie in the remainders goes to
prose, then coding, then objective, so the result is total and the same on
every run: N=10 is 7/2/1, N=8 is 6/1/1, N=15 is 11/3/1.

WHO IS A CODING ROLE (`coding_eligibility`)
-------------------------------------------
Three conditions, each recorded by name when it fails, because "no coding
question" must never be indistinguishable from "a coding question was lost":
the job's STEM verdict, a computing occupation read from the title
(`stem_classification.is_computing_occupation`, so a Civil Engineer is STEM
and is not asked to program), and a sandbox that can run code
(`code_execution.is_enabled`). The sandbox is asked by the caller and passed
in, which keeps this module pure.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from app.core.config import get_settings
from app.services import stem_classification

__all__ = [
    "DEFAULT_GRADE",
    "GRADES",
    "CODING_EXCLUDED_DISABLED",
    "CODING_EXCLUDED_NOT_COMPUTING",
    "CODING_EXCLUDED_NOT_STEM",
    "CODING_EXCLUSION_REASONS",
    "CodingEligibility",
    "Mix",
    "coding_eligibility",
    "mix",
    "question_budget",
    "question_floor",
]

GRADES: tuple[str, ...] = ("non_managerial", "managerial", "leadership", "cxo")
DEFAULT_GRADE = "non_managerial"

#: Why a role was given no coding question. A closed vocabulary, recorded on
#: the conversation's composition record and read by the report's provenance.
CODING_EXCLUDED_NOT_STEM = "not_stem"
CODING_EXCLUDED_NOT_COMPUTING = "not_computing_occupation"
CODING_EXCLUDED_DISABLED = "code_execution_disabled"
CODING_EXCLUSION_REASONS: tuple[str, ...] = (
    CODING_EXCLUDED_NOT_STEM,
    CODING_EXCLUDED_NOT_COMPUTING,
    CODING_EXCLUDED_DISABLED,
)


def _grade(grade: str | None) -> str:
    return grade if grade in GRADES else DEFAULT_GRADE


def question_floor(grade: str | None) -> int:
    """The fewest questions an assessment at this grade may hold."""
    return int(getattr(get_settings(), f"assessment_question_floor_{_grade(grade)}"))


def question_budget(grade: str | None, skill_count: int) -> int:
    """How many questions: one per skill, and never fewer than the floor."""
    if skill_count < 0:
        raise ValueError("a skill count cannot be negative")
    return max(int(skill_count), question_floor(grade))


@dataclass(frozen=True)
class Mix:
    """How many questions of each family. Counts, never shares."""

    prose: int
    coding: int
    objective: int

    @property
    def total(self) -> int:
        return self.prose + self.coding + self.objective

    def as_dict(self) -> dict[str, int]:
        return {"prose": self.prose, "coding": self.coding, "objective": self.objective}


def mix(total: int, *, coding: bool) -> Mix:
    """Apportion `total` questions over the three families by largest remainder.

    `coding=False` moves the coding share onto prose, so a role that is not a
    coding role (or whose sandbox is down) is asked prose instead, never fewer
    questions.
    """
    if total < 0:
        raise ValueError("a question budget cannot be negative")
    settings = get_settings()
    # EXACT ARITHMETIC. The shares are decimals written by a person, and a
    # float remainder of 0.6000000000000005 against 0.6000000000000001 would
    # decide a "tie" by representation error instead of by the stated order.
    prose_share = Fraction(str(settings.assessment_share_prose))
    coding_share = Fraction(str(settings.assessment_share_coding))
    objective_share = Fraction(str(settings.assessment_share_objective))
    if not coding:
        prose_share += coding_share
        coding_share = Fraction(0)
    # The order of this tuple IS the tie-break: prose, then coding, then
    # objective. Python's sort is stable, so equal remainders keep it.
    shares = (("prose", prose_share), ("coding", coding_share), ("objective", objective_share))
    exact = {name: total * share for name, share in shares}
    counts = {name: int(exact[name]) for name, _ in shares}
    remaining = total - sum(counts.values())
    by_remainder = sorted(
        (name for name, share in shares if share > 0),
        key=lambda name: -(exact[name] - counts[name]),
    )
    for name in by_remainder[:remaining]:
        counts[name] += 1
    result = Mix(prose=counts["prose"], coding=counts["coding"], objective=counts["objective"])
    if result.total != total:
        # Unreachable while the shares sum to one (the settings validator
        # refuses anything else); loud rather than a silently short plan.
        raise RuntimeError(f"mix of {total} apportioned to {result.total}")
    return result


@dataclass(frozen=True)
class CodingEligibility:
    """Whether a job's candidates are given coding questions, and if not, why."""

    eligible: bool
    excluded_reason: str | None


def coding_eligibility(
    *, role_classification: str | None, job_title: str, execution_enabled: bool
) -> CodingEligibility:
    """The three conditions for a coding question, checked in a fixed order so
    the recorded reason is the first one that failed."""
    if role_classification != stem_classification.STEM:
        return CodingEligibility(False, CODING_EXCLUDED_NOT_STEM)
    if not stem_classification.is_computing_occupation(job_title):
        return CodingEligibility(False, CODING_EXCLUDED_NOT_COMPUTING)
    if not execution_enabled:
        return CodingEligibility(False, CODING_EXCLUDED_DISABLED)
    return CodingEligibility(True, None)
