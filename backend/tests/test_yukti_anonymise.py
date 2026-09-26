"""The name-blind pass, asserted where it matters: on the RENDERED Yukti prompt.

Moved from the pre-screen fairness tests (`test_prescreen_fairness_helpers`,
`test_yukti_live`) with the module (Phase 2), and extended by the one change
the move made: a name is removed on word boundaries, so the cost of the scrub
no longer depends on the candidate's name.

Mutation check recorded in the Phase 2 report: reverting the name scrub to a
bare substring replace fails `test_a_short_name_never_mangles_technical_words`.
"""
from __future__ import annotations

import json
import re
import uuid

import pytest

from app.services.assessment_contract import ContractSkill
from app.services.yukti import anonymise, judge
from app.services.yukti.inputs import CandidateInput, JobContext, prepare_resume

CHECKABLE = (
    "Built a data pipeline in Python for sensor streams and owned the "
    "relational database migration end to end."
)

NAMES = (
    "Priya Raghunathan",
    "Mohammed Farooq",
    "Siobhan O'Connell",
    "Nguyen Thi Lan",
    "Rahul Mehta",
    "Ram Das",
)


# ── The scrub itself ─────────────────────────────────────────────────────────


def test_the_named_identity_is_removed_case_insensitively() -> None:
    cleaned = anonymise.anonymise("ADA Lovelace built it", identities=["Ada Lovelace"])
    assert "ada" not in cleaned.lower()
    assert "built it" in cleaned


def test_contact_details_are_stripped_whatever_the_name_is() -> None:
    dirty = (
        "priya.raghunathan@example.com +91 98765 43210 "
        "linkedin.com/in/priyaraghunathan " + CHECKABLE
    )
    cleaned = anonymise.anonymise(dirty)
    assert "@" not in cleaned
    assert "linkedin" not in cleaned.lower()
    assert not re.search(r"\d{5}", cleaned)
    assert "data pipeline" in cleaned


def test_an_address_built_from_the_name_is_removed_whole() -> None:
    """The shapes are scrubbed BEFORE the name. The other order removed "priya"
    from "priya@priyacodes.dev" first, the rest no longer looked like an
    address, and the personal domain reached the model."""
    cleaned = anonymise.anonymise(
        "Priya Raghunathan, priya@priyacodes.dev, linkedin.com/in/priya " + CHECKABLE,
        identities=anonymise.name_tokens("Priya Raghunathan"),
    )
    assert "priyacodes" not in cleaned
    assert "@" not in cleaned
    assert "linkedin" not in cleaned.lower()
    assert "data pipeline" in cleaned


def test_a_one_character_identity_is_ignored() -> None:
    cleaned = anonymise.anonymise("A candidate who shipped an API", identities=["A"])
    assert "candidate who shipped an API" in cleaned


def test_none_and_empty_text_anonymise_to_a_string() -> None:
    assert anonymise.anonymise(None) == ""
    assert anonymise.anonymise("") == ""


@pytest.mark.parametrize(
    ("name", "text", "survivor"),
    [
        ("Ram Das", "Ram led programming and RAM tuning work", "programming"),
        ("Anu Rao", "Wrote the manual for manufacturing lines", "manufacturing"),
        ("Ali Khan", "Built alignment tooling for Alibaba Cloud", "alignment"),
    ],
)
def test_a_short_name_never_mangles_technical_words(name: str, text: str, survivor: str) -> None:
    cleaned = anonymise.anonymise(text, identities=anonymise.name_tokens(name))
    assert survivor in cleaned


def test_a_name_word_that_is_a_skill_the_job_asks_about_is_left_in_place() -> None:
    text = "Taylor Swift\nShipped three iOS apps in Swift and SwiftUI"
    cleaned = anonymise.anonymise(
        text, identities=anonymise.name_tokens("Taylor Swift"), protected_terms=["Swift"]
    )
    assert "Taylor" not in cleaned
    assert "apps in Swift" in cleaned


