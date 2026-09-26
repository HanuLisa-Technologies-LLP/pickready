"""The deterministic sufficiency gate, per generator and per field.

WHAT THESE TESTS ARE FOR
------------------------
`ai-upgrade-spec-doc.md` "case 2" Step 5. For every generator on the inventory in
`services/generation_sufficiency`, three fixtures:

  * insufficient or ambiguous input -> the FIXED empty-state key, never
    generated text;
  * genuinely sufficient input -> generation runs, and what comes back carries
    no banned phrase;
  * a MISMATCHED entity ("Foo IT" data against a "Foo Corp" target) -> none of
    that content reaches the output.

The banned-phrase sweep over the prompt files and the empty-state catalogue is
`tests/test_no_meta_commentary.py`, deliberately in its own module: one asks
whether a gate routed correctly, the other asks whether any string anywhere
carries the failure mode, and the second must sweep a SET rather than a call
site.

NOTHING HERE REACHES A PROVIDER. Every generator is exercised with its model
call replaced, because a test that needed a key would be skipped in CI and a
skipped assertion is the failure this repository keeps having to repair.
"""
from __future__ import annotations

import json

import pytest

from app.models.email_log import (
    EMAIL_TYPE_ASSESSMENT_INVITATION,
    EMAIL_TYPE_REJECTED,
    EMAIL_TYPE_SHORTLIST,
)
from app.services import (
    company_research,
    gap_analysis,
    generation_sufficiency as gs,
    jd_generation,
    lifecycle_email,
    outreach_content,
)


# ── The catalogue and the verdict type ───────────────────────────────────────


def test_an_insufficient_verdict_must_name_a_key_that_exists() -> None:
    """A key with no copy is a caller about to invent a sentence."""
    with pytest.raises(ValueError):
        gs.Sufficiency(False, "company_profile.no_such_key", "x")


def test_a_sufficient_verdict_carries_no_key() -> None:
    with pytest.raises(ValueError):
        gs.Sufficiency(True, "company_profile.about_company.no_sources", "x")


def test_unknown_empty_state_copy_raises_rather_than_defaulting() -> None:
    with pytest.raises(gs.UnknownEmptyState):
        gs.empty_state_copy("nothing.like.this")


def test_every_banned_phrase_is_long_enough_for_the_shared_gate() -> None:
    """`agent_loop.banned_phrase_gate` has a three-word floor for partial
    matches. A shorter entry adds nothing and reads as though it did."""
    for phrase in gs.META_COMMENTARY_PHRASES:
        assert len(phrase.split()) >= 3, phrase


def test_a_legal_form_is_never_also_a_descriptor() -> None:
    """The two sets answer opposite questions about the same token, and a token
    in both would make the entity guard's behaviour depend on iteration
    order."""
    assert not (gs._LEGAL_FORMS & gs._ENTITY_DESCRIPTORS)


# ── Step 4, the entity and attribution guard ─────────────────────────────────


@pytest.mark.parametrize(
    "candidate",
    ["Foo IT", "Foo Group", "Foo Enterprises", "Foo Technologies", "Foo Solutions"],
)
def test_a_descriptor_makes_it_a_different_company(candidate: str) -> None:
    """The defect this guard exists for, stated one name at a time."""
    assert not gs.entity_matches("Foo Corp", candidate)


@pytest.mark.parametrize(
    "candidate",
    [
        "Foo Corp",
        "Foo Corporation",
        "Foo Corp Pvt Ltd",
        "Foo Corp Reviews",
        "Working at Foo Corp",
        "Fooo Corp",
    ],
)
def test_a_legal_form_or_a_surrounding_word_still_matches(candidate: str) -> None:
    assert gs.entity_matches("Foo Corp", candidate)


def test_the_host_guard_separates_two_names_one_edit_apart() -> None:
    """A character ratio alone cannot do this, which is why the descriptor rule
    is applied to the joined host label as well."""
    assert gs.host_matches("Halden Corp", "https://halden.com/about")
    assert gs.host_matches("Halden Corp", "https://haldencorp.com/about")
    assert not gs.host_matches("Halden Corp", "https://haldenit.com/about")


