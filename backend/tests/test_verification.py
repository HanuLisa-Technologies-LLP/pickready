"""The domain critics: what they refuse, and the two spec checks they replace.

A verifier is only worth having if it fails the outputs a human reviewer would
fail and passes the ones they would pass. Both directions are asserted here,
because a critic that rejects everything is indistinguishable from a broken
generator, and a critic that accepts everything is indistinguishable from no
critic at all -- and only one of those two failure modes is visible in
production.
"""
from __future__ import annotations

import ast

import pytest

from app.services import agent_loop, ppi, rating
from app.services.verification import (
    base,
    contradiction,
    email,
    generic_language,
    ppi_report,
    probes,
    ranking,
)


def _code_without_prose(module) -> str:
    """A module's executable source with every docstring removed.

    Needed because these modules deliberately QUOTE the rule they no longer
    implement -- the retired five-label scale, the deleted weight table -- so
    that a reader finds out why it is absent rather than re-adding it. A naive
    grep cannot tell an explanation from an implementation; the parser can.
    """
    with open(module.__file__, encoding="utf-8") as handle:
        tree = ast.parse(handle.read())
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


def _comment(words: int = 27, seed: str = "kafka partition rebalance") -> str:
    """A comment of an exact length, specific to its seed and free of filler.

    The padding is derived FROM the seed rather than being a shared tail, so
    two different seeds produce two genuinely different remarks. A shared tail
    would make every fixture near-identical and quietly defeat the one check
    that looks at a ranked list as a whole.
    """
    words_in_seed = seed.split()
    out = list(words_in_seed)
    index = 0
    while len(out) < words:
        out.append(words_in_seed[index % len(words_in_seed)])
        out.append(("during" if index % 2 else "throughout") + "")
        index += 1
    return " ".join(out[:words])


def _remark(words: int = 47) -> str:
    return _comment(words, seed="rebuilt the ingestion path")


# ── base ─────────────────────────────────────────────────────────────────────


def test_confidence_is_arithmetic_a_reviewer_can_reconstruct() -> None:
    """No model is asked. One high finding is disqualifying on its own."""
    clean = base.verdict("v", [])
    assert clean.confidence == 1.0 and clean.passed

    one_high = base.verdict("v", [base.high("i", "l", "d", "r")])
    assert one_high.confidence == 0.0 and not one_high.passed

    one_low = base.verdict("v", [base.low("i", "l", "d", "r")])
    assert one_low.passed, "a low-severity note must not cost a regeneration"


def test_three_mediums_fail_where_one_does_not() -> None:
    """An output individually borderline on three checks is not a passing one."""
    assert base.verdict("v", [base.medium("i", "l", "d", "r")]).passed
    assert not base.verdict("v", [base.medium("i", "l", "d", "r")] * 3).passed


def test_a_verdict_hands_itself_to_the_loop_as_a_critique() -> None:
    """This is the whole integration: no second retry framework."""
    critique = base.verdict("v", [base.high("bad_thing", "here", "d", "fix it")]).to_critique()
    assert isinstance(critique, agent_loop.Critique)
    assert not critique.ok
    assert "fix it" in " ".join(critique.reasons)

    assert base.verdict("v", []).to_critique().ok


def test_combining_verdicts_accumulates_cost_rather_than_resetting_it() -> None:
    merged = base.combine(
        "all",
        [
            base.verdict("a", [base.medium("i", "l", "d", "r")]),
            base.verdict("b", [base.medium("i", "l", "d", "r")]),
            base.verdict("c", [base.medium("i", "l", "d", "r")]),
        ],
    )
    assert not merged.passed


# ── generic language ─────────────────────────────────────────────────────────


def test_filler_is_caught_and_real_content_is_not() -> None:
    """Calibration matters more than coverage.

    A detector that fires on genuine content teaches its callers to ignore it,
    which is worse than not having one.
    """
    assert generic_language.findings("A proven track record of delivery.", location="x")
    assert not generic_language.findings(
        "Led the shard rebalance after the Kafka consumer group stalled.",
        location="x",
    )


