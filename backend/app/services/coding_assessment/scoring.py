"""The coding question's score: hidden tests and code quality. PURE.

CONTRACT v2 fixes the split: seventy parts from the hidden tests and thirty
from the code-quality review. The weight is data
(`settings.coding_score_test_weight`), passed in rather than read here, so
this module has no I/O and a test can pin the arithmetic exactly.

NONE IS NOT ZERO, AND THAT IS THE WHOLE CONTRACT OF THIS FUNCTION
------------------------------------------------------------------
A score exists only when BOTH halves exist: every hidden test has an outcome
AND the review was written. Anything less returns None, which the grader reads
as "Not assessed" and routes to a person. Returning 0 for a missing half would
grade a candidate for a sandbox outage or a provider failure, which is the
synthetic score this release removes everywhere else.

A compile error is NOT missing: the tests ran and none passed, so the pass
rate is zero and the review still counts. An EMPTY answer never reaches here:
nothing was submitted, so it is an evidence gap, not a result.

The number is INTERNAL. It is handed to Miti, the one grading authority, and
never serialised; the report states coding results in spelled-out words
(`phrasing.outcome_sentence`).
"""
from __future__ import annotations

__all__ = ["SCORE_MIN", "SCORE_MAX", "coding_question_score"]

SCORE_MIN = 0
SCORE_MAX = 100


def coding_question_score(
    *,
    tests_passed: int | None,
    tests_total: int | None,
    review_score: int | None,
    test_weight: float,
) -> int | None:
    """`round(w * 100 * passed / total + (1 - w) * review)`, or None.

    Raises ValueError for inputs that can only come from a defect upstream
    (a weight outside (0, 1), more tests passed than exist, a review score off
    the scale), because clamping them would publish a number computed from a
    state that should not exist.
    """
    if not 0 < test_weight < 1:
        raise ValueError("the hidden-test weight must be strictly between 0 and 1")
    if tests_total is None or tests_passed is None or review_score is None:
        return None
    if tests_total < 1 or not 0 <= tests_passed <= tests_total:
        raise ValueError("hidden-test counts are inconsistent")
    if not SCORE_MIN <= review_score <= SCORE_MAX:
        raise ValueError("the review score is off the 0 to 100 scale")
    pass_rate = tests_passed / tests_total
    return int(round(test_weight * SCORE_MAX * pass_rate + (1 - test_weight) * review_score))
