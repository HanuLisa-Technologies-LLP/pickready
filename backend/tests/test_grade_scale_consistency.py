"""The one four-grade scale, asserted across every module that publishes it.

CLAUDE.md (2026-07-30): "There is ONE rating scale, it has FOUR grades, and it
lives in `services/rating.py` ... `matching.matching_label` and
`functional_assessment.rating_label` are now thin aliases over it and must stay
that way. The cut-points are unchanged (90 / 75 / 60)."

`services/tiers.py` was not converted in that release and kept its own 90 / 70
/ 50 table with `matching` and `moderately_matching` transposed, so
`assign_tier(75)` said Moderately Matching while `grade_for_percent(75)` said
Matching -- on a value persisted in `job_candidate_links.tier` and returned by
`GET /matching/...`. Every alias in the product is swept here rather than each
being spot-checked in its own file, because a per-module test is exactly what
was in place while the two disagreed: `test_tiers.py` and `test_rating.py` both
passed, neither compared the two, and nothing in the suite could see the gap.

Full-range, not sampled. A boundary bug is a bug at one point, and a test that
checks nine hand-picked points is a test that checks whether the author guessed
the same nine points as the defect.
"""
from app.models.enums import Tier
from app.services import functional_assessment, matching, rating
from app.services.tiers import TIER_FOR_GRADE, assign_tier

#: 0.0 to 100.0 in tenths. Fine enough to land on both sides of every
#: cut-point without depending on knowing where the cut-points are. Swept
#: inside each test rather than parametrised: a thousand test ids per alias
#: would triple the suite's reported count to say one thing.
SCORES = [tenths / 10.0 for tenths in range(0, 1001)]


def test_assign_tier_agrees_with_grade_for_percent() -> None:
    """The assertion that would have caught the divergence, at every point."""
    for score in SCORES:
        expected = TIER_FOR_GRADE[rating.grade_for_percent(score)]
        assert assign_tier(score) is expected, f"assign_tier disagrees at {score}"


def test_matching_label_is_an_alias() -> None:
    """`matching_label` takes the 1-10 parameter score, so the whole 0-100
    range is swept through the x10 conversion the AI Score already applies."""
    for score in SCORES:
        expected = rating.grade_for_percent(score)
        assert matching.matching_label(score / 10.0) == expected, f"at {score}"


def test_rating_label_is_an_alias() -> None:
    for score in SCORES:
        expected = rating.grade_for_percent(score)
        assert functional_assessment.rating_label(score) == expected, f"at {score}"


def test_the_four_grades_and_the_four_tiers_are_the_same_set() -> None:
    """One scale means one cardinality. A fifth tier or a fifth grade would
    make one of the two a superset and the mapping ambiguous."""
    assert len(rating.GRADES) == 4
    assert set(TIER_FOR_GRADE) == set(rating.GRADES)
    assert set(TIER_FOR_GRADE.values()) == set(Tier)


def test_the_tier_ordering_matches_the_published_grade_ordering() -> None:
    """`rating.GRADES` is ordered best to worst and the UI colour ramp keys off
    it. The tiers must descend in the same order as the scores that produce
    them, or a persisted label ranks candidates differently from the report."""
    ordered_by_score = [
        assign_tier(score)
        for score in (95.0, 80.0, 65.0, 20.0)
    ]
    assert ordered_by_score == [TIER_FOR_GRADE[grade] for grade in rating.GRADES]


def test_the_cut_points_are_ninety_seventyfive_sixty() -> None:
    """Named because CLAUDE.md names them, so a silent move fails here with the
    rule quoted beside it rather than as an opaque parametrised diff."""
    assert rating.grade_for_percent(90) == rating.GRADE_HIGHLY
    assert rating.grade_for_percent(89.99) == rating.GRADE_MATCHING
    assert rating.grade_for_percent(75) == rating.GRADE_MATCHING
    assert rating.grade_for_percent(74.99) == rating.GRADE_MODERATELY
    assert rating.grade_for_percent(60) == rating.GRADE_MODERATELY
    assert rating.grade_for_percent(59.99) == rating.GRADE_NOT


def test_no_module_carries_its_own_cut_points() -> None:
    """One implementation per concept (spec-doc6 §10.1 rule 12).

    `tiers.py` held a duplicate 90 / 70 / 50 table for a month. The names are
    gone; assert their absence so a helpful future edit that reintroduces
    "local constants for readability" fails immediately rather than drifting.
    """
    from app.services import tiers

    for banned in ("HIGHLY_THRESHOLD", "MODERATELY_THRESHOLD", "MATCHING_THRESHOLD"):
        assert not hasattr(tiers, banned), (
            f"services/tiers.py re-declared {banned}; cut-points live in rating.py"
        )
