"""Every way `validate_answer` refuses, and what it says when it does.

THE REFUSALS ARE THE FEATURE. Runbook section 16.2 makes Section 2 a forced
trade-off rather than free text, because a free-text "what do you value" is
always "excellence and integrity" and modifies nothing; Section 3 rejects an
adjective and asks again, because "ownership mindset" cannot be probed and "has
taken a project from an unclear brief to a shipped outcome" can. Both rules are
enforced HERE rather than in the control that renders the question, since a
rule enforced only by the UI is a rule anybody with a terminal is exempt from.

So each arm below is a refusal a real client will meet, and the assertion is on
the message as well as the raising: a refusal that blocks without teaching sends
the client back to type the same thing again.

Pure functions and hand-built `Question` values. No database, no network.
"""
from __future__ import annotations

import pytest

from app.services.hiring import company_dna
from app.services.hiring import dna_compilation as compilation
from app.services.hiring.dna_compilation import AnswerRejected


def _question(kind: str, **kwargs) -> company_dna.Question:
    fields = {
        "key": kwargs.pop("key", "s1_q1"),
        "kind": kind,
        "prompt": kwargs.pop("prompt", "A question."),
    }
    fields.update(kwargs)
    return company_dna.Question(**fields)


# ── Free text ────────────────────────────────────────────────────────────────


def test_a_required_text_answer_left_blank_is_refused() -> None:
    question = _question(company_dna.TEXT_QUESTION, required=True)
    with pytest.raises(AnswerRejected) as excinfo:
        compilation.validate_answer(question, "   ")
    assert excinfo.value.question_key == question.key
    assert str(excinfo.value)


def test_an_optional_text_answer_left_blank_is_accepted_as_empty() -> None:
    question = _question(company_dna.TEXT_QUESTION, required=False)
    assert compilation.validate_answer(question, None) == ""


def test_text_is_stripped_rather_than_stored_with_its_whitespace() -> None:
    question = _question(company_dna.TEXT_QUESTION, required=True)
    assert compilation.validate_answer(question, "  a real answer  ") == "a real answer"


# ── Observable evidence, Section 3 ───────────────────────────────────────────


def test_an_adjective_is_refused_and_the_message_says_what_is_wanted() -> None:
    """The canonical case from the Runbook: "ownership mindset" is refused."""
    question = _question(company_dna.EVIDENCE_QUESTION, key="s3_q1")
    with pytest.raises(AnswerRejected) as excinfo:
        compilation.validate_answer(question, "ownership mindset")
    assert excinfo.value.question_key == "s3_q1"
    # The refusal has to teach. An empty or generic message leaves the client
    # retyping the same adjective.
    assert len(str(excinfo.value)) > 20


def test_an_observable_statement_is_accepted() -> None:
    question = _question(company_dna.EVIDENCE_QUESTION, key="s3_q1")
    answer = "has taken a project from an unclear brief to a shipped outcome"
    assert compilation.validate_answer(question, answer) == answer


# ── Observable-evidence lists, Section 4 ─────────────────────────────────────


#: The evidence-list question that carries BOTH bounds, so the min and max
#: arms are reachable. Resolved from the instrument rather than hardcoded, and
#: asserted rather than skipped past: a conditional skip here would be a new
#: undeclared skip the inventory gate fails on, and it would hide the very case
#: it claims to guard.
def _bounded_list_question() -> tuple[company_dna.Question, int, int]:
    for section in company_dna.SECTIONS:
        minimum = getattr(section, "min_items", None)
        maximum = getattr(section, "max_items", None)
        if not minimum or not maximum:
            continue
        for question in section.questions:
            if question.kind == company_dna.EVIDENCE_LIST_QUESTION:
                return question, minimum, maximum
    raise AssertionError(
        "the instrument no longer declares a bounded evidence-list section, so "
        "the minimum and maximum refusals below cannot be reached. Point these "
        "at whatever replaced it rather than deleting them."
    )


def test_an_empty_list_is_refused() -> None:
    question, _minimum, _maximum = _bounded_list_question()
    with pytest.raises(AnswerRejected) as excinfo:
        compilation.validate_answer(question, "   \n  \n")
    assert excinfo.value.question_key == question.key


def test_a_list_below_the_minimum_says_how_many_are_wanted() -> None:
    """One broad statement cannot be probed the way several specific ones can,
    which is why the minimum exists and why the refusal names it."""
    question, minimum, _maximum = _bounded_list_question()
    assert minimum >= 2, "a minimum of one makes this refusal unreachable"
    one_item = "has taken a project from an unclear brief to a shipped outcome"
    with pytest.raises(AnswerRejected) as excinfo:
        compilation.validate_answer(question, one_item)
    assert excinfo.value.question_key == question.key
    # Spelled, not numeric: the client-facing text carries no digits.
    assert not any(character.isdigit() for character in str(excinfo.value))


def test_a_list_above_the_maximum_is_refused() -> None:
    question, _minimum, maximum = _bounded_list_question()
    item = "has taken a project from an unclear brief to a shipped outcome"
    too_many = "\n".join(f"{item} number {index}" for index in range(maximum + 3))
    with pytest.raises(AnswerRejected) as excinfo:
        compilation.validate_answer(question, too_many)
    assert excinfo.value.question_key == question.key
    assert not any(character.isdigit() for character in str(excinfo.value))


# ── A question that belongs to no section ────────────────────────────────────


def test_a_list_question_outside_every_section_still_validates() -> None:
    """`_section_for` returns None for a key the instrument does not carry, and
    the validator must fall back to "at least one item" rather than raise a
    TypeError comparing against a missing minimum."""
    orphan = _question(company_dna.EVIDENCE_LIST_QUESTION, key="not_in_any_section")
    with pytest.raises(AnswerRejected):
        compilation.validate_answer(orphan, "")
    accepted = compilation.validate_answer(
        orphan, "has shipped a change end to end without being asked twice"
    )
    assert accepted
