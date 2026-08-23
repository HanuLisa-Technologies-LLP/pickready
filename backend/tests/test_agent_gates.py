"""Per-agent quality gates and the structured escalation.

Each gate exists for a specific defect that is cheap to catch where the artifact
is published and permanent by the time the report is read. So each gate gets two
tests: a sound input passes, and the one thing the gate exists for fails it.

Nothing here monkeypatches a provider, because nothing under test calls one.
That is the property: a guard that needs a model fails open exactly when the
model is down, which is the moment it is worth having.
"""
from __future__ import annotations

import pytest

from app.services import rating
from app.services.agents import escalation, gates, identity
from app.services.safety import actions
from app.services.verification import base as verification

# ── Bodha ────────────────────────────────────────────────────────────────────

GOOD_SWOT = {
    "strengths": ["deep payments domain"],
    "weaknesses": ["no platform team"],
    "opportunities": ["new market"],
    "threats": ["attrition in the squad"],
    "context_covered": list(gates.REQUIRED_ROLE_CONTEXT),
    "sources": ["reporting authority interview 2026-08-20"],
    "contradictions": [{"id": "c1", "critical": True, "resolved": True}],
}


def test_a_complete_swot_intake_passes() -> None:
    assert gates.bodha_gate(GOOD_SWOT).passed


def test_a_swot_with_no_sources_fails() -> None:
    """A SWOT with no provenance is indistinguishable from one a model invented,
    and the matrix built from it inherits the invention silently."""
    swot = dict(GOOD_SWOT, sources=[])
    verdict = gates.bodha_gate(swot)
    assert not verdict.passed
    assert "no_sources_recorded" in {f.issue for f in verdict.findings}


def test_an_unresolved_critical_contradiction_fails() -> None:
    """Two conflicting statements about what the role is for produce a matrix
    that is wrong in a way no later stage can detect."""
    swot = dict(GOOD_SWOT, contradictions=[{"id": "c1", "critical": True, "resolved": False}])
    assert not gates.bodha_gate(swot).passed


def test_uncovered_role_context_is_recorded() -> None:
    """A quadrant full of adjectives about the team tells Sutra nothing about
    what the person will be measured on."""
    swot = dict(GOOD_SWOT, context_covered=["role_objectives"])
    verdict = gates.bodha_gate(swot)
    assert not verdict.passed
    assert "role_context_not_covered" in {f.issue for f in verdict.findings}


# ── Sutra ────────────────────────────────────────────────────────────────────


def _matrix_items(prefix: str, count: int = gates.MIN_MATRIX_ITEMS) -> list[dict]:
    return [{"name": f"{prefix}-{n}", "rubric": "bands"} for n in range(count)]


GOOD_MATRIX = {
    "must_have": _matrix_items("must"),
    "nice_to_have": _matrix_items("nice"),
    "behavioural": _matrix_items("behaviour"),
    "critical_requirements": ["kafka"],
    "covered_requirements": ["kafka"],
}


def test_a_complete_matrix_passes() -> None:
    assert gates.sutra_gate(GOOD_MATRIX).passed


def test_a_critical_jd_requirement_with_no_criterion_fails() -> None:
    """The matrix is frozen once anyone is assessed against it, so the omission
    is permanent for that job."""
    matrix = dict(GOOD_MATRIX, covered_requirements=[])
    verdict = gates.sutra_gate(matrix)
    assert not verdict.passed
    assert "critical_requirement_unrepresented" in {f.issue for f in verdict.findings}


def test_a_criterion_in_two_categories_fails() -> None:
    """One criterion in both Must-have and Nice-to-have makes the hard cap
    ambiguous: the same finding both caps Overall and does not."""
    matrix = dict(GOOD_MATRIX, nice_to_have=_matrix_items("must"))
    verdict = gates.sutra_gate(matrix)
    assert "duplicate_criterion" in {f.issue for f in verdict.findings}


