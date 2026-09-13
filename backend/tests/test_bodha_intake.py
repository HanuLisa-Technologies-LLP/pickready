"""Bodha: Role SWOT quality control, the seven probes, and the situation type.

TWO RULES CARRY MOST OF THE VALUE AND BOTH ARE ABOUT REFUSING THINGS:

  * 18.5's rejection rules hand a SWOT back to the Hiring Manager. A session
    that accepts whatever it is given produces a matrix that looks complete
    and grades nobody usefully.
  * A requirement stated as a trait rather than as observable evidence is
    refused and the refusal shows the difference, because a manager handed a
    bare "no" simply rephrases the same adjective.

The situation-type tests are here rather than in the layers file because the
CONFIRMATION is Bodha's job: spec-doc5 names misclassification as the single
most expensive error available at intake, and the only available check is a
person who knows the role reading the classification back.

The observable-evidence detector these rules lean on is pinned on its own in
`test_observable_detector.py`; what is asserted here is that this session
holds its inputs to it.
"""
from __future__ import annotations

import pytest

from app.services.hiring import situations, swot_quality


# ── SWOT quality control (§18.5) ─────────────────────────────────────────────


def _good_swot() -> dict[str, list[str]]:
    return {
        "strengths": [
            "They shipped the payments rewrite and owned it through two incidents"
        ],
        "weaknesses": [
            "The last person left because they could not get product to commit "
            "to a scope and stopped pushing"
        ],
        "opportunities": [
            "They will take over the platform roadmap once the migration finished"
        ],
        "threats": [
            "We removed two engineers from the team last quarter and the on-call "
            "rota is now three people"
        ],
    }


def test_a_good_swot_is_accepted() -> None:
    report = swot_quality.review(_good_swot(), situation_key="turnaround")
    assert report.accepted, [r.rule for r in report.rejections]


def test_absent_weaknesses_are_refused() -> None:
    """A role with no internal weakness is a role nobody has thought about
    failing in, and failure modes are where the discriminating criteria come
    from."""
    captured = _good_swot()
    captured["weaknesses"] = []
    report = swot_quality.review(captured, situation_key="turnaround")
    assert "weaknesses_absent" in {r.rule for r in report.rejections}


def test_external_only_weaknesses_are_refused() -> None:
    """"The market is tight" is a threat to hiring, not a weakness in the
    role."""
    captured = _good_swot()
    captured["weaknesses"] = [
        "The market for these skills is extremely tight right now",
        "Salaries have gone up 30% and our budget has not",
    ]
    report = swot_quality.review(captured, situation_key="turnaround")
    assert "weaknesses_external_only" in {r.rule for r in report.rejections}


def test_a_mixed_weakness_list_is_accepted() -> None:
    """Refusing a manager who mentioned the market ALONGSIDE a real internal
    weakness would be pedantry that gets the session abandoned."""
    captured = _good_swot()
    captured["weaknesses"] = [
        "The market for these skills is tight",
        "The last person left because they could not get product to commit to a scope",
    ]
    report = swot_quality.review(captured, situation_key="turnaround")
    assert "weaknesses_external_only" not in {r.rule for r in report.rejections}


def test_everything_being_must_have_is_refused() -> None:
    """A matrix where every item caps the report grades every imperfect
    candidate the same, which is the same as not grading."""
    report = swot_quality.review(
        _good_swot(),
        categories=["must_have"] * 8 + ["nice_to_have"],
        situation_key="turnaround",
    )
    assert "everything_is_must_have" in {r.rule for r in report.rejections}


def test_a_demanding_role_may_legitimately_have_more_essentials() -> None:
    """Refusing that would be the platform telling a hiring manager they are
    wrong about their own job."""
    report = swot_quality.review(
        _good_swot(),
        categories=["must_have"] * 3 + ["nice_to_have"] * 2 + ["behavioural"],
        situation_key="turnaround",
    )
    assert "everything_is_must_have" not in {r.rule for r in report.rejections}


def test_a_trait_rather_than_evidence_is_refused() -> None:
    captured = _good_swot()
    captured["strengths"] = ["strong ownership mindset and a real can-do attitude"]
    report = swot_quality.review(captured, situation_key="turnaround")
    assert "trait_not_evidence" in {r.rule for r in report.rejections}