def test_cosmetic_rewording_does_not_evade_the_detector() -> None:
    assert generic_language.findings("They are a Strong  Communicator!", location="x")


def test_the_rate_metric_counts_outputs_not_occurrences() -> None:
    """Two filler phrases in one remark is one bad remark."""
    texts = [
        "A team player and a quick learner.",
        "Rebuilt the ingestion path after the regional failover.",
    ]
    assert generic_language.rate(texts) == 0.5


# ── ranking ──────────────────────────────────────────────────────────────────


def _entry(comment: str | None = None) -> dict:
    comment = comment or _comment()
    return {
        "skills_match": {"score": 8, "comment": comment},
        "experience_relevance": {"score": 7, "comment": comment},
        "role_alignment": {"score": 6, "comment": comment},
        "education_fit": {"score": 5, "comment": comment},
        "overall_comment": comment,
    }


def test_a_well_formed_entry_passes() -> None:
    assert ranking.verify_entry(_entry()).passed


def test_a_missing_parameter_is_disqualifying() -> None:
    entry = _entry()
    del entry["role_alignment"]
    verdict = ranking.verify_entry(entry)
    assert not verdict.passed
    assert any(f.issue == "missing_parameter" for f in verdict.findings)


def test_a_score_outside_one_to_ten_is_disqualifying() -> None:
    entry = _entry()
    entry["skills_match"]["score"] = 87
    assert not ranking.verify_entry(entry).passed


def test_a_comment_that_states_a_score_is_disqualifying() -> None:
    """The oldest standing rule in the product: no number reaches a client."""
    entry = _entry(_comment(26) + " scoring 87%")
    verdict = ranking.verify_entry(entry)
    assert not verdict.passed
    assert any(f.issue == "number_leaked" for f in verdict.findings)


def test_a_ranked_list_of_paraphrases_is_flagged() -> None:
    """What the deleted weight-sum check was actually standing in for.

    Five individually well-formed remarks that all say the same thing have told
    the recruiter nothing about which of the five to interview first.
    """
    identical = [_entry() for _ in range(5)]
    verdict = ranking.verify_ranked_list(identical)
    assert any(f.issue == "ranking_lacks_diversity" for f in verdict.findings)


def test_a_genuinely_discriminating_list_is_not_flagged() -> None:
    seeds = [
        "rebuilt the payments ledger",
        "owned the Android release train",
        "migrated the warehouse to Iceberg",
        "ran the on call rotation redesign",
        "led the fraud model rollout",
    ]
    entries = [_entry(_comment(27, seed=seed)) for seed in seeds]
    verdict = ranking.verify_ranked_list(entries)
    assert not any(f.issue == "ranking_lacks_diversity" for f in verdict.findings)


def test_the_ranking_critic_has_no_weight_check() -> None:
    """`WEIGHTS` was deleted on 2026-07-30 and `test_scoring` asserts its absence.

    Adding the specification's "weights sum to 1.0" check back would require
    adding the concept back, and the concept is what put "35% role-fit
    weighting" in front of a client.
    """
    from app.services import matching

    assert not hasattr(matching, "WEIGHTS")
    assert "WEIGHTS" not in _code_without_prose(ranking)


# ── PPI report ───────────────────────────────────────────────────────────────


def _dimension(category: str, name: str, score: int, remark: str | None = None) -> dict:
    return {
        "category": category,
        "name": name,
        "score": score,
        "remark": remark or _remark(),
        "ordinal": 0,
    }


def _full_report(must_have_score: int = 85) -> list[dict]:
    return [
        _dimension(ppi.CATEGORY_MUST_HAVE, "Distributed systems", must_have_score),
        _dimension(ppi.CATEGORY_NICE_TO_HAVE, "Terraform", 80),
        _dimension(ppi.CATEGORY_BEHAVIOURAL, "Handling ambiguity", 78),
    ]


