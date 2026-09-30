"""Every completed assessment consumes one credit regardless of role type."""
from __future__ import annotations

from app.models.billing import (
    EVENT_COMPLETED, EVENT_INCOMPLETE, EVENT_NO_SHOW,
    EVENT_OLD_PROFILE_REVIEW, ROLE_NON_STEM, ROLE_STEM,
    SUBUNITS_PER_CREDIT, consumption_subunits,
)
from app.services import credits


def test_completed_assessment_has_one_rate() -> None:
    for classification in (ROLE_STEM, ROLE_NON_STEM, None, "unknown"):
        assert consumption_subunits(EVENT_COMPLETED, classification) == SUBUNITS_PER_CREDIT
        for event in (EVENT_INCOMPLETE, EVENT_NO_SHOW, EVENT_OLD_PROFILE_REVIEW):
            assert consumption_subunits(event, classification) is None


def test_balance_estimate_uses_one_assessment_per_credit() -> None:
    assert credits.DEFAULT_CREDITS_PER_ASSESSMENT == 1
    assert credits.estimated_assessments_remaining(75 * SUBUNITS_PER_CREDIT, credits.DEFAULT_CREDITS_PER_ASSESSMENT) == 75
