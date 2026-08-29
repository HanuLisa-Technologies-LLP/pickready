"""Tier boundaries: the persisted half of the ONE four-grade scale.

This file previously asserted the defect. It pinned 90 / 70 / 50 with the
middle two bands inverted -- `assign_tier(75.0) == Tier.moderately_matching`
was a named test -- which is exactly why the divergence from
`services/rating.py` survived a full test suite for a month. The cut-points
below are `rating.grade_for_percent`'s, and the ordering assertion is the one
that would have caught it: `matching` outranks `moderately_matching`, so the
score that earns it must be HIGHER, not lower.
"""
import pytest

from app.models.enums import Tier
from app.services import rating
from app.services.tiers import TIER_FOR_GRADE, assign_tier


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (100.0, Tier.highly_matching),
        (90.01, Tier.highly_matching),
        (90.0, Tier.highly_matching),        # boundary -> higher tier
        (89.99, Tier.matching),
        (75.0, Tier.matching),               # boundary -> higher tier
        (74.99, Tier.moderately_matching),
        (60.0, Tier.moderately_matching),    # boundary -> higher tier
        (59.99, Tier.not_matching),
        (55.0, Tier.not_matching),
        (0.0, Tier.not_matching),
    ],
)
def test_tier_boundaries(score: float, expected: Tier) -> None:
    assert assign_tier(score) == expected


def test_tiers_evaluated_top_down() -> None:
    # A score qualifying for several thresholds always lands in the highest.
    assert assign_tier(95.0) == Tier.highly_matching
    assert assign_tier(75.0) == Tier.matching


def test_a_better_score_never_earns_a_worse_grade() -> None:
    """The shape of the defect, stated directly.

    Under the old 90 / 70 / 50 table a candidate scoring 55 was filed as
    `Tier.matching` and one scoring 75 as `Tier.moderately_matching`, so the
    weaker candidate carried the better-sounding label on a value the API
    returns. Rank by the grade order the product actually publishes and walk
    the range: the rank must never improve as the score falls.
    """
    rank = {TIER_FOR_GRADE[grade]: i for i, grade in enumerate(rating.GRADES)}
    previous = rank[assign_tier(100.0)]
    for tenths in range(1000, -1, -1):
        current = rank[assign_tier(tenths / 10.0)]
        assert current >= previous, f"score {tenths / 10.0} outranks a higher score"
        previous = current


def test_every_grade_is_reachable_and_distinct() -> None:
    """A four-grade scale with a member no score can reach is a three-grade
    scale with a dead enum value in it."""
    assert set(TIER_FOR_GRADE) == set(rating.GRADES)
    reached = {assign_tier(tenths / 10.0) for tenths in range(0, 1001)}
    assert reached == set(Tier)


def test_assign_tier_refuses_a_score_it_cannot_grade() -> None:
    """No silent default: a missing score is an upstream defect, not a band."""
    for bad in (None, True, "high"):
        with pytest.raises(ValueError):
            assign_tier(bad)  # type: ignore[arg-type]
