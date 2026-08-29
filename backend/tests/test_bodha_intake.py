"""Bodha's DUAL MANDATE: Company DNA intake, and Role SWOT quality control.

spec-doc5 §A.3 gives Bodha a second responsibility on the same agent: the
one-time-per-client Company DNA Intake, alongside the existing per-job SWOT
session. This file covers what is new in both.

TWO RULES CARRY MOST OF THE VALUE AND BOTH ARE ABOUT REFUSING THINGS:

  * SECTION 3 of the DNA instrument rejects an adjective and asks again.
    spec-doc5 quotes the bar literally: reject "ownership mindset", require
    "has taken a project from an unclear brief to a shipped outcome". An
    unrejected adjective becomes a competency nobody can evidence, which
    becomes a grade nobody can defend.
  * §18.5's five rejection rules hand a SWOT back to the Hiring Manager. A
    session that accepts whatever it is given produces a matrix that looks
    complete and grades nobody usefully.

The situation-type tests are here rather than in the layers file because the
CONFIRMATION is Bodha's job: spec-doc5 names misclassification as the single
most expensive error available at intake, and the only available check is a
person who knows the role reading the classification back.
"""
from __future__ import annotations

import pytest

from app.services.hiring import company_dna, situations, swot_quality


# ── The instrument's shape ───────────────────────────────────────────────────


def test_the_twelve_sections_are_the_twelve_the_runbook_names() -> None:
    """RPN-PHIL-001 §16 and Appendix A. FIVE of these were missing.

    CORRECTED. The pre-Runbook instrument had twelve sections and only seven of
    §16's twelve among them. Diversity commitments, data and consent, offer
    reality and historical calibration were absent entirely, and organisational
    context was present in name with four of its six fields missing.

    The two that matter most are worth naming here so a future edit that drops
    one has to argue with a sentence rather than with a set literal:

      * HISTORICAL CALIBRATION DATA is what §16 calls "the highest-value input
        in the entire intake". It is the only answer that turns §11.1's weight
        baselines from professional judgement into evidence about this client,
        and an instrument that never asks cannot close the calibration loop the
        whole of Part X is built around.
      * DIVERSITY COMMITMENTS carries the client's explicit confirmation of the
        prohibited-filter list. Its value is procedural: a client who has
        confirmed the list is having a conversation when they later ask for
        something on it, and a client who has not is hearing "no" for the first
        time at the worst possible moment.
    """
    assert len(company_dna.SECTIONS) == 12
    assert company_dna.SECTION_KEYS == (
        "organisational_context",
        "evaluation_philosophy",
        "observable_evidence",
        "failure_modes",
        "non_negotiables",
        "process_shape",
        "diversity_commitments",
        "data_and_consent",
        "offer_reality",
        "sourcing_preferences",
        "dossier_preferences",
        "historical_calibration",
    )
    for section in company_dna.SECTIONS:
        assert section.source.startswith("RPN-PHIL-001 §16"), section.key


def test_section_two_is_six_forced_scales_on_the_runbook_s_range() -> None:
    """§16 S2 and Appendix A2: six questions, each "answered on a forced scale,
    not free text", printed 1 to 5.

    CORRECTED twice over. The pre-Runbook section had five scales of which two
    were §16's, and ran them -2..+2. The range mattered beyond fidelity: 0 is
    "no preference" on the old scale and out of range on the Runbook's, so two
    intakes stored as integers could not be told apart.
    """
    section = next(s for s in company_dna.SECTIONS if s.key == "evaluation_philosophy")
    assert len(section.questions) == 6
    assert {q.key for q in section.questions} == {
        "proven_vs_potential",
        "depth_vs_range",
        "credentials_vs_practice",
        "stability_vs_velocity",
        "training_capacity",
        "non_traditional_tolerance",
    }
    for question in section.questions:
        assert question.kind == company_dna.SCALE_QUESTION, question.key
        assert question.poles, question.key
    assert (company_dna.SCALE_MIN, company_dna.SCALE_MAX) == (1, 5)
    assert company_dna.SCALE_NEUTRAL == 3