def test_a_mismatched_entity_page_never_becomes_evidence() -> None:
    hits = [
        {"url": "https://en.wikipedia.org/wiki/a", "title": "Halden Corp - Wikipedia",
         "content": "valve manufacturer"},
        {"url": "https://haldenit.com/about", "title": "Halden IT Solutions",
         "content": "an unrelated software consultancy"},
        {"url": "https://glassdoor.com/x", "title": "Halden Group Reviews",
         "content": "a different company"},
    ]
    kept = gs.attributable_sources("Halden Corp", hits)
    assert [hit["url"] for hit in kept] == ["https://en.wikipedia.org/wiki/a"]


# ── Company profile ──────────────────────────────────────────────────────────


def _company_hits() -> list[dict[str, str]]:
    return [
        {
            "url": "https://en.wikipedia.org/wiki/Halden_Corp",
            "title": "Halden Corp - Wikipedia",
            "content": (
                "Halden Corp manufactures industrial valves. Employees describe "
                "shift work at the plant, flexible hours in the office, and "
                "benefits including medical insurance and paid leave."
            ),
        },
        {
            "url": "https://haldencorp.com/careers",
            "title": "Careers",
            "content": "Life at the plant, hybrid office weeks, learning budget.",
        },
    ]


def test_company_sections_are_judged_one_at_a_time() -> None:
    """PER SECTION. One page that names the company and never mentions leave or
    insurance is not evidence about its benefits."""
    hits = [
        {
            "url": "https://en.wikipedia.org/wiki/Halden_Corp",
            "title": "Halden Corp - Wikipedia",
            "content": "Halden Corp manufactures industrial valves.",
        },
        {
            "url": "https://haldencorp.com/",
            "title": "Halden Corp",
            "content": "Industrial valves for process plants.",
        },
    ]
    states = gs.company_profile_states("Halden Corp", hits)
    assert states["about_company"].sufficient
    assert not states["work_life"].sufficient
    assert not states["benefits"].sufficient
    assert states["benefits"].empty_state_key == "company_profile.benefits.no_sources"


def test_one_attributable_page_is_not_enough_for_the_about_section() -> None:
    hits = _company_hits()[:1]
    states = gs.company_profile_states("Halden Corp", hits)
    assert not states["about_company"].sufficient



async def test_company_research_returns_the_key_and_never_calls_a_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INSUFFICIENT INPUT -> the fixed key. And the model is not reached at all,
    which is the architectural half of the fix: a prompt that is never sent
    cannot produce a paragraph about its own sources."""
    called: list[str] = []

    async def _gather(*_args, **_kwargs):
        return [
            {
                "url": "https://haldenit.com/about",
                "title": "Halden IT Solutions",
                "content": "an unrelated software consultancy",
            }
        ]

    async def _never(*_args, **_kwargs):
        called.append("model")
        raise AssertionError("the model must not be called")

    monkeypatch.setattr(company_research, "_gather", _gather)
    monkeypatch.setattr(company_research.llm_router, "chat_completion", _never)

    draft = await company_research.research_company(None, company="Halden Corp")

    assert called == []
    assert draft.degraded is True
    assert draft.is_empty()
    assert draft.empty_state_keys["about_company"] == (
        "company_profile.about_company.no_sources"
    )
    assert draft.message == gs.empty_state_copy(
        "company_profile.about_company.no_sources"
    )



async def test_company_research_writes_only_the_sections_the_gate_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SUFFICIENT INPUT for one section only, so only that one is written."""

    async def _gather(*_args, **_kwargs):
        return [
            {
                "url": "https://en.wikipedia.org/wiki/Halden_Corp",
                "title": "Halden Corp - Wikipedia",
                "content": "Halden Corp manufactures industrial valves.",
            },
            {
                "url": "https://haldencorp.com/",
                "title": "Halden Corp",
                "content": "Industrial valves for process plants.",
            },
        ]

    body = " ".join(["Halden Corp manufactures industrial valves for process plants."] * 22)

    async def _reply(*_args, **_kwargs):
        return json.dumps(
            {"about_company": body, "sources": ["https://haldencorp.com/"]}
        )

    monkeypatch.setattr(company_research, "_gather", _gather)
    monkeypatch.setattr(company_research.llm_router, "chat_completion", _reply)

    draft = await company_research.research_company(None, company="Halden Corp")

    assert draft.about_company
    assert not gs.meta_commentary_defects(draft.about_company)
    assert draft.benefits == ""
    assert draft.work_life == ""
    assert draft.empty_state_keys["benefits"] == "company_profile.benefits.no_sources"


