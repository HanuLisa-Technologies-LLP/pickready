"""What the client is asked to confirm, branch by branch.

`plain_language` turns the compiled artifact into the sentences Bodha reads
back before a Company DNA session closes. It is the only place a client sees
what they have actually configured, so every arm of it is a thing somebody will
be asked to agree to, and an arm nobody exercises is a sentence nobody has read.

`test_company_dna_compilation` already covers the properties of the OUTPUT as a
whole: determinism, freedom from model calls, the absence of numbers, the
narrow exit. What it does not do is walk the arms -- the raised phrasing versus
the lowered one, each recency band, a bar above the standard one versus below
it versus level with it. Those are the branches this file exists for, and each
assertion is about the sentence a client would read rather than about coverage.

Pure functions, plain dicts, no database and no network.
"""
from __future__ import annotations

import pytest

from app.services.hiring import dna_compilation as compilation


def _sections(document: dict) -> dict[str, list[str]]:
    """`plain_language` output flattened to {section key: lines}."""
    return {block["key"]: block["lines"] for block in compilation.plain_language(document)}


def _all_text(document: dict) -> str:
    return " ".join(
        line for lines in _sections(document).values() for line in lines
    ).lower()


# ── Emphasis: the trade-offs the client leaned on ────────────────────────────


def test_a_raised_dimension_and_a_lowered_one_read_differently() -> None:
    """The two arms are opposite promises and must not collapse into one."""
    raised = _sections({"weight_modifiers": {"track_record_impact": 1.35}})["emphasis"]
    lowered = _sections({"weight_modifiers": {"track_record_impact": 0.75}})["emphasis"]
    assert raised and lowered
    assert raised != lowered
    assert any("look harder at" in line for line in raised)
    assert any("given what you told us" in line.lower() for line in lowered)


def test_a_dimension_left_at_the_default_says_nothing() -> None:
    """1.0 is "no opinion", and a sentence about it would be noise the client
    still has to read and agree to."""
    lines = _sections({"weight_modifiers": {"track_record_impact": 1.0}})["emphasis"]
    assert not any("look harder" in line for line in lines)


def test_no_lean_at_all_still_produces_a_statement() -> None:
    """An empty section would read as though the question was never asked."""
    lines = _sections({"weight_modifiers": {}})["emphasis"]
    assert lines, "a client with no leaning must still be told what that means"


def test_an_unparseable_modifier_is_skipped_rather_than_crashing() -> None:
    """The artifact is data; a bad value must not take the readback down and
    must not invent a sentence either."""
    lines = _sections({"weight_modifiers": {"track_record_impact": "not-a-number"}})["emphasis"]
    assert not any("look harder" in line for line in lines)


def test_an_unknown_dimension_name_produces_no_sentence() -> None:
    """A dimension with no phrasing has nothing truthful to say about it."""
    lines = _sections({"weight_modifiers": {"invented_dimension": 1.5}})["emphasis"]
    assert not any("look harder" in line for line in lines)


# ── Evidence: how old is too old ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "days,expected",
    [
        (365, "has to be current"),
        (730, "has to be current"),
        (1000, "fairly recent"),
        (1500, "fairly recent"),
        (2000, "older experience still counts"),
    ],
)
def test_each_recency_band_reads_as_its_own_promise(days: int, expected: str) -> None:
    """The boundaries are inclusive upward, matching every other threshold in
    this product, and each band says something materially different to a
    client about whose experience will still count."""
    lines = _sections({"evidence_max_age_days": days})["evidence"]
    assert any(expected in line.lower() for line in lines), (days, lines)


def test_a_missing_recency_setting_still_states_a_position() -> None:
    lines = _sections({})["evidence"]
    assert lines, "silence here would leave the client agreeing to nothing"


def test_a_non_integer_recency_falls_back_rather_than_guessing() -> None:
    lines = _sections({"evidence_max_age_days": "recent"})["evidence"]
    assert lines
    assert not any("has to be current" in line.lower() for line in lines)


# ── The bar: above, below, or level with the standard one ────────────────────


@pytest.mark.parametrize(
    "level,expected",
    [
        (1.2, "above our standard"),
        (0.8, "below our standard"),
        (1.0, "at our standard"),
    ],
)
def test_the_threshold_readback_names_the_direction(level: float, expected: str) -> None:
    """A client raising their bar and a client lowering it are told opposite
    things about how many people will reach them."""
    text = _all_text({"threshold_modifier": level})
    assert expected in text, (level, text)


def test_a_threshold_that_is_not_a_number_is_refused_loudly() -> None:
    """Not skipped. A bar the compiler cannot read is a bar nobody can confirm,
    and quietly presenting the standard one would have the client agree to a
    setting that is not theirs."""
    with pytest.raises(Exception) as excinfo:
        compilation.plain_language({"threshold_modifier": "high"})
    assert "threshold_modifier" in str(excinfo.value)


# ── The standing rule the readback exists to make checkable ──────────────────


def test_no_arm_of_the_readback_leaks_a_number() -> None:
    """Every branch above, in one document. The no-numbers rule is what makes
    the restatement checkable by the person who has to check it: a client
    confirming a multiplier is confirming that arithmetic looks plausible."""
    import re

    text = " ".join(
        line
        for lines in _sections(
            {
                "weight_modifiers": {"track_record_impact": 1.35, "trajectory_potential": 0.7},
                "evidence_max_age_days": 1000,
                "threshold_modifier": 1.2,
            }
        ).values()
        for line in lines
    )
    assert not re.search(r"\d", text), text