def test_thin_coverage_fails() -> None:
    """Fewer than five per category is not a thin matrix, it is one that cannot
    distinguish two candidates."""
    matrix = dict(GOOD_MATRIX, behavioural=_matrix_items("behaviour", 2))
    assert not gates.sutra_gate(matrix).passed


def test_culture_is_refused_as_a_behavioural_competency() -> None:
    """Cultural fit cannot be assessed from a single conversation, and the
    Hiring Manager's Edit control can type anything."""
    matrix = dict(
        GOOD_MATRIX,
        behavioural=[{"name": "Culture", "rubric": "bands"}] + _matrix_items("behaviour", 4),
    )
    verdict = gates.sutra_gate(matrix)
    assert "culture_as_competency" in {f.issue for f in verdict.findings}


def test_a_criterion_with_no_rubric_is_flagged() -> None:
    """No rubric means no answer can be graded against it, so the criterion is
    on the report and scores nothing."""
    matrix = dict(
        GOOD_MATRIX,
        must_have=[{"name": "Kafka", "rubric": ""}] + _matrix_items("must", 4),
    )
    assert "unusable_rubric" in {f.issue for f in gates.sutra_gate(matrix).findings}


# ── Yukti ────────────────────────────────────────────────────────────────────

GOOD_MATCH = {
    "resume_parsed": True,
    "categories": [
        {"name": f"category-{n}", "grade": rating.GRADE_MATCHING, "evidence": ["line 4"]}
        for n in range(gates.MIN_MATCHING_CATEGORIES)
    ],
    "inferred_fields": ["years_of_experience"],
}


def test_a_complete_matching_pass_passes() -> None:
    assert gates.yukti_gate(GOOD_MATCH).passed


def test_an_unparsed_resume_fails() -> None:
    """Grading against a file nothing parsed produces a grade about nothing."""
    assert not gates.yukti_gate(dict(GOOD_MATCH, resume_parsed=False)).passed


def test_an_inference_about_a_protected_attribute_fails() -> None:
    """An inference on age or gender is unlawful in hiring and would be stated
    in a document a client keeps."""
    match = dict(GOOD_MATCH, inferred_fields=["age"])
    verdict = gates.yukti_gate(match)
    assert not verdict.passed
    assert "forbidden_inference" in {f.issue for f in verdict.findings}


def test_a_graded_category_citing_nothing_is_flagged() -> None:
    """A conclusion with no resume line behind it is the AI Score claiming to
    have read something it did not."""
    match = dict(
        GOOD_MATCH,
        categories=[
            {"name": "skills", "grade": rating.GRADE_HIGHLY, "evidence": []},
            *GOOD_MATCH["categories"],
        ],
    )
    assert "conclusion_without_evidence" in {f.issue for f in gates.yukti_gate(match).findings}


# ── Vaada ────────────────────────────────────────────────────────────────────

GOOD_CONVERSATION = {
    "required_competencies": ["Kafka", "Ownership"],
    "covered_competencies": ["Kafka", "Ownership"],
    "completed": True,
    "follow_ups_used": 4,
    "follow_up_budget": 15,
    "redundant_questions": 0,
}


def test_a_complete_conversation_passes() -> None:
    assert gates.vaada_gate(GOOD_CONVERSATION).passed


def test_a_competency_the_conversation_never_probed_fails() -> None:
    """A grade is written for every competency in the matrix, so an unprobed one
    is graded from nothing."""
    conversation = dict(GOOD_CONVERSATION, covered_competencies=["Kafka"])
    verdict = gates.vaada_gate(conversation)
    assert not verdict.passed
    assert "evidence_coverage_insufficient" in {f.issue for f in verdict.findings}


def test_an_assessment_closed_early_fails() -> None:
    """Completion is what fires the charge and dispatches scoring; closing early
    charges the customer while the candidate is still typing."""
    assert not gates.vaada_gate(dict(GOOD_CONVERSATION, completed=False)).passed


