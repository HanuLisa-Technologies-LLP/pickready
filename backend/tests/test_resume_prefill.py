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

#: Any category that is NOT behavioural, for the cases about the other rules.
MUST_HAVE = "must_have"

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
        category=MUST_HAVE,
        resume_anchor=LONG_ANCHOR,
        parsed_fields=_fields("Kafka", "Postgres"),
        question_type=qt.EVIDENCE_BASED,
        text_types=TEXT,
    ) is not None
    # Substantive anchor, but the skill list never claims it: asked.
    assert rp.evidence_for(
        competency_name="Kafka",
        category=MUST_HAVE,
        resume_anchor=LONG_ANCHOR,
        parsed_fields=_fields("Postgres"),
        question_type=qt.EVIDENCE_BASED,
        text_types=TEXT,
    ) is None
    # Claimed skill, but only a keyword-graze anchor: asked.
    assert rp.evidence_for(
        competency_name="Kafka",
        category=MUST_HAVE,
        resume_anchor="Kafka",
        parsed_fields=_fields("Kafka"),
        question_type=qt.EVIDENCE_BASED,
        text_types=TEXT,
    ) is None
    # No parsed fields at all: asked.
    assert rp.evidence_for(
        competency_name="Kafka",
        category=MUST_HAVE,
        resume_anchor=LONG_ANCHOR,
        parsed_fields=None,
        question_type=qt.EVIDENCE_BASED,
        text_types=TEXT,
    ) is None


def test_an_exercise_is_never_prefilled():
    for exercise in (qt.MCQ_SINGLE, qt.MCQ_MULTI, qt.FILL_BLANK, qt.CODING):
        assert rp.evidence_for(
            competency_name="Kafka",
            category=MUST_HAVE,
            resume_anchor=LONG_ANCHOR,
            parsed_fields=_fields("Kafka"),
            question_type=exercise,
            text_types=TEXT,
        ) is None


def test_word_boundaries_hold_in_the_skill_match():
    """'Java' on the claim list must not pre-fill 'JavaScript' questions."""
    assert rp.evidence_for(
        competency_name="JavaScript",
        category=MUST_HAVE,
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


# ── A BEHAVIOURAL COMPETENCY IS NEVER PRE-FILLED (CR 23, 2026-09-22) ─────────
#
# This module shipped with no category exclusion at all, so a behavioural
# competency whose name appeared in the parsed skills, beside a substantive
# anchor, was recorded and skipped exactly like a technical one. These are the
# tests that were missing.


def test_a_behavioural_competency_is_never_prefilled():
    """Both signals present, both strong, and the answer is still None.

    The inputs here are the ones that DO pre-fill a must-have two tests up.
    Nothing about the evidence changes the outcome, which is the point: a
    behavioural dimension is graded on the account a person gives of a
    situation under this job's framing, and a resume bullet is not an account.
    """
    for question_type in (qt.EVIDENCE_BASED, qt.SHORT_ANSWER):
        assert rp.evidence_for(
            competency_name="Kafka",
            category="behavioural",
            resume_anchor=LONG_ANCHOR,
            parsed_fields=_fields("Kafka", "Postgres"),
            question_type=question_type,
            text_types=TEXT,
        ) is None


def test_the_refusal_is_the_category_and_not_the_wording():
    """A behavioural competency named exactly like a skill is still refused.

    Named for the failure it pins: a reader could assume the guard is a word
    list over competency names, add a name to it, and believe the rule holds.
    The rule is the CATEGORY, which is the one field a hiring manager cannot
    fill in by accident.
    """
    assert rp.evidence_for(
        competency_name="Ownership",
        category="behavioural",
        resume_anchor=LONG_ANCHOR,
        parsed_fields=_fields("Ownership"),
        question_type=qt.EVIDENCE_BASED,
        text_types=TEXT,
    ) is None
    # The same name under a non-behavioural category pre-fills, so the
    # assertion above is about the category and not about the word.
    assert rp.evidence_for(
        competency_name="Ownership",
        category=MUST_HAVE,
        resume_anchor=LONG_ANCHOR,
        parsed_fields=_fields("Ownership"),
        question_type=qt.EVIDENCE_BASED,
        text_types=TEXT,
    ) is not None


def test_category_is_required_and_has_no_default():
    """The argument that closes the defect must not be omittable.

    A default is what let the defect exist for as long as it did, so calling
    `evidence_for` without a category is a TypeError rather than a silent
    behavioural pre-fill.
    """
    import pytest

    with pytest.raises(TypeError):
        rp.evidence_for(  # type: ignore[call-arg]
            competency_name="Kafka",
            resume_anchor=LONG_ANCHOR,
            parsed_fields=_fields("Kafka"),
            question_type=qt.EVIDENCE_BASED,
            text_types=TEXT,
        )


def test_the_never_prefilled_category_matches_ppis_own_constant():
    """The literal here and `ppi.CATEGORY_BEHAVIOURAL` are the same string.

    Restated rather than imported because `ppi` imports this module. The copy
    costs this assertion; the import would cost a cycle. Checked in BOTH
    directions, so neither side can drift alone.
    """
    from app.services import ppi

    assert ppi.CATEGORY_BEHAVIOURAL in rp.NEVER_PREFILLED_CATEGORIES
    assert rp.NEVER_PREFILLED_CATEGORIES <= set(ppi.CATEGORIES)


def test_the_transcript_label_names_which_source_produced_the_answer():
    """Two pre-fill sources, two labels, and no collapsing of one into the other."""
    assert rp.answer_label_for(rp.PREFILL_SOURCE_RESUME) == rp.ANSWER_LABEL
    assert (
        rp.answer_label_for(rp.PREFILL_SOURCE_PORTABLE) == rp.PORTABLE_ANSWER_LABEL
    )
    assert rp.ANSWER_LABEL != rp.PORTABLE_ANSWER_LABEL
    # A row written before `prefill_source` existed could only have come from
    # the resume anchor, so the resume label is a true statement about it
    # rather than a default standing in for an unknown.
    assert rp.answer_label_for(None) == rp.ANSWER_LABEL
