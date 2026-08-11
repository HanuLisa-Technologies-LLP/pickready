"""A report remark may not name something the candidate never mentioned.

THE GAP THIS CLOSES
-------------------
`bounded_remark` already rejected a remark that references NOTHING from the
evidence. That catches a remark that says too little. Nothing caught one that
says too much: "Demonstrates strong Kubernetes experience" for a candidate who
never mentioned Kubernetes passes every existing check -- right length, no
number, anchored on some other term -- and is the failure a client would
actually notice, because it is a factual claim about a person.

BOTH DIRECTIONS ARE TESTED, AND THE SECOND ONE MATTERS MORE
------------------------------------------------------------
A guard that rejects a GOOD remark costs a round of latency every time it
fires, is invisible when it does, and teaches the next reader to loosen it
until it catches nothing. So most of this file is false-positive pressure:
ordinary prose, sentence-initial capitals, the dimension's own name, plurals
and possessives of terms that ARE in the evidence.

The rejection names the offending tokens, because `agent_loop`'s contract is
that a rejection is fed back as an INSTRUCTION. "Do not invent" is not
actionable; "do not name Kubernetes" is.
"""
from __future__ import annotations

import pytest

from app.services.functional_assessment import invented_terms

EVIDENCE = (
    "Candidate described migrating a Kafka ingestion pipeline at Northwind, "
    "reduced replay time, and debugged a Postgres locking issue with the data team."
)
NAME = "Distributed systems"


def _flag(remark: str, *, evidence: str = EVIDENCE, name: str = NAME) -> list[str]:
    return invented_terms(remark, evidence=evidence, name=name)


# ── The defect ──────────────────────────────────────────────────────────────

def test_a_technology_the_candidate_never_mentioned_is_caught() -> None:
    remark = (
        "Shows dependable capability across the pipeline work described, and "
        "strong Kubernetes experience is evident throughout the discussion."
    )
    assert _flag(remark) == ["Kubernetes"]


def test_an_invented_employer_is_caught() -> None:
    remark = (
        "Handled the ingestion migration competently, including the work at "
        "Contoso that shaped the approach."
    )
    assert _flag(remark) == ["Contoso"]


def test_every_invented_token_is_reported_so_the_model_can_be_told() -> None:
    """The rejection is fed back verbatim, so it has to name them all."""
    remark = (
        "Good grounding overall, with Kubernetes and Terraform used at Contoso "
        "on the same programme."
    )
    assert _flag(remark) == ["Contoso", "Kubernetes", "Terraform"]


# ── The direction that matters more: no false positives ────────────────────

def test_ordinary_prose_is_not_flagged() -> None:
    remark = (
        "Evidence shows dependable capability, with clear reasoning about the "
        "migration and the outcome it produced. Interview discussion should "
        "confirm depth and independent ownership across comparable work."
    )
    assert _flag(remark) == []


def test_a_term_from_the_evidence_is_not_an_invention() -> None:
    remark = (
        "Describes the Kafka migration at Northwind in concrete terms, "
        "including the Postgres locking issue and how it was resolved."
    )
    assert _flag(remark) == []


def test_a_plural_or_possessive_of_an_evidence_term_is_not_an_invention() -> None:
    """Morphology is not fabrication. Flagging "Kafka's" would make the guard
    fire on correct prose, which is how a guard gets switched off."""
    for variant in ("Kafka's", "Kafkas", "Postgres'"):
        remark = f"Handled the {variant} replay behaviour carefully during the migration."
        assert _flag(remark) == [], variant


def test_the_dimensions_own_name_is_always_legitimate() -> None:
    """The name comes from the job's framework, not from the candidate.
    Naming the skill being assessed is the one proper noun that is always
    allowed."""
    remark = "Applies Distributed systems reasoning to the ingestion work described."
    assert _flag(remark) == []


def test_a_sentence_initial_capital_carries_no_information() -> None:
    """"Stakeholder influence was limited." starts with a capital because it
    starts a sentence, not because it is a product."""
    remark = (
        "Handled the migration well. Stakeholder conversations were described "
        "only briefly. Probing should confirm the depth of that involvement."
    )
    assert _flag(remark) == []


def test_a_two_letter_token_is_not_a_technology() -> None:
    """Initials and short words are noise; flagging them would produce a
    defect the model cannot act on."""
    remark = "Worked on the pipeline at Northwind, and on QA and CI alongside it."
    assert "QA" not in _flag(remark)
    assert "CI" not in _flag(remark)


def test_an_empty_remark_reports_nothing() -> None:
    """Emptiness is a different defect, already caught by its own check. This
    one must not pile a second, misleading reason on top of it."""
    assert _flag("") == []
    assert _flag("   ") == []


def test_it_is_stable_and_deduplicated() -> None:
    """The same defect must read the same way twice, or a reflection loop can
    oscillate between two phrasings of one problem."""
    remark = (
        "Notes the Kubernetes work, and the Kubernetes rollout in particular, "
        "as part of the same programme."
    )
    assert _flag(remark) == ["Kubernetes"]


# ── It is actually wired into the loop ─────────────────────────────────────

def test_the_check_is_a_rejection_reason_and_not_merely_available() -> None:
    """A guard nobody calls is a guard that does nothing.

    `bounded_remark` builds its critique inline, so this reads the source: the
    alternative is standing up a model stub for a string check.
    """
    import inspect

    from app.services import functional_assessment

    source = inspect.getsource(functional_assessment.bounded_remark)
    assert "invented_terms(" in source, "the check is defined but never called"
    assert "invented_term" in source, "there is no defect code for it"


def test_the_rejection_tells_the_model_what_to_stop_naming() -> None:
    """`agent_loop`'s contract: a rejection is an instruction. "Do not invent"
    is not actionable; "do not name Kubernetes" is."""
    import inspect

    from app.services import functional_assessment

    source = inspect.getsource(functional_assessment.bounded_remark)
    assert "do not name anything the candidate did not mention" in source
    assert "join(fabricated" in source, "the offending terms are not fed back"


@pytest.mark.parametrize(
    "remark",
    [
        "Demonstrates dependable capability in this dimension, with relevant practical examples.",
        "Available evidence supports the conclusion drawn from the discussion.",
    ],
)
def test_the_shipped_fallback_remarks_pass_their_own_guard(remark: str) -> None:
    """The fallback is what a client reads during a provider outage. A guard
    that would reject the product's own fallback is a guard that turns an
    outage into an empty report."""
    assert _flag(remark, evidence="", name="this area") == []
