"""The four evidence-tier modifiers, band by band.

These multiply into a candidate's evidence strength, so every band is a
different answer to "how much is this worth". `test_miti_pipeline` covers the
STRUCTURE around them -- the isolation of the evaluators, the model-free
aggregator -- and leaves the bands themselves unwalked, which is where a wrong
comparison operator would live and never be noticed: a modifier that returns
the neutral 1.0 for a band it should discount is invisible in every test that
only checks the pipeline ran.

Each assertion below is about the ORDERING the Runbook argues for, not just the
literal number, so a deliberate re-tune of a value does not fail the file while
an inverted comparison still does.

Pure functions. No database, no network, no model.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.miti import tiering


NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


# ── Specificity: how checkable the answer is ─────────────────────────────────


def test_a_very_short_answer_is_discounted_whatever_it_contains() -> None:
    """Word count matters independently of specifics: a three-word answer has
    nothing to interrogate even if it carries a number."""
    assert tiering.specificity_modifier(has_specifics=True, word_count=3) < 1.0
    assert tiering.specificity_modifier(has_specifics=False, word_count=3) < 1.0


def test_specifics_with_room_to_develop_them_score_highest() -> None:
    detailed = tiering.specificity_modifier(has_specifics=True, word_count=40)
    brief = tiering.specificity_modifier(has_specifics=True, word_count=10)
    assert detailed > brief > 1.0


def test_long_prose_with_no_specifics_is_worth_less_than_a_short_answer() -> None:
    """The argument in the source: an answer with plenty of room for a specific
    and none in it is weaker than a short one that never had room."""
    long_vague = tiering.specificity_modifier(has_specifics=False, word_count=60)
    short_vague = tiering.specificity_modifier(has_specifics=False, word_count=20)
    assert long_vague < short_vague


def test_a_middling_unspecific_answer_is_neutral() -> None:
    assert tiering.specificity_modifier(has_specifics=False, word_count=20) == 1.0


@pytest.mark.parametrize("word_count", [7, 8, 24, 25, 39, 40])
def test_every_specificity_band_stays_inside_a_sane_range(word_count: int) -> None:
    """A modifier outside this range would swamp the other three."""
    for has_specifics in (True, False):
        value = tiering.specificity_modifier(
            has_specifics=has_specifics, word_count=word_count
        )
        assert 0.5 <= value <= 1.25


# ── Scale: evidence at the seniority the role needs ──────────────────────────


def test_evidence_at_exactly_the_needed_scale_is_rewarded() -> None:
    assert (
        tiering.scale_modifier(role_seniority="managerial", evidence_scale="team")
        > 1.0
    )


def test_evidence_broader_than_needed_is_neutral_and_never_a_bonus() -> None:
    """Running an organisation does not make someone better at the individual
    work a non-managerial role is actually asking about."""
    assert (
        tiering.scale_modifier(
            role_seniority="non_managerial", evidence_scale="organisation"
        )
        == 1.0
    )


def test_evidence_one_step_short_is_discounted_less_than_two_steps_short() -> None:
    one_short = tiering.scale_modifier(role_seniority="leadership", evidence_scale="team")
    two_short = tiering.scale_modifier(
        role_seniority="leadership", evidence_scale="individual"
    )
    assert 1.0 > one_short > two_short


def test_an_unknown_scale_is_treated_as_meeting_the_need_rather_than_failing_it() -> None:
    """An unrecognised label is missing information, not evidence of a gap, and
    penalising it would punish a candidate for our vocabulary."""
    assert (
        tiering.scale_modifier(role_seniority="managerial", evidence_scale="squad")
        > 1.0
    )


def test_no_scale_at_all_is_neutral() -> None:
    assert tiering.scale_modifier(role_seniority="managerial", evidence_scale=None) == 1.0
    assert tiering.scale_modifier(role_seniority="managerial", evidence_scale="") == 1.0


# ── Decay: how old the evidence is ───────────────────────────────────────────


def test_undated_evidence_is_not_treated_as_old() -> None:
    """"We do not know when this was" is not "this was long ago"."""
    assert tiering.decay_modifier(as_of=None, now=NOW) == 1.0


def test_recent_evidence_is_undiscounted() -> None:
    assert tiering.decay_modifier(as_of=NOW - timedelta(days=30), now=NOW) == 1.0


def test_evidence_decays_with_age_and_then_stops() -> None:
    """Floored rather than zeroed, for the reason the source gives: it still
    happened."""
    middling = tiering.decay_modifier(as_of=NOW - timedelta(days=1400), now=NOW)
    ancient = tiering.decay_modifier(as_of=NOW - timedelta(days=4000), now=NOW)
    older_still = tiering.decay_modifier(as_of=NOW - timedelta(days=9000), now=NOW)
    assert 1.0 > middling > ancient
    assert ancient == older_still > 0.0


def test_a_client_horizon_replaces_the_platform_curve() -> None:
    """Inside the client's stated horizon nothing is discounted; past it the
    floor applies at once rather than sliding."""
    inside = tiering.decay_modifier(
        as_of=NOW - timedelta(days=900), now=NOW, max_age_days=1000
    )
    outside = tiering.decay_modifier(
        as_of=NOW - timedelta(days=1100), now=NOW, max_age_days=1000
    )
    assert inside == 1.0
    assert outside < 1.0


def test_a_naive_timestamp_is_read_as_utc_rather_than_crashing() -> None:
    """Rows written before timezone awareness still have to be scoreable."""
    naive = (NOW - timedelta(days=10)).replace(tzinfo=None)
    assert tiering.decay_modifier(as_of=naive, now=NOW) == 1.0


def test_evidence_dated_in_the_future_is_not_rewarded() -> None:
    """A clock skew or a typo must not produce an age below zero and a
    multiplier above the maximum."""
    assert tiering.decay_modifier(as_of=NOW + timedelta(days=30), now=NOW) == 1.0
