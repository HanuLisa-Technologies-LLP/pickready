"""Miti's five-dimension engine: isolation, determinism, and the gates.

THIS FILE CARRIES FOUR OF SPEC-DOC5'S ACCEPTANCE CRITERIA:

  * "Miti's five dimension evaluators run isolated from each other and from the
     composite" -> the isolation section, asserted structurally.
  * "the aggregator is pure deterministic code with zero model calls" -> read
     from SOURCE, not from a docstring.
  * "the Must-have hard cap still applies exactly as before".
  * "No flag has ever caused an auto-rejection; every flag has a human
     disposition recorded."

The isolation tests read the DATACLASS FIELD SET rather than checking for
specific absent attributes. That is deliberate: a future field called `notes`
would pass a test that only asserted `not hasattr(payload, "candidate_name")`,
and would reopen the entire hole. Asserting the exact set means adding any field
fails until somebody has thought about it.
"""
from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
import pathlib

import pytest

from app.services import rating
from app.services.evidence import contradictions as detector
from app.services.hiring import gates
from app.services.miti import aggregation, claims, dimensions, pipeline, tiering, triangulation
from app.services.miti.dimensions import (
    DimensionResult,
    EvaluatorInput,
    EvidenceView,
)

MITI_ROOT = pathlib.Path(inspect.getfile(aggregation)).parent


def _view(ref: str, group: str = "candidate", trust: str = "observed") -> EvidenceView:
    return EvidenceView(
        ref=ref,
        text=f"evidence {ref}",
        trust=trust,
        source_kind="answer",
        independence_group=group,
        freshness="current",
    )


def _result(dimension: str, band: str = "solid", **kwargs) -> DimensionResult:
    kwargs.setdefault("evidence_refs", ("e1",))
    return DimensionResult(dimension=dimension, band=band, **kwargs)


# ── ISOLATION: structural, not instructed ────────────────────────────────────


def test_the_evaluator_input_field_set_is_closed() -> None:
    """THE security boundary, asserted as a SET.

    A future field called `notes` would pass a test that only checked for
    `candidate_name`, and would reopen the whole hole. Asserting the exact set
    means adding any field fails here until somebody has thought about it.
    """
    fields = {f.name for f in dataclasses.fields(EvaluatorInput)}
    assert fields == {
        "dimension",
        "competencies",
        "rubric_anchor",
        "evidence",
        "role_context",
    }


def test_an_evaluator_input_cannot_carry_a_candidate_or_a_composite() -> None:
    payload = EvaluatorInput(
        dimension="verified_competence",
        competencies=("Core craft depth",),
        rubric_anchor="anchor",
        evidence=(_view("e1"),),
    )
    for forbidden in (
        "candidate",
        "candidate_name",
        "name",
        "composite",
        "overall",
        "other_scores",
        "dimension_scores",
        "context",
    ):
        assert not hasattr(payload, forbidden), forbidden