def test_two_of_the_six_scales_deliberately_move_no_dimension_weight() -> None:
    """§16 S2 maps credentials-versus-practice to "evidence tier preferences
    within D1" and stability-versus-velocity to "tenure reading rules (§8.4)".

    Neither is a dimension weight, and treating them as one would double-count:
    a client asking for demonstrated practice over credentials is raising the
    evidence bar, not saying Verified Competence matters more.
    """
    unweighted = {"credentials_vs_practice", "stability_vs_velocity", "non_traditional_tolerance"}
    for question in company_dna.SECTIONS[1].questions:
        if question.key in unweighted:
            assert question.dimension is None, question.key
            assert question.counter_dimension is None, question.key
        else:
            assert question.dimension or question.counter_dimension, question.key


def test_every_scale_question_offers_a_real_trade_off() -> None:
    """Not "how important is X" -- everything is important, so a one-sided
    scale collects fives and modifies nothing. Both poles must be things a
    reasonable person would want, or a real constraint they might have."""
    for section in company_dna.SECTIONS:
        for question in section.questions:
            if question.kind != company_dna.SCALE_QUESTION:
                continue
            assert question.poles and len(question.poles) == 2, question.key
            assert question.poles[0] != question.poles[1], question.key


def test_section_three_asks_for_five_to_eight_observable_items() -> None:
    """§16 S3: "The client names five to eight behaviours their strongest
    performers demonstrably show." Appendix A3 fixes the format too."""
    section = next(s for s in company_dna.SECTIONS if s.key == "observable_evidence")
    assert section.min_items == 5
    assert section.max_items == 8
    assert "Has" in section.item_format
    for question in section.questions:
        assert question.kind == company_dna.EVIDENCE_LIST_QUESTION, question.key


def test_both_runbook_example_pairs_are_used_literally() -> None:
    """§16 S3 prints TWO accepted/rejected pairs and the pre-Runbook instrument
    carried one.

    The second is the harder teaching example: "Team player" is a compliment
    rather than an abstraction, and its accepted form names a structural
    condition -- responsibility without authority -- rather than a nicer
    adjective. A client who has seen only the first pair tends to produce a
    longer adjective on the second attempt.
    """
    section = next(s for s in company_dna.SECTIONS if s.key == "observable_evidence")
    assert len(section.examples) == 2
    rejected = {e.rejected.rstrip(".").lower() for e in section.examples}
    assert rejected == {"ownership mindset", "team player"}
    accepted = " ".join(e.accepted for e in section.examples).lower()
    assert "unclear brief to shipped outcome" in accepted
    assert "responsibility without authority" in accepted


# ── Section 3: rejecting an adjective ────────────────────────────────────────


def test_ownership_mindset_is_rejected() -> None:
    assert not company_dna.is_observable("ownership mindset")
    assert not company_dna.is_observable("They have a real ownership mindset")


def test_the_accepted_example_is_accepted() -> None:
    assert company_dna.is_observable(
        "has taken a project from an unclear brief to a shipped outcome and "
        "resolved the ambiguity themselves"
    )


def test_an_event_verb_survives_a_trait_word_beside_it() -> None:
    """"She took ownership of the migration and shipped it in six weeks" is a
    real answer with a trait word in it. The test is asymmetric on purpose."""
    assert company_dna.is_observable(
        "She took ownership of the migration and shipped it in six weeks"
    )


def test_prose_that_is_too_short_to_be_an_account_is_rejected() -> None:
    """The other common form of "ownership mindset": three words, no verb,
    nothing to probe."""
    assert not company_dna.is_observable("very proactive")
    assert not company_dna.is_observable("great communicator")
    assert not company_dna.is_observable("")


