"""The BGV Agent writes an email and decides nothing, and both halves are tested.

The dangerous failure for this particular email is not a bad sentence, it is a
CONFIDENT WRONG FACT. A verification request carrying an invented date asks a
stranger to confirm something nobody claimed, the stranger correctly answers
"no, that is not right", and a true claim is recorded as a discrepancy about a
real person's employment history. So the grounding gate is deterministic, it
runs on every attempt, and it is asserted here rather than requested in a
prompt.
"""
from __future__ import annotations

import ast
import asyncio
import datetime
import pathlib

from app.services import bgv_agent

BACKEND = pathlib.Path(__file__).resolve().parents[1]

FACTS = bgv_agent.FactBlock(
    candidate_name="Karthik Kumar",
    employer_name="Company A",
    designation="Java Developer",
    started_on=datetime.date(2019, 1, 7),
    ended_on=datetime.date(2021, 6, 30),
    hr_name="Asha R",
    recruiter_team="Recruitment team, Sarkar Corp",
)


def test_the_deterministic_template_passes_its_own_gate() -> None:
    """The fallback has to satisfy the same check the model output does.

    A template that could not pass would mean the degraded path ships
    something the product would have rejected from a model, which is the
    fallback quietly being worse than the failure it stands in for.
    """
    assert bgv_agent.verify_grounding(bgv_agent.deterministic_body(FACTS), FACTS).ok


def test_an_invented_year_is_rejected() -> None:
    """The single most damaging hallucination this email can carry."""
    body = bgv_agent.deterministic_body(FACTS).replace("2019", "2018")
    critique = bgv_agent.verify_grounding(body, FACTS)
    assert not critique.ok
    assert critique.reasons, "a rejection has to tell the next attempt what to fix"


def test_every_stated_fact_must_appear_verbatim() -> None:
    """Dropping a fact is as wrong as inventing one: an employer asked to
    confirm an unnamed role at an unnamed company cannot answer."""
    body = bgv_agent.deterministic_body(FACTS)
    for original, replacement in (
        ("Asha R", "Sir or Madam"),
        ("Company A", "your organisation"),
        ("Java Developer", "a technical role"),
        ("Karthik Kumar", "the candidate"),
    ):
        assert not bgv_agent.verify_grounding(
            body.replace(original, replacement), FACTS
        ).ok, original


def test_the_house_style_rules_are_enforced_here_too() -> None:
    body = bgv_agent.deterministic_body(FACTS)
    assert not bgv_agent.verify_grounding(body + chr(8212), FACTS).ok


def test_an_email_that_asserts_rather_than_asks_is_rejected() -> None:
    """The claim is unconfirmed. An email that states it as fact invites an
    employer to agree with something Vivekium has no basis to say."""
    asserted = (
        "Hello Asha R,\n\nKarthik Kumar worked at Company A as a Java Developer "
        "from 07 January 2019 to 30 June 2021. This is recorded in our system "
        "and we are noting it for our files. Regards, Recruitment team, Sarkar "
        "Corp. We appreciate your organisation and we have logged this record "
        "against the candidate profile for future reference by our team."
    )
    assert not bgv_agent.verify_grounding(asserted, FACTS).ok


def test_the_subject_is_composed_and_names_both_parties() -> None:
    """Never model-written: the subject is what a busy HR contact reads in a
    list, and it is the line a model most wants to make interesting."""
    subject = bgv_agent.subject_for(FACTS)
    assert "Karthik Kumar" in subject and "Company A" in subject


def test_with_no_model_credential_it_degrades_honestly() -> None:
    """The suite runs with no model credential by design, so this is the real
    outage path rather than a simulation of one.

    `generated_by_ai` comes back False and the body IS the template. Presenting
    template output as generation is the failure this codebase names by name.
    """
    subject, body, generated_by_ai = asyncio.run(
        bgv_agent.draft_verification_email(FACTS)
    )
    assert generated_by_ai is False
    assert body == bgv_agent.deterministic_body(FACTS)
    assert subject == bgv_agent.subject_for(FACTS)


def test_the_fact_block_has_no_free_form_escape_hatch() -> None:
    """The same rule `EvaluatorInput` follows: the grounding check can only
    verify what it can enumerate, so a `context` or `notes` field would be a
    documented way to put ungrounded text in front of the model."""
    fields = set(bgv_agent.FactBlock.__dataclass_fields__)
    assert fields == {
        "candidate_name",
        "employer_name",
        "designation",
        "started_on",
        "ended_on",
        "hr_name",
        "recruiter_team",
    }


def test_the_agent_introduces_no_new_model_string_or_task_type() -> None:
    """Composing an email is what `email_composition` already is. A second task
    type for one act would be a second answer to one question, and a new model
    id is forbidden outright."""
    from app.config import llm_providers

    assert bgv_agent.TASK_TYPE in llm_providers.MODEL_FOR_TASK
    source = (BACKEND / "app" / "services" / "bgv_agent.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "gpt-" not in node.value, node.value
            assert "claude-" not in node.value, node.value