def test_the_trait_rule_uses_the_same_detector_as_the_dna_instrument() -> None:
    """Two copies of "is this an adjective" would drift, and the drift would be
    invisible -- one intake accepting what the other refuses."""
    import inspect

    source = inspect.getsource(swot_quality.review)
    assert "observable.is_observable" in source
    assert "observable.rejection_message" in source


def test_at_most_one_trait_refusal_per_area() -> None:
    """A manager handed six refusals at once stops doing the session, and the
    first one teaches the pattern."""
    captured = {
        "strengths": ["proactive", "driven", "hungry"],
        "weaknesses": ["The last person could not get product to commit to a scope"],
        "opportunities": [],
        "threats": [],
    }
    report = swot_quality.review(captured, situation_key="turnaround")
    traits = [r for r in report.rejections if r.rule == "trait_not_evidence"]
    assert len(traits) == 1


def test_a_prohibited_disqualifier_is_refused_in_the_swot_too() -> None:
    report = swot_quality.review(
        _good_swot(),
        disqualifiers=["Nobody over 50, they will not keep up"],
        situation_key="turnaround",
    )
    assert "prohibited_disqualifier" in {r.rule for r in report.rejections}


def test_an_undeterminable_situation_is_refused() -> None:
    """Named in spec-doc5 as the single most expensive error available at
    intake, because it re-weights the whole matrix coherently and invisibly."""
    captured = {
        "strengths": ["They shipped the reporting rewrite and owned it end to end"],
        "weaknesses": ["The last person could not get product to commit to a scope"],
        "opportunities": [],
        "threats": [],
    }
    report = swot_quality.review(captured, situation_key=None)
    assert "situation_undeterminable" in {r.rule for r in report.rejections}


def test_a_confirmed_situation_needs_no_signal() -> None:
    """A human confirmed it, which is stronger than any signal count."""
    captured = {
        "strengths": ["They shipped the reporting rewrite and owned it end to end"],
        "weaknesses": ["The last person could not get product to commit to a scope"],
        "opportunities": [],
        "threats": [],
    }
    report = swot_quality.review(captured, situation_key="steady_state")
    assert "situation_undeterminable" not in {r.rule for r in report.rejections}


def test_every_rejection_carries_a_sentence_to_say() -> None:
    captured = _good_swot()
    captured["weaknesses"] = []
    captured["strengths"] = ["proactive"]
    report = swot_quality.review(
        captured, categories=["must_have"] * 9, disqualifiers=["no women"]
    )
    assert report.rejections
    for rejection in report.rejections:
        assert rejection.say.strip()
        assert len(rejection.say.split()) > 8, rejection.rule


# ── The seven high-value probes (§18.3) ──────────────────────────────────────
def test_the_seven_probes_are_the_seven_the_runbook_names() -> None:
    """RPN-PHIL-001 §18.3 names all seven, and five of them were different.

    CORRECTED AGAINST THE RUNBOOK. The pre-Runbook set had seven probes and
    shared only the trade-off probe and (loosely) the last-person probe with
    §18.3. The three that were missing outright are the ones worth naming:

      * the EMPTY-SEAT probe, which is what turns an abstract requirement into
        the concrete work that is not getting done;
      * the SCALE-REALITY probe, without which a scope mismatch is invisible
        until the hire arrives; and
      * the REJECTION probe, which is the session's only instrument for
        surfacing an UNDECLARED criterion, and an undeclared criterion is
        exactly what becomes an invisible filter later.

    The verbatim comparison against the document lives in
    `test_runbook_reconciliation.py`; this pins the set so a future edit
    replacing one with a nicer-sounding question is caught here first.
    """
    assert len(swot_quality.HIGH_VALUE_PROBES) == 7
    assert {p.key for p in swot_quality.HIGH_VALUE_PROBES} == {
        "empty_seat",
        "first_90_days",
        "last_person",
        "rejection",
        "trade_off",
        "scale_reality",
        "autonomy",
    }
    for probe in swot_quality.HIGH_VALUE_PROBES:
        assert probe.question.strip().endswith("?"), probe.key
        assert probe.purpose.strip(), probe.key
        assert probe.source == "RPN-PHIL-001 §18.3", probe.key