@pytest.mark.parametrize(
    "answer",
    [
        "self-starter with a growth mindset",
        "someone who is detail-oriented and dependable",
        "a real team player with strong communication",
        "a rockstar engineer with passion for the craft",
    ],
)
def test_common_adjective_answers_are_all_rejected(answer: str) -> None:
    assert not company_dna.is_observable(answer)


def test_the_rejection_names_the_phrase_it_caught() -> None:
    """A rejection that does not show the difference teaches nothing, and the
    client simply rephrases the same adjective."""
    message = company_dna.rejection_message("we want someone with an ownership mindset")
    assert "ownership" in message
    assert "unclear brief to a shipped outcome" in message


def test_the_rejection_is_a_sentence_not_an_error_code() -> None:
    """The client is a busy person being asked to think harder, not a user
    submitting a malformed form."""
    message = company_dna.rejection_message("driven")
    assert message.endswith(".")
    assert "invalid" not in message.lower()
    assert "error" not in message.lower()


# ── Compilation ──────────────────────────────────────────────────────────────


def test_compilation_is_deterministic() -> None:
    """This artifact constrains every job the client will ever post, so it must
    be reproducible, diffable between versions, and explainable without a
    provider."""
    answers = {"proven_vs_potential": 2, "depth_vs_breadth": -1, "overall_bar": "The short version"}
    assert company_dna.compile_artifact(answers).as_dict() == (
        company_dna.compile_artifact(answers).as_dict()
    )


def test_the_midpoint_of_the_scale_modifies_nothing() -> None:
    """A genuine "no preference" is a common and correct answer. Forcing a
    client off it would manufacture a preference and then weight a matrix by
    it. On §16's 1..5 scale the midpoint is 3, not 0."""
    dna = company_dna.compile_artifact({"proven_vs_potential": company_dna.SCALE_NEUTRAL})
    assert not dna.weight_modifiers


def test_opposite_scale_answers_move_a_weight_in_opposite_directions() -> None:
    proven = company_dna.compile_artifact({"proven_vs_potential": 1})
    potential = company_dna.compile_artifact({"proven_vs_potential": 5})
    axis = "track_record_impact"
    assert proven.weight_modifiers[axis] > 1.0
    assert potential.weight_modifiers[axis] < 1.0


def test_a_paired_scale_moves_both_dimensions_the_runbook_names() -> None:
    """§11.2: "We hire for potential and train" is D5 UP and D2 DOWN, not D5 up
    alone.

    CORRECTED. The pre-Runbook compiler recorded only the dimension a question
    was tagged with, so every client looked like they wanted more of something
    and less of nothing. Across successive intakes that drifts the vector in
    one direction with no answer having asked for it.
    """
    potential = company_dna.compile_artifact({"proven_vs_potential": 5})
    assert potential.weight_modifiers["track_record_impact"] < 1.0
    assert potential.weight_modifiers["trajectory_potential"] > 1.0

    proven = company_dna.compile_artifact({"proven_vs_potential": 1})
    assert proven.weight_modifiers["track_record_impact"] > 1.0
    assert proven.weight_modifiers["trajectory_potential"] < 1.0


def test_a_regulated_industry_raises_authenticity_without_being_asked() -> None:
    """§11.2's last row: "We are regulated and audit-sensitive" gives D4 up.

    It is the only Layer 2 modifier the Runbook takes from Section 1 rather
    than from Section 2's scales, and it is the one a client never thinks to
    ask for. Absent before reconciliation, so a bank and a games studio got the
    same authenticity weighting.
    """
    regulated = company_dna.compile_artifact(
        {"industry_regulatory": "Retail banking, regulated by the RBI"}
    )
    assert regulated.weight_modifiers["authenticity_consistency"] > 1.0

    unregulated = company_dna.compile_artifact(
        {"industry_regulatory": "Consumer mobile games, no regulatory exposure"}
    )
    assert "authenticity_consistency" not in unregulated.weight_modifiers


