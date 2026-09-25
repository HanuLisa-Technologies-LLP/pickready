"""Time allocations, weights and generation bounds, from Settings.

One frozen object per process. Every number the composer, the writers and the
scorer use comes from here, and every field here comes from an `assessment_*`
setting in `core/config.py`; no module in this package carries a literal.
`tests/test_assessment_formats_config.py` asserts that.

HOW MANY QUESTIONS, AND OF WHICH FAMILY, IS NOT DECIDED HERE. That is
`assessment_questions.budget` (one question per skill above a grade floor,
70/20/10 by count). The share bounds, the per-grade durations and the
evidence-majority share this object carried until 2026-09-25 are gone with
the rules that read them: the mix is now a count the composer serves exactly,
and the time a candidate has is the server's per-turn clock, never a total the
composer scaled allocations down to fit.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.core.config import get_settings
from app.services.assessment_formats import types

__all__ = ["FormatConfig", "get_config", "SENIOR_GRADES"]

#: The grades that "skew further toward evidence and away from recall-style
#: MCQs": their objective questions never use the single-answer MCQ.
SENIOR_GRADES: frozenset[str] = frozenset({"leadership", "cxo"})


@dataclass(frozen=True)
class FormatConfig:
    time_seconds_by_type: dict[str, int]
    weight_by_type: dict[str, float]
    composition_attempts: int
    evaluation_min_reasoning_words: int
    anchor_min_chars: int
    misconception_min_words: int


@lru_cache(maxsize=1)
def get_config() -> FormatConfig:
    settings = get_settings()
    prose = settings.assessment_time_prose_seconds
    objective = settings.assessment_time_objective_seconds
    return FormatConfig(
        # One allocation per FAMILY (Appendix B section 3), keyed by format so
        # a row's allocation is a lookup on its own type.
        time_seconds_by_type={
            types.EVIDENCE_BASED: prose,
            types.SHORT_ANSWER: prose,
            types.MCQ_SINGLE: objective,
            types.MCQ_MULTI: objective,
            types.FILL_BLANK: objective,
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
        evaluation_min_reasoning_words=settings.assessment_evaluation_min_reasoning_words,
        anchor_min_chars=settings.assessment_anchor_min_chars,
        misconception_min_words=settings.assessment_misconception_min_words,
    )
