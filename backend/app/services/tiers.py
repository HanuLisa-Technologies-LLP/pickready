"""Pure tier assignment (FR-4.5 / ESD §8.2 step 4).

Thresholds are evaluated top-down; a score landing exactly on a boundary takes
the HIGHER tier (claude.md rule 8): 90.0 is Highly Matching, 70.0 is
Moderately Matching, 50.0 is Matching.
"""
from app.models.enums import Tier

HIGHLY_THRESHOLD = 90.0
MODERATELY_THRESHOLD = 70.0
MATCHING_THRESHOLD = 50.0


def assign_tier(score: float) -> Tier:
    """Map a 0-100 contextual score to its tier, checking >=90 first so exact
    boundaries land in the higher tier."""
    if score >= HIGHLY_THRESHOLD:
        return Tier.highly_matching
    if score >= MODERATELY_THRESHOLD:
        return Tier.moderately_matching
    if score >= MATCHING_THRESHOLD:
        return Tier.matching
    return Tier.not_matching