def test_a_complete_report_passes() -> None:
    verdict = ppi_report.verify_report(
        _full_report(), overall_remark=_remark(), overall_grade=rating.GRADE_MATCHING
    )
    assert verdict.passed, [f.as_dict() for f in verdict.findings]


def test_a_missing_aspect_is_disqualifying() -> None:
    """Not a shorter report: a report that did not assess something."""
    rows = [row for row in _full_report() if row["category"] != ppi.CATEGORY_BEHAVIOURAL]
    verdict = ppi_report.verify_report(rows)
    assert not verdict.passed
    assert any(f.issue == "missing_category" for f in verdict.findings)


def test_culture_is_refused_as_a_competency_here_too() -> None:
    """The fourth layer. A report can be written against a framework row that
    predates the Postgres CHECK."""
    rows = _full_report()
    rows[2]["name"] = "Culture fit"
    verdict = ppi_report.verify_report(rows)
    assert any(f.issue == "forbidden_competency" for f in verdict.findings)


def test_a_not_matching_must_have_caps_the_overall_grade() -> None:
    verdict = ppi_report.verify_report(
        _full_report(must_have_score=30),
        overall_grade=rating.GRADE_MATCHING,
    )
    assert not verdict.passed
    assert any(f.issue == "must_have_cap_violated" for f in verdict.findings)


def test_the_cap_is_satisfied_when_overall_is_already_capped() -> None:
    verdict = ppi_report.verify_report(
        _full_report(must_have_score=30),
        overall_grade=rating.GRADE_MODERATELY,
    )
    assert not any(f.issue == "must_have_cap_violated" for f in verdict.findings)


def test_a_remark_outside_forty_five_to_fifty_words_is_flagged() -> None:
    rows = _full_report()
    rows[0]["remark"] = _remark(12)
    verdict = ppi_report.verify_report(rows)
    assert any(f.issue == "remark_word_count" for f in verdict.findings)


def test_filler_in_a_ppi_remark_is_high_severity() -> None:
    """A 48-word remark built from filler passes every mechanical check and
    tells a hiring manager nothing, which is what this package exists for."""
    rows = _full_report()
    rows[0]["remark"] = (
        "The candidate is a strong communicator and a proven team player who "
        + _remark(38)
    )
    verdict = ppi_report.verify_report(rows)
    assert not verdict.passed
    assert any(
        f.issue == "generic_language" and f.severity == base.SEVERITY_HIGH
        for f in verdict.findings
    )


def test_the_report_critic_checks_the_four_grades_not_the_retired_five() -> None:
    """The five labels were collapsed on 2026-07-30. A critic enforcing them
    would reject every report written since."""
    code = _code_without_prose(ppi_report)
    assert "Developing" not in code
    assert "rating.GRADE_" in code


# ── email ────────────────────────────────────────────────────────────────────


def _email_context() -> dict:
    return {"candidate_name": "Priya Raman", "job_title": "Staff Engineer"}


def _body() -> str:
    return (
        "Hello Priya, thank you for the time you gave the Staff Engineer "
        "assessment. We would like to move ahead and will write again with "
        "the next step."
    )


def test_a_sound_draft_passes() -> None:
    verdict = email.verify_draft(
        email_type="email_shortlist",
        subject="Staff Engineer: next step",
        body=_body(),
        context=_email_context(),
    )
    assert verdict.passed, [f.as_dict() for f in verdict.findings]


def test_an_impossible_transition_is_disqualifying() -> None:
    """The email would state something about the application that is not true."""
    verdict = email.verify_draft(
        email_type="email_offer_extended",
        subject="Offer",
        body=_body(),
        context=_email_context(),
        current_status="applied",
        target_status="offer_extended",
    )
    assert not verdict.passed
    assert any(f.issue == "invalid_transition" for f in verdict.findings)


