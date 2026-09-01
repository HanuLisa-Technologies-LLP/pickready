"""Stage 5 over a whole report, which is where the outage floor is decided.

`test_miti_pipeline` exercises `escalate` on one contradiction at a time. This
file runs `triangulate` over a REPORT, because three of its properties only
appear when the model's explanations and the deterministic stock list meet:

  THE STOCK LIST IS MERGED, NEVER REPLACED. If a provider outage left the
  generated explanations empty and the stock list were only a fallback, the
  two-explanation floor would quietly stop holding -- and an outage that
  silently disabled integrity escalation is the worst failure this stage has,
  because it reads as a clean run.

  A DUPLICATE IS NOT A SECOND EXPLANATION. The model returning the stock
  sentence back must not satisfy the floor twice over; two explanations means
  two, and counting one twice is the manufactured-corroboration error moved one
  layer up.

  NOTHING HERE CAN END A CANDIDACY. `TriangulationResult` has no reject field,
  no status and no decision, and the enforcement is that absence. G3 fails
  LOUDLY and blocks NOTHING, because a blocking integrity gate would end a
  candidacy without a person ever seeing the finding.

Pure functions. No database, no network, no model.
"""
from __future__ import annotations

import dataclasses

import pytest

from app.services.evidence import contradictions as detector
from app.services.miti import triangulation


AXIS = detector.AXIS_RESUME_VS_ANSWERS


def _contradiction(severity: str, axis: str = AXIS) -> detector.Contradiction:
    return detector.Contradiction(
        axis=axis,
        severity=severity,
        location="competency:stream-processing",
        detail="The stated span and the answered span do not agree.",
        recommendation="Ask the candidate to describe the sequence of dates.",
        actions=detector.actions_for(
            severity, phase=detector.PHASE_POST_CONVERSATION
        ),
    )


def _report(*severities: str) -> detector.ContradictionReport:
    return detector.ContradictionReport(
        contradictions=tuple(_contradiction(s) for s in severities)
    )


def _sources(*groups: str) -> list[dict[str, str]]:
    return [
        {"ref": f"ref:{index}", "independence_group": group}
        for index, group in enumerate(groups)
    ]


# ── The empty report ─────────────────────────────────────────────────────────


def test_a_report_with_nothing_in_it_concludes_nothing() -> None:
    result = triangulation.triangulate(detector.ContradictionReport())
    assert result.contradictions == []
    assert result.unresolved == 0
    assert result.integrity_flags == []
    assert result.severity == detector.NONE
    assert result.needs_human_review is False


def test_independence_never_reads_as_zero() -> None:
    """A candidate always spoke for themselves, so the floor is one. Zero would
    make the single-source cap in `escalate` behave as though there were no
    account at all."""
    assert triangulation.triangulate(detector.ContradictionReport()).independence == 1
    assert (
        triangulation.triangulate(_report(detector.MINOR), sources=[]).independence == 1
    )


# ── The outage floor ─────────────────────────────────────────────────────────


def test_the_stock_list_holds_the_floor_when_nothing_was_generated() -> None:
    """The whole reason the stock explanations are deterministic.

    Note WHICH cap then binds, because it is not the obvious one. With the
    stock list in place the two-explanation floor is always satisfied, so a
    provider outage does not reach that rule at all -- it reaches the
    single-source cap instead, and a CRITICAL comes out at MATERIAL rather than
    MINOR. That is the intended behaviour and it is worth writing down: the
    two-explanation rule protects against a REASONING failure, and the stock
    list is what stops an OUTAGE from being mistaken for one.
    """
    result = triangulation.triangulate(_report(detector.CRITICAL), generated=None)
    held = result.contradictions[0]
    assert len(held.explanations) >= triangulation.REQUIRES_BENIGN_EXPLANATIONS
    assert held.proposed_severity == detector.CRITICAL
    assert held.severity == detector.MATERIAL
    assert held.escalation_withheld is True


