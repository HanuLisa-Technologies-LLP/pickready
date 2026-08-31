"""The one client-facing rating scale.

FOUR grades, and only four (spec §10.2, 2026-07-30):

    Highly Matching | Matching | Moderately Matching | Not Matching

They replace the product's previous two parallel five-label scales -- the
assessment's *Very High / High / Medium / Low / Developing* and matching's
*Highly Matching / Matching / Moderate / Low / No Matching*. Those two scales
had to be kept in step by hand, in two modules, and a reader had no way to know
that a "High" and a "Matching" meant the same thing. One scale, one module.

Every rated item in the product now resolves through here: the four AI Score
matching parameters, Primary Skills, Secondary Skills, Behavioural
Competencies, and the Overall grade. NO NUMBER crosses the API boundary -- the
internal 0-100 score is converted to a word HERE, server-side, so a score
cannot leak by a caller forgetting to convert it.

Band boundaries are INCLUSIVE UPWARD (CLAUDE.md rule 8): a score of exactly 90
is Highly Matching, never Matching. The three cut-points are unchanged from the
five-label scale -- 90 / 75 / 60 -- so a report written before this release
regrades identically, with the old Low and Developing collapsing into
Not Matching.
"""
from __future__ import annotations

__all__ = [
    "GRADES",
    "GRADE_HIGHLY",
    "GRADE_MATCHING",
    "GRADE_MODERATELY",
    "GRADE_NOT",
    "MODERATELY_CEILING",
    "MODERATE_OR_BELOW",
    "PROBE_THRESHOLD",
    "band_index_for",
    "grade_for_percent",
    "grade_for_ten",
]

GRADE_HIGHLY = "Highly Matching"
GRADE_MATCHING = "Matching"
GRADE_MODERATELY = "Moderately Matching"
GRADE_NOT = "Not Matching"

#: Ordered best to worst. The UI colour ramp and the radar geometry both key
#: off this order, so nothing may be inserted into the middle of it.
GRADES: tuple[str, ...] = (
    GRADE_HIGHLY,
    GRADE_MATCHING,
    GRADE_MODERATELY,
    GRADE_NOT,
)

#: The two grades that make an item probe-worthy (spec §10.3, suggested
#: interview questions).
MODERATE_OR_BELOW: frozenset[str] = frozenset({GRADE_MODERATELY, GRADE_NOT})

#: Internal 0-100 score at or above which an item is NOT probe-worthy. Kept as
#: a number because it is a scoring-side threshold; it never leaves the server.
PROBE_THRESHOLD = 75

#: The highest internal score that still grades Moderately Matching. Derived
#: from the cut-points above rather than typed: the bands are inclusive upward,
#: so the Moderately band ends one point below where Matching begins, and a
#: hand-written 74 here would silently stop agreeing with `grade_for_percent`
#: the first time a cut-point moved.
#:
#: IT IS NOT THE BAND CAP'S CEILING, and it used to be. The Runbook's controls
#: cap at the top of "Consider with reservations", which is 71 (RPN-PHIL-001
#: section 10.8), and this is the top of "Moderately Matching" on the product's
#: four-grade scale, which is 74. They are the SAME BAND on two scales and
#: three points apart as numbers. The ceiling now comes from
#: `runbook_data/bands.yaml` through `miti/caps.py`, and
#: `tests/test_band_caps.py` pins both numbers so that anybody who "fixes" one
#: to match the other has to decide which document they are following.
MODERATELY_CEILING = PROBE_THRESHOLD - 1


def grade_for_percent(score: int | float | None) -> str | None:
    """Grade for an internal 0-100 score. None in, None out.

    Pure and side-effect free. Swept across the whole 0-100 range by
    tests/test_grade_scale_consistency.py, which also asserts that the
    persisted `Tier` scale agrees with it at every point.
    """
    if score is None or isinstance(score, bool):
        return None
    try:
        percent = float(score)
    except (TypeError, ValueError):
        return None
    if percent >= 90:
        return GRADE_HIGHLY
    if percent >= 75:
        return GRADE_MATCHING
    if percent >= 60:
        return GRADE_MODERATELY
    return GRADE_NOT


def grade_for_ten(score: int | float | None) -> str | None:
    """Grade for a 1-10 matching-parameter score. None in, None out."""
    if score is None or isinstance(score, bool):
        return None
    try:
        return grade_for_percent(float(score) * 10.0)
    except (TypeError, ValueError):
        return None


# `cap_to_moderately` was DELETED on 2026-08-29, not deprecated. It was the
# arithmetic half of a rule called "the Must-have hard cap" that cited a spec
# section the Runbook does not have, and it implemented ONE of the three
# band-capping controls RPN-PHIL-001 states. All three now live in
# `services/miti/caps.py`, with their ceilings read from
# `runbook_data/bands.yaml` and composed by taking the lowest. Leaving this
# function in place would have been a second implementation of one concept,
# capping three points higher than the Runbook does, available to any caller
# who reached for the obvious name.


def band_index_for(label: str | None) -> int:
    """Radar radius for a grade: 1 (Not Matching, innermost) .. 4 (outermost).

    This is a RENDERING COORDINATE, not a disclosed score -- a radar chart has
    no geometry without a radius, and the four grades ARE the radial axis. It
    is the coarsest value that can draw the required chart and is never shown
    to anyone as a number.

    An unknown label lands on the innermost ring rather than raising, so a
    report written by an older build still draws.
    """
    try:
        return len(GRADES) - GRADES.index(str(label))
    except ValueError:
        return 1