def test_a_section_the_gate_refused_is_rejected_even_if_the_model_writes_it() -> None:
    """The evaluator's half of the same rule, as a unit.

    A model volunteering a section the caller decided had nothing behind it IS
    the defect in its original form, so it is a rejection with a reason the next
    attempt is told, not a value quietly dropped later.
    """
    evaluate = company_research._evaluate_for(("about_company",))
    critique = evaluate(
        {
            "about_company": " ".join(["Halden Corp makes industrial valves."] * 30),
            "benefits": "Halden Corp offers a generous package.",
        }
    )
    assert not critique.ok
    assert any(defect.type == "unrequested_section" for defect in critique.defects)


def test_the_evaluator_refuses_a_section_that_describes_its_own_sources() -> None:
    evaluate = company_research._evaluate_for(("about_company",))
    critique = evaluate(
        {
            "about_company": " ".join(
                ["The retrieved material does not establish what this company does."]
                * 20
            )
        }
    )
    assert not critique.ok
    assert any(defect.type == "banned_phrase" for defect in critique.defects)


# ── Job description ──────────────────────────────────────────────────────────


def test_a_jd_section_with_no_backing_field_is_never_requested() -> None:
    """Education has never had a field behind it on this brief, so it was
    written from nothing every time."""
    states = gs.jd_document_states({"title": "Warehouse Supervisor", "skills": ["WMS"]})
    assert states["description"].sufficient
    assert states["skills"].sufficient
    assert not states["education"].sufficient
    assert not states["experience"].sufficient



