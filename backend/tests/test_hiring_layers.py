"""The layers engine and the Layer 1 department models Miti still reads.

The seven-stage transformation that used to be the centre of this file, and
its acceptance criterion ("a Layer 3 input demonstrably moves a weight"), were
DELETED in the Vivekium release with the Tatva matrix compiler: skills carry a
bucket, a priority and an evidence line, and no weight is derived on the
job-setup path any more. What remains here is what the scoring path still
reads until the grading phase moves it: the precedence and bounds engine
(`layers`), the department models, the ontology and the situations table.
"""
from __future__ import annotations

import pytest

from app.services.hiring import (
    department_models,
    layers,
    situations,
)

#: A magnitude for each §18.4 arrow, SUPPLIED BY THE TEST rather than read from
#: the product.
#:
#: RPN-PHIL-001 §18.4 states its weight consequences as arrows and attaches no
#: multiplier to them, and §11.3 supplies an additive bound for only four of the
#: six situation types. So the product has no value here yet and
#: `situations._arrow_magnitudes` raises, by design, rather than inventing one
#: (RUNBOOK-AMBIGUITY, recorded in RUNBOOK_OPEN_QUESTIONS_PHASE0B.md).
#:
#: The tests below are about the MECHANISM -- that a Layer 3 input reaches the
#: output and that the provenance names it -- and that mechanism is testable
#: without settling the open question. Pinning a product magnitude here would
#: also quietly answer the question in a test file, which is the worst place for
#: an unreviewed number to live.
_TEST_ARROW_MAGNITUDES = {
    situations.STRONG_UP: 1.30,
    situations.UP: 1.15,
    situations.DOWN: 0.85,
}


@pytest.fixture(autouse=True)
def _arrow_magnitudes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        situations, "_arrow_magnitudes", lambda: dict(_TEST_ARROW_MAGNITUDES)
    )


# ── Precedence ───────────────────────────────────────────────────────────────


def test_the_layers_are_strictly_ordered() -> None:
    assert layers.LAYERS == ("platform", "company", "role")
    assert layers.layer_rank("platform") < layers.layer_rank("company")
    assert layers.layer_rank("company") < layers.layer_rank("role")


def test_an_unknown_layer_raises_rather_than_defaulting() -> None:
    """A typo defaulting to `platform` would grant a role-level input the
    authority to overrule the platform, which is the precise inversion the whole
    module exists to prevent."""
    with pytest.raises(ValueError):
        layers.layer_rank("tenant")


def test_a_lower_layer_may_tune_within_bounds() -> None:
    resolution = layers.resolve("competency_weight", company={"delivery": 1.4})
    assert resolution.multiplier_for("delivery") == pytest.approx(1.4)
    assert not resolution.refusals


def test_a_lower_layer_may_not_tune_past_the_bound() -> None:
    """Clamped, and the clamp is RECORDED. A clamp that left no trace would be
    indistinguishable from an input that was already in range, so nobody would
    ever learn a client asked for something the platform will not do."""
    resolution = layers.resolve("competency_weight", company={"delivery": 9.0})
    bound = layers.BOUNDS["competency_weight"]
    assert resolution.multiplier_for("delivery") == bound.high
    assert resolution.adjustments[0].clamped
    assert resolution.adjustments[0].requested == 9.0


def test_a_competency_can_never_be_weighted_to_zero() -> None:
    """A 0.0 floor would let a client silently REMOVE a requirement the
    department model considers material, rather than argue with it."""
    resolution = layers.resolve("competency_weight", company={"delivery": 0.0})
    assert resolution.multiplier_for("delivery") >= layers.BOUNDS["competency_weight"].low
    assert resolution.multiplier_for("delivery") > 0


def test_two_layers_compose_rather_than_overwrite() -> None:
    """A company that weights delivery highly and a role that also does should
    end up higher than either alone; a role pulling the other way should partly
    cancel rather than erase."""
    both_up = layers.resolve(
        "competency_weight", company={"delivery": 1.3}, role={"delivery": 1.3}
    )
    one_up = layers.resolve("competency_weight", company={"delivery": 1.3})
    assert both_up.multiplier_for("delivery") > one_up.multiplier_for("delivery")

    opposed = layers.resolve(
        "competency_weight", company={"delivery": 1.5}, role={"delivery": 0.7}
    )
    assert 0.9 < opposed.multiplier_for("delivery") < 1.2


def test_the_composed_product_is_clamped_too() -> None:
    """Each step was in range; the outcome has to be as well, or "within
    declared bounds" is a claim about the steps and not about the result."""
    resolution = layers.resolve(
        "competency_weight", company={"delivery": 2.0}, role={"delivery": 2.0}
    )
    assert resolution.multiplier_for("delivery") == layers.BOUNDS["competency_weight"].high


def test_an_integrity_rule_is_refused_outright_not_clamped() -> None:
    """There is no bound inside which switching off the Must-have cap is
    acceptable, so there is no clamp -- only a refusal, and it is recorded."""
    for key in ("disable_must_have_cap", "auto_reject_on_flag", "skip_authenticity"):
        resolution = layers.resolve(key, company={"x": 1.0})
        assert resolution.refusals, key
        assert not resolution.multipliers, key


