"""Contradiction severity, and the work each level obliges.

The rule under test is spec 14's: MATERIAL or CRITICAL must trigger additional
retrieval or re-evaluation and must never be silently averaged. So the
assertions are about the returned ACTIONS, not about a comment: a caller that
ignores them has to ignore a value it was handed, and the one function that
hands back a concluded answer refuses while any of them stands.

The other half is that none of this may have changed `verify_consistency`. Its
thresholds are wide on purpose and a detector nobody trusts gets switched off,
so its existing results are imported and asserted unchanged.
"""
from __future__ import annotations

import uuid

import pytest

from app.services.evidence import contradictions as cd
from app.services.evidence import ledger
from app.services.verification import base
from app.services.verification.contradiction import (
    EXPERIENCE_TOLERANCE_YEARS,
    verify_consistency,
)


def _claim(supporting=(), contradicting=(), dimension="Kafka") -> ledger.Claim:
    return ledger.Claim(
        claim_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        link_id=uuid.uuid4(),
        subject="candidate",
        dimension=dimension,
        claim="has run partition rebalances in production",
        supporting_evidence=tuple(supporting),
        contradicting_evidence=tuple(contradicting),
    )


def _evidence(trust=ledger.TRUST_OBSERVED, status=ledger.STATUS_ACTIVE):
    return ledger.EvidenceItem(
        evidence_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        job_id=uuid.uuid4(),
        link_id=uuid.uuid4(),
        source_type=ledger.SOURCE_ANSWER,
        source_id=uuid.uuid4(),
        text_ref=ledger.text_ref(table="assessment_messages", row_id=uuid.uuid4()),
        trust=trust,
        status=status,
    )


# ── the existing detector is unchanged ───────────────────────────────────────


def test_the_existing_cross_source_detector_behaves_exactly_as_it_did() -> None:
    """Its tolerances are tuned to MISS ambiguous cases on purpose.

    Extending it from another module must not have narrowed them: a detector
    that fires on normal rounding buries the eight-year case that matters, and
    then gets switched off.
    """
    assert EXPERIENCE_TOLERANCE_YEARS == 2.0

    clean = verify_consistency(
        resume_skills=["kafka", "postgres"],
        resume_experience_years=8,
        validation_experience_years=7,
        claimed_skills=["kafka"],
        unanswered_skills=[],
    )
    assert clean.findings == ()
    assert clean.passed

    conflicted = verify_consistency(
        resume_skills=["kafka", "postgres"],
        resume_experience_years=8,
        validation_experience_years=5,
        claimed_skills=["terraform"],
        unanswered_skills=["postgres"],
    )
    issues = {finding.issue for finding in conflicted.findings}
    assert issues == {
        "experience_conflict",
        "claimed_but_unevidenced",
        "claimed_beyond_resume",
    }
    severities = {finding.issue: finding.severity for finding in conflicted.findings}
    assert severities["experience_conflict"] == base.SEVERITY_MEDIUM
    assert severities["claimed_but_unevidenced"] == base.SEVERITY_MEDIUM
    assert severities["claimed_beyond_resume"] == base.SEVERITY_LOW
    assert conflicted.verifier == "contradiction"


def test_a_one_year_difference_still_raises_nothing_through_the_new_entry_point() -> None:
    """The wrapper must inherit the tolerance rather than re-deciding it."""
    report = cd.detect(resume_experience_years=8, validation_experience_years=7)
    assert report.contradictions == ()
    assert report.severity == cd.NONE
    assert report.actions == ()


# ── severity obliges an action, and the action is returned ───────────────────


def test_material_returns_an_action_rather_than_a_resolution() -> None:
    """The whole rule. A three-year gap between two stated lengths of
    experience must send the system back for more, not produce the mean."""
    report = cd.detect(
        resume_experience_years=8,
        validation_experience_years=5,
        phase=cd.PHASE_POST_CONVERSATION,
    )
    assert report.severity == cd.MATERIAL
    assert report.requires_reevaluation
    assert cd.ACTION_RETRIEVE in report.actions
    # And no averaged answer is available to anybody who wanted one.
    with pytest.raises(cd.UnresolvedContradiction):
        report.settle(6.5)


def test_critical_returns_an_action_and_demands_a_person() -> None:
    """A draft grading a dimension scoring never produced is a sentence about a
    real person that nothing in the system supports."""
    report = cd.detect(
        draft_dimensions={"Stream Processing": "Matching"},
        scored_dimensions={},
    )
    assert report.severity == cd.CRITICAL
    assert report.requires_reevaluation
    assert report.needs_human_review
    assert cd.ACTION_HUMAN_REVIEW in report.actions
    with pytest.raises(cd.UnresolvedContradiction):
        report.settle("Matching")


