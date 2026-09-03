"""The report states the evaluation's grade; it never recomputes one.

ONE ARITHMETIC, NOT TWO. `_grade_of` takes the word off the row when the row
carries one. Recomputing it in the generator would create a second arithmetic
that has to agree with the first, and the day they disagreed there would be no
way to tell which one the client saw -- the report would say one thing and the
evaluation another, both honestly.

The fallback exists for a row written before the grade was stored, and it goes
through `services.rating`, the single scale, rather than through a comparison
written here. That is the same rule `tiers.py` broke: one implementation per
concept.
"""
from __future__ import annotations

from app.services import rating
from app.services.siddhi import synthesis


def test_a_row_that_carries_its_grade_is_quoted_not_recomputed() -> None:
    """Even when the stored score would grade differently. The row is the
    record of what the evaluation decided."""
    quoted = synthesis._grade_of({"grade": "Matching", "score": 12.0})
    assert quoted == "Matching"


def test_a_row_without_a_grade_falls_back_to_the_one_rating_scale() -> None:
    """Not to a comparison written in the generator. `services.rating` is the
    only place a number becomes a word."""
    score = 82.0
    assert synthesis._grade_of({"score": score}) == rating.grade_for_percent(score)


def test_a_row_with_neither_grade_nor_score_reads_as_empty() -> None:
    """Empty rather than a guessed grade: a report that invented a band for a
    row nobody scored would be stating an evaluation that never happened."""
    assert synthesis._grade_of({}) == ""


def test_an_empty_grade_string_is_not_treated_as_a_grade() -> None:
    """`""` is absence, not a band, and returning it verbatim would render a
    blank where a client expects a word."""
    assert synthesis._grade_of({"grade": "", "score": 82.0}) == rating.grade_for_percent(82.0)