def test_the_trade_off_probe_is_parameterised_on_this_role_s_competencies() -> None:
    """§18.3 writes it as "deep X or deep Y" and Appendix B6 says to repeat it
    until the ranking is stable.

    A probe hardcoded to one pair can be asked once and force-ranks nothing.
    """
    question = swot_quality.trade_off_question("incident response", "systems design")
    assert "incident response" in question
    assert "systems design" in question
    with pytest.raises(ValueError):
        swot_quality.trade_off_question("incident response", "")

def test_probes_are_offered_in_order_and_not_repeated() -> None:
    """The probes build on each other; a session that asks "what would make this
    harder" before "who is this replacing" gets a worse answer to both."""
    first = swot_quality.probe_for("weaknesses")
    assert first is not None
    second = swot_quality.probe_for("weaknesses", asked=[first.key])
    assert second is not None and second.key != first.key
    exhausted = swot_quality.probe_for(
        "weaknesses", asked=[p.key for p in swot_quality.HIGH_VALUE_PROBES]
    )
    assert exhausted is None


# ── Situation types (§18.4) ──────────────────────────────────────────────────


def test_there_are_exactly_six_situation_types() -> None:
    assert len(situations.SITUATION_TYPES) == 6
    assert set(situations.SITUATIONS) == set(situations.SITUATION_TYPES)


def test_the_two_quoted_consequences_are_implemented_exactly() -> None:
    """RPN-PHIL-001 §18.4 states both, and spec-doc5 quoted them: "a Turnaround
    role weights Track Record/Impact and Role Fit up; a Greenfield role weights
    Trajectory and Role Fit up".

    Asserted on the ARROWS now rather than on resolved multipliers. The arrows
    are what §18.4 states; the multipliers are Runbook data and are checked
    against the document itself in `test_runbook_reconciliation.py`.
    """
    turnaround = situations.SITUATIONS[situations.TURNAROUND].effects
    assert turnaround["track_record_impact"] == situations.STRONG_UP
    assert turnaround["role_context_fit"] == situations.UP

    greenfield = situations.SITUATIONS[situations.GREENFIELD].effects
    assert greenfield["trajectory_potential"] == situations.STRONG_UP
    assert greenfield["role_context_fit"] == situations.UP


def test_no_situation_lifts_a_dimension_the_runbook_does_not_name() -> None:
    """The defect this pins is the one reconciliation actually found.

    Four of the six rows carried extra lifts and cuts that §18.4 does not state
    -- Gap-fill cut Trajectory, Turnaround lifted Verified Competence, Scale-up
    lifted Verified Competence and Role Fit, Succession lifted Role Fit and
    Authenticity. Every one of them was defensible in isolation and none of them
    was in the Runbook, and nothing downstream could tell the difference,
    because a coherently mis-weighted matrix has nothing inconsistent in it.

    The authoritative comparison against the document lives in
    `test_runbook_reconciliation.py`; this is the cheap invariant that a future
    edit adding "just one more" effect trips immediately.
    """
    expected = {
        situations.GAP_FILL: {"role_context_fit", "verified_competence"},
        situations.TURNAROUND: {"track_record_impact", "role_context_fit"},
        situations.SCALE_UP: {"track_record_impact", "trajectory_potential"},
        situations.GREENFIELD: {"trajectory_potential", "role_context_fit"},
        situations.STEADY_STATE: {"verified_competence", "trajectory_potential"},
        situations.SUCCESSION: {"trajectory_potential", "track_record_impact"},
    }
    for key, dimensions in expected.items():
        assert set(situations.SITUATIONS[key].effects) == dimensions, key


def test_every_situation_names_its_evidence_emphasis() -> None:
    """§18.4's fourth column, absent from the first implementation entirely.

    A situation type that re-weighted the matrix without changing what evidence
    was sought would re-rank candidates on evidence nobody went looking for.
    """
    for key in situations.SITUATION_TYPES:
        assert situations.evidence_emphasis(key).strip(), key
    assert situations.evidence_emphasis(None) == ""