def test_a_draft_that_grades_differently_from_the_scoring_state_is_critical() -> None:
    report = cd.detect(
        draft_dimensions={"Stream Processing": "Matching"},
        scored_dimensions={"Stream Processing": "Not Matching"},
    )
    assert report.severity == cd.CRITICAL
    found = report.by_axis(cd.AXIS_DRAFT_VS_STATE)
    assert len(found) == 1
    # The recommendation is an instruction, because it is what gets fed back to
    # a regenerating loop verbatim.
    assert "regenerate" in found[0].recommendation


def test_minor_records_and_does_not_send_anything_back_for_retrieval() -> None:
    """MINOR exists precisely so the detector stays affordable. A hiring
    manager naming something the JD does not is normal and is most of the value
    of the intake; charging a retrieval pass for it would make the whole
    mechanism something an operator turns off.
    """
    report = cd.detect(
        jd_requirements=["kafka", "postgres"],
        swot_requirements=["stakeholder management"],
    )
    assert report.severity == cd.MINOR
    assert report.actions == (cd.ACTION_RECORD,)
    assert not report.requires_reevaluation
    assert not report.needs_human_review
    # A minor disagreement does not block a conclusion.
    assert report.settle("Matching") == "Matching"


def test_minor_findings_never_add_up_into_a_material_one() -> None:
    """Three rounding-level disagreements are not one real one. A detector that
    summed them would escalate on exactly the noise it was tuned to tolerate."""
    report = cd.detect(
        jd_requirements=["kafka"],
        swot_requirements=["mentoring", "stakeholder management", "budgeting"],
    )
    assert len(report.contradictions) == 3
    assert report.severity == cd.MINOR
    assert not report.requires_reevaluation


def test_a_dismissed_jd_requirement_is_material_and_an_added_one_is_not() -> None:
    """The asymmetry is the point: the matrix every candidate on this job is
    graded against is built downstream of both sources."""
    report = cd.detect(
        jd_requirements=["kafka", "postgres"],
        swot_dismissed=["kafka"],
        swot_requirements=["mentoring"],
    )
    by_severity = {item.severity for item in report.by_axis(cd.AXIS_JD_VS_SWOT)}
    assert by_severity == {cd.MATERIAL, cd.MINOR}
    assert report.severity == cd.MATERIAL


# ── the conversational branch (spec 32) ──────────────────────────────────────


def test_inside_the_conversation_the_action_is_to_ask() -> None:
    """The cheapest and most informative move while a candidate is still there."""
    report = cd.detect(
        resume_experience_years=8,
        validation_experience_years=5,
        phase=cd.PHASE_CONVERSATIONAL,
    )
    assert cd.ACTION_FOLLOW_UP in report.actions
    assert cd.ACTION_PRESERVE_UNCERTAINTY not in report.actions
    # Re-retrieval is owed in both phases: a disagreement is also evidence that
    # the wrong context was assembled.
    assert cd.ACTION_RETRIEVE in report.actions


def test_after_the_conversation_the_uncertainty_is_preserved_instead() -> None:
    """Asking is no longer available, and picking a side on the candidate's
    behalf is the thing this whole module exists to prevent."""
    report = cd.detect(
        resume_experience_years=8,
        validation_experience_years=5,
        phase=cd.PHASE_POST_CONVERSATION,
    )
    assert cd.ACTION_PRESERVE_UNCERTAINTY in report.actions
    assert cd.ACTION_FOLLOW_UP not in report.actions


def test_the_phase_never_lowers_a_severity() -> None:
    """Being mid-conversation changes WHAT is owed, never HOW MUCH matters."""
    kwargs = dict(resume_experience_years=8, validation_experience_years=5)
    assert (
        cd.detect(phase=cd.PHASE_CONVERSATIONAL, **kwargs).severity
        == cd.detect(phase=cd.PHASE_POST_CONVERSATION, **kwargs).severity
    )


# ── answers across turns ─────────────────────────────────────────────────────


def test_affirming_and_denying_the_same_thing_under_one_key_is_material() -> None:
    """A follow-up shares its parent's `question_key`, which is exactly how the
    scorers file it. Two answers that cannot both hold must not be scored
    together as one richer answer."""
    report = cd.detect(
        turn_claims=[
            {"question_key": "primary.kafka", "affirmed": ["kafka"]},
            {"question_key": "primary.kafka", "denied": ["kafka"]},
        ]
    )
    assert report.severity == cd.MATERIAL
    assert report.by_axis(cd.AXIS_ANSWERS_ACROSS_TURNS)
    assert report.requires_reevaluation


def test_the_same_term_under_two_different_keys_is_not_a_contradiction() -> None:
    """Different questions can honestly get different answers, and firing here
    would penalise a candidate for answering two questions accurately."""
    report = cd.detect(
        turn_claims=[
            {"question_key": "primary.kafka", "affirmed": ["kafka"]},
            {"question_key": "secondary.ops", "denied": ["kafka"]},
        ]
    )
    assert report.by_axis(cd.AXIS_ANSWERS_ACROSS_TURNS) == ()