def test_the_client_cannot_lower_the_corroboration_floor() -> None:
    """§7.4 sets minimum independent groups by SENIORITY, and C2 states it as a
    commitment rather than a preference.

    CORRECTED, and this one was a layering inversion rather than a wrong value.
    The pre-Runbook instrument asked the client how much corroboration they
    wanted and let "a convincing account is enough" set the requirement to one
    source. That is a Layer 2 answer lowering a Layer 1 floor, which
    `layers.INVARIANTS` exists to make impossible and which this route walked
    straight around.
    """
    assert company_dna.minimum_independent_groups("non_managerial") == 2
    assert company_dna.minimum_independent_groups("cxo") == 4
    # It rises with seniority and is never below two.
    for seniority in ("non_managerial", "managerial", "leadership", "cxo"):
        assert company_dna.minimum_independent_groups(seniority) >= 2

    # There is no intake answer that can move it.
    lax = company_dna.compile_artifact(
        {"credentials_vs_practice": 1, "proven_vs_potential": 5}
    )
    assert lax.independence_required == 2

    with pytest.raises(ValueError):
        company_dna.minimum_independent_groups("intern")

def test_every_compiled_modifier_is_within_the_declared_bound() -> None:
    from app.services.hiring import layers

    extreme = company_dna.compile_artifact(
        {
            "proven_vs_potential": 2,
            "depth_vs_breadth": 2,
            "fit_vs_challenge": 2,
            "speed_vs_certainty": 2,
            "individual_vs_team": 2,
            "autonomy": 2,
            "pace": 2,
        }
    )
    bound = layers.BOUNDS["competency_weight"]
    for value in extreme.weight_modifiers.values():
        assert bound.contains(value)


def test_a_prohibited_disqualifier_is_refused_and_recorded() -> None:
    """Recorded rather than dropped: a client who asked for something unlawful
    should be visible to whoever supports them, not silently ignored."""
    dna = company_dna.compile_artifact(
        {
            "hard_disqualifiers": (
                "Must hold a valid CA licence\n"
                "No candidates over 45\n"
                "Must be able to work in India"
            )
        }
    )
    assert "Must hold a valid CA licence" in dna.disqualifiers
    assert "Must be able to work in India" in dna.disqualifiers
    assert "No candidates over 45" in dna.refused_disqualifiers
    assert dna.refusals


def test_only_observable_answers_become_retrieval_context() -> None:
    """A question generated from "ownership mindset" is a question nobody can
    answer with evidence.

    §16 S3 collects five to eight items in one field, so the filter runs per
    LINE: one adjective among six real behaviours must cost that line and not
    the other five.
    """
    dna = company_dna.compile_artifact(
        {
            "observable_behaviours": (
                "ownership mindset\n"
                "They shipped the reporting rewrite and rewrote the runbook "
                "nobody had touched in two years\n"
                "team player\n"
                "Has taken a project from an unclear brief to a shipped outcome "
                "and resolved the ambiguity themselves"
            ),
        }
    )
    assert len(dna.observable_signals) == 2
    assert any("reporting rewrite" in signal for signal in dna.observable_signals)
    assert not any("ownership mindset" == signal for signal in dna.observable_signals)


def test_failure_modes_compile_to_risk_probes_and_not_to_signals() -> None:
    """§16 S4: failure modes "convert into risk probes in the validation
    instrument and into risk-register items in the dossier".

    CORRECTED. They went into the same undifferentiated `observable_signals`
    bucket before, which lost the distinction that makes them useful: a signal
    is something to look FOR and a risk probe is something to look OUT for, and
    they steer questioning in opposite directions. A failure mode compiled as a
    signal would have Vaada probing for the thing the client says goes wrong,
    as though it were a strength.
    """
    dna = company_dna.compile_artifact(
        {
            "observable_behaviours": (
                "They shipped the reporting rewrite and rewrote the runbook "
                "nobody had touched in two years"
            ),
            "failed_hires": (
                "He delivered nothing for two quarters and escalated every "
                "decision back to his manager rather than making it"
            ),
        }
    )
    assert len(dna.observable_signals) == 1
    assert len(dna.risk_probes) == 1
    assert "escalated every" in dna.risk_probes[0]
    assert not any("escalated every" in s for s in dna.observable_signals)


