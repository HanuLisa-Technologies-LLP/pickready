"""Evidence vs Claim Summary: selection, and the one sentence that must be right.

The property this file exists for is the last one:

  * ABSENCE OF EVIDENCE IS NEVER RENDERED AS THE CLAIM BEING FALSE. A claim
    nothing addressed reports that nothing addressed it, and says in so many
    words that this is a gap in what was examined. "No evidence found" printed
    beside a hiring decision is read as a verdict however it was meant, and a
    reader cannot recover the distinction from the wording alone.
"""
from __future__ import annotations

import re

import pytest

from app.models.bgv_verification import (
    VERIFICATION_NOT_VERIFIED,
    VERIFICATION_PENDING,
    VERIFICATION_VERIFIED,
)
from app.services import evidence_confidence
from app.services.evidence import ledger
from app.services.miti import claims as miti_claims
from app.services.siddhi import claim_evidence, evidence as siddhi_evidence


# -- The vocabularies it restates --------------------------------------------

def test_the_four_claim_states_are_the_ledgers_own():
    """Restated as literals so Siddhi stays off the scoring side of the import
    cycle; pinned here, in both directions, so they cannot drift."""
    restated = {
        claim_evidence.CLAIM_SUPPORTED,
        claim_evidence.CLAIM_CONTRADICTED,
        claim_evidence.CLAIM_INFERRED_ONLY,
        claim_evidence.CLAIM_UNSUPPORTED,
    }
    owned = {
        ledger.CLAIM_SUPPORTED,
        ledger.CLAIM_CONTRADICTED,
        ledger.CLAIM_INFERRED_ONLY,
        ledger.CLAIM_UNSUPPORTED,
    }
    assert restated == owned


def test_every_claim_state_has_a_sentence_written_for_it():
    """A state with no sentence would fall through to one written for another
    state, which is the shape the status table exists to refuse."""
    for state in (
        ledger.CLAIM_SUPPORTED,
        ledger.CLAIM_CONTRADICTED,
        ledger.CLAIM_INFERRED_ONLY,
        ledger.CLAIM_UNSUPPORTED,
    ):
        record = claim_evidence.ClaimRecord(
            claim="Ran the migration.",
            sources=(evidence_confidence.SOURCE_RESUME,),
            status=state,
        )
        section = claim_evidence.build([record])
        assert section["entries"][0]["evidence"]


def test_an_unknown_claim_state_raises_rather_than_borrowing_a_sentence():
    record = claim_evidence.ClaimRecord(
        claim="Ran the migration.",
        sources=(evidence_confidence.SOURCE_RESUME,),
        status="probably_fine",
    )
    with pytest.raises(ValueError, match="Unknown claim status"):
        claim_evidence.build([record])


# -- The sentence the module exists to get right -----------------------------

#: Matched on WORD BOUNDARIES, not as substrings. The same lesson the
#: disqualifier matcher learned: "hold" is inside "old", and here "lied" is
#: inside "implied", so a substring sweep fails on the honest sentence for an
#: inferred-only claim and tells nobody anything about the dishonest one.
_DISPROVEN_READINGS = (
    r"false", r"untrue", r"not true", r"disproven", r"disproved", r"fabricat",
    r"invented", r"made up", r"lied", r"lying", r"exaggerat", r"unfounded",
    r"did not happen", r"never happened", r"misrepresent", r"cannot be true",
)


def _reads_as_disproven(sentence: str) -> str | None:
    lowered = sentence.casefold()
    for reading in _DISPROVEN_READINGS:
        if re.search(rf"\b{reading}", lowered):
            return reading
    return None