def test_questioning_past_the_follow_up_budget_fails() -> None:
    """An over-long interview is an abandoned one, and the budget is owned by
    the interviewer rather than restated here."""
    conversation = dict(GOOD_CONVERSATION, follow_ups_used=20)
    verdict = gates.vaada_gate(conversation)
    assert not verdict.passed
    assert "excessive_questioning" in {f.issue for f in verdict.findings}


# ── Miti ─────────────────────────────────────────────────────────────────────

GOOD_SCORING = {
    "required_events": ["q1", "q2"],
    "scored_events": ["q1", "q2"],
    "item_grades": [
        {
            "name": "Kafka",
            "category": "must_have",
            "grade": rating.GRADE_MATCHING,
            "evidence_count": 3,
        },
        {
            "name": "Ownership",
            "category": "behavioural",
            "grade": rating.GRADE_MATCHING,
            "evidence_count": 2,
        },
    ],
    "overall_grade": rating.GRADE_MATCHING,
    "contradictions": [],
}


def test_a_complete_scoring_state_passes() -> None:
    assert gates.miti_gate(GOOD_SCORING).passed


def test_an_unscored_answer_fails() -> None:
    """A grade computed over a subset of the answers is a grade about a
    conversation that did not happen."""
    scoring = dict(GOOD_SCORING, scored_events=["q1"])
    verdict = gates.miti_gate(scoring)
    assert not verdict.passed
    assert "scoring_event_missing" in {f.issue for f in verdict.findings}


def test_the_hard_cap_fails_when_overall_outranks_a_failed_must_have() -> None:
    """Any Must-have graded Not Matching caps Overall at Moderately Matching,
    with no override. Checked arithmetically, because asking a model whether it
    applied its own cap asks the component that got it wrong."""
    scoring = dict(
        GOOD_SCORING,
        item_grades=[
            {
                "name": "Kafka",
                "category": "must_have",
                "grade": rating.GRADE_NOT,
                "evidence_count": 3,
            }
        ],
        overall_grade=rating.GRADE_HIGHLY,
    )
    verdict = gates.miti_gate(scoring)
    assert not verdict.passed
    assert "hard_cap_not_applied" in {f.issue for f in verdict.findings}


def test_the_hard_cap_allows_an_overall_at_or_below_the_ceiling() -> None:
    """The cap is `min`, never an assignment: a candidate already grading Not
    Matching must stay there rather than being promoted into the capped band."""
    for overall in (rating.GRADE_MODERATELY, rating.GRADE_NOT):
        scoring = dict(
            GOOD_SCORING,
            item_grades=[
                {
                    "name": "Kafka",
                    "category": "must_have",
                    "grade": rating.GRADE_NOT,
                    "evidence_count": 3,
                }
            ],
            overall_grade=overall,
        )
        assert "hard_cap_not_applied" not in {
            f.issue for f in gates.miti_gate(scoring).findings
        }


def test_a_consequential_grade_on_one_answer_is_flagged() -> None:
    """One answer is an anecdote, and the grade is stated as a finding about a
    person in a document a client keeps."""
    scoring = dict(
        GOOD_SCORING,
        item_grades=[
            {
                "name": "Kafka",
                "category": "must_have",
                "grade": rating.GRADE_MATCHING,
                "evidence_count": 1,
            }
        ],
    )
    assert "insufficient_evidence_for_grade" in {
        f.issue for f in gates.miti_gate(scoring).findings
    }


def test_an_unresolved_contradiction_in_scoring_fails() -> None:
    """Two answers that conflict produce a grade that rests on whichever one was
    read last, which is not a criterion."""
    scoring = dict(GOOD_SCORING, contradictions=[{"id": "x", "resolved": False}])
    assert not gates.miti_gate(scoring).passed


# ── Siddhi ───────────────────────────────────────────────────────────────────

VALIDATION = {"notice_period": "60 days", "current_ctc": "4,00,000"}

