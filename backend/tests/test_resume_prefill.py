"""Resume pre-fill and the asked-question ceiling (feature 2, C2).

Pure functions, whole truth table, no database. The assertions that matter
most are the refusals: a keyword graze must not pre-fill, an exercise type
must never be pre-filled, and the trim must never touch a pre-filled slot
or an essential written early.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.services import resume_prefill as rp
from app.services.assessment_formats import types as qt

TEXT = frozenset({qt.EVIDENCE_BASED, qt.SHORT_ANSWER})

LONG_ANCHOR = (
    "Led the migration of the payments platform to Kafka, owning the "
    "partition rebalancing strategy across nine consumer groups in production."
)


def _fields(*skills: str) -> dict:
    return {"skills": list(skills)}


def test_prefill_needs_both_signals():
    # Anchor + skill on the claim list: pre-filled.
    assert rp.evidence_for(
        competency_name="Kafka",
        resume_anchor=LONG_ANCHOR,
        parsed_fields=_fields("Kafka", "Postgres"),
        question_type=qt.EVIDENCE_BASED,
        text_types=TEXT,
    ) is not None
    # Substantive anchor, but the skill list never claims it: asked.
    assert rp.evidence_for(
        competency_name="Kafka",
        resume_anchor=LONG_ANCHOR,
        parsed_fields=_fields("Postgres"),
        question_type=qt.EVIDENCE_BASED,
        text_types=TEXT,
    ) is None
    # Claimed skill, but only a keyword-graze anchor: asked.
    assert rp.evidence_for(
        competency_name="Kafka",
        resume_anchor="Kafka",
        parsed_fields=_fields("Kafka"),
        question_type=qt.EVIDENCE_BASED,
        text_types=TEXT,
    ) is None
    # No parsed fields at all: asked.
    assert rp.evidence_for(
        competency_name="Kafka",
        resume_anchor=LONG_ANCHOR,
        parsed_fields=None,
        question_type=qt.EVIDENCE_BASED,
        text_types=TEXT,
    ) is None


def test_an_exercise_is_never_prefilled():
    for exercise in (qt.MCQ_SINGLE, qt.MCQ_MULTI, qt.FILL_BLANK, qt.CODING):
        assert rp.evidence_for(
            competency_name="Kafka",
            resume_anchor=LONG_ANCHOR,
            parsed_fields=_fields("Kafka"),
            question_type=exercise,
            text_types=TEXT,
        ) is None


def test_word_boundaries_hold_in_the_skill_match():
    """'Java' on the claim list must not pre-fill 'JavaScript' questions."""
    assert rp.evidence_for(
        competency_name="JavaScript",
        resume_anchor=LONG_ANCHOR,
        parsed_fields=_fields("Java"),
        question_type=qt.EVIDENCE_BASED,
        text_types=TEXT,
    ) is None


def test_the_prefill_text_says_where_it_came_from():
    text = rp.prefill_text(LONG_ANCHOR)
    assert text.startswith("Pre-filled from the candidate's resume")
    assert "Kafka" in text
    assert len(text) <= rp.MAX_PREFILL_CHARS


@dataclass
class _Slot:
    index: int
    weight: float


def test_the_ceiling_trims_lowest_weight_latest_first_and_spares_prefills():
    slots = [_Slot(index=i, weight=w) for i, w in enumerate(
        [3.0, 1.0, 1.0, 2.0, 1.0, 2.5]
    )]
    # Slot 4 is pre-filled, so five are asked against a ceiling of three:
    # trim two. Lowest weight first, latest index among equals: 2 then 1.
    trimmed = rp.trim_to_ceiling(slots, prefilled_indexes={4}, ceiling=3)
    assert trimmed == {2, 1}
    # Under the ceiling: nothing trimmed.
    assert rp.trim_to_ceiling(slots, prefilled_indexes=set(), ceiling=6) == set()


def test_the_ceiling_never_trims_a_prefilled_slot():
    slots = [_Slot(index=i, weight=1.0) for i in range(5)]
    trimmed = rp.trim_to_ceiling(slots, prefilled_indexes={0, 1, 2, 3, 4}, ceiling=1)
    assert trimmed == set()


def test_the_configured_ceiling_is_the_briefs_forty():
    from app.core.config import get_settings

    assert get_settings().assessment_question_ceiling == 40