def test_a_legal_transition_passes() -> None:
    verdict = email.verify_draft(
        email_type="email_shortlist",
        subject="Next step",
        body=_body(),
        context=_email_context(),
        current_status="assessment_completed",
        target_status="shortlisted",
    )
    assert not any(f.issue == "invalid_transition" for f in verdict.findings)


def test_an_em_dash_is_refused_in_a_user_facing_string() -> None:
    verdict = email.verify_draft(
        email_type="email_shortlist",
        subject="Next step",
        body=_body() + " We will be in touch" + chr(8212) + " soon.",
        context=_email_context(),
    )
    assert not verdict.passed
    assert any(f.issue == "em_dash" for f in verdict.findings)


def test_the_em_dash_detector_is_built_from_the_code_point() -> None:
    """A repo-wide dash sweep must not be able to rewrite the detector."""
    with open(email.__file__, encoding="utf-8") as handle:
        source = handle.read()
    assert "chr(8212)" in source
    assert chr(8212) not in source


def test_an_unfilled_placeholder_is_disqualifying() -> None:
    verdict = email.verify_draft(
        email_type="email_shortlist",
        subject="Next step",
        body="Hello {{candidate_name}}, thank you for your time on the Staff Engineer role.",
        context=_email_context(),
    )
    assert not verdict.passed
    assert any(f.issue == "unfilled_placeholder" for f in verdict.findings)


def test_a_score_in_an_email_is_disqualifying() -> None:
    verdict = email.verify_draft(
        email_type="email_shortlist",
        subject="Next step",
        body=_body() + " You scored 82% on the assessment.",
        context=_email_context(),
    )
    assert any(f.issue == "number_leaked" for f in verdict.findings)


def test_a_missing_first_name_is_noted_but_does_not_fail_the_draft() -> None:
    """A perfectly good email can open 'Hello,'. Spending a regeneration on
    that is worse than recording it."""
    verdict = email.verify_draft(
        email_type="email_shortlist",
        subject="Next step",
        body="Hello, thank you for the time you gave the Staff Engineer assessment.",
        context=_email_context(),
    )
    assert verdict.passed
    assert any(f.issue == "missing_recipient_name" for f in verdict.findings)


# ── probes ───────────────────────────────────────────────────────────────────

_ANSWER = (
    "We ran Kafka with three brokers and I handled the consumer group "
    "rebalance when the partition count changed during the migration."
)


def _probe(words: int = 27) -> str:
    text = (
        "When the Kafka partition count changed mid migration, what did the "
        "consumer group rebalance cost you, and which part of that would you "
        "redesign now given"
    )
    parts = text.split()
    while len(parts) < words - 1:
        parts.append("again")
    return " ".join(parts[: words - 1]) + " today?"


def test_a_grounded_probe_passes() -> None:
    verdict = probes.verify_probe(_probe(), location="p", answer=_ANSWER)
    assert verdict.passed, [f.as_dict() for f in verdict.findings]


def test_an_ungrounded_probe_is_disqualifying() -> None:
    """It could have been written without reading a word the candidate wrote."""
    verdict = probes.verify_probe(
        "Tell me about your general experience working within a wider "
        "engineering organisation and how you approached that overall please?",
        location="p",
        answer=_ANSWER,
    )
    assert not verdict.passed
    assert any(f.issue == "probe_not_grounded" for f in verdict.findings)


def test_a_probe_that_is_not_a_question_is_flagged() -> None:
    verdict = probes.verify_probe(
        _probe().rstrip("?") + ".", location="p", answer=_ANSWER
    )
    assert any(f.issue == "probe_not_a_question" for f in verdict.findings)


def test_a_probe_repeating_an_asked_question_is_flagged() -> None:
    asked = "What did the consumer group rebalance cost you?"
    verdict = probes.verify_probe(
        _probe(), location="p", answer=_ANSWER, asked_questions=[asked]
    )
    assert any(f.issue == "probe_repeats_question" for f in verdict.findings)