def test_compensation_reality_is_recruiter_context_and_never_configuration() -> None:
    """§16 S9: "Not used in scoring. Used in the risk register."

    §15's compilation rule requires anything that is not one of the six output
    kinds to be LABELLED as recruiter context. An unlabelled leftover is how
    compensation reality ends up influencing a score, and §12.4 prohibits
    salary as a ranking input outright.
    """
    dna = company_dna.compile_artifact(
        {
            "band_vs_market": "We sit at the 40th percentile above senior level",
            "counter_offer_patterns": "Our competitors counter at 30 percent",
        }
    )
    assert "40th percentile" in str(dna.recruiter_context["band_vs_market"])
    assert "never a scoring input" in dna.recruiter_context["note"].lower()
    # It reached no weight, no threshold and no disqualifier.
    assert not dna.weight_modifiers
    assert not dna.disqualifiers


def test_the_prohibited_filter_list_must_be_explicitly_confirmed() -> None:
    """§16 S7 and §12.4. Absent from the pre-Runbook instrument entirely.

    Its value is procedural rather than legal: a client who has confirmed the
    list is having a conversation when they later ask for something on it, and
    a client who has not is hearing "no" for the first time at the worst moment.
    """
    assert not company_dna.compile_artifact({}).prohibited_filters_confirmed
    confirmed = company_dna.compile_artifact({"prohibited_filters_confirmed": "Confirmed"})
    assert confirmed.prohibited_filters_confirmed


def test_the_artifact_never_carries_the_raw_answers() -> None:
    """spec-doc5 §A.3: Sutra must read the compiled artifact and "never the
    client's free-text preferences directly"."""
    dna = company_dna.compile_artifact(
        {"what_the_company_does": "SECRET INTERNAL STRATEGY", "proven_vs_potential": 1}
    )
    import json

    assert "SECRET INTERNAL STRATEGY" not in json.dumps(dna.as_dict())


def test_sourcing_hints_state_that_they_are_a_ranking_prior() -> None:
    """A hint that could quietly become a filter is how a preference for
    "companies of a similar size" turns into a candidate never seeing a job."""
    dna = company_dna.compile_artifact(
        {"background_preference": "Companies of a similar size"}
    )
    assert "never decides who is assessed" in dna.sourcing_hints["note"].lower()


def test_dossier_preferences_change_emphasis_and_not_what_is_assessed() -> None:
    dna = company_dna.compile_artifact({"depth": "The short version"})
    assert "never what is assessed" in dna.dossier_preferences["note"].lower()


# ── Session progress ─────────────────────────────────────────────────────────


def test_the_session_walks_the_instrument_in_order() -> None:
    """The instrument builds context section by section, and a model free to
    reorder would ask about failure modes before it knew what the company
    does."""
    first = company_dna.next_unanswered({})
    assert first is not None
    assert first.key == company_dna.SECTIONS[0].questions[0].key


def test_the_session_closes_when_every_required_question_is_answered() -> None:
    answers = {key: "an answer that is long enough to count" for key in company_dna.required_keys()}
    assert company_dna.next_unanswered(answers) is None
    assert company_dna.completeness(answers)["complete"]


def test_an_optional_question_never_blocks_the_close() -> None:
    answers = {key: "x" for key in company_dna.required_keys()}
    report = company_dna.completeness(answers)
    assert report["complete"]
    assert "hard_disqualifiers" not in report["missing"]


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
    assert "company_dna.is_observable" in source
    assert "company_dna.rejection_message" in source


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