def test_an_unknown_situation_weights_neutrally_rather_than_raising() -> None:
    """A job whose intake predates the feature must still get a matrix.
    Weighting it neutrally is exactly what "no situation type expressed" should
    mean."""
    modifiers = situations.dimension_modifiers(None)
    assert set(modifiers.values()) == {1.0}


def test_signal_classification_proposes_and_never_decides() -> None:
    proposals = situations.classify_signals(
        ["We need someone to turn around a team that is losing people"]
    )
    assert proposals
    assert proposals[0][0] == situations.TURNAROUND
    # It returns candidates, not a decision.
    assert isinstance(proposals, list)


def test_the_confirmation_prompt_states_the_consequence() -> None:
    """The manager is agreeing to something with an effect, not answering a
    survey question."""
    prompt = situations.confirmation_prompt(
        situations.TURNAROUND, evidence=["the team losing people"]
    )
    assert "Turnaround" in prompt
    assert "lean harder on" in prompt
    assert "tell me and I will change it" in prompt


def test_the_confirmation_prompt_offers_the_alternative() -> None:
    """"Is it this or that" is a far easier question to answer correctly than
    "is this right", which people agree to."""
    prompt = situations.confirmation_prompt(situations.GREENFIELD)
    assert "Scale-up" in prompt


def test_the_confirmation_prompt_carries_no_numbers() -> None:
    for key in situations.SITUATION_TYPES:
        prompt = situations.confirmation_prompt(key)
        assert not any(char.isdigit() for char in prompt), key


def test_the_artifact_projection_never_carries_the_modifiers() -> None:
    """Internal ranking data, like every other weight."""
    import json

    projected = json.dumps(situations.as_dict(situations.TURNAROUND))
    assert "1.35" not in projected
    assert "modifiers" not in projected


# ── §18.5 rule 6: the best-performer test ────────────────────────────────────


def test_the_best_performer_test_refuses_when_the_manager_says_yes() -> None:
    """RPN-PHIL-001 §18.5, the rule the pre-Runbook implementation did not have.

    "The stated requirements would exclude the hiring manager's own current best
    performer (a devastating and highly effective test -- run it)."

    It is the only §18.5 trigger that catches a requirement set which is
    internally coherent. The other five catch a malformed intake; this one
    catches a well-formed intake whose bar the manager's own strongest person
    would fail, which is the most common way a real scorecard goes wrong.
    """
    report = swot_quality.review(
        _good_swot(), situation_key="turnaround", best_performer_excluded=True
    )
    assert "excludes_best_performer" in {r.rule for r in report.rejections}
    assert not report.accepted


def test_the_best_performer_test_accepts_when_the_manager_says_no() -> None:
    report = swot_quality.review(
        _good_swot(), situation_key="turnaround", best_performer_excluded=False
    )
    assert "excludes_best_performer" not in {r.rule for r in report.rejections}
    assert "excludes_best_performer" not in report.outstanding


def test_an_unasked_best_performer_test_is_outstanding_and_never_a_pass() -> None:
    """A test nobody asked is not a test somebody passed.

    Collapsing the two would let the most effective rule in §18.5 be satisfied
    by never running it, which is precisely what the Runbook's "run it" guards
    against.
    """
    report = swot_quality.review(_good_swot(), situation_key="turnaround")
    assert "excludes_best_performer" in report.outstanding
    assert "excludes_best_performer" not in {r.rule for r in report.rejections}


def test_an_ambiguous_best_performer_answer_is_outstanding_not_a_pass() -> None:
    """When unsure between two readings, take the one that blocks progression."""
    for answer in ("I am not sure", "maybe", "hard to say"):
        report = swot_quality.review(
            _good_swot(), situation_key="turnaround", best_performer_excluded=answer
        )
        assert "excludes_best_performer" in report.outstanding, answer


def test_every_runbook_rejection_rule_has_an_implementation() -> None:
    """§18.5 lists six triggers; this file's `review` must be able to emit all
    of them, plus the external-only split of the first.

    The count is asserted because "does this code implement all of §18.5" is
    exactly the question that went unanswered for the life of the previous
    implementation, which had five.
    """
    import inspect

    source = inspect.getsource(swot_quality.review)
    for rule, description in swot_quality.REJECTION_RULES:
        assert f'rule="{rule}"' in source or f'"{rule}"' in source, description