def test_a_not_matching_must_have_earns_two_probes() -> None:
    """One probe is not enough interview time for the item that caps Overall."""
    item = {"name": "Distributed systems", "score": 30}
    one = probes.verify_group(
        category=ppi.CATEGORY_MUST_HAVE,
        item=item,
        probes=[_probe()],
        answer=_ANSWER,
    )
    assert any(f.issue == "probe_count" for f in one.findings)

    two = probes.verify_group(
        category=ppi.CATEGORY_MUST_HAVE,
        item=item,
        probes=[_probe(), _probe(28)],
        answer=_ANSWER,
    )
    assert not any(f.issue == "probe_count" for f in two.findings)


def test_probing_a_criterion_the_candidate_cleared_is_disqualifying() -> None:
    verdict = probes.verify_group(
        category=ppi.CATEGORY_MUST_HAVE,
        item={"name": "Distributed systems", "score": 95},
        probes=[_probe()],
        answer=_ANSWER,
    )
    assert not verdict.passed
    assert any(f.issue == "probe_on_a_non_gap" for f in verdict.findings)


def test_gaps_are_ordered_worst_first() -> None:
    ordered = [{"score": 30}, {"score": 65}]
    assert probes.verify_ordering(ordered).passed
    reversed_order = [{"score": 65}, {"score": 30}]
    assert any(
        f.issue == "gap_order" for f in probes.verify_ordering(reversed_order).findings
    )


# ── contradiction ────────────────────────────────────────────────────────────


def test_a_wide_experience_gap_is_surfaced_and_a_narrow_one_is_not() -> None:
    wide = contradiction.verify_consistency(
        resume_experience_years=9, validation_experience_years=4
    )
    assert any(f.issue == "experience_conflict" for f in wide.findings)

    narrow = contradiction.verify_consistency(
        resume_experience_years=8, validation_experience_years=7
    )
    assert not narrow.findings, "one year is rounding, not a contradiction"


def test_a_skill_claimed_on_a_resume_and_abandoned_when_asked_is_surfaced() -> None:
    """The most useful contradiction in the set, and the one a report reading
    only the resume can never find."""
    verdict = contradiction.verify_consistency(
        resume_skills=["Kafka", "Terraform"], unanswered_skills=["Kafka"]
    )
    assert any(f.issue == "claimed_but_unevidenced" for f in verdict.findings)


def test_a_skill_described_beyond_the_resume_is_not_held_against_the_candidate() -> None:
    verdict = contradiction.verify_consistency(
        resume_skills=["Kafka"], claimed_skills=["Terraform"]
    )
    finding = next(f for f in verdict.findings if f.issue == "claimed_beyond_resume")
    assert finding.severity == base.SEVERITY_LOW
    assert verdict.passed


def test_no_critic_resolves_a_contradiction_it_finds() -> None:
    """Detected, surfaced, never resolved. Which source is right is a different
    conversation for the recruiter each time."""
    verdict = contradiction.verify_consistency(
        resume_experience_years=9, validation_experience_years=4
    )
    recommendation = verdict.findings[0].recommendation
    assert "raise" in recommendation.lower()
    assert "do not state either as fact" in recommendation.lower()


@pytest.mark.parametrize(
    "module",
    [ranking, ppi_report, email, probes, contradiction, generic_language],
)
def test_no_critic_calls_a_model(module) -> None:
    """The guard matters most when the provider is down.

    An LLM judge turns one flaky dependency into two and makes the criteria
    unfalsifiable: you can no longer write a test that says "this output is
    rejected", only one that says "the judge usually rejects it".
    """
    with open(module.__file__, encoding="utf-8") as handle:
        source = handle.read()
    assert "invoke_llm" not in source
    assert "llm_router" not in source
