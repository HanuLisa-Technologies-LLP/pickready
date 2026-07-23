"""Tier boundaries (FR-4.5 / claude.md rule 8): exact boundary -> HIGHER tier."""
import pytest

from app.models.enums import Tier
from app.services.tiers import assign_tier


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (100.0, Tier.highly_matching),
        (90.01, Tier.highly_matching),
        (90.0, Tier.highly_matching),      # boundary -> higher tier
        (89.99, Tier.moderately_matching),
        (70.0, Tier.moderately_matching),  # boundary -> higher tier
        (69.99, Tier.matching),
        (50.0, Tier.matching),             # boundary -> higher tier
        (49.99, Tier.not_matching),
        (0.0, Tier.not_matching),
    ],
)
def test_tier_boundaries(score: float, expected: Tier) -> None:
    assert assign_tier(score) == expected


def test_tiers_evaluated_top_down() -> None:
    # A score qualifying for several thresholds always lands in the highest.
    assert assign_tier(95.0) == Tier.highly_matching
    assert assign_tier(75.0) == Tier.moderately_matching