def test_an_unevidenced_claim_is_not_presented_as_disproven():
    """THE ONE THAT MATTERS. A claim nothing addressed is a gap in what was
    examined, and the sentence says so explicitly rather than leaving a reader
    to infer it from "no evidence found"."""
    record = claim_evidence.ClaimRecord(
        claim="Led the migration of the ingest path onto Kafka.",
        materiality=miti_claims.MATERIALITY_CRITICAL,
        sources=(),
        status=ledger.CLAIM_UNSUPPORTED,
        evidence_refs=("searched:kafka",),
    )
    entry = claim_evidence.build([record])["entries"][0]
    assert _reads_as_disproven(entry["evidence"]) is None, entry["evidence"]
    assert "not a finding about the claim" in entry["evidence"].casefold()
    assert entry["confidence"] == "Insufficient evidence"


def test_a_supported_status_with_nothing_to_name_reports_the_gap_anyway():
    """The safe direction. A `supported` claim with no source to name is a
    defect in the ledger, and "Identified in ." would state provenance the
    entry cannot produce."""
    record = claim_evidence.ClaimRecord(
        claim="Ran the migration.",
        sources=(),
        status=ledger.CLAIM_SUPPORTED,
    )
    entry = claim_evidence.build([record])["entries"][0]
    assert entry["evidence"] == claim_evidence.ABSENT_EVIDENCE


def test_no_entry_the_module_can_write_reads_as_a_verdict_on_truth():
    for state in (
        ledger.CLAIM_SUPPORTED,
        ledger.CLAIM_CONTRADICTED,
        ledger.CLAIM_INFERRED_ONLY,
        ledger.CLAIM_UNSUPPORTED,
    ):
        for sources in ((), (evidence_confidence.SOURCE_RESUME,),
                        (evidence_confidence.SOURCE_VALIDATION,
                         evidence_confidence.SOURCE_BGV)):
            section = claim_evidence.build(
                [
                    claim_evidence.ClaimRecord(
                        claim="Ran the migration.", sources=sources, status=state
                    )
                ]
            )
            found = _reads_as_disproven(section["entries"][0]["evidence"])
            assert found is None, (state, sources, found)


def test_nothing_the_section_writes_carries_a_number_or_an_em_dash():
    dash = chr(8212)
    section = claim_evidence.build(
        claim_evidence.employment_claims(
            [
                {
                    "employer_name": "Acme Logistics",
                    "designation": "Staff Engineer",
                    "status": VERIFICATION_VERIFIED,
                }
            ]
        )
    )
    body = [section["note"], *(entry["evidence"] for entry in section["entries"])]
    body.append(claim_evidence.NO_CLAIMS_STATEMENT)
    body.append(claim_evidence.ABSENT_EVIDENCE)
    for sentence in body:
        assert not re.search(r"\d", sentence), sentence
        assert dash not in sentence


# -- Selection ---------------------------------------------------------------

def test_the_most_material_claims_are_the_ones_kept():
    records = [
        claim_evidence.ClaimRecord(
            claim=f"Claim {word}",
            materiality=materiality,
            sources=(evidence_confidence.SOURCE_RESUME,),
            status=ledger.CLAIM_SUPPORTED,
        )
        for word, materiality in (
            ("one", miti_claims.MATERIALITY_LOW),
            ("two", miti_claims.MATERIALITY_CRITICAL),
            ("three", miti_claims.MATERIALITY_MODERATE),
            ("four", miti_claims.MATERIALITY_HIGH),
        )
    ]
    entries = claim_evidence.build(records)["entries"]
    assert [entry["claim"] for entry in entries] == [
        "Claim two",
        "Claim four",
        "Claim three",
        "Claim one",
    ]


def test_an_unrecognised_materiality_sorts_last_rather_than_first():
    """Assuming a value nobody recognises is critical would push a real
    Must-have claim out of a six-entry section."""
    records = [
        claim_evidence.ClaimRecord(claim="Unknown", materiality="enormous"),
        claim_evidence.ClaimRecord(
            claim="Known", materiality=miti_claims.MATERIALITY_LOW
        ),
    ]
    entries = claim_evidence.build(records)["entries"]
    assert [entry["claim"] for entry in entries] == ["Known", "Unknown"]


