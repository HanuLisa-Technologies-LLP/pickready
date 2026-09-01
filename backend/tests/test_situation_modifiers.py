"""Section 18.4's six situation types, and the one place degrading is right.

SITUATION MISCLASSIFICATION IS THE MOST EXPENSIVE ERROR AT INTAKE, because it
re-weights the WHOLE matrix coherently. Nothing downstream can detect it: there
is no inconsistency to find, only a plausible grade against a role shaped
differently from the one being hired for. Every property below exists because
of that.

THE TWO ABSENCES ARE NOT THE SAME ABSENCE, and this module is the one that
distinguishes them:

  * NO SITUATION TYPE -- a job whose intake predates the feature -- weights
    neutrally. That is the honest reading of an absent input, and refusing
    would mean refusing to generate the matrix at all.
  * A KNOWN SITUATION WHOSE MAGNITUDE CANNOT BE READ raises. That is a FAILED
    lookup, and substituting 1.0 for it would silently deliver a matrix the
    situation never touched while reporting that it had.

A tie between two proposed types is also left as a tie. Breaking one arbitrarily
would hand a coin flip the authority to re-weight a whole matrix, which is what
the human confirmation step exists to prevent.

Pure functions over Runbook data. No database, no network, no model.
"""
from __future__ import annotations

import dataclasses

import pytest

from app.services.hiring import layers, situations


# ── The six types ────────────────────────────────────────────────────────────


def test_section_eighteen_four_has_six_situation_types() -> None:
    assert len(situations.SITUATIONS) == 6


def test_every_type_carries_what_the_confirmation_step_reads_back() -> None:
    """The description is read to a hiring manager verbatim, so it is written
    for them; the emphasis steers what gets ASKED. A type missing either is one
    that re-weights the matrix without anybody being able to confirm it."""
    for key, situation in situations.SITUATIONS.items():
        assert situation.key == key
        assert situation.label
        assert situation.description
        assert situation.evidence_emphasis
        assert situation.rationale


def test_a_row_never_has_an_opinion_about_all_five_dimensions() -> None:
    """A dimension the row does not name is ABSENT rather than neutral. A
    situation with a view on all five would be expressing a preference rather
    than a fact about the role."""
    for situation in situations.SITUATIONS.values():
        assert len(situation.effects) < 5, situation.key
        for arrow in situation.effects.values():
            assert arrow in situations.ARROW_LEVELS, situation.key


def test_the_five_dimensions_have_one_spelling_each_way() -> None:
    """Two spellings of the same five things is how this product ended up with
    two parallel rating scales."""
    assert set(situations.DIMENSION_BY_RUNBOOK_ID) == {"D1", "D2", "D3", "D4", "D5"}
    for runbook_id, name in situations.DIMENSION_BY_RUNBOOK_ID.items():
        assert situations.RUNBOOK_ID_BY_DIMENSION[name] == runbook_id


def test_an_unknown_key_is_not_valid_and_gets_nothing() -> None:
    assert situations.is_valid("turnaround") is True
    assert situations.is_valid("not_a_situation") is False
    assert situations.is_valid(None) is False
    assert situations.get("not_a_situation") is None
    assert situations.get(None) is None
    assert situations.evidence_emphasis(None) == ""


# ── The two absences ─────────────────────────────────────────────────────────


def test_no_situation_type_weights_every_dimension_neutrally() -> None:
    """The honest reading of an ABSENT input: the matrix is generated from
    Layers 1 and 2 alone, which is precisely what "no situation type
    expressed" should mean."""
    for missing in (None, "", "not_a_situation"):
        modifiers = situations.dimension_modifiers(missing)
        assert set(modifiers) == set(situations._ALL_DIMENSIONS), missing
        assert set(modifiers.values()) == {1.0}, missing


def test_a_known_situation_whose_magnitude_is_unreadable_raises(monkeypatch) -> None:
    """A FAILED lookup, not an absent input. Substituting 1.0 would deliver a
    matrix the situation never touched while reporting that it had."""
    monkeypatch.setattr(situations, "_arrow_magnitudes", lambda: {})
    key = next(
        k for k, s in situations.SITUATIONS.items() if s.effects
    )
    with pytest.raises(layers.RunbookDataUnavailable) as excinfo:
        situations.dimension_modifiers(key)
    assert key in str(excinfo.value)


def test_every_type_names_all_five_dimensions_in_its_modifier_map() -> None:
    """So a caller never has to distinguish "unaffected" from "forgotten"."""
    for key in situations.SITUATIONS:
        modifiers = situations.dimension_modifiers(key)
        assert set(modifiers) == set(situations._ALL_DIMENSIONS), key


def test_an_emphasised_dimension_is_lifted_and_a_deprioritised_one_lowered() -> None:
    """The acceptance criterion for this layer is that it MOVES a weight. A
    situation type that appeared in a summary and changed no number would be
    decorative."""
    moved = False
    for key, situation in situations.SITUATIONS.items():
        modifiers = situations.dimension_modifiers(key)
        for dimension, arrow in situation.effects.items():
            if arrow in (situations.STRONG_UP, situations.UP):
                assert modifiers[dimension] > 1.0, (key, dimension)
                moved = True
            elif arrow == situations.DOWN:
                assert modifiers[dimension] < 1.0, (key, dimension)
                moved = True
    assert moved, "no situation type moved any weight"


def test_a_double_arrow_lifts_harder_than_a_single_one() -> None:
    """The arrows are ordinal in section 18.4 and the magnitudes must keep that
    order, or the table's own ranking stops meaning anything."""
    magnitudes = situations._arrow_magnitudes()
    assert magnitudes[situations.STRONG_UP] > magnitudes[situations.UP] > 1.0
    assert magnitudes[situations.DOWN] < 1.0