# ── conclusions against the ledger ───────────────────────────────────────────


def test_a_conclusion_with_no_evidence_under_it_is_critical() -> None:
    """This is what a degraded scoring pass produces, and it is the one shape a
    reader of the report can never detect for themselves."""
    report = cd.detect(claims=[_claim()])
    assert report.severity == cd.CRITICAL
    assert report.needs_human_review


def test_a_conclusion_standing_only_on_inference_is_material() -> None:
    """The product agreeing with itself. Invisible without the trust lattice,
    and indistinguishable from real support once it reaches a report."""
    report = cd.detect(
        claims=[_claim(supporting=[_evidence(ledger.TRUST_INFERRED)] * 3)]
    )
    assert report.severity == cd.MATERIAL
    assert report.requires_reevaluation


def test_a_properly_evidenced_conclusion_raises_nothing() -> None:
    """The direction that matters most: a detector with a false-positive habit
    sends every sound assessment back for another retrieval pass."""
    report = cd.detect(
        claims=[
            _claim(supporting=[_evidence(ledger.TRUST_VALIDATED)]),
            _claim(supporting=[_evidence(ledger.TRUST_OBSERVED)], dimension="Postgres"),
        ]
    )
    assert report.contradictions == ()
    assert report.severity == cd.NONE
    assert report.settle("Highly Matching") == "Highly Matching"


def test_a_contradicted_claim_is_carried_forward_and_not_collapsed() -> None:
    report = cd.detect(
        claims=[
            _claim(
                supporting=[_evidence(ledger.TRUST_AUTHORITATIVE)],
                contradicting=[_evidence(ledger.TRUST_OBSERVED)],
            )
        ]
    )
    assert report.severity == cd.MATERIAL
    assert "do not choose" in report.contradictions[0].recommendation


def test_revoked_evidence_does_not_leave_a_conclusion_unsupported() -> None:
    """A claim whose only supporting item was withdrawn IS unsupported, and the
    detector must say so rather than reading the retired row as support."""
    report = cd.detect(
        claims=[_claim(supporting=[_evidence(status=ledger.STATUS_REVOKED)])]
    )
    assert report.severity == cd.CRITICAL


# ── the two scales stay distinct ─────────────────────────────────────────────


def test_the_two_severity_scales_are_mapped_in_exactly_one_place() -> None:
    """Finding severity answers "regenerate or ship". Contradiction severity
    answers "how much more work is owed". A second mapping would leave a reader
    unable to tell which scale a value came from."""
    assert set(cd._from_finding()) == {
        base.SEVERITY_HIGH,
        base.SEVERITY_MEDIUM,
        base.SEVERITY_LOW,
    }
    assert set(cd._from_finding().values()) == {cd.CRITICAL, cd.MATERIAL, cd.MINOR}
    assert cd.NONE not in cd._from_finding().values()


def test_a_report_converts_to_a_verdict_the_existing_loop_already_understands() -> None:
    """Reuse rather than a second confidence: `agent_loop` already knows what to
    do with a Verdict, and a parallel accept/reject rule would drift from it."""
    verdict = cd.detect(
        draft_dimensions={"Stream Processing": "Matching"}, scored_dimensions={}
    ).to_verdict()
    assert isinstance(verdict, base.Verdict)
    assert not verdict.passed
    assert verdict.by_severity(base.SEVERITY_HIGH)

    clean = cd.detect().to_verdict()
    assert clean.passed
    assert clean.findings == ()


def test_escalation_takes_the_highest_and_never_the_average() -> None:
    assert cd.escalate(cd.MINOR, cd.CRITICAL, cd.NONE) == cd.CRITICAL
    assert cd.escalate(cd.MINOR, cd.MINOR) == cd.MINOR
    assert cd.escalate() == cd.NONE
    assert cd.at_least(cd.MATERIAL, cd.MATERIAL)
    assert not cd.at_least(cd.MINOR, cd.MATERIAL)


def test_settle_has_no_way_to_be_told_to_proceed_anyway() -> None:
    """A caller that genuinely must proceed records the uncertainty and reads
    the value itself, which is a visible line in a diff. A `force` argument
    would be the same act, invisible."""
    import inspect

    params = set(inspect.signature(cd.ContradictionReport.settle).parameters)
    assert params == {"self", "value"}


def test_nothing_in_the_detector_calls_a_model() -> None:
    """The moment a guard matters most is the moment the provider is already
    failing, which is the same reason `answer_classification` settles empty and
    gibberish deterministically."""
    with open(cd.__file__, "r", encoding="utf-8") as handle:
        body = handle.read()
    assert "invoke_llm" not in body
    assert "llm_router" not in body