def test_with_no_explanations_at_all_the_two_explanation_rule_binds(
    monkeypatch,
) -> None:
    """The rule spec-doc5 states, seen on its own. It does not warn: the
    escalation simply does not happen."""
    monkeypatch.setattr(triangulation, "standard_explanations", lambda axis: ())
    result = triangulation.triangulate(
        _report(detector.CRITICAL),
        sources=_sources("candidate", "employer"),
        generated=None,
    )
    held = result.contradictions[0]
    assert held.explanations == ()
    assert held.severity == detector.MINOR
    assert held.escalation_withheld is True
    assert "benign explanation" in held.withheld_reason


def test_a_generated_explanation_is_merged_with_the_stock_ones() -> None:
    """Merged, not replaced. A model that returned one explanation must not
    lower the floor to one."""
    mine = triangulation.BenignExplanation(
        text="The two systems record the end date differently."
    )
    result = triangulation.triangulate(
        _report(detector.CRITICAL), generated={AXIS: [mine]}
    )
    texts = [e.text for e in result.contradictions[0].explanations]
    assert mine.text in texts
    assert len(texts) >= triangulation.REQUIRES_BENIGN_EXPLANATIONS


def test_the_same_sentence_returned_twice_is_not_two_explanations() -> None:
    """The dedup arm. Counting one explanation twice would satisfy the
    two-explanation floor with one, which is the manufactured-corroboration
    error moved a layer up."""
    stock = triangulation.standard_explanations(AXIS)[0]
    echoed = triangulation.BenignExplanation(text=stock.text)
    result = triangulation.triangulate(
        _report(detector.CRITICAL), generated={AXIS: [echoed]}
    )
    texts = [e.text for e in result.contradictions[0].explanations]
    assert texts.count(stock.text) == 1
    assert len(set(texts)) >= triangulation.REQUIRES_BENIGN_EXPLANATIONS


def test_explanations_are_generated_for_an_axis_the_model_said_nothing_about() -> None:
    """A model answering about one axis must not leave another axis with no
    floor at all."""
    report = detector.ContradictionReport(
        contradictions=(
            _contradiction(detector.CRITICAL, detector.AXIS_RESUME_VS_ANSWERS),
            _contradiction(detector.CRITICAL, detector.AXIS_ANSWERS_ACROSS_TURNS),
        )
    )
    result = triangulation.triangulate(
        report,
        generated={
            detector.AXIS_RESUME_VS_ANSWERS: [
                triangulation.BenignExplanation(text="Only this axis was answered.")
            ]
        },
    )
    for triangulated in result.contradictions:
        assert (
            len(triangulated.explanations)
            >= triangulation.REQUIRES_BENIGN_EXPLANATIONS
        ), triangulated.base.axis


# ── What counts as unresolved ────────────────────────────────────────────────


def test_a_minor_contradiction_is_recorded_and_is_not_unresolved() -> None:
    """The false arm of the flag rule. A minor disagreement is a fact about the
    evidence, not an integrity concern, and flagging it would fill a
    recruiter's report with noise until they stopped reading the flags."""
    result = triangulation.triangulate(_report(detector.MINOR))
    assert len(result.contradictions) == 1
    assert result.unresolved == 0
    assert result.integrity_flags == []


def test_a_supported_explanation_settles_it_rather_than_flagging_it() -> None:
    """Escalating anyway would be ignoring the answer we went looking for."""
    supported = [
        triangulation.BenignExplanation(
            text="The company renamed itself in the period.", supported=True
        ),
        triangulation.BenignExplanation(text="The phrasing differs, the substance does not."),
    ]
    result = triangulation.triangulate(
        _report(detector.CRITICAL), generated={AXIS: supported}
    )
    settled = result.contradictions[0]
    assert settled.settled_benignly is True
    assert settled.severity == detector.MINOR
    assert result.unresolved == 0
    assert result.integrity_flags == []


def test_a_material_contradiction_across_two_source_groups_is_flagged() -> None:
    """Two independent sources disagreeing is the case the stage exists for.
    The flag carries the axis and what to do, because a flag a recruiter cannot
    act on is one they learn to skip."""
    result = triangulation.triangulate(
        _report(detector.MATERIAL),
        sources=_sources("candidate", "employer"),
    )
    assert result.independence == 2
    assert result.unresolved == 1
    assert result.integrity_flags
    flag = result.integrity_flags[0]
    assert AXIS in flag
    assert "describe the sequence of dates" in flag


