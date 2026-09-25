"""The one four-grade scale, asserted across every module that publishes it.

CLAUDE.md (2026-07-30): "There is ONE rating scale, it has FOUR grades, and it
lives in `services/rating.py` ... The cut-points are unchanged (90 / 75 / 60)."

`services/tiers.py` was not converted in that release and kept its own 90 / 70
/ 50 table with `matching` and `moderately_matching` transposed, so a persisted
label disagreed with the report beside it. It is DELETED (Vivekium release,
Phase 2 WP-F) with `matching.matching_label`: nothing writes `tier` any more.
The publishers that remain are the report's `rating_label` and the ranked
table's AI Match word, `yukti.ranking.grade_word`, and every one of them is
swept here rather than spot-checked in its own file, because a per-module test
is exactly what was in place while two of them disagreed.

Full-range, not sampled. A boundary bug is a bug at one point, and a test that
checks nine hand-picked points is a test that checks whether the author guessed
the same nine points as the defect.
"""
from app.services import functional_assessment, rating
from app.services.yukti import ranking

#: 0.0 to 100.0 in tenths. Fine enough to land on both sides of every
#: cut-point without depending on knowing where the cut-points are. Swept
#: inside each test rather than parametrised: a thousand test ids per alias
#: would triple the suite's reported count to say one thing.
SCORES = [tenths / 10.0 for tenths in range(0, 1001)]


def test_the_ai_match_word_is_the_rating_scale() -> None:
    """The ranked table's word for a Yukti key, at every point."""
    for score in SCORES:
        expected = rating.grade_for_percent(score)
        assert ranking.grade_word(score) == expected, f"at {score}"


def test_rating_label_is_an_alias() -> None:
    for score in SCORES:
        expected = rating.grade_for_percent(score)
        assert functional_assessment.rating_label(score) == expected, f"at {score}"


def test_there_are_exactly_four_grades() -> None:
    """One scale means one cardinality. A fifth grade would make the mapping
    from any score ambiguous."""
    assert len(rating.GRADES) == 4
    assert len(set(rating.GRADES)) == 4


def test_the_published_order_descends_with_the_score() -> None:
    """`rating.GRADES` is ordered best to worst and the UI colour ramp keys off
    it. A better score never earns a worse word."""
    ordered_by_score = [
        rating.grade_for_percent(score) for score in (95.0, 80.0, 65.0, 20.0)
    ]
    assert ordered_by_score == list(rating.GRADES)


def test_the_cut_points_are_ninety_seventyfive_sixty() -> None:
    """Named because CLAUDE.md names them, so a silent move fails here with the
    rule quoted beside it rather than as an opaque parametrised diff."""
    assert rating.grade_for_percent(90) == rating.GRADE_HIGHLY
    assert rating.grade_for_percent(89.99) == rating.GRADE_MATCHING
    assert rating.grade_for_percent(75) == rating.GRADE_MATCHING
    assert rating.grade_for_percent(74.99) == rating.GRADE_MODERATELY
    assert rating.grade_for_percent(60) == rating.GRADE_MODERATELY
    assert rating.grade_for_percent(59.99) == rating.GRADE_NOT


def test_no_publisher_carries_its_own_cut_points() -> None:
    """One implementation per concept (spec-doc6 §10.1 rule 12).

    `tiers.py` held a duplicate 90 / 70 / 50 table for a month. Assert the
    names are absent from every module that still publishes a grade word, so
    a helpful future edit that reintroduces "local constants for readability"
    fails immediately rather than drifting.
    """
    for module in (ranking, functional_assessment):
        for banned in ("HIGHLY_THRESHOLD", "MODERATELY_THRESHOLD", "MATCHING_THRESHOLD"):
            assert not hasattr(module, banned), (
                f"{module.__name__} re-declared {banned}; cut-points live in rating.py"
            )
