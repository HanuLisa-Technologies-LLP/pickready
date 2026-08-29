"""The three-layer framework: precedence, bounds, and the weights moving.

THE CENTRAL TEST IN THIS FILE is
`test_a_layer_2_change_moves_a_weight_in_the_output`, which is spec-doc5's
acceptance criterion executed:

    "Sutra's matrix generation visibly runs the seven-stage transformation
     pipeline; a change to a Layer 2 or Layer 3 input demonstrably moves a
     weight in the output -- not just appears in a summary."

Note the "not just appears in a summary". The test asserts the NUMBER moves and
that the provenance names which layer moved it, because a summary that mentions
a company preference while every weight stays put is exactly the shape of
traceability theatre the criterion is written against.
"""
from __future__ import annotations

import pytest

from app.services.hiring import (
    company_dna,
    department_models,
    layers,
    ontology,
    situations,
    swot_quality,
    transformation,
)

DEPT = "engineering"
SENIORITY = "managerial"
#: Resolves to the `production_ownership` anchor via its "reliability" alias.
PHRASE = "reliability of what they ship - they must own it in production"


def _item(**kwargs):
    return transformation.build_item(
        phrase=PHRASE, category="must_have", department=DEPT, seniority=SENIORITY, **kwargs
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


# ── The seven stages ─────────────────────────────────────────────────────────


def test_all_seven_stages_are_named() -> None:
    assert transformation.STAGES == (
        "competency",
        "observable_evidence",
        "evidence_sources",
        "assessment_method",
        "weight",
        "threshold",
        "disqualifier",
    )


def test_an_item_completes_every_required_stage() -> None:
    item = _item()
    assert item.is_complete()
    assert not item.missing_stages()
    assert item.anchor_key == "production_ownership"
    assert item.observable_evidence.strip()
    assert item.evidence_sources
    assert item.assessment_method in transformation.METHODS
    assert item.weight.value > 0
    assert item.threshold.independence_required >= 1


def test_nothing_enters_the_matrix_without_stage_two() -> None:
    """spec-doc5: "Nothing enters the Tatva matrix without completing all seven
    stages." An item with no observable evidence is a criterion whose grade
    rests on an adjective."""
    with pytest.raises(transformation.TransformationError, match="Stage 2"):
        transformation.build_item(
            phrase="must have gravitas",
            category="must_have",
            department=DEPT,
            seniority=SENIORITY,
        )


def test_a_role_specific_phrase_transforms_when_evidence_is_supplied() -> None:
    """No Layer 1 anchor is an honest provenance, not a failure."""
    item = transformation.build_item(
        phrase="must be able to read Japanese technical documentation",
        category="must_have",
        department=DEPT,
        seniority=SENIORITY,
        observable_evidence=(
            "Has worked from a Japanese-language specification and can describe "
            "what it required them to resolve"
        ),
    )
    assert item.is_complete()
    assert item.anchor_key is None
    assert item.weight.baseline_source is None
    assert item.weight.baseline == transformation.NEUTRAL_BASELINE


def test_the_method_is_chosen_by_the_evidence_sources() -> None:
    """Picking a method first and then asking what evidence it produces is how a
    competency ends up probed by a question that cannot evidence it."""
    item = _item()
    assert item.assessment_method == transformation.METHOD_WORKED_EXAMPLE


def test_an_out_of_band_source_is_recorded_rather_than_counted_as_met() -> None:
    """The platform cannot call a reference. Pretending a competency is
    evidenced because a reference WOULD have evidenced it is the same error as a
    timestamp standing in for work that happened."""
    item = transformation.build_item(
        phrase="developing other people",
        category="behavioural",
        department="generic",
        seniority="managerial",
    )
    assert "reference" in item.unreachable_sources
    assert item.assessment_method != transformation.METHOD_OUT_OF_BAND


# ── THE ACCEPTANCE CRITERION ─────────────────────────────────────────────────


def test_a_layer_3_change_moves_a_weight_in_the_output() -> None:
    """RPN-PHIL-001 §18.4, reconciled.

    CORRECTED AGAINST THE RUNBOOK. This test previously asserted that a
    Greenfield weights Track Record DOWN, which is what the pre-Runbook
    implementation did and is not what §18.4 says: the Greenfield row is
    "D5 up-up, D3 up" and does not mention D2 at all. A situation type that
    silently cut a dimension the Runbook leaves alone is exactly the class of
    error §18.4 warns about, because the resulting matrix is coherent and
    therefore undetectable downstream.

    `production_ownership` sits on Track Record (D2). Turnaround leads on D2, so
    it must lift the weight; Greenfield says nothing about D2, so it must leave
    it exactly where it was. The Layer 3 term still demonstrably reaches the
    output, which is the acceptance criterion.
    """
    baseline = _item().weight.value
    turnaround = _item(situation_key="turnaround").weight.value
    greenfield = _item(situation_key="greenfield").weight.value
    steady = _item(situation_key="steady_state").weight.value

    assert turnaround > baseline, "a Turnaround must weight Track Record up"
    assert greenfield == pytest.approx(baseline), (
        "§18.4's Greenfield row is D5 up-up and D3 up; it says nothing about "
        "Track Record and must therefore not move it"
    )
    assert steady == pytest.approx(baseline), (
        "§18.4's Steady-state row is D1 up-up and D5 down; it says nothing "
        "about Track Record"
    )
    assert turnaround != greenfield


def test_no_situation_moves_a_dimension_its_runbook_row_does_not_name() -> None:
    """The general form of the defect above, across all six rows and all five
    dimensions.

    Four of the six situation types carried at least one modifier §18.4 does not
    state. Each looked like a reasonable reading of the situation on its own;
    together they re-weighted every matrix in the product away from the
    document it claims to implement.
    """
    for key in situations.SITUATION_TYPES:
        named = set(situations.SITUATIONS[key].effects)
        modifiers = situations.dimension_modifiers(key)
        for dimension, value in modifiers.items():
            if dimension in named:
                assert value != 1.0, f"{key}/{dimension} is named by §18.4"
            else:
                assert value == 1.0, (
                    f"{key} moves {dimension}, which its §18.4 row does not name"
                )


def test_a_layer_2_change_moves_a_weight_in_the_output() -> None:
    """THE acceptance criterion, and the "not just appears in a summary" half.

    A company that hires for potential over proven track record must produce a
    LOWER weight on a track-record competency, and the provenance must name the
    layer that moved it.
    """
    dna = company_dna.compile_artifact(
        {
            # §16 S2, position 5 on "Proven delivery <-1 ... 5-> Potential".
            "proven_vs_potential": 5,
            "credentials_vs_practice": 5,
        }
    )
    baseline = _item()
    tuned = _item(company=dna)

    assert tuned.weight.value < baseline.weight.value
    # The NUMBER moved, and the term that moved it is named.
    assert tuned.weight.company < 1.0
    assert baseline.weight.company == 1.0
    assert tuned.weight.baseline == baseline.weight.baseline, (
        "Layer 1 must be unchanged; only the Layer 2 term moved"
    )


def test_the_weight_records_all_four_terms() -> None:
    """"Why is this weighted 1.62" must be answerable by reading the row, not by
    rerunning the pipeline."""
    item = _item(
        company=company_dna.compile_artifact({"proven_vs_potential": -1}),
        situation_key="turnaround",
        role_emphasis={"Operating what they built": 1.2},
    )
    terms = item.weight.as_dict()["terms"]
    assert set(terms) == {
        "baseline_layer1",
        "company_layer2",
        "situation_layer3",
        "role_layer3",
    }
    product = (
        terms["baseline_layer1"]
        * terms["company_layer2"]
        * terms["situation_layer3"]
        * terms["role_layer3"]
    )
    assert item.weight.value == pytest.approx(product, rel=1e-6)


def test_a_role_emphasis_cannot_exceed_the_bound_every_layer_is_held_to() -> None:
    item = _item(role_emphasis={"Operating what they built": 50.0})
    assert item.weight.role == layers.BOUNDS["competency_weight"].high


def test_a_must_have_needs_more_evidence_than_a_nice_to_have() -> None:
    """Asymmetric on purpose: a Must-have graded Not Matching caps the whole
    report, so the cost of getting one wrong is asymmetric and the bar should
    be too."""
    must = transformation.derive_threshold("must_have", None)
    nice = transformation.derive_threshold("nice_to_have", None)
    assert must.independence_required > nice.independence_required


def test_a_company_may_raise_the_evidence_bar_and_not_lower_it() -> None:
    """CORRECTED. §7.4 sets the corroboration FLOOR by seniority as a Layer 1
    table, and the intake no longer offers a question that can lower it.

    The asymmetry that survives is the one on the evidence THRESHOLD: §16 S2's
    credentials-versus-practice scale may raise the bar freely and may lower it
    only marginally, which is what `layers.BOUNDS["evidence_threshold"]`
    encodes at 0.8 to 3.0.
    """
    lax = company_dna.compile_artifact({"credentials_vs_practice": 1})
    strict = company_dna.compile_artifact({"credentials_vs_practice": 5})

    assert strict.threshold_modifier > 1.0
    assert lax.threshold_modifier < 1.0
    bound = layers.BOUNDS["evidence_threshold"]
    assert bound.contains(lax.threshold_modifier)
    assert bound.contains(strict.threshold_modifier)
    # The floor a client cannot reach past: §7.4 is indexed by seniority alone.
    assert lax.independence_required == strict.independence_required
    assert lax.independence_required == company_dna.minimum_independent_groups(
        "non_managerial"
    )


# ── build(): partial success ─────────────────────────────────────────────────


def test_one_bad_phrase_does_not_cost_the_whole_matrix() -> None:
    """The same partial-success reasoning the databank bulk upload uses: one
    unreadable PDF may not discard the other twenty-four."""
    items, rejections = transformation.build(
        [
            {"phrase": PHRASE, "category": "must_have"},
            {"phrase": "must have gravitas", "category": "must_have"},
            {"phrase": "systems design under real constraints", "category": "must_have"},
        ],
        department=DEPT,
        seniority=SENIORITY,
    )
    assert len(items) == 2
    assert len(rejections) == 1
    assert "gravitas" in rejections[0]["phrase"]


def test_two_phrases_naming_one_competency_take_one_row() -> None:
    """Grading a candidate twice on one axis double-counts it, and
    `job_competencies` is UNIQUE on (job, category, name) anyway."""
    items, rejections = transformation.build(
        [
            {"phrase": "reliability in production", "category": "must_have"},
            {"phrase": "must own their on-call rotation", "category": "must_have"},
        ],
        department=DEPT,
        seniority=SENIORITY,
    )
    assert len(items) == 1
    assert len(rejections) == 1
    assert "double-count" in rejections[0]["reason"]


# ── Weights are internal ─────────────────────────────────────────────────────


def test_matching_still_has_no_weights_table() -> None:
    """The 2026-07-30 deletion stands. Those weights were a fixed table applied
    to every role AND were shown to the client as "35% role-fit weighting".
    Neither fault is reintroduced: these weights are per-job, derived from three
    declared layers, and never cross an API boundary."""
    import app.services.matching as matching

    assert not hasattr(matching, "WEIGHTS")


def test_the_matrix_provenance_is_internal_and_says_so() -> None:
    items, _ = transformation.build(
        [{"phrase": PHRASE, "category": "must_have"}],
        department=DEPT,
        seniority=SENIORITY,
    )
    provenance = transformation.matrix_provenance(items)
    # It carries numbers, which is exactly why it must never be rendered.
    assert provenance["items"][0]["weight"]["value"] > 0
    assert "stages" in provenance