def test_an_evaluator_input_is_frozen() -> None:
    """A caller must not be able to attach an attribute after construction."""
    payload = EvaluatorInput(
        dimension="verified_competence",
        competencies=(),
        rubric_anchor="",
        evidence=(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        payload.candidate_name = "Priya"  # type: ignore[attr-defined]


def test_an_unknown_dimension_is_refused_at_construction() -> None:
    """An evaluator with no defined question cannot produce a defensible
    score."""
    with pytest.raises(ValueError):
        EvaluatorInput(
            dimension="vibes", competencies=(), rubric_anchor="", evidence=()
        )


def test_the_rendered_prompt_contains_only_what_the_input_carries() -> None:
    payload = EvaluatorInput(
        dimension="track_record_impact",
        competencies=("Delivery ownership",),
        rubric_anchor="anchor text",
        evidence=(_view("e1"),),
        role_context="Turnaround, managerial",
    )
    rendered = json.dumps(dimensions.render_prompt(payload))
    assert "Delivery ownership" in rendered
    assert "anchor text" in rendered
    # The other four dimensions are not named.
    for other in ("Verified Competence", "Role & Context Fit", "Trajectory"):
        assert other not in rendered
    assert "composite" not in rendered.lower()


def test_render_prompt_cannot_reach_anything_the_input_does_not_carry() -> None:
    """Its signature is the guarantee: one argument, and that argument is the
    closed dataclass."""
    signature = inspect.signature(dimensions.render_prompt)
    assert list(signature.parameters) == ["payload"]


def test_evidence_is_routed_only_to_the_dimension_that_owns_it() -> None:
    inputs = pipeline.EvaluationInputs(
        competency_dimensions={
            "Core craft depth": "verified_competence",
            "Delivery ownership": "track_record_impact",
        },
        evidence=[_view("e1"), _view("e2")],
        evidence_competencies={"e1": ["Core craft depth"], "e2": ["Delivery ownership"]},
    )
    built = {p.dimension: p for p in pipeline.build_evaluator_inputs(inputs)}
    assert [v.ref for v in built["verified_competence"].evidence] == ["e1"]
    assert [v.ref for v in built["track_record_impact"].evidence] == ["e2"]
    assert built["role_context_fit"].evidence == ()


def test_a_candidate_name_is_scrubbed_from_evidence_text() -> None:
    """Defence in depth on top of the structural guarantee. Evidence excerpts
    are a candidate's own prose and will contain names."""
    scrubbed = dimensions.scrub(
        "Priya and I rewrote the scheduler", subject_names=["Priya"]
    )
    assert "Priya" not in scrubbed


def test_scrubbing_does_not_eat_technical_nouns() -> None:
    """Over-scrubbing would hand an evaluator a mutilated answer, which is worse
    than an anonymised one."""
    text = "I moved us from RabbitMQ to Kafka in Bengaluru using Spring Boot"
    scrubbed = dimensions.scrub(text)
    for noun in ("RabbitMQ", "Kafka", "Bengaluru", "Spring Boot"):
        assert noun in scrubbed


# ── DETERMINISM: read from source ────────────────────────────────────────────


def _executable_names(path: pathlib.Path) -> set[str]:
    """Every name an AST walk can reach in a module: imports, calls, attributes.

    READS THE AST, NOT THE PROSE. These modules' docstrings deliberately explain
    WHY there is no router import, and a naive substring scan would report the
    explanation as the violation. That is not a hypothetical -- it is what the
    first version of this test did, and a check that flags its own
    documentation is a check somebody weakens rather than fixes.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module)
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    return names


def test_the_aggregator_imports_no_router() -> None:
    """spec-doc5 §B.3: "Miti -- aggregator | **No model.** Deterministic code
    only".

    Read from SOURCE rather than trusted to a docstring, the same technique
    `test_question_count_range.py` and `test_vaada_miti_loop.py` already use: a
    rule that lives only in a comment is a rule the next person adding "just one
    quick call to clean up the phrasing" will not see.
    """
    names = _executable_names(MITI_ROOT / "aggregation.py")
    for banned in (
        "llm_router",
        "invoke_llm",
        "chat_completion",
        "anthropic",
        "app.services.llm_router",
    ):
        assert banned not in names, f"aggregation.py reaches {banned!r}"


def test_the_aggregator_has_no_async_and_no_awaits() -> None:
    """A model call needs one of them. Their absence is a second, independent
    statement of the same rule."""
    source = (MITI_ROOT / "aggregation.py").read_text(encoding="utf-8")
    assert "async def" not in source
    assert "await " not in source


def test_the_aggregator_is_reproducible() -> None:
    results = [
        _result("verified_competence", "strong"),
        _result("track_record_impact", "solid"),
        _result("role_context_fit", "partial"),
        _result("authenticity_consistency", "strong"),
        _result("trajectory_potential", "solid"),
    ]
    first = aggregation.aggregate(results, independence=2).as_dict()
    second = aggregation.aggregate(results, independence=2).as_dict()
    assert first == second


def test_claim_extraction_cannot_carry_an_opinion() -> None:
    """Runbook §57.1: extraction must not evaluate. A field an opinion could be
    written into is a field an opinion eventually appears in."""
    fields = {f.name for f in dataclasses.fields(claims.Claim)}
    for forbidden in ("score", "confidence", "assessment", "plausibility", "rating"):
        assert forbidden not in fields, forbidden


# ── THE MUST-HAVE HARD CAP ───────────────────────────────────────────────────


def _strong_results() -> list[DimensionResult]:
    return [
        _result("verified_competence", "strong"),
        _result("track_record_impact", "strong"),
        _result("role_context_fit", "strong"),
        _result("authenticity_consistency", "strong"),
        _result("trajectory_potential", "strong"),
    ]


def test_a_not_matching_must_have_caps_the_overall_grade() -> None:
    uncapped = aggregation.aggregate(_strong_results(), independence=3)
    assert uncapped.overall_grade == rating.GRADE_HIGHLY

    capped = aggregation.aggregate(
        _strong_results(),
        must_have_grades=[rating.GRADE_NOT, rating.GRADE_HIGHLY],
        independence=3,
    )
    assert capped.overall_grade == rating.GRADE_MODERATELY
    assert capped.must_have_cap_applied


def test_the_cap_never_raises_a_weak_candidate() -> None:
    """`min`, not an assignment. A cap that SET the score would quietly promote
    the weakest candidates into the band it exists to keep the strong ones out
    of."""
    weak = [
        _result("verified_competence", "absent"),
        _result("track_record_impact", "absent"),
        _result("role_context_fit", "absent"),
        _result("authenticity_consistency", "absent"),
        _result("trajectory_potential", "absent"),
    ]
    capped = aggregation.aggregate(
        weak, must_have_grades=[rating.GRADE_NOT], independence=3
    )
    assert capped.overall_grade == rating.GRADE_NOT


def test_the_cap_is_applied_after_the_authenticity_multiplier() -> None:
    """A cap applied earlier could be lifted by arithmetic running afterwards,
    and a hard cap a later multiplication can undo is not a hard cap."""
    results = _strong_results()
    results[3] = _result("authenticity_consistency", "absent")
    out = aggregation.aggregate(
        results, must_have_grades=[rating.GRADE_NOT], independence=3
    )
    assert out.authenticity_factor < 1.0
    assert out.adjusted_composite < out.raw_composite
    assert out.overall_grade in {rating.GRADE_MODERATELY, rating.GRADE_NOT}


def test_the_cap_is_recorded_when_it_binds() -> None:
    """A cap that fired silently is indistinguishable from a candidate who
    simply scored there."""
    capped = aggregation.aggregate(
        _strong_results(), must_have_grades=[rating.GRADE_NOT], independence=3
    )
    assert capped.client_projection()["capped_by_must_have"] is True


# ── INSUFFICIENT EVIDENCE IS NOT NEGATIVE EVIDENCE ───────────────────────────


def test_an_insufficient_dimension_is_excluded_not_scored_low() -> None:
    """spec-doc5: "Missing evidence should reduce confidence, not silently
    reduce score ... conflating them is a fairness failure."

    The practical consequence: a career-changer with a thin track record gets a
    lower-confidence report that goes to a human, rather than a confidently poor
    grade that does not.
    """
    results = _strong_results()
    results[1] = DimensionResult(
        dimension="track_record_impact",
        band="partial",
        evidence_refs=(),
        insufficient_evidence=True,
    )
    out = aggregation.aggregate(results, independence=3)

    assert "track_record_impact" in out.insufficient_dimensions
    # Excluded, so the surviving categories are still strong.
    assert out.category_scores[aggregation.CATEGORY_MUST_HAVE] == pytest.approx(92.0)
    # Paid for in confidence and in review, not in score.
    assert out.needs_human_review
    assert any("insufficient" in reason for reason in out.review_reasons)


def test_insufficient_authenticity_does_not_penalise_the_composite() -> None:
    """Not having run the check is not evidence of a problem. Penalising a
    candidate for a stage that did not run would make an infrastructure failure
    look like a finding about them."""
    results = _strong_results()
    results[3] = DimensionResult(
        dimension="authenticity_consistency",
        band="absent",
        evidence_refs=(),
        insufficient_evidence=True,
    )
    out = aggregation.aggregate(results, independence=3)
    assert out.authenticity_factor == 1.0


def test_a_missing_authenticity_result_is_neutral() -> None:
    out = aggregation.aggregate(
        [_result("verified_competence", "solid")], independence=1
    )
    assert out.authenticity_factor == 1.0
    assert "not evaluated" in out.authenticity_reason


# ── CONFIDENCE IS ARITHMETIC ─────────────────────────────────────────────────


def test_confidence_never_asks_a_model_how_sure_it_is() -> None:
    assert aggregation.confidence_for(
        judged=5, independence=3, unresolved_contradictions=0
    ) == aggregation.CONFIDENCE_HIGH
    assert aggregation.confidence_for(
        judged=3, independence=1, unresolved_contradictions=0
    ) == aggregation.CONFIDENCE_MEDIUM
    assert aggregation.confidence_for(
        judged=1, independence=1, unresolved_contradictions=0
    ) == aggregation.CONFIDENCE_LOW


def test_one_independent_source_is_not_enough_for_high_confidence() -> None:
    """One source is an account; two is corroboration. That is the entire
    evidence model in one assertion."""
    assert aggregation.confidence_for(
        judged=5, independence=1, unresolved_contradictions=0
    ) != aggregation.CONFIDENCE_HIGH


def test_an_unresolved_contradiction_caps_confidence() -> None:
    """A well-evidenced account that contradicts itself is not a confident
    result -- it is the case most in need of a person."""
    assert aggregation.confidence_for(
        judged=5, independence=5, unresolved_contradictions=1
    ) == aggregation.CONFIDENCE_MEDIUM
    assert aggregation.confidence_for(
        judged=5, independence=5, unresolved_contradictions=3
    ) == aggregation.CONFIDENCE_LOW


# ── THE BENIGN-EXPLANATION RULE ──────────────────────────────────────────────


def _contradiction(severity: str) -> detector.Contradiction:
    return detector.Contradiction(
        axis=detector.AXIS_RESUME_VS_ANSWERS,
        severity=severity,
        location="tenure",
        detail="the resume and the answer describe different durations",
        recommendation="ask about the dates directly",
        actions=detector.actions_for(severity, phase=detector.PHASE_POST_CONVERSATION),
    )


def test_two_benign_explanations_are_required_before_any_escalation() -> None:
    """Not one. Two. The first explanation a system reaches for is the one that
    confirms the suspicion; the second is where the honest answer usually is."""
    assert triangulation.REQUIRES_BENIGN_EXPLANATIONS == 2

    withheld = triangulation.escalate(
        _contradiction(detector.CRITICAL),
        explanations=[triangulation.BenignExplanation("a rounding")],
        independence=3,
    )
    assert withheld.severity == detector.MINOR
    assert withheld.escalation_withheld
    assert withheld.proposed_severity == detector.CRITICAL


def test_a_supported_benign_explanation_settles_the_contradiction() -> None:
    """The disagreement has an ordinary explanation with evidence behind it.
    Escalating anyway would be ignoring the answer we went looking for."""
    settled = triangulation.escalate(
        _contradiction(detector.CRITICAL),
        explanations=[
            triangulation.BenignExplanation("a rounding", supported=True),
            triangulation.BenignExplanation("a probation period counted differently"),
        ],
        independence=3,
    )
    assert settled.severity == detector.MINOR
    assert settled.settled_benignly


def test_a_single_source_group_caps_critical_at_material() -> None:
    """A candidate being imprecise about their own history twice is a real
    signal and a weaker one than an independent source disagreeing."""
    held = triangulation.escalate(
        _contradiction(detector.CRITICAL),
        explanations=[
            triangulation.BenignExplanation("a"),
            triangulation.BenignExplanation("b"),
        ],
        independence=1,
    )
    assert held.severity == detector.MATERIAL


def test_a_material_contradiction_survives_two_unsupported_explanations() -> None:
    """The rule holds severity DOWN, it does not make escalation impossible."""
    kept = triangulation.escalate(
        _contradiction(detector.MATERIAL),
        explanations=[
            triangulation.BenignExplanation("a"),
            triangulation.BenignExplanation("b"),
        ],
        independence=3,
    )
    assert kept.severity == detector.MATERIAL
    assert not kept.escalation_withheld


def test_the_stock_explanations_hold_the_rule_during_an_outage() -> None:
    """A provider outage that left every contradiction un-escalatable would
    silently disable integrity escalation, which looks like a clean run."""
    report = detector.ContradictionReport(
        contradictions=(_contradiction(detector.MATERIAL),)
    )
    result = triangulation.triangulate(
        report,
        sources=[
            {"ref": "e1", "independence_group": "candidate"},
            {"ref": "e2", "independence_group": "employer"},
        ],
        generated=None,  # the model produced nothing
    )
    assert result.contradictions[0].severity == detector.MATERIAL
    assert len(result.contradictions[0].explanations) >= 2


def test_severity_is_never_raised_by_the_benign_rule() -> None:
    unchanged = triangulation.escalate(
        _contradiction(detector.MINOR), explanations=[], independence=1
    )
    assert unchanged.severity == detector.MINOR


def test_triangulation_has_no_way_to_reject_a_candidate() -> None:
    """spec-doc5 states this as a hard constraint. The enforcement is the
    absence of the capability."""
    fields = {f.name for f in dataclasses.fields(triangulation.TriangulationResult)}
    for forbidden in ("reject", "rejected", "disqualify", "status", "decision"):
        assert forbidden not in fields, forbidden


def test_a_contradiction_never_reaches_a_client() -> None:
    """A client sees the grade and, where a recruiter needs it, that the report
    is held for review. Never "we think this candidate overstated something",
    which is an accusation the platform is in no position to make."""
    result = triangulation.TriangulationResult(
        contradictions=[
            triangulation.escalate(
                _contradiction(detector.MATERIAL), explanations=[], independence=1
            )
        ]
    )
    assert result.client_projection() == {}


# ── INDEPENDENCE COUNTING ────────────────────────────────────────────────────


def test_a_resume_and_an_answer_are_one_source_group() -> None:
    """Two pieces of evidence corroborate each other only if they COULD have
    disagreed. A resume line and the candidate restating it in the interview
    could not."""
    from app.services.evidence import ledger

    assert tiering.independence_group_for(ledger.SOURCE_RESUME) == tiering.GROUP_CANDIDATE
    assert tiering.independence_group_for(ledger.SOURCE_ANSWER) == tiering.GROUP_CANDIDATE
    assert (
        tiering.independence_group_for(ledger.SOURCE_VALIDATION)
        == tiering.GROUP_CANDIDATE
    )


def test_platform_memory_is_never_an_independent_source() -> None:
    """It is derived from things already counted. Treating it as independent
    would let the product corroborate a claim with its own earlier reading of
    the same claim."""
    from app.services.evidence import ledger

    assert tiering.independence_group_for(ledger.SOURCE_MEMORY) == tiering.GROUP_CANDIDATE


def test_an_unknown_source_is_assumed_dependent() -> None:
    """Assuming independence would MANUFACTURE corroboration, which is the
    failure the grouping exists to prevent."""
    assert tiering.independence_group_for("some_new_thing") == tiering.GROUP_CANDIDATE


def test_independence_counts_groups_not_documents() -> None:
    sources = [
        {"ref": "a", "independence_group": "candidate"},
        {"ref": "b", "independence_group": "candidate"},
        {"ref": "c", "independence_group": "candidate"},
        {"ref": "d", "independence_group": "employer"},
    ]
    assert triangulation.count_independence(sources) == 2


# ── TIERING MODIFIERS ────────────────────────────────────────────────────────


def test_a_team_attribution_is_discounted_not_discarded() -> None:
    """Being on the team that did it is real evidence of proximity and exposure.
    It is weaker evidence of personal capability, which is what the discount
    says."""
    self_weight = tiering.attribution_modifier(claims.SUBJECT_SELF)
    team_weight = tiering.attribution_modifier(claims.SUBJECT_TEAM)
    assert 0 < team_weight < self_weight


def test_old_evidence_decays_to_a_floor_and_not_to_zero() -> None:
    """A candidate who led a migration eight years ago DID lead it. What decays
    is how much it tells you about them now."""
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    assert tiering.decay_modifier(now - timedelta(days=30), now=now) == 1.0
    ancient = tiering.decay_modifier(now - timedelta(days=4000), now=now)
    assert ancient == tiering._DECAY_FLOOR
    assert ancient > 0


def test_an_unknown_date_does_not_decay() -> None:
    """"We do not know when this was" is not "this was long ago", and
    penalising the candidate for the platform's gap in provenance would be
    exactly that conflation."""
    assert tiering.decay_modifier(None) == 1.0


def test_the_client_stated_horizon_overrides_the_platform_curve() -> None:
    from datetime import datetime, timedelta, timezone

    now = datetime(2026, 8, 28, tzinfo=timezone.utc)
    three_years = now - timedelta(days=1100)
    assert tiering.decay_modifier(three_years, now=now) < 1.0
    # A client who said recency does not matter much (5 years).
    assert tiering.decay_modifier(three_years, now=now, max_age_days=5 * 365) == 1.0


def test_a_model_may_sharpen_specificity_and_not_invent_it() -> None:
    base = tiering.tier_evidence(
        ref="e1",
        trust="observed",
        source_type="answer",
        subject=claims.SUBJECT_SELF,
        text="I cut p99 latency from 800ms to 180ms by batching writes",
        has_specifics=True,
    )
    runaway = tiering.refine_specificity(base, 99.0)
    assert runaway.specificity <= tiering._SPECIFICITY_MAX
    assert tiering.refine_specificity(base, None).specificity == base.specificity
    assert tiering.refine_specificity(base, "nonsense").specificity == base.specificity


def test_every_modifier_has_a_floor_above_zero() -> None:
    """Weak evidence is not the same as no evidence. A zero would silently
    delete it, and the deletion would be invisible."""
    weak = tiering.tier_evidence(
        ref="e1",
        trust="inferred",
        source_type="resume",
        subject=claims.SUBJECT_TEAM,
        text="worked on stuff",
        has_specifics=False,
    )
    assert weak.weight > 0


# ── CLAIMS ───────────────────────────────────────────────────────────────────


def test_materiality_comes_from_the_matrix_not_from_the_wording() -> None:
    """Otherwise a confidently written resume would rate itself material."""
    matrix = {"Core craft depth": "must_have", "Learning velocity": "nice_to_have"}
    assert claims.materiality_for(["Core craft depth"], matrix) == claims.MATERIALITY_CRITICAL
    assert claims.materiality_for(["Learning velocity"], matrix) == claims.MATERIALITY_MODERATE
    assert claims.materiality_for(["Nothing"], matrix) == claims.MATERIALITY_LOW


def test_a_claim_mapped_to_nothing_is_kept_not_dropped() -> None:
    """Dropping it would lose a piece of the candidate's account for the crime
    of not matching a matrix item, and triangulation legitimately reads claims
    the matrix does not grade."""
    parsed = claims.parse_claims(
        {"claims": [{"text": "I taught myself Rust over a weekend", "subject": "self"}]},
        source_kind="answer",
        source_ref="a1",
        matrix={"Core craft depth": "must_have"},
    )
    assert len(parsed) == 1
    assert parsed[0].competencies == ()


def test_attribution_is_derived_and_beats_the_models_answer() -> None:
    """A grammatical property should not vary between runs, and attribution has
    direct scoring consequences."""
    parsed = claims.parse_claims(
        {"claims": [{"text": "I personally rewrote the scheduler", "subject": "team"}]},
        source_kind="answer",
        source_ref="a1",
        matrix={},
    )
    assert parsed[0].subject == claims.SUBJECT_SELF


def test_a_malformed_row_does_not_discard_the_good_ones() -> None:
    parsed = claims.parse_claims(
        {"claims": [{"text": "I shipped it"}, "not an object", {"text": ""}]},
        source_kind="answer",
        source_ref="a1",
        matrix={},
    )
    assert len(parsed) == 1


def test_the_claim_projection_carries_no_text() -> None:
    """This shape travels into traces, and a trace never carries content -- the
    same rule `agent_execution_traces` follows by dropping a defect's detail."""
    parsed = claims.parse_claims(
        {"claims": [{"text": "SECRET ANSWER CONTENT", "subject": "self"}]},
        source_kind="answer",
        source_ref="a1",
        matrix={},
    )
    assert "SECRET" not in json.dumps(parsed[0].as_dict())


# ── THE GATES ────────────────────────────────────────────────────────────────


def test_g1_blocks_and_asks_the_table_before_the_stamp() -> None:
    """A TIMESTAMP IS NOT EVIDENCE THAT WORK HAPPENED. 19 of 35 live jobs once
    carried `framework_generated_at` with zero competency rows."""
    stamped_but_empty = gates.scorecard_gate(matrix_items=[], approved_at="2026-08-01")
    assert not stamped_but_empty.passed
    assert stamped_but_empty.blocking
    assert any("stamp" in r for r in stamped_but_empty.reasons)

    assert gates.scorecard_gate(
        matrix_items=[{"name": "x"}], approved_at="2026-08-01"
    ).passed


def test_g1_blocks_an_unapproved_scorecard() -> None:
    result = gates.scorecard_gate(matrix_items=[{"name": "x"}], approved_at=None)
    assert not result.passed and result.blocking


def test_g2_is_not_blocking() -> None:
    """A blocking sufficiency gate would refuse a report to exactly the
    candidates who most need a person to look. That is not neutrality, it is a
    silent rejection with better manners."""
    result = gates.evidence_sufficiency_gate(
        independent_sources=1, judged_dimensions=1
    )
    assert not result.passed
    assert not result.blocking


def test_g3_is_not_blocking_because_blocking_would_be_an_auto_rejection() -> None:
    """spec-doc5 states as a hard constraint that no flag ever auto-rejects. A
    gate that stopped the pipeline on an authenticity finding would end the
    candidacy without a person seeing the finding."""
    result = gates.integrity_gate(
        unresolved_contradictions=2,
        contradiction_severity="critical",
        authenticity_band="absent",
    )
    assert not result.passed
    assert not result.blocking
    assert len(result.reasons) >= 2


def test_g4_requires_a_decision_not_an_approval() -> None:
    """A gate requiring approval could be satisfied by nagging until somebody
    clicked yes. A gate requiring a recorded decision is satisfied only by
    someone having actually looked."""
    for disposition in gates.DISPOSITIONS:
        result = gates.human_review_gate(
            needs_review=True, disposition=disposition, decided_by="user-1"
        )
        assert result.passed, disposition


def test_g4_blocks_when_a_flag_has_no_disposition() -> None:
    result = gates.human_review_gate(
        needs_review=True, disposition=None, decided_by=None
    )
    assert not result.passed and result.blocking


def test_g4_refuses_a_disposition_with_nobody_attached() -> None:
    """A decision nobody is named for is indistinguishable from the pipeline
    having written it itself."""
    result = gates.human_review_gate(
        needs_review=True, disposition=gates.DISPOSITION_CLEARED, decided_by=None
    )
    assert not result.passed


def test_there_is_no_automatic_disposition() -> None:
    """An automatic disposition would satisfy G4 without a human, which is the
    entire thing G4 exists to prevent."""
    for invented in ("auto_cleared", "auto", "system", "pipeline", "none"):
        assert invented not in gates.DISPOSITIONS


def test_g4_passes_when_nothing_was_flagged() -> None:
    assert gates.human_review_gate(
        needs_review=False, disposition=None, decided_by=None
    ).passed


def test_an_unknown_gate_raises() -> None:
    with pytest.raises(ValueError):
        gates.run_gate("G5_vibes")


# ── THE PIPELINE ─────────────────────────────────────────────────────────────


def _inputs(**kwargs) -> pipeline.EvaluationInputs:
    base = dict(
        matrix={"Core craft depth": "must_have", "Delivery ownership": "nice_to_have"},
        competency_dimensions={
            "Core craft depth": "verified_competence",
            "Delivery ownership": "track_record_impact",
        },
        evidence=[_view("e1"), _view("e2", group="employer", trust="authoritative")],
        evidence_competencies={
            "e1": ["Core craft depth"],
            "e2": ["Delivery ownership"],
        },
        matrix_items=[{"name": "Core craft depth"}],
        scorecard_approved_at="2026-08-01",
        must_have_grades=[rating.GRADE_MATCHING],
    )
    base.update(kwargs)
    return pipeline.EvaluationInputs(**base)


async def _ok(task, messages, response_format_json=False):
    return json.dumps(
        {
            "band": "solid",
            "rationale": "ok",
            "evidence_refs": ["e1"],
            "insufficient_evidence": False,
        }
    )


def test_the_pipeline_runs_every_gate_in_order() -> None:
    out = asyncio.run(pipeline.evaluate(_inputs(), invoke=_ok))
    assert [g.gate for g in out.gate_results] == list(gates.GATES)
    assert out.deliverable


def test_an_unapproved_scorecard_stops_before_any_evaluation() -> None:
    """Evaluating against a draft would let the first candidate set the criteria
    for everyone by being assessed against it."""
    calls: list[str] = []

    async def _counting(task, messages, response_format_json=False):
        calls.append(task)
        return await _ok(task, messages, response_format_json)

    out = asyncio.run(
        pipeline.evaluate(_inputs(scorecard_approved_at=None), invoke=_counting)
    )
    assert calls == []
    assert not out.deliverable
    assert out.gate_results[0].gate == gates.G1


def test_an_evaluator_outage_yields_insufficient_not_a_low_band() -> None:
    """A provider outage is not a finding about a candidate. Converting one into
    the other is the same class of error as a hash deciding whether gibberish
    failed."""
    async def _down(task, messages, response_format_json=False):
        raise RuntimeError("provider down")

    out = asyncio.run(pipeline.evaluate(_inputs(), invoke=_down))
    assert len(out.degraded_dimensions) == len(dimensions.DIMENSIONS)
    for result in out.results:
        assert result.insufficient_evidence


def test_a_band_with_no_citation_is_refused() -> None:
    """An uncitable score is one that would be reported without a citation, or
    not reported at all."""
    async def _uncited(task, messages, response_format_json=False):
        return json.dumps({"band": "strong", "evidence_refs": []})

    out = asyncio.run(pipeline.evaluate(_inputs(), invoke=_uncited))
    for result in out.results:
        assert result.insufficient_evidence


def test_an_invented_band_is_refused_rather_than_defaulted() -> None:
    """A silent default would convert a malformed response into a real grade for
    a real person."""
    async def _invented(task, messages, response_format_json=False):
        return json.dumps({"band": "excellent", "evidence_refs": ["e1"]})

    out = asyncio.run(pipeline.evaluate(_inputs(), invoke=_invented))
    assert all(r.insufficient_evidence for r in out.results)


def test_malformed_json_degrades_rather_than_raising() -> None:
    async def _garbage(task, messages, response_format_json=False):
        return "not json at all"

    out = asyncio.run(pipeline.evaluate(_inputs(), invoke=_garbage))
    assert all(r.insufficient_evidence for r in out.results)


def test_a_flagged_evaluation_is_not_deliverable_without_a_disposition() -> None:
    """The no-auto-reject rule's other half: a flag cannot resolve itself
    either."""
    async def _weak(task, messages, response_format_json=False):
        return json.dumps({"band": "absent", "evidence_refs": ["e1"]})

    out = asyncio.run(
        pipeline.evaluate(
            _inputs(evidence=[_view("e1")]),  # one source group -> G2 fails
            invoke=_weak,
        )
    )
    assert out.aggregate.needs_human_review
    assert not out.deliverable
    assert any(g.gate == gates.G4 and not g.passed for g in out.gate_results)


def test_a_recorded_disposition_makes_a_flagged_evaluation_deliverable() -> None:
    async def _weak(task, messages, response_format_json=False):
        return json.dumps({"band": "absent", "evidence_refs": ["e1"]})

    out = asyncio.run(
        pipeline.evaluate(
            _inputs(
                evidence=[_view("e1")],
                review_disposition=gates.DISPOSITION_CLEARED,
                review_decided_by="user-1",
            ),
            invoke=_weak,
        )
    )
    assert out.deliverable


def test_a_failed_non_blocking_gate_still_reaches_human_review() -> None:
    """A gate whose failure had no consequence at all would be
    documentation."""
    out = asyncio.run(pipeline.evaluate(_inputs(evidence=[_view("e1")]), invoke=_ok))
    g2 = next(g for g in out.gate_results if g.gate == gates.G2)
    assert not g2.passed
    assert out.aggregate.needs_human_review


def test_the_client_projection_never_names_an_internal_dimension() -> None:
    """"Verified Competence" and "Trajectory & Potential" are how the grade was
    arrived at. Naming them would leak the internal model and imply a fourth and
    fifth thing the client can act on."""
    out = asyncio.run(pipeline.evaluate(_inputs(), invoke=_ok))
    rendered = json.dumps(out.client_projection())
    for internal in dimensions.DIMENSIONS:
        assert internal not in rendered
    for label in dimensions.DIMENSION_LABELS.values():
        assert label not in rendered


def test_the_client_projection_carries_no_numbers() -> None:
    out = asyncio.run(pipeline.evaluate(_inputs(), invoke=_ok))
    rendered = json.dumps(out.client_projection())
    assert not any(char.isdigit() for char in rendered)


def test_the_pipeline_module_imports_no_router() -> None:
    """`invoke` is injected, which keeps the pipeline unit-testable offline and
    makes it impossible for `aggregation` to reach a model through a sibling."""
    names = _executable_names(MITI_ROOT / "pipeline.py")
    for banned in ("llm_router", "invoke_llm", "chat_completion", "app.services.llm_router"):
        assert banned not in names, f"pipeline.py reaches {banned!r}"


def test_no_miti_module_reaches_a_model_except_through_the_injected_invoke() -> None:
    """The whole package, not just the two modules named above.

    `dimensions`, `claims`, `tiering` and `triangulation` all BUILD prompts and
    none of them SENDS one. That separation is what lets every stage be tested
    offline, and it is what stops a future edit from giving the deterministic
    aggregator a route to a provider through a sibling import.
    """
    for module in sorted(MITI_ROOT.glob("*.py")):
        names = _executable_names(module)
        assert "llm_router" not in names, module.name
        assert "invoke_llm" not in names, module.name
