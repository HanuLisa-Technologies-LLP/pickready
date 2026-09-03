"""Section 10.7's confidence, which is arithmetic and never a self-estimate.

CONFIDENCE IS COUNTS, NOT AN OPINION. The same rule `Verdict` follows: an LLM
judging its own certainty makes the criterion unfalsifiable and fails exactly
when the provider is already failing. So this is a weighted sum over four
measured terms, read from `bands.yaml`, and the tests below are about what the
sum does at its edges rather than about a number.

The edge that matters most is the missing term. A dimension nobody could
measure is not a dimension that scored zero -- INSUFFICIENT EVIDENCE IS NOT
NEGATIVE EVIDENCE, and the practical consequence is the point: a career
changer gets a low-confidence report that goes to a human, rather than a
confidently poor grade that does not. So an absent term is dropped from the sum
and paid for in confidence, never folded in as a zero.

Pure functions over Runbook data. No database, no network, no model.
"""
from __future__ import annotations

import pytest

from app.services.miti import aggregation, caps


FULL = {
    "evidence_coverage": 1.0,
    "evidence_depth": 1.0,
    "independence": 1.0,
    "consistency": 1.0,
}


# ── The weighted sum ─────────────────────────────────────────────────────────


def test_every_term_at_its_maximum_scores_at_the_top() -> None:
    assert aggregation.confidence_score(**FULL) == pytest.approx(1.0, abs=0.02)


def test_every_term_at_zero_scores_at_the_bottom() -> None:
    floored = {key: 0.0 for key in FULL}
    assert aggregation.confidence_score(**floored) == pytest.approx(0.0, abs=0.02)


def test_the_score_rises_with_every_term() -> None:
    """Each term carries a positive coefficient, so improving any one of them
    alone must not lower the result."""
    base = {key: 0.5 for key in FULL}
    baseline = aggregation.confidence_score(**base)
    for term in FULL:
        lifted = dict(base)
        lifted[term] = 1.0
        assert aggregation.confidence_score(**lifted) >= baseline, term


def test_a_missing_term_is_dropped_rather_than_counted_as_zero() -> None:
    """The load-bearing case. Counting an unmeasurable term as zero would turn
    "we could not tell" into "we looked and found nothing", which is the
    confidently-poor-grade outcome the rule exists to prevent."""
    measured = dict(FULL)
    measured["independence"] = None
    dropped = aggregation.confidence_score(**measured)

    as_zero = dict(FULL)
    as_zero["independence"] = 0.0
    counted_zero = aggregation.confidence_score(**as_zero)

    assert dropped > counted_zero


def test_one_term_alone_still_produces_a_score() -> None:
    """A candidate measured on a single axis is low-confidence, not
    unscoreable: the report still has to reach a person."""
    only_one = {key: None for key in FULL}
    only_one["evidence_coverage"] = 1.0
    assert 0.0 <= aggregation.confidence_score(**only_one) <= 1.0


def test_no_measurable_term_at_all_is_refused_rather_than_guessed() -> None:
    """A weighted sum over nothing has no value. Returning zero would state a
    confidence nobody computed, and returning one would be worse."""
    with pytest.raises(caps.CapDataError):
        aggregation.confidence_score(**{key: None for key in FULL})


def test_a_missing_coefficient_stops_the_sum(monkeypatch) -> None:
    """The coefficients are Runbook numbers and are never typed into this
    module, so an absent one cannot be defaulted around."""
    monkeypatch.setattr(
        aggregation,
        "_confidence_data",
        lambda: {"terms": {"evidence_coverage": {}}},
    )
    with pytest.raises(caps.CapDataError):
        aggregation.confidence_score(**FULL)


# ── The label, and the sufficiency floor OR'd into it ────────────────────────


def test_a_sufficiency_breach_forces_the_insufficient_label() -> None:
    """Section 6.7's floor is OR'd in: however well the four terms scored, a
    breach means the evidence base was too thin to describe as anything else."""
    assert (
        aggregation.confidence_label(1.0, sufficiency_breached=True)
        == aggregation.CONFIDENCE_INSUFFICIENT
    )


def test_the_label_falls_as_the_score_falls() -> None:
    """Distinct labels across the range, in order. Two scores far apart sharing
    a label would make the field useless to the person deciding whether to
    read the report closely."""
    labels = [aggregation.confidence_label(score) for score in (0.95, 0.6, 0.2)]
    assert len(set(labels)) > 1, labels
    assert labels[0] != labels[-1]


def test_a_top_score_is_the_highest_label() -> None:
    assert aggregation.confidence_label(1.0) == aggregation.CONFIDENCE_HIGH


def test_every_label_the_module_can_return_is_one_it_declares() -> None:
    known = set(aggregation.CONFIDENCE_LABELS)
    for score in (0.0, 0.1, 0.35, 0.5, 0.7, 0.85, 1.0):
        assert aggregation.confidence_label(score) in known, score
    assert aggregation.confidence_label(0.9, sufficiency_breached=True) in known
