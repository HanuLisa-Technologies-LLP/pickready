"""The Team Review verdict vocabulary, and its mapping onto the machine grades.

WHY THIS IS A SEPARATE VOCABULARY FROM `services/rating.py`
-----------------------------------------------------------
`rating.py` holds the product's ONE assessment scale: the four grades an agent
outputs about a candidate against a job. This module holds something different
in kind: what a human on the hiring team DECIDED after looking.

The two must never be rendered on the same words. A colleague's note written in
the machine's vocabulary reads as a machine grade, which is the opposite of what
the Team Review panel is for. That argument was already made at the call site in
`components/candidate-team-review-modal.tsx` and it is correct; what was wrong
was the vocabulary it protected, not the reasoning.

WHY PASS / HOLD / REJECT
-------------------------
The Candidate Dashboard Specification (`docs/spec/CANDIDATE_DASHBOARD_SPECIFICATION.md`,
Column 7) is precedence rank 4 and is the authority for this exact surface. It
specifies "Checkbox verdicts: Pass / Hold / Reject".

This replaces the RETIRED five-label scale (`very_high`, `high`, `medium`,
`low`, `developing`) which survived here alone after the 2026-07-30
consolidation deleted the product's other two five-label scales. It survived
because it was DELIBERATE rather than forgotten: the whole stack agreed on it,
from the CHECK constraint through the Pydantic literal to the frontend labels.
The reason it still had to go is that a decision vocabulary and an assessment
vocabulary are different things, and `very_high` is an assessment word.

Note the shape of the change: an assessment scale is ORDINAL and answers "how
good", a decision vocabulary is CATEGORICAL and answers "what now". `hold` is
not "medium"; it is "I am not deciding yet".

THE OVERRIDE-RATE MAPPING IS THE REASON THIS MODULE EXISTS AT ALL
------------------------------------------------------------------
spec-doc6 §8.2 requires measuring recruiter deviation from the Ready Pick Score
(the Dashboard Specification targets under 15%). That measurement needs the two
vocabularies to be COMPARABLE, which is a weaker and better requirement than
identical: they stay distinct on screen, and comparison happens here, once, in
code a reviewer can check.

**Measure, never nudge.** spec-doc6 §8.2 is explicit, and `PRODUCT.md` records
it as a constraint: implement the measurement, and implement no nudge, warning,
friction or visual discouragement when a reviewer disagrees with the score. A
recruiter's independent judgment is data, and a target that quietly discourages
disagreement destroys the calibration signal it exists to measure. Nothing in
this module returns anything a UI could render as disapproval.
"""
from __future__ import annotations

from app.services import rating

__all__ = [
    "VERDICT_PASS",
    "VERDICT_HOLD",
    "VERDICT_REJECT",
    "VERDICTS",
    "VERDICT_LABELS",
    "GRADES_FOR_VERDICT",
    "verdict_for_grade",
    "agrees_with_grade",
]

#: Machine values. Stored in `candidate_team_reviews.rating`, accepted by the
#: `ck_candidate_team_reviews_rating` CHECK, and validated by `TeamRating` in
#: `schemas/candidates.py`. All three read this tuple, so they cannot drift.
VERDICT_PASS = "pass"
VERDICT_HOLD = "hold"
VERDICT_REJECT = "reject"

#: Ordered strongest-first, which is the order the panel lists them and the
#: order `_team_reviews_out` sorts by. The order is a display and tie-break
#: convention only; nothing scores these.
VERDICTS: tuple[str, ...] = (VERDICT_PASS, VERDICT_HOLD, VERDICT_REJECT)

#: What a person reads. Deliberately plain: these are decisions, not ratings,
#: and dressing them up would make them look like a graded output.
VERDICT_LABELS: dict[str, str] = {
    VERDICT_PASS: "Pass",
    VERDICT_HOLD: "Hold",
    VERDICT_REJECT: "Reject",
}

#: Which machine grades a verdict is taken to AGREE with, for the override-rate
#: metric only. This is never shown to anyone and never converts one vocabulary
#: into the other for display.
#:
#: `pass` spans two grades because a reviewer passing a candidate is consistent
#: with the machine rating them either Highly Matching or Matching; splitting
#: those would count a reviewer as deviating for agreeing slightly less
#: enthusiastically, which is not a deviation.
GRADES_FOR_VERDICT: dict[str, frozenset[str]] = {
    VERDICT_PASS: frozenset({rating.GRADE_HIGHLY, rating.GRADE_MATCHING}),
    VERDICT_HOLD: frozenset({rating.GRADE_MODERATELY}),
    VERDICT_REJECT: frozenset({rating.GRADE_NOT}),
}


def verdict_for_grade(grade: str | None) -> str | None:
    """The verdict a machine grade corresponds to. None in, None out.

    The inverse of `GRADES_FOR_VERDICT`, and total over `rating.GRADES` by
    construction: a test asserts every grade maps to exactly one verdict, so an
    added grade fails the suite rather than silently falling through here.
    """
    if grade is None:
        return None
    for verdict, grades in GRADES_FOR_VERDICT.items():
        if grade in grades:
            return verdict
    raise ValueError(
        f"{grade!r} is not one of rating.GRADES, so it has no Team Review "
        "verdict. Add it to GRADES_FOR_VERDICT deliberately rather than "
        "letting the override-rate metric silently skip it."
    )


def agrees_with_grade(verdict: str, grade: str | None) -> bool:
    """Whether a reviewer's verdict agrees with the machine grade.

    Used ONLY to compute the override rate. A False here is a calibration
    signal, never a correction: it means either the scorecard needs
    recalibration or the reviewer saw something the assessment missed, and the
    system has no way to tell which. That is exactly why it is recorded and not
    acted on.

    An absent grade is not a disagreement. A candidate whose profile has not
    been written yet cannot be deviated from, and counting that as an override
    would inflate the metric with rows that carry no machine opinion at all.
    """
    if verdict not in GRADES_FOR_VERDICT:
        raise ValueError(
            f"{verdict!r} is not a Team Review verdict; expected one of {VERDICTS}"
        )
    if grade is None:
        return True
    return grade in GRADES_FOR_VERDICT[verdict]
