"""Deterministic grounding: a claim of evidence counts only if it is IN the
resume, and a tag reaches a recruiter only if it is clean.

Mutation check recorded in the Phase 2 report: making `is_grounded` return
True fails the paraphrase, the short quote, the invented quote and the
ungrounded-claim tests below.
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from app.services.assessment_contract import ContractSkill
from app.services.yukti import config, grounding
from app.services.yukti.grounding import RawItem, RawJudgement, RawNeed, ResumeIndex

EM_DASH = chr(8212)

RESUME = "\n".join(
    [
        "Senior engineer, payments platform",
        "Owned the Kafka ingestion pipeline serving the fraud team in production",
        "Led a team of four data engineers across two releases a quarter",
        "Tuned slow PostgreSQL queries with EXPLAIN plans",
        "Wrote the \u201cchargeback\u201d reconciliation job \u2013 nightly, in Python",
    ]
)


def _skill(name: str, bucket: str = "must_have", priority: int = 1) -> ContractSkill:
    return ContractSkill(
        id=uuid.uuid4(), name=name, bucket=bucket, priority=priority, evidence_line=""
    )


def _index(text: str = RESUME) -> ResumeIndex:
    return ResumeIndex.of(text)


# ── is_grounded ──────────────────────────────────────────────────────────────


def test_a_verbatim_quote_grounds() -> None:
    assert grounding.is_grounded(
        "Owned the Kafka ingestion pipeline serving the fraud team", _index()
    )


def test_a_paraphrase_does_not_ground() -> None:
    assert not grounding.is_grounded("Ran Kafka pipelines for the fraud team", _index())


def test_an_invented_quote_does_not_ground() -> None:
    assert not grounding.is_grounded("Built a Kubernetes operator from scratch", _index())


def test_a_quote_under_the_minimum_words_does_not_ground() -> None:
    assert config.MIN_QUOTE_WORDS == 3
    assert not grounding.is_grounded("Kafka ingestion", _index())
    assert grounding.is_grounded("Kafka ingestion pipeline", _index())


def test_a_quote_over_the_character_cap_does_not_ground() -> None:
    long_resume = "word " * 200
    assert not grounding.is_grounded(("word " * 100).strip(), _index(long_resume))


def test_grounding_is_on_word_boundaries() -> None:
    # "ingestion pipeline serv" is a substring of the resume but not a quote of it.
    assert not grounding.is_grounded("Kafka ingestion pipeline serv", _index())


def test_smart_quotes_dashes_case_and_spacing_are_normalised() -> None:
    assert grounding.is_grounded(
        'wrote the "chargeback" reconciliation job - nightly,   in python', _index()
    )
    assert grounding.is_grounded(
        f"reconciliation job {EM_DASH} nightly", _index()
    )


def test_a_quote_spanning_a_line_break_grounds_as_the_model_saw_it() -> None:
    assert grounding.is_grounded("in production Led a team of four", _index())


# ── skill terms ──────────────────────────────────────────────────────────────


def test_skill_term_line_finds_the_line_that_names_the_skill() -> None:
    assert grounding.skill_term_line("Kafka", _index()).startswith("Owned the Kafka")
    assert grounding.skill_term_line("Kubernetes", _index()) is None


def test_skill_term_line_uses_the_ontology_equivalents() -> None:
    assert grounding.skill_term_line("Postgres", _index()) is not None


# ── the rules ────────────────────────────────────────────────────────────────


def _raw(skills: dict, *, experience=None, role_fit=None, needs=()) -> RawJudgement:
    return RawJudgement(
        skills=skills,
        experience=experience or RawItem("none"),
        role_fit=role_fit or RawItem("none"),
        needs=tuple(needs),
    )


def test_rule_one_a_grounded_claim_is_kept() -> None:
    kafka = _skill("Kafka stream processing")
    out = grounding.ground(
        [kafka],
        {},
        _raw({kafka.id: RawItem("strong", "Owned the Kafka ingestion pipeline")}),
        RESUME,
    )
    (entry,) = out.skills
    assert entry.verdict == "strong"
    assert entry.quote == "Owned the Kafka ingestion pipeline"
    assert not entry.grounded_by_term
    assert out.ungrounded == ()


def test_rule_two_an_ungrounded_claim_with_the_term_on_a_line_becomes_some() -> None:
    kafka = _skill("Kafka")
    out = grounding.ground(
        [kafka], {}, _raw({kafka.id: RawItem("strong", "Expert in Kafka streaming")}), RESUME
    )
    (entry,) = out.skills
    assert entry.verdict == "some"
    assert entry.grounded_by_term
    assert entry.quote.startswith("Owned the Kafka")
    assert {"kind": "skill", "ref": str(kafka.id)} in [dict(u) for u in out.ungrounded]


def test_rule_two_an_ungrounded_claim_without_the_term_becomes_none() -> None:
    k8s = _skill("Kubernetes")
    out = grounding.ground(
        [k8s], {}, _raw({k8s.id: RawItem("strong", "Ran Kubernetes clusters at scale")}), RESUME
    )
    (entry,) = out.skills
    assert entry.verdict == "none"
    assert entry.quote is None
    assert out.ungrounded
    assert out.negative_skills == (k8s,)


def test_rule_three_never_say_no_x_when_the_resume_says_x() -> None:
    kafka = _skill("Kafka")
    out = grounding.ground([kafka], {}, _raw({kafka.id: RawItem("none")}), RESUME)
    (entry,) = out.skills
    assert entry.verdict == "some"
    assert out.negative_skills == ()
    assert out.contradicted_negative == (str(kafka.id),)


def test_a_negative_tag_only_for_a_must_have() -> None:
    must = _skill("Kubernetes", "must_have")
    nice = _skill("Terraform", "nice_to_have")
    out = grounding.ground(
        [must, nice], {}, _raw({must.id: RawItem("none"), nice.id: RawItem("none")}), RESUME
    )
    assert out.negative_skills == (must,)


def test_a_negative_tag_is_suppressed_when_the_words_appear_across_lines() -> None:
    resume = "Worked on stream processors\nMoved topics onto Kafka"
    skill = _skill("Kafka stream processing")
    out = grounding.ground([skill], {}, _raw({skill.id: RawItem("none")}), resume)
    assert out.skills[0].verdict == "none"
    assert out.negative_skills == ()


def test_a_skill_the_reading_omits_is_none() -> None:
    skill = _skill("Kubernetes")
    out = grounding.ground([skill], {}, _raw({}), RESUME)
    assert out.skills[0].verdict == "none"


def test_rule_four_experience_needs_a_grounded_quote_and_a_clean_tag() -> None:
    good = grounding.ground(
        [],
        {},
        _raw({}, experience=RawItem("strong", "Led a team of four data engineers", "Led a data team")),
        RESUME,
    )
    assert good.experience.verdict == "strong"
    assert good.experience.tag == "Led a data team"

    ungrounded = grounding.ground(
        [], {}, _raw({}, experience=RawItem("strong", "Managed forty engineers", "Led a big team")), RESUME
    )
    assert ungrounded.experience is None
    assert {"kind": "experience", "ref": "experience"} in [dict(u) for u in ungrounded.ungrounded]

    dirty_tag = grounding.ground(
        [], {}, _raw({}, role_fit=RawItem("some", "Owned the Kafka ingestion pipeline", "Top candidate, ranked first")), RESUME
    )
    assert dirty_tag.role_fit is None
    assert {"kind": "role_fit", "ref": "role_fit"} in [dict(u) for u in dirty_tag.tags_refused]


def test_rule_four_none_needs_nothing_and_counts_as_judged() -> None:
    out = grounding.ground([], {}, _raw({}, experience=RawItem("none", "", "")), RESUME)
    assert out.experience is not None
    assert out.experience.verdict == "none"
    assert out.experience.tag is None


def test_rule_four_an_unlisted_need_is_dropped_and_recorded() -> None:
    listed = {"n1": SimpleNamespace(source="weakness")}
    out = grounding.ground(
        [],
        listed,
        _raw(
            {},
            needs=[
                RawNeed("n1", "strong", "Owned the Kafka ingestion pipeline", "Streaming in production"),
                RawNeed("n9", "strong", "Owned the Kafka ingestion pipeline", "Invented need"),
            ],
        ),
        RESUME,
    )
    assert [n.need_ref for n in out.needs] == ["n1"]
    assert out.needs[0].source == "weakness"
    assert out.unknown_needs == ("n9",)


def test_rule_four_an_ungrounded_need_is_excluded_not_negative() -> None:
    listed = {"n1": SimpleNamespace(source="threat")}
    out = grounding.ground(
        [], listed, _raw({}, needs=[RawNeed("n1", "some", "Beat three competitors", "Competitive")]), RESUME
    )
    assert out.needs == ()
    assert {"kind": "company_need", "ref": "n1"} in [dict(u) for u in out.ungrounded]


# ── clean_tag ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "tag",
    ["Led payments migration", "Hospital billing systems", "C++ and Rust", "CI/CD pipelines"],
)
def test_a_clean_tag_passes(tag: str) -> None:
    assert grounding.clean_tag(tag) == tag


@pytest.mark.parametrize(
    "tag",
    [
        "Led 4 engineers",                # a digit
        f"Kafka {EM_DASH} production",    # an em dash
        "Great culture fit",              # culture
        "Strong cultural alignment",      # culture
        "Young and energetic",            # prohibited attribute
        "Highly Matching candidate",      # a grade word
        "Top ranked engineer",            # score vocabulary
        "Scored well on SQL",             # score vocabulary
        "Kafka experience unverified",    # meta-commentary
        "one two three four five six",    # six words
        "A" * 41,                         # over the character cap
        "",                               # blank
        "   ",                            # blank after tidy
        "Kafka \U0001F680",               # an emoji
    ],
)
def test_a_dirty_tag_is_refused(tag: str) -> None:
    assert grounding.clean_tag(tag) is None


def test_the_digit_rule_is_about_digits_not_about_duration() -> None:
    """Spelled-out duration is the honest content of an experience tag."""
    assert grounding.clean_tag("Nine years of Kafka") == "Nine years of Kafka"


def test_a_non_string_tag_is_refused() -> None:
    assert grounding.clean_tag(None) is None
    assert grounding.clean_tag(["Kafka"]) is None
