"""Sutra's seven stages: an item enters the matrix only when all of them ran.

`Item.is_complete` refuses at BUILD, not later, and the reason is in the
refusal text the builder raises: "Nothing enters the Tatva matrix without
completing all seven." A partially-transformed item is one whose grade rests on
a stage nobody ran, and it would not look wrong on the way past -- a criterion
with no observable evidence still renders, still gets asked about, and still
produces a band.

So each stage is checked on its own below. A single "is it complete" test that
only ever passes a complete item proves the happy path and nothing about the
six ways an item can be short.

`test_hiring_layers` covers the layering contract and
`test_runbook_reconciliation` covers the values. This is the completeness gate
between them.

Pure dataclasses. No database, no network, no model.
"""
from __future__ import annotations

import dataclasses

import pytest

from app.services.hiring import transformation


def _weight(value: float = 1.0) -> transformation.Weight:
    return transformation.Weight(
        value=value,
        baseline=1.0,
        company=1.0,
        situation=1.0,
        role=1.0,
        baseline_source="test",
        dimension="verified_competence",
        provenance={"raw_value": value},
    )


def _threshold(independence: int = 2) -> transformation.Threshold:
    return transformation.Threshold(
        independence_required=independence, level=1.0, max_age_days=None
    )


def _item(**overrides) -> transformation.Item:
    fields = {
        "name": "Stream processing",
        "category": "must_have",
        "anchor_key": "verified_competence",
        "dimension": "verified_competence",
        "observable_evidence": "has rebalanced partitions under load",
        "evidence_sources": next(iter(transformation.EVIDENCE_SOURCES)),
        "assessment_method": transformation.METHODS[0],
        "unreachable_sources": (),
        "weight": _weight(),
        "threshold": _threshold(),
    }
    # `evidence_sources` is a tuple of sources; take one the module declares
    # rather than inventing a name the stage-3 filter would drop.
    fields["evidence_sources"] = (fields["evidence_sources"],)
    fields.update(overrides)
    return transformation.Item(**fields)


# ── The happy path, so the negatives below mean something ────────────────────


def test_an_item_with_all_seven_stages_is_complete() -> None:
    item = _item()
    assert item.missing_stages() == []
    assert item.is_complete() is True


def test_the_optional_seventh_stage_does_not_block_completeness() -> None:
    """A disqualifier applies "if applicable"; requiring one would refuse every
    ordinary criterion."""
    assert _item(disqualifier=None).is_complete() is True
    assert _item(disqualifier="Must hold a valid practising licence").is_complete() is True


# ── Each stage, missing on its own ───────────────────────────────────────────


def test_stage_one_a_nameless_item_is_refused() -> None:
    """A matrix item with no name is a criterion nobody can be graded against."""
    assert transformation.STAGE_COMPETENCY in _item(name="   ").missing_stages()


def test_stage_two_no_observable_evidence_is_refused() -> None:
    """Without it the criterion is an adjective, and an adjective cannot be
    probed or evidenced -- the same bar Company DNA section 3 enforces."""
    assert transformation.STAGE_OBSERVABLE in _item(observable_evidence="  ").missing_stages()


def test_stage_three_no_evidence_source_is_refused() -> None:
    """Nothing says where the evidence would come from, so nothing can be
    collected and the item can only ever grade on absence."""
    assert transformation.STAGE_SOURCES in _item(evidence_sources=()).missing_stages()


def test_stage_four_a_method_outside_the_declared_set_is_refused() -> None:
    """An unrecognised method is not a new way of assessing; it is a value
    nothing downstream knows how to run."""
    assert (
        transformation.STAGE_METHOD
        in _item(assessment_method="telepathy").missing_stages()
    )


def test_stage_five_a_missing_or_zero_weight_is_refused() -> None:
    """Zero is refused as firmly as absent: a criterion weighted zero is one
    nobody is accountable for, while still appearing in the matrix as though
    it counts."""
    assert transformation.STAGE_WEIGHT in _item(weight=None).missing_stages()
    assert transformation.STAGE_WEIGHT in _item(weight=_weight(0.0)).missing_stages()


def test_stage_six_a_threshold_requiring_no_independence_is_refused() -> None:
    """Zero independent pieces means one person saying one thing once is
    enough, which is the corroboration rule with the corroboration removed."""
    assert transformation.STAGE_THRESHOLD in _item(threshold=None).missing_stages()
    assert (
        transformation.STAGE_THRESHOLD
        in _item(threshold=_threshold(0)).missing_stages()
    )


def test_several_missing_stages_are_all_reported_together() -> None:
    """One at a time would mean a builder fixes six items in six passes, and
    the sixth failure looks like a new problem rather than the rest of the
    first one."""
    missing = _item(
        name="", observable_evidence="", evidence_sources=(), weight=None
    ).missing_stages()
    assert {
        transformation.STAGE_COMPETENCY,
        transformation.STAGE_OBSERVABLE,
        transformation.STAGE_SOURCES,
        transformation.STAGE_WEIGHT,
    } <= set(missing)


def test_every_reported_stage_is_one_the_module_names() -> None:
    """A stage reported under an ad-hoc string could never be matched against
    the constants a caller branches on."""
    declared = {
        transformation.STAGE_COMPETENCY,
        transformation.STAGE_OBSERVABLE,
        transformation.STAGE_SOURCES,
        transformation.STAGE_METHOD,
        transformation.STAGE_WEIGHT,
        transformation.STAGE_THRESHOLD,
        transformation.STAGE_DISQUALIFIER,
    }
    everything_missing = transformation.Item(
        name="",
        category="must_have",
        anchor_key=None,
        dimension="verified_competence",
        observable_evidence="",
        evidence_sources=(),
        assessment_method="",
        unreachable_sources=(),
        weight=None,
        threshold=None,
    )
    assert set(everything_missing.missing_stages()) <= declared


# ── Provenance ───────────────────────────────────────────────────────────────


def test_a_role_specific_item_carries_no_anchor_and_that_is_honest() -> None:
    """`match_competency` returns None rather than a best guess: forcing a
    role phrase onto the nearest baseline would relabel it as something the
    department model already knew about, which looks like traceability and is
    not."""
    item = _item(anchor_key=None)
    assert item.is_complete() is True
    assert item.anchor_key is None


def test_the_weight_keeps_all_four_terms_it_was_built_from() -> None:
    """"Why is this weighted 1.62" is answered by reading the row rather than
    by rerunning the pipeline. That is the acceptance criterion, not a
    nicety."""
    weight = _item().weight
    for term in ("baseline", "company", "situation", "role"):
        assert getattr(weight, term) is not None, term
    assert dataclasses.asdict(weight)["provenance"]["raw_value"] is not None
