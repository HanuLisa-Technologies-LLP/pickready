"""The release gate, and the one property that makes it worth having.

A gate that passes when it could not measure anything is worse than no gate:
it converts a broken evaluation harness into a green build, silently, for every
release after the breakage. Most of this file is about that single branch.
"""
from __future__ import annotations

import types

from app.evaluation import release_gate as gate


def _result(**metrics):
    """A `JudgeResult`-shaped stand-in exposing `as_dict`."""
    return types.SimpleNamespace(as_dict=lambda: dict(metrics))


# ── unavailable is not a pass ────────────────────────────────────────────────


def test_an_unmeasurable_metric_blocks_and_is_not_a_pass() -> None:
    """THE POINT OF THE FILE.

    This is the state the product is in today: the reasoning and decision sets
    are empty because they must be human labelled. The gate must refuse, and
    must refuse in a way that cannot be mistaken for either a pass or a zero.
    """
    outcome = gate.evaluate(_result(mcc=None, cohens_kappa=None), floor=0.6)
    assert outcome.outcome == gate.UNAVAILABLE
    assert outcome.releasable is False
    assert outcome.observed is None
    assert "human labelled" in outcome.reason


def test_unavailable_is_a_third_outcome_and_not_a_spelling_of_fail() -> None:
    """They need different fixes, so they must not be one value.

    A FAIL means the system got worse. An UNAVAILABLE means nobody knows, and
    the action is to go and label a dataset. Collapsing them would send someone
    to debug a regression that was never measured.
    """
    assert gate.UNAVAILABLE not in gate.RELEASING_OUTCOMES
    assert gate.FAIL not in gate.RELEASING_OUTCOMES
    assert gate.RELEASING_OUTCOMES == frozenset({gate.PASS})


def test_a_missing_metric_key_is_unavailable_rather_than_an_exception() -> None:
    """A harness that changed its output shape must block, not crash.

    A crash in a gate is often caught by whatever runs it and reported as a
    step failure that someone reruns. An UNAVAILABLE is a decision.
    """
    outcome = gate.evaluate(_result(raw_agreement=0.99), floor=0.6)
    assert outcome.outcome == gate.UNAVAILABLE


# ── raw agreement must never be gated on ─────────────────────────────────────


def test_raw_agreement_alone_never_satisfies_the_gate() -> None:
    """It overstates chance-corrected agreement by a mean of 38.6 points.

    A gate reading it would pass exactly the releases a kappa refuses, which is
    the failure the whole reporting protocol exists to prevent.
    """
    outcome = gate.evaluate(_result(raw_agreement=1.0), floor=0.9)
    assert outcome.outcome == gate.UNAVAILABLE
    assert outcome.observed is None


def test_mcc_is_preferred_over_kappa_when_both_are_present() -> None:
    outcome = gate.evaluate(_result(mcc=0.80, cohens_kappa=0.10), floor=0.6)
    assert outcome.outcome == gate.PASS
    assert outcome.details["metric"] == "mcc"


def test_kappa_is_used_when_mcc_is_absent() -> None:
    outcome = gate.evaluate(_result(cohens_kappa=0.72), floor=0.6)
    assert outcome.outcome == gate.PASS
    assert outcome.details["metric"] == "cohens_kappa"


# ── the floor, and the noise band around a fall ──────────────────────────────


def test_below_the_floor_fails() -> None:
    outcome = gate.evaluate(_result(mcc=0.42), floor=0.6)
    assert outcome.outcome == gate.FAIL
    assert outcome.releasable is False


def test_a_fall_inside_the_measured_noise_band_is_reported_not_failed() -> None:
    """The judge disagreeing with itself is not a regression.

    W7.2 measured the worst pooled self-disagreement at 0.03. Failing on a
    smaller movement than that would make the gate fire on noise, and a gate
    that fires on noise is a gate somebody switches off.
    """
    outcome = gate.evaluate(_result(mcc=0.78), floor=0.6, baseline=0.80)
    assert outcome.outcome == gate.PASS
    assert outcome.releasable is True
    # It is REPORTED, so the fall is visible even though it passed.
    assert "fell" in outcome.reason
    assert outcome.details["drop"] > 0


def test_a_fall_wider_than_the_band_fails_even_above_the_floor() -> None:
    """Clearing the floor is not enough when the system has got worse.

    Otherwise a component that started high could decay release by release,
    each step inside its own comfortable margin, and never trip anything.
    """
    outcome = gate.evaluate(_result(mcc=0.62), floor=0.6, baseline=0.95)
    assert outcome.outcome == gate.FAIL
    assert "wider than" in outcome.reason


def test_an_improvement_against_a_baseline_passes() -> None:
    outcome = gate.evaluate(_result(mcc=0.90), floor=0.6, baseline=0.70)
    assert outcome.outcome == gate.PASS


# ── the threshold is derived from the measurement, not typed ─────────────────


def test_the_noise_band_is_derived_from_the_measured_probe() -> None:
    """A gate constant somebody typed is a gate constant somebody will retype.

    If the probe is re-run and `MEASURED_SELF_DISAGREEMENT` moves, the band
    must move with it. Pinning the product rather than the literal is what
    makes that true.
    """
    assert gate.NOISE_BAND == gate.MEASURED_SELF_DISAGREEMENT * gate.NOISE_MULTIPLIER
    # And the measured value is the one W7.2 actually produced, not a guess.
    assert gate.MEASURED_SELF_DISAGREEMENT == 0.03
    assert gate.MEASURED_WORST_CASE_DISAGREEMENT == 0.15


def test_the_band_is_wider_than_the_judges_own_noise() -> None:
    """The property that stops the gate firing on the judge itself.

    Below 1x, a judge that merely re-rolled its own dispersion would fail a
    release. The multiplier is a choice; that it exceeds 1 is a requirement.
    """
    assert gate.NOISE_BAND > gate.MEASURED_SELF_DISAGREEMENT
