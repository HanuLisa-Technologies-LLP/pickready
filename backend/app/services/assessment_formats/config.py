"""Composition bounds, time allocations and weights, from Settings.

One frozen object per process. Every number the composer, the validator and
the scorer use comes from here, and every field here comes from an
`assessment_*` setting in `core/config.py`; no module in this package carries a
literal. `tests/test_assessment_formats_config.py` asserts that.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.core.config import get_settings
from app.services.assessment_formats import types

__all__ = ["FormatConfig", "get_config", "SENIOR_GRADES"]

#: The grades that "skew further toward evidence and away from recall-style
#: MCQs" (composition rule 4).
SENIOR_GRADES: frozenset[str] = frozenset({"leadership", "cxo"})


@dataclass(frozen=True)
class FormatConfig:
    evidence_min_share: float
    supporting_max_share: float
    supporting_max_share_senior: float
    duration_seconds_by_grade: dict[str, int]
    time_seconds_by_type: dict[str, int]
    weight_by_type: dict[str, float]
    composition_attempts: int

    def duration_for(self, grade: str) -> int:
        return self.duration_seconds_by_grade[grade]

    def supporting_share_for(self, grade: str) -> float:
        return self.supporting_max_share_senior if grade in SENIOR_GRADES else self.supporting_max_share


@lru_cache(maxsize=1)
def get_config() -> FormatConfig:
    settings = get_settings()
    return FormatConfig(
        evidence_min_share=settings.assessment_evidence_min_share,
        supporting_max_share=settings.assessment_supporting_max_share,
        supporting_max_share_senior=settings.assessment_supporting_max_share_senior,
        duration_seconds_by_grade={
            "non_managerial": settings.assessment_duration_minutes_non_managerial * 60,
            "managerial": settings.assessment_duration_minutes_managerial * 60,
            "leadership": settings.assessment_duration_minutes_leadership * 60,
            "cxo": settings.assessment_duration_minutes_cxo * 60,
        },
        time_seconds_by_type={
            types.EVIDENCE_BASED: settings.assessment_time_evidence_seconds,
            types.SHORT_ANSWER: settings.assessment_time_short_answer_seconds,
            types.MCQ_SINGLE: settings.assessment_time_mcq_single_seconds,
            types.MCQ_MULTI: settings.assessment_time_mcq_multi_seconds,
            types.FILL_BLANK: settings.assessment_time_fill_blank_seconds,
            types.CODING: settings.assessment_time_coding_seconds,
        },
        weight_by_type={
            types.EVIDENCE_BASED: settings.assessment_weight_evidence,
            types.SHORT_ANSWER: settings.assessment_weight_short_answer,
            types.MCQ_SINGLE: settings.assessment_weight_mcq_single,
            types.MCQ_MULTI: settings.assessment_weight_mcq_multi,
            types.FILL_BLANK: settings.assessment_weight_fill_blank,
            types.CODING: settings.assessment_weight_coding,
        },
        composition_attempts=settings.assessment_composition_attempts,
    )