async def test_an_unbacked_jd_section_gets_the_catalogue_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INSUFFICIENT INPUT -> fixed copy, whatever the model wrote there."""

    invented = "\n\n".join(
        [
            "## Description\n\nA real description of the role.",
            "## Role\n\nA real role paragraph.",
            "## Responsibilities\n\n- Run the shift.",
            "## Accountabilities\n\n- Own despatch accuracy.",
            "## Education\n\nThe brief does not establish any requirement here.",
            "## Skills\n\n- WMS",
            "## Experience\n\nThis role suits someone with 3 to 6 years.",
        ]
    )

    async def _reply(*_args, **_kwargs):
        return invented

    monkeypatch.setattr(jd_generation.llm_router, "chat_completion", _reply)

    result = await jd_generation.generate_jd_document(
        {
            "title": "Warehouse Supervisor",
            "skills": ["WMS"],
            "experience_min_years": 3,
            "experience_max_years": 6,
        }
    )
    document = result["jd_markdown"]
    assert "## Education" in document
    assert "does not establish" not in document
    assert gs.empty_state_copy("job_description.education.no_brief_detail") in document
    # The sections the brief DOES back are untouched, including the band, which
    # a re-render would have collapsed to a single figure.
    assert "3 to 6 years" in document
    assert result["jd"]["skills"] == ["WMS"]



async def test_a_sufficient_brief_produces_no_banned_phrase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _reply(*_args, **_kwargs):
        return "\n\n".join(
            [
                "## Description\n\nMeridian Foods runs three packing lines.",
                "## Role\n\nYou will own one shift end to end.",
                "## Responsibilities\n\n- Plan the shift against despatch.",
                "## Accountabilities\n\n- Despatch accuracy for the shift.",
                "## Education\n\nA degree in a related field.",
                "## Skills\n\n- WMS\n- Safety compliance",
                "## Experience\n\nThis role suits someone with 3 to 6 years.",
            ]
        )

    monkeypatch.setattr(jd_generation.llm_router, "chat_completion", _reply)
    result = await jd_generation.generate_jd_document(
        {
            "title": "Warehouse Supervisor",
            "skills": ["WMS"],
            "education": "A degree in a related field.",
            "experience_min_years": 3,
            "experience_max_years": 6,
        }
    )
    assert not gs.meta_commentary_defects(result["jd_markdown"])


# ── Gap analysis ─────────────────────────────────────────────────────────────


def test_a_gap_with_no_recorded_answer_is_refused() -> None:
    assert not gs.gap_probe_state([]).sufficient
    assert not gs.gap_probe_state([{"question": "q", "answer": "  "}]).sufficient
    assert gs.gap_probe_state([{"question": "q", "answer": "we failed over"}]).sufficient



async def test_gap_probes_skip_the_model_when_nothing_was_answered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """INSUFFICIENT INPUT -> the fixed key and the deterministic probes. A model
    asked to probe an unanswered item writes about the assessment."""

    async def _never(*_args, **_kwargs):
        raise AssertionError("the model must not be called")

    monkeypatch.setattr(gap_analysis.llm_router, "chat_completion", _never)

    probes, source, key = await gap_analysis._write_probes(
        None,
        {"name": "Incident response", "remark": "r"},
        "must_have",
        "Not Matching",
        [],
        1,
        None,
    )
    assert key == "gap_analysis.probes.no_recorded_answer"
    assert source == gap_analysis.PROBES_EMPTY_STATE
    assert probes and all(probes)
    assert not gs.meta_commentary_defects(" ".join(probes))



async def test_a_probe_that_describes_the_evidence_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SUFFICIENT INPUT -> generation runs, and the deterministic gate refuses a
    body that narrates the record. The loop degrades to the grounded fallback
    rather than shipping it."""

    async def _reply(*_args, **_kwargs):
        return json.dumps(
            {
                "probes": [
                    "The candidate's answer does not establish whether they led "
                    "the recovery and this remains unverified."
                ]
            }
        )

    monkeypatch.setattr(gap_analysis.llm_router, "chat_completion", _reply)

    probes, source, key = await gap_analysis._write_probes(
        None,
        {"name": "Incident response", "remark": "r"},
        "must_have",
        "Not Matching",
        [{"question": "Tell me about an incident.", "answer": "We failed over."}],
        1,
        None,
    )
    assert key is None
    # The refused body never ships, and what ships instead says it is the
    # fallback rather than reading as a model's probe.
    assert source == gap_analysis.PROBES_TEMPLATE
    assert not gs.meta_commentary_defects(" ".join(probes))


# ── Outreach ─────────────────────────────────────────────────────────────────


def test_an_absent_ranking_comment_leaves_no_trace_in_the_prompt() -> None:
    """The sentence "No specific evidence was recorded for this category." used
    to be rendered here, four times, under the heading "Strengths"."""
    block = outreach_content._evidence_block({"skills_comment": "Django and Postgres"})
    assert block == "- Skills: Django and Postgres"
    assert not gs.meta_commentary_defects(block)
    assert outreach_content._evidence_block({}) == ""



async def test_outreach_with_no_evidence_never_reaches_the_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _never(*_args, **_kwargs):
        raise AssertionError("the model must not be called")

    monkeypatch.setattr(outreach_content.llm_router, "chat_completion", _never)

    email = await outreach_content.generate_outreach_email(
        {"name": "Priya"}, {"title": "SRE"}, {"name": "Meridian Foods"}
    )
    assert not gs.meta_commentary_defects(email["text"])
    assert "No specific evidence" not in email["text"]