def test_several_contradictions_are_each_counted() -> None:
    result = triangulation.triangulate(
        _report(detector.MATERIAL, detector.MATERIAL, detector.MINOR),
        sources=_sources("candidate", "employer"),
    )
    assert len(result.contradictions) == 3
    assert result.unresolved == 2
    assert len(result.integrity_flags) == 2


def test_one_source_group_holds_a_critical_at_material() -> None:
    """A candidate being imprecise about their own history twice is a real
    signal and a weaker one than an independent source disagreeing."""
    result = triangulation.triangulate(
        _report(detector.CRITICAL),
        sources=_sources("candidate", "candidate"),
        generated={
            AXIS: [
                triangulation.BenignExplanation(text="One."),
                triangulation.BenignExplanation(text="Two."),
            ]
        },
    )
    held = result.contradictions[0]
    assert result.independence == 1
    assert held.proposed_severity == detector.CRITICAL
    assert held.severity == detector.MATERIAL
    assert held.escalation_withheld is True


# ── Nothing here can end a candidacy ─────────────────────────────────────────


def test_the_result_carries_no_way_to_reject_anybody() -> None:
    """The enforcement is the ABSENCE of the capability, so this asserts the
    field set rather than the absence of particular names."""
    fields = {f.name for f in dataclasses.fields(triangulation.TriangulationResult)}
    assert fields == {
        "contradictions",
        "independence",
        "unresolved",
        "integrity_flags",
    }


def test_the_client_is_shown_nothing_from_this_stage() -> None:
    """"We think this candidate may have overstated something" is an accusation
    the platform is in no position to make. It routes to a person precisely so
    a person decides."""
    result = triangulation.triangulate(
        _report(detector.MATERIAL), sources=_sources("candidate", "employer")
    )
    assert result.client_projection() == {}


def test_a_severe_contradiction_asks_for_a_human_rather_than_a_decision() -> None:
    """Human review is owed at CRITICAL and only at CRITICAL, so this is the
    case that survives every hold: two independent groups disagreeing, with two
    ordinary explanations offered and neither of them supported."""
    result = triangulation.triangulate(
        _report(detector.CRITICAL),
        sources=_sources("candidate", "employer"),
        generated={
            AXIS: [
                triangulation.BenignExplanation(text="One reading."),
                triangulation.BenignExplanation(text="Another reading."),
            ]
        },
    )
    assert result.contradictions[0].severity == detector.CRITICAL
    assert result.severity == detector.CRITICAL
    assert result.needs_human_review is True
    # And it still cannot end the candidacy: a human is ASKED, not obeyed.
    assert result.client_projection() == {}


def test_a_material_contradiction_does_not_by_itself_summon_a_human() -> None:
    """Owing a human review at every material disagreement would make the
    obligation routine, and an obligation nobody can meet is one everybody
    learns to clear."""
    result = triangulation.triangulate(
        _report(detector.MATERIAL), sources=_sources("candidate", "employer")
    )
    assert result.severity == detector.MATERIAL
    assert result.needs_human_review is False


def test_the_serialised_form_carries_the_working_and_no_verdict() -> None:
    result = triangulation.triangulate(
        _report(detector.MATERIAL), sources=_sources("candidate", "employer")
    )
    payload = result.as_dict()
    assert set(payload) == {
        "severity",
        "independence",
        "unresolved",
        "needs_human_review",
        "integrity_flags",
        "contradictions",
    }
    entry = payload["contradictions"][0]
    assert entry["proposed_severity"] == detector.MATERIAL
    assert "escalation_withheld" in entry


def test_a_held_contradiction_carries_the_actions_of_the_severity_applied() -> None:
    """Rebuilt at the applied severity so its ACTIONS match. A MINOR carrying
    CRITICAL's actions would send a human-review obligation the severity does
    not justify."""
    result = triangulation.triangulate(_report(detector.CRITICAL), generated=None)
    held = result.contradictions[0]
    assert held.severity == detector.MATERIAL
    assert held.base.actions == detector.actions_for(
        detector.MATERIAL, phase=detector.PHASE_POST_CONVERSATION
    )
    assert detector.ACTION_HUMAN_REVIEW not in held.base.actions