def test_the_section_is_capped_at_six():
    records = [
        claim_evidence.ClaimRecord(claim=f"Claim {index}")
        for index in range(20)
    ]
    assert len(claim_evidence.build(records)["entries"]) == claim_evidence.MAX_ENTRIES


def test_an_empty_section_says_so_rather_than_rendering_blank():
    section = claim_evidence.build([])
    assert section["entries"] == []
    assert section["no_claims_statement"] == claim_evidence.NO_CLAIMS_STATEMENT


def test_the_ordering_is_stable_across_two_identical_runs():
    """A recruiter comparing two candidates reads a reordering as meaning
    something, and it would mean nothing."""
    records = [
        claim_evidence.ClaimRecord(
            claim=f"Claim {index}", materiality=miti_claims.MATERIALITY_HIGH
        )
        for index in range(6)
    ]
    first = claim_evidence.build(records)
    second = claim_evidence.build(records)
    assert first == second


# -- BGV ---------------------------------------------------------------------

def _employment(status: str | None) -> dict:
    return {
        "employer_name": "Acme Logistics",
        "designation": "Staff Engineer",
        "status": status,
    }


def test_a_confirmed_employment_is_the_one_thing_that_reads_as_corroborated():
    """An employer is the only ORIGINATOR in this product that is not the
    candidate, so it is the only source that can take a claim to High."""
    record = claim_evidence.employment_claims([_employment(VERIFICATION_VERIFIED)])[0]
    entry = claim_evidence.build([record])["entries"][0]
    assert entry["confidence"] == "High"
    assert "Previous employer" in entry["evidence"]
    assert "confirmed this employment" in entry["evidence"]


def test_an_unconfirmed_employment_is_the_candidates_own_account():
    record = claim_evidence.employment_claims([_employment(None)])[0]
    entry = claim_evidence.build([record])["entries"][0]
    assert entry["confidence"] == "Low"


def test_a_pending_request_is_not_reported_as_an_answer_either_way():
    """Nothing infers a verdict from a reply, and nothing infers one from the
    absence of a reply. A pending request reads exactly as an unstarted one."""
    pending = claim_evidence.employment_claims([_employment(VERIFICATION_PENDING)])[0]
    unstarted = claim_evidence.employment_claims([_employment(None)])[0]
    assert pending.status == unstarted.status
    assert pending.sources == unstarted.sources
    assert pending.evidence_note == unstarted.evidence_note == ""


def test_a_refused_confirmation_never_prints_the_word_confirmed():
    """The source is WHO SPOKE. Naming it "Employer Confirmed" would print the
    word Confirmed beside an employment the employer had just declined to
    confirm, which is the defect this separation exists to prevent."""
    record = claim_evidence.employment_claims(
        [_employment(VERIFICATION_NOT_VERIFIED)]
    )[0]
    entry = claim_evidence.build([record])["entries"][0]
    assert "did not confirm" in entry["evidence"]
    assert "Confirmed" not in entry["evidence"]
    assert record.status == ledger.CLAIM_CONTRADICTED


def test_a_refused_confirmation_is_the_most_material_thing_in_the_section():
    """One `not_verified` dominates the candidate-level BGV status and holds an
    offer on its own, so it outranks every competency claim."""
    refused = claim_evidence.employment_claims(
        [_employment(VERIFICATION_NOT_VERIFIED)]
    )[0]
    assert refused.materiality == miti_claims.MATERIALITY_CRITICAL


def test_no_contact_detail_can_reach_the_section():
    """`hr_name` and `hr_email` are a third party's personal contact details a
    candidate handed over for one purpose. The builder has nowhere to put one:
    a row carrying them produces an entry that does not mention them."""
    row = _employment(VERIFICATION_VERIFIED)
    row["hr_name"] = "Rekha Iyer"
    row["hr_email"] = "rekha.iyer@acme.example"
    section = claim_evidence.build(claim_evidence.employment_claims([row]))
    blob = repr(section)
    assert "Rekha" not in blob
    assert "acme.example" not in blob
    assert "@" not in blob


