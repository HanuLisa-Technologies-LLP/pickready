"""Tier assignment: the PERSISTED form of the one client-facing rating scale.

`Tier` is what `job_candidate_links.tier` stores and what
`schemas/matching.MatchedCandidateOut.tier` returns, so it is the same grade a
client eventually reads -- it is not a second, internal, coarser thing.
CLAUDE.md (2026-07-30) therefore already governs it: "There is ONE rating
scale, it has FOUR grades, and it lives in `services/rating.py` ... the
cut-points are unchanged (90 / 75 / 60)."

THIS MODULE WAS A THIRD SCALE UNTIL IT WAS COLLAPSED, and its middle two bands
were INVERTED. It carried its own 90 / 70 / 50 cut-points and, worse, mapped
70-89 onto `moderately_matching` and 50-69 onto `matching`, which is the
opposite order to the one the four grades are ranked in. A candidate scoring 55
was written as `Tier.matching` while `rating.grade_for_percent(55)` called the
same candidate Not Matching, and a candidate scoring 75 was written as
`Tier.moderately_matching` while the report beside it said Matching. The
consequence was not a cosmetic disagreement: the WORSE candidate got the
BETTER-sounding label, on a value that is persisted and returned over the API.

There is no arithmetic here any more, deliberately. `assign_tier` resolves
through `rating.grade_for_percent` and then names the grade, so the cut-points
and the inclusive-upward boundary rule (CLAUDE.md rule 8: exactly 90 is Highly
Matching, checked top-down) exist in exactly one place. A future edit to the
cut-points cannot move one scale and leave the other behind, which is precisely
how the two drifted apart. `tests/test_grade_scale_consistency.py` asserts the
two agree across the whole 0-100 range.
"""
from __future__ import annotations

from app.models.enums import Tier
from app.services.rating import (
    GRADE_HIGHLY,
    GRADE_MATCHING,
    GRADE_MODERATELY,
    GRADE_NOT,
    grade_for_percent,
)

__all__ = ["TIER_FOR_GRADE", "assign_tier"]

#: The persisted enum member each of the four grades is stored as. The member
#: NAMES are unchanged from before the collapse: they are values already
#: written into `job_candidate_links.tier`, so renaming one would be a schema
#: change and a data migration, while remapping which SCORES reach which member
#: is neither. Only the mapping moved.
TIER_FOR_GRADE: dict[str, Tier] = {
    GRADE_HIGHLY: Tier.highly_matching,
    GRADE_MATCHING: Tier.matching,
    GRADE_MODERATELY: Tier.moderately_matching,
    GRADE_NOT: Tier.not_matching,
}


def assign_tier(score: float) -> Tier:
    """Map a 0-100 contextual score to the tier its grade is stored as.

    Raises on a score `rating.grade_for_percent` cannot grade (None, a bool, a
    non-numeric). A caller reaching here with no score has a defect upstream,
    and substituting a default would file that candidate under a band nobody
    computed -- the quiet degradation spec-doc6 §10.1 rule 1 forbids.
    """
    grade = grade_for_percent(score)
    if grade is None:
        raise ValueError(f"assign_tier needs a 0-100 score, got {score!r}")
    return TIER_FOR_GRADE[grade]