GOOD_REPORT = {
    "ai_score": {"summary": "Strong platform background across streaming systems."},
    "ppi_assessment": {"summary": "Consistent ownership of production incidents."},
    "validation": dict(VALIDATION),
    "validation_source": dict(VALIDATION),
    "gap_analysis": [{"id": "g1", "grounded_in_answer": True}],
    "claims": [{"id": "c1", "text": "Led a migration", "evidence_refs": ["q4"]}],
    "grades": {"Kafka": rating.GRADE_MATCHING},
    "miti_grades": {"Kafka": rating.GRADE_MATCHING},
}


def test_a_complete_report_passes() -> None:
    assert gates.siddhi_gate(GOOD_REPORT).passed


def test_a_missing_section_fails() -> None:
    """An absent Gap Analysis and an empty one read the same to a client and
    mean opposite things."""
    report = {k: v for k, v in GOOD_REPORT.items() if k != "gap_analysis"}
    verdict = gates.siddhi_gate(report)
    assert not verdict.passed
    assert "missing_report_section" in {f.issue for f in verdict.findings}


def test_a_grade_that_disagrees_with_scoring_fails() -> None:
    """The report states grades; it does not regrade. A disagreement means two
    documents about one candidate contradict each other."""
    report = dict(GOOD_REPORT, grades={"Kafka": rating.GRADE_HIGHLY})
    verdict = gates.siddhi_gate(report)
    assert not verdict.passed
    assert "grade_disagrees_with_scoring" in {f.issue for f in verdict.findings}


def test_altered_validation_fails() -> None:
    """Validation is factual application data that nothing scores; a reworded
    notice period is a fabricated fact in a document a client decides from."""
    report = dict(GOOD_REPORT, validation={"notice_period": "about two months",
                                           "current_ctc": "4,00,000"})
    verdict = gates.siddhi_gate(report)
    assert not verdict.passed
    assert "validation_altered" in {f.issue for f in verdict.findings}


def test_a_dropped_validation_field_fails() -> None:
    """A field the candidate answered and the report omits is not a shorter
    section, it is a missing answer the recruiter will assume was never given."""
    report = dict(GOOD_REPORT, validation={"notice_period": "60 days"})
    assert "validation_field_dropped" in {f.issue for f in gates.siddhi_gate(report).findings}


def test_a_number_bound_to_the_assessment_fails() -> None:
    """No score, percentage or rank reaches a client, in the UI, an API response
    or an email."""
    report = dict(
        GOOD_REPORT,
        ppi_assessment={"summary": "The candidate scored 82 out of 100 on this competency."},
    )
    verdict = gates.siddhi_gate(report)
    assert not verdict.passed
    assert "number_reaches_client" in {f.issue for f in verdict.findings}


def test_a_generic_gap_probe_is_flagged() -> None:
    """A probe not anchored in what this candidate said is a probe written for
    nobody in particular."""
    report = dict(GOOD_REPORT, gap_analysis=[{"id": "g1", "grounded_in_answer": False}])
    assert "generic_gap_probe" in {f.issue for f in gates.siddhi_gate(report).findings}


def test_an_ungrounded_claim_is_flagged() -> None:
    """A claim with no evidence ref is prose the report cannot defend."""
    report = dict(GOOD_REPORT, claims=[{"id": "c1", "text": "Led a migration",
                                        "evidence_refs": []}])
    assert "claim_not_grounded" in {f.issue for f in gates.siddhi_gate(report).findings}


# ── the gate table ───────────────────────────────────────────────────────────


def test_every_named_agent_has_a_gate() -> None:
    """An agent added without a gate should be visibly absent from a mapping,
    not silently unchecked."""
    assert set(gates.GATES) == set(identity.AGENTS)


def test_an_unknown_agent_has_no_default_pass() -> None:
    """A default pass is how an ungated agent ships looking gated."""
    with pytest.raises(gates.NoGate):
        gates.run_gate("nobody", {})