async def test_outreach_discards_a_second_hedging_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retry budget is spent and the draft still describes the record. A
    mis-sized body is repairable by trimming; this is not."""

    async def _reply(*_args, **_kwargs):
        return json.dumps(
            {
                "subject": "Next steps",
                "body": " ".join(
                    ["Based on the sources available your profile appears strong."]
                    * 20
                ),
            }
        )

    monkeypatch.setattr(outreach_content.llm_router, "chat_completion", _reply)

    email = await outreach_content.generate_outreach_email(
        {"name": "Priya", "skills_comment": "Django and Postgres"},
        {"title": "SRE"},
        {"name": "Meridian Foods"},
    )
    assert not gs.meta_commentary_defects(email["text"])


# ── Lifecycle email ──────────────────────────────────────────────────────────


def test_the_generic_strengths_default_is_treated_as_no_evidence() -> None:
    """A default that exists so a prompt renders is not evidence, and the prompt
    it renders says "name TWO specific strengths drawn from the evidence"."""
    state = gs.lifecycle_email_state(
        EMAIL_TYPE_SHORTLIST,
        {"strengths": gs.GENERIC_STRENGTHS_PLACEHOLDER},
    )
    assert not state.sufficient
    assert state.empty_state_key == "lifecycle_email.no_recorded_strengths"
    assert gs.lifecycle_email_state(
        EMAIL_TYPE_SHORTLIST, {"strengths": "- Led the queue migration"}
    ).sufficient


def test_an_email_making_no_evidence_backed_claim_is_not_gated() -> None:
    """A rejection is true from the transition alone. Gating it would send the
    template to every candidate for no reason."""
    assert gs.lifecycle_email_state(EMAIL_TYPE_REJECTED, {}).sufficient



async def test_a_shortlist_without_strengths_sends_the_fixed_template(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _never(*_args, **_kwargs):
        raise AssertionError("the model must not be called")

    monkeypatch.setattr(lifecycle_email.llm_router, "invoke_llm", _never)

    draft = await lifecycle_email.draft(
        EMAIL_TYPE_SHORTLIST,
        {"candidate_name": "Priya", "job_title": "SRE", "company_name": "Meridian"},
    )
    assert draft["generated_by_ai"] is False
    assert draft["empty_state_key"] == "lifecycle_email.no_recorded_strengths"
    assert not gs.meta_commentary_defects(f"{draft['subject']}\n{draft['body']}")



async def test_an_invitation_without_a_link_is_never_generated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The claim this email exists to make is "here is your assessment"."""

    async def _never(*_args, **_kwargs):
        raise AssertionError("the model must not be called")

    monkeypatch.setattr(lifecycle_email.llm_router, "invoke_llm", _never)

    draft = await lifecycle_email.draft(
        EMAIL_TYPE_ASSESSMENT_INVITATION,
        {"candidate_name": "Arun", "job_title": "Supervisor", "company_name": "M"},
    )
    assert draft["empty_state_key"] == "lifecycle_email.no_link_available"



async def test_a_sufficient_email_is_generated_and_carries_no_banned_phrase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    link = "https://app.example.test/assessments/token-alpha"

    async def _reply(*_args, **_kwargs):
        return json.dumps(
            {
                "subject": "Your assessment for Supervisor at Meridian",
                "body": (
                    "Hi Arun, the team would like you to complete a short "
                    f"assessment.\n\n{link}\n\nYour answers save as you go."
                ),
            }
        )

    monkeypatch.setattr(lifecycle_email.llm_router, "invoke_llm", _reply)

    draft = await lifecycle_email.draft(
        EMAIL_TYPE_ASSESSMENT_INVITATION,
        {
            "candidate_name": "Arun",
            "job_title": "Supervisor",
            "company_name": "Meridian",
            "assessment_link": link,
        },
    )
    assert draft["generated_by_ai"] is True
    assert draft["empty_state_key"] is None
    assert link in draft["body"]
    assert not gs.meta_commentary_defects(f"{draft['subject']}\n{draft['body']}")



async def test_a_hedging_email_is_refused_and_the_template_is_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SUFFICIENT INPUT, and the model hedges anyway. The deterministic
    evaluator refuses it on every attempt and the template goes out."""
    link = "https://app.example.test/assessments/token-alpha"

    async def _reply(*_args, **_kwargs):
        return json.dumps(
            {
                "subject": "Your assessment",
                "body": (
                    "Hi Arun, no information was found about your experience "
                    f"and this remains unverified.\n\n{link}"
                ),
            }
        )

    monkeypatch.setattr(lifecycle_email.llm_router, "invoke_llm", _reply)

    draft = await lifecycle_email.draft(
        EMAIL_TYPE_ASSESSMENT_INVITATION,
        {
            "candidate_name": "Arun",
            "job_title": "Supervisor",
            "company_name": "Meridian",
            "assessment_link": link,
        },
    )
    assert draft["generated_by_ai"] is False
    assert not gs.meta_commentary_defects(f"{draft['subject']}\n{draft['body']}")