def test_an_employer_scrub_never_removes_a_term_the_job_asks_about() -> None:
    text = "Database administrator at Oracle, tuning Oracle Database clusters for Oracle Corporation"
    cleaned = anonymise.anonymise(
        text,
        organisations=["Oracle Corporation", "Oracle"],
        protected_terms=["Oracle Database"],
    )
    assert "Oracle Database" in cleaned
    assert "Corporation" not in cleaned


def test_an_employer_is_removed_on_word_boundaries_only() -> None:
    cleaned = anonymise.anonymise(
        "Designed the namespace tracer for Ace Global", organisations=["Ace Global"]
    )
    assert "namespace tracer" in cleaned
    assert "Ace Global" not in cleaned


def test_organisations_are_read_from_the_parsed_history_and_education() -> None:
    parsed = {
        "employment_history": [{"company": "Goldman  Sachs", "title": "Engineer"}, {"company": ""}],
        "education": [{"institution": "IIT Bombay"}, "junk"],
        "skills": ["Python"],
    }
    assert anonymise.organisations_from(parsed) == ("Goldman Sachs", "IIT Bombay")
    assert anonymise.organisations_from(None) == ()
    assert anonymise.organisations_from(["not", "a", "dict"]) == ()


def test_name_tokens_are_longest_first() -> None:
    assert anonymise.name_tokens("Priya Raghunathan") == ("Priya Raghunathan", "Raghunathan", "Priya")
    assert anonymise.name_tokens("  ") == ()
    assert anonymise.name_tokens(None) == ()


@pytest.mark.asyncio
async def test_identities_are_read_from_the_candidate_row() -> None:
    class Session:
        def __init__(self, row):
            self.row = row

        async def get(self, model, key):
            return self.row

    row = type("Candidate", (), {"full_name": "Grace Hopper"})()
    assert await anonymise.identities(Session(row), uuid.uuid4()) == (
        "Grace Hopper", "Hopper", "Grace"
    )
    assert await anonymise.identities(Session(None), uuid.uuid4()) == ()
    assert await anonymise.identities(Session(row), None) == ()


# ── On the rendered prompt ───────────────────────────────────────────────────


SKILL = ContractSkill(uuid.uuid4(), "Python data engineering", "must_have", 1, "")


def _ctx() -> JobContext:
    return JobContext(
        job_id=uuid.uuid4(),
        title="Data Engineer",
        department=None,
        grade_label="Managerial",
        experience_band="5 to 9 years",
        jd_text="Owns the data platform.",
        role_summary="",
        skills=(SKILL,),
        all_skill_names=(SKILL.name,),
        needs=(),
        contract_digest="d" * 64,
        contract_version=0,
        jd_redacted=False,
    )


def _rendered(name: str, email: str, phone: str, employer: str) -> str:
    resume = (
        f"{name}\n{email} | {phone}\n"
        f"{name.split()[0]} is a data engineer at {employer}.\n{CHECKABLE}"
    )
    prepared = prepare_resume(
        resume,
        identities=anonymise.name_tokens(name),
        organisations=(employer,),
        protected_terms=(SKILL.name,),
    )
    candidate = CandidateInput(uuid.UUID(int=1), uuid.UUID(int=2), prepared.text, False, False, False)
    messages, _ = judge.build_messages(_ctx(), [candidate])
    return json.dumps(messages)


def test_the_rendered_prompt_is_identical_under_every_name_and_employer() -> None:
    """Section 52.2's fairness property, on the exact bytes the model is sent."""
    employers = ("Goldman Sachs", "Tata Consultancy Services", "Acme Widgets", "Infosys", "Zoho", "Flipkart")
    rendered = {
        _rendered(name, f"person{i}@example.com", f"+91 9876{i}43210", employers[i])
        for i, name in enumerate(NAMES)
    }
    assert len(rendered) == 1, "the prompt varies with the candidate's identity"
    (only,) = rendered
    for name in NAMES:
        for token in name.split():
            if len(token) > 2:
                assert token not in only
    for employer in employers:
        assert employer not in only
    assert "@" not in json.loads(only)[1]["content"]
    assert "data pipeline in Python" in only