def test_no_date_reaches_the_claim_text():
    """A year is a digit, and a delivered report states words. The tenure is on
    the BGV screen, where the recruiter who runs the verification reads it."""
    row = _employment(VERIFICATION_VERIFIED)
    row["started_on"] = "2019-04-01"
    row["ended_on"] = "2023-06-30"
    entry = claim_evidence.build(claim_evidence.employment_claims([row]))["entries"][0]
    assert not re.search(r"\d", entry["claim"])


def test_an_employment_with_no_employer_name_is_skipped_not_rendered_blank():
    assert claim_evidence.employment_claims([_employment(None) | {"employer_name": " "}]) == []
    assert claim_evidence.employment_nodes([{"employer_name": ""}]) == ()


# -- The citable nodes -------------------------------------------------------

def test_every_employment_gets_a_searched_node_and_only_answers_get_more():
    """The `searched` node is the record that a confirmation was LOOKED FOR,
    which is what makes the unevidenced claim citable at all. The employer node
    sits on top of it only where an employer actually answered."""
    nodes = claim_evidence.employment_nodes(
        [
            _employment(None),
            {"employer_name": "Globex", "designation": "Lead", "status": VERIFICATION_VERIFIED},
        ]
    )
    kinds = sorted(node.kind for node in nodes)
    assert kinds == [
        siddhi_evidence.KIND_EMPLOYER,
        siddhi_evidence.KIND_SEARCHED,
        siddhi_evidence.KIND_SEARCHED,
    ]


def test_every_ref_a_claim_cites_is_a_node_the_index_holds():
    """A ref that is citable but absent from the index is a ref the persisted
    trail cannot explain, which is worse than none: it reads as complete."""
    employments = [
        _employment(VERIFICATION_VERIFIED),
        {"employer_name": "Globex", "designation": "Lead", "status": None},
    ]
    index = siddhi_evidence.EvidenceIndex(
        nodes=claim_evidence.employment_nodes(employments)
    )
    for record in claim_evidence.employment_claims(employments):
        assert set(record.evidence_refs) <= index.refs


def test_an_employer_ref_carries_the_name_and_never_the_outcome():
    """A ref is a locator. Encoding the verification decision into it would put
    a fact a reader should see stated into an identifier instead."""
    confirmed = claim_evidence.employment_nodes([_employment(VERIFICATION_VERIFIED)])
    refused = claim_evidence.employment_nodes([_employment(VERIFICATION_NOT_VERIFIED)])
    assert [node.ref for node in confirmed] == [node.ref for node in refused]


def test_an_employer_can_never_collide_with_a_competency_of_the_same_name():
    """Namespaced, so an employer called Observability cannot silently
    corroborate a competency called Observability."""
    index = siddhi_evidence.EvidenceIndex.build(items=["Observability"])
    employer = siddhi_evidence.employer_item("Observability")
    assert employer not in {node.item for node in index.nodes}


def test_the_lower_bound_is_a_target_and_is_never_padded_to():
    """`MIN_ENTRIES` is the brief's three, and `build` does not enforce it.

    Pinned here because the constant is the decision: a section that padded to
    reach three would invent a claim the ledger never recorded, which is the
    one thing a claim summary must not do. The assertion is that two records in
    produce two entries out, beside the constant that says three was wanted.
    """
    assert claim_evidence.MIN_ENTRIES == 3
    records = [
        claim_evidence.ClaimRecord(claim="Claim one"),
        claim_evidence.ClaimRecord(claim="Claim two"),
    ]
    entries = claim_evidence.build(records)["entries"]
    assert len(entries) == 2
    assert claim_evidence.build(records)["no_claims_statement"] is None