def test_a_no_flag_auto_rejection_cannot_be_configured() -> None:
    assert layers.is_invariant("auto_reject_on_flag")
    assert layers.is_invariant("skip_human_review")


def test_an_undeclared_quantity_raises_rather_than_being_unbounded() -> None:
    with pytest.raises(ValueError, match="No bound declared"):
        layers.resolve("some_new_knob", company={"x": 2.0})


def test_a_non_numeric_modifier_is_refused_not_coerced() -> None:
    resolution = layers.resolve("competency_weight", company={"delivery": "lots"})
    assert resolution.refusals
    assert "delivery" not in resolution.multipliers


def test_true_is_not_a_multiplier_of_one() -> None:
    """`float(True)` is 1.0, so a checkbox reaching this function would silently
    read as "no change" instead of as the malformed input it is."""
    resolution = layers.resolve("competency_weight", company={"delivery": True})
    assert resolution.refusals


# ── Layer 1 ──────────────────────────────────────────────────────────────────


def test_every_department_model_is_well_formed() -> None:
    for key, model in department_models.DEPARTMENTS.items():
        assert model.key == key
        assert model.competencies, key
        for competency in model.competencies:
            assert competency.baseline_weight > 0, competency.key
            assert competency.observable_evidence.strip(), competency.key
            assert competency.evidence_sources, competency.key
            assert competency.primary_dimension, competency.key
            for source in competency.evidence_sources:
                assert source in department_models.EVIDENCE_SOURCES, source
            for seniority in competency.seniorities:
                assert seniority in department_models.SENIORITIES


def test_every_department_has_an_anchor_for_every_seniority() -> None:
    """An evaluator handed the wrong anchor grades the wrong job, which shows up
    as a senior candidate reading as merely competent -- wrong, and not
    obviously so."""
    for key, model in department_models.DEPARTMENTS.items():
        for seniority in department_models.SENIORITIES:
            assert department_models.rubric_anchors(model, seniority).strip(), (
                key,
                seniority,
            )


def test_people_leadership_is_absent_for_non_managerial_roles() -> None:
    """ABSENT, not weighted to zero. A zero-weight row is a thing a recruiter
    has to read and dismiss."""
    junior = department_models.baseline_for("generic", "non_managerial")
    senior = department_models.baseline_for("generic", "managerial")
    assert "people_leadership" not in {c.key for c in junior}
    assert "people_leadership" in {c.key for c in senior}


def test_a_department_resolves_from_a_job_title() -> None:
    assert department_models.department_for("Senior Backend Engineer").key == "engineering"
    assert department_models.department_for("Enterprise Account Executive").key == "sales"
    assert department_models.department_for("Financial Controller").key == "finance"
    assert department_models.department_for("Warehouse Operations Lead").key == "operations"


def test_an_unrecognised_title_gets_the_generic_baseline_not_none() -> None:
    """A matrix built with NO baseline is exactly the "opaque model output"
    spec-doc5 is replacing. A weaker baseline is not an absent one."""
    assert department_models.department_for("Chief Vibes Officer").key == "generic"
    assert department_models.department_for(None, "").key == "generic"


def test_an_unmatched_phrase_returns_none_rather_than_a_best_guess() -> None:
    """Forcing a role-specific phrase onto the nearest baseline would quietly
    relabel it as something the department model already knew about -- which
    looks like traceability and is not."""
    assert department_models.match_competency(
        "must speak conversational Japanese", "engineering", "managerial"
    ) is None


def test_an_alias_resolves_to_its_competency() -> None:
    matched = department_models.match_competency(
        "we need someone with real on-call experience", "engineering", "managerial"
    )
    assert matched is not None
    assert matched.key == "production_ownership"


# ── Bounds the scoring path still reads ──────────────────────────────────────


def test_the_evidence_threshold_bound_stays_asymmetric() -> None:
    """The asymmetry that governs the evidence bar, asserted on the BOUND.

    Demanding more corroboration is always safe and demanding less is how a
    Must-have bar stops being one, so `evidence_threshold` may be raised far
    and lowered only marginally. Nothing supplies a modifier for it today, and
    that is exactly why the bound is asserted here rather than through a
    caller: an asymmetry with no live supplier is the one most likely to be
    "simplified" to a symmetric range by somebody who cannot see what it was
    protecting.
    """
    bound = layers.BOUNDS["evidence_threshold"]
    assert bound.low > 0.5, "the bar may be lowered only marginally"
    assert bound.high >= 2.0, "the bar may be raised freely"
    assert (1.0 - bound.low) < (bound.high - 1.0), (
        "the bound has become symmetric; lowering an evidence requirement is "
        "not as safe as raising one"
    )


# ── Weights are internal ─────────────────────────────────────────────────────


def test_matching_still_has_no_weights_table() -> None:
    """The 2026-07-30 deletion stands. Those weights were a fixed table applied
    to every role AND were shown to the client as "35% role-fit weighting".
    Neither fault is reintroduced: these weights are per-job, derived from three
    declared layers, and never cross an API boundary."""
    import app.services.matching as matching

    assert not hasattr(matching, "WEIGHTS")