# ── Applying it, and keeping the provenance ──────────────────────────────────


def test_applying_returns_both_the_result_and_the_multiplier() -> None:
    """A PAIR, because a function returning only the result would make the
    provenance unreconstructable, and provenance is the acceptance criterion
    for this part of the spec."""
    key, situation = next(
        (k, s) for k, s in situations.SITUATIONS.items() if s.effects
    )
    dimension = next(iter(situation.effects))
    weighted, multiplier = situations.apply_to(2.0, dimension, key)
    assert multiplier != 1.0
    assert weighted == pytest.approx(2.0 * multiplier)


def test_a_dimension_the_situation_says_nothing_about_is_left_alone() -> None:
    key, situation = next(
        (k, s) for k, s in situations.SITUATIONS.items()
        if len(s.effects) < len(situations._ALL_DIMENSIONS)
    )
    untouched = next(
        d for d in situations._ALL_DIMENSIONS if d not in situation.effects
    )
    weighted, multiplier = situations.apply_to(2.0, untouched, key)
    assert multiplier == 1.0
    assert weighted == pytest.approx(2.0)


def test_no_situation_leaves_the_baseline_exactly_where_it_was() -> None:
    weighted, multiplier = situations.apply_to(
        2.0, situations._ALL_DIMENSIONS[0], None
    )
    assert (weighted, multiplier) == (2.0, 1.0)


# ── Proposing a type from the SWOT text ──────────────────────────────────────


def test_signals_propose_and_are_ordered_strongest_first() -> None:
    key, situation = next(
        (k, s) for k, s in situations.SITUATIONS.items() if s.signals
    )
    scored = situations.classify_signals([situation.signals[0].upper()])
    assert scored
    assert scored[0][0] == key or any(row[0] == key for row in scored)
    counts = [hits for _key, hits, _matched in scored]
    assert counts == sorted(counts, reverse=True)


def test_swot_text_matching_nothing_proposes_nothing() -> None:
    """No default type. Proposing one from silence is the misclassification
    this module says is the most expensive error at intake."""
    assert situations.classify_signals(["xyzzy plugh frobnicate"]) == []
    assert situations.classify_signals([]) == []
    assert situations.classify_signals(["", None]) == []


def test_a_tie_is_left_as_a_tie() -> None:
    """Breaking one arbitrarily would hand a coin flip the authority to
    re-weight a whole matrix. The order is by count then key, so it is stable
    and reproducible, but two rows keep the same count."""
    first, second = list(situations.SITUATIONS.values())[:2]
    scored = situations.classify_signals([first.signals[0], second.signals[0]])
    counts = {key: hits for key, hits, _ in scored}
    assert counts.get(first.key) == counts.get(second.key)


def test_the_matched_phrases_are_returned_so_the_prompt_can_quote_them() -> None:
    """A confirmation that cannot say WHY it proposed a type is asking a hiring
    manager to agree with a black box."""
    key, situation = next(
        (k, s) for k, s in situations.SITUATIONS.items() if s.signals
    )
    scored = situations.classify_signals([situation.signals[0]])
    row = next(r for r in scored if r[0] == key)
    assert situation.signals[0] in row[2]


# ── The confirmation prompt ──────────────────────────────────────────────────


def test_the_prompt_offers_the_alternative_it_is_confused_with() -> None:
    """"Is it this or that" is a far easier question to answer correctly than
    "is this right", which people agree to."""
    key, situation = next(
        (k, s) for k, s in situations.SITUATIONS.items() if s.confused_with
    )
    prompt = situations.confirmation_prompt(key)
    assert situation.label in prompt
    for other in situation.confused_with:
        assert situations.SITUATIONS[other].label in prompt


def test_the_prompt_quotes_the_evidence_when_there_is_some() -> None:
    key = next(iter(situations.SITUATIONS))
    with_evidence = situations.confirmation_prompt(
        key, evidence=["the two failed launches", "  ", "the board pressure"]
    )
    assert "the two failed launches" in with_evidence
    assert "the board pressure" in with_evidence
    assert "the two failed launches" not in situations.confirmation_prompt(key)


def test_the_prompt_carries_no_number(monkeypatch) -> None:
    """The weights behind it are internal, and a hiring manager reading "Track
    Record x1.35" would be reading a number the product does not show and could
    not usefully argue with."""
    for key in situations.SITUATIONS:
        prompt = situations.confirmation_prompt(key, evidence=["the rebuild"])
        assert not any(char.isdigit() for char in prompt), (key, prompt)


def test_the_prompt_survives_a_data_outage(monkeypatch) -> None:
    """It reads the section 18.4 ARROWS rather than the resolved multipliers. A
    confirmation step that stopped working during a data outage would close the
    session on an unconfirmed classification, which is the error this module
    calls the most expensive available at intake."""
    def _unavailable():
        raise layers.RunbookDataUnavailable("situation_types.yaml is unreadable")

    monkeypatch.setattr(situations, "_arrow_magnitudes", _unavailable)
    for key in situations.SITUATIONS:
        assert situations.confirmation_prompt(key)


# ── The artifact projection ──────────────────────────────────────────────────


def test_the_artifact_carries_the_word_and_never_the_weight() -> None:
    """Evidence emphasis is a WORD, safe on an artifact a recruiter reads. The
    modifiers are internal ranking data like every other weight."""
    for key in situations.SITUATIONS:
        projected = situations.as_dict(key)
        assert set(projected) == {
            "key", "label", "description", "evidence_emphasis"
        }, projected
        assert "effects" not in projected