def test_no_gate_calls_a_model() -> None:
    """A guard that needs a provider fails open exactly when it is needed."""
    with open(gates.__file__, encoding="utf-8") as handle:
        source = handle.read()
    assert "invoke_llm" not in source and "llm_router" not in source


# ── escalation (spec 38) ─────────────────────────────────────────────────────


def test_a_sensitive_action_escalates_at_full_confidence() -> None:
    """Building it the other way round means the agent's own opinion of itself
    authorises an irreversible act, and a confidently wrong agent is the one
    that should be stopped."""
    raised = escalation.for_action(actions.REJECT_CANDIDATE, confidence=1.0)
    assert raised is not None
    assert raised.reason == escalation.REASON_SENSITIVE_ACTION


def test_low_confidence_widens_the_review_set_rather_than_narrowing_it() -> None:
    """An ordinary action below the floor also needs a person; nothing about a
    low score lets anything through."""
    ordinary = "draft_email"
    assert escalation.for_action(ordinary, confidence=0.95) is None
    assert escalation.for_action(ordinary, confidence=0.2) is not None


def test_every_sensitive_action_escalates() -> None:
    """The list is short and every entry is irreversible to a person."""
    for action in actions.SENSITIVE_ACTIONS:
        assert escalation.for_action(action, confidence=1.0) is not None


def test_an_escalation_must_state_all_five_parts() -> None:
    """'Needs review' gets routed to a queue, aged, and resolved by someone
    guessing, which is the outcome escalating was meant to avoid."""
    with pytest.raises(escalation.EscalationContract):
        escalation.escalate(
            reason=escalation.REASON_INSUFFICIENT_EVIDENCE,
            uncertainty="",
            evidence_present=(),
            evidence_missing=("q4",),
            stopped_because="two attempts failed",
            human_must_resolve="decide",
        )


def test_an_unknown_escalation_reason_is_refused() -> None:
    """A free-text reason is one nothing can route or count."""
    with pytest.raises(escalation.EscalationContract):
        escalation.escalate(
            reason="felt_wrong",
            uncertainty="something",
            evidence_present=(),
            evidence_missing=("q4",),
            stopped_because="two attempts failed",
            human_must_resolve="decide",
        )


def test_a_failed_gate_becomes_an_escalation_carrying_its_instructions() -> None:
    """The findings are already written as instructions, so a person reading one
    knows what to do."""
    verdict = gates.bodha_gate(dict(GOOD_SWOT, sources=[]))
    raised = escalation.from_verdict(
        verdict, uncertainty="whether the role context is trustworthy"
    )
    assert raised is not None
    assert raised.human_must_resolve
    assert raised.evidence_missing


def test_a_passing_gate_raises_nothing() -> None:
    """An escalation for a sound output trains a recruiter to ignore the queue."""
    assert (
        escalation.from_verdict(
            gates.bodha_gate(GOOD_SWOT), uncertainty="whether the intake is sound"
        )
        is None
    )


def test_an_escalation_serialises_without_content() -> None:
    """It is persisted and shown in a queue; the transcript is not."""
    raised = escalation.for_action(actions.REVOKE_OFFER, confidence=1.0)
    assert raised is not None
    payload = raised.as_dict()
    assert set(
        [
            "reason",
            "uncertainty",
            "evidence_present",
            "evidence_missing",
            "stopped_because",
            "human_must_resolve",
        ]
    ) <= set(payload)


def test_the_verdict_confidence_is_arithmetic_not_a_models_opinion() -> None:
    """A judge makes the criteria unfalsifiable and fails exactly when the
    provider is already failing."""
    one_high = verification.verdict(
        "x", [verification.high("i", "l", "d", "fix it")]
    )
    two_medium = verification.verdict(
        "x",
        [verification.medium("i", "l", "d", "fix it") for _ in range(2)],
    )
    one_medium = verification.verdict(
        "x", [verification.medium("i", "l", "d", "fix it")]
    )
    assert not one_high.passed
    assert not two_medium.passed
    assert one_medium.passed
