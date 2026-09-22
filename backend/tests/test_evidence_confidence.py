"""Evidence Confidence: what it is derived from, and what it may never touch.

The three properties under test, in the order they matter:

  * CONFIDENCE NEVER MOVES A GRADE. It is computed after scoring and it is
    written to its own fields. If that ever stops being true, a word about the
    strength of the record becomes a word about the candidate, and every grade
    in the product becomes a function of how much paperwork arrived.
  * IT COUNTS ORIGINATORS, NOT DOCUMENTS. A resume line and the candidate
    restating it in the interview are one person saying one thing twice.
    Counting them as two is how a confidently written resume becomes a well
    corroborated candidate.
  * INSUFFICIENT IS REPORTED. A line with nothing behind it says so. It does
    not say Low, which reads as a weak finding about a person, and it does not
    say High, which would be a fabrication.
"""
from __future__ import annotations

import pytest

from app.services import evidence_confidence
from app.services.evidence import ledger
from app.services.miti import aggregation, tiering


# -- The vocabulary ----------------------------------------------------------

def test_the_four_words_are_the_aggregators_own_four():
    """Restated rather than imported, so this is what stops them drifting.

    `miti.aggregation` reaches the Runbook data loader at call time and a
    report renderer should not have to; the cost of the copy is this
    assertion, which is cheaper than the import and strictly louder.
    """
    assert evidence_confidence.CONFIDENCE_WORDS == aggregation.CONFIDENCE_LABELS


def test_every_word_has_a_client_facing_display_form():
    for code in evidence_confidence.CONFIDENCE_WORDS:
        assert evidence_confidence.display_word(code)


def test_insufficient_spells_itself_out_rather_than_printing_one_adjective():
    """A bare fourth adjective would read as a fourth rung on the same ladder.
    It is not one: it is the absence of a reading."""
    assert (
        evidence_confidence.display_word(evidence_confidence.CONFIDENCE_INSUFFICIENT)
        == "Insufficient evidence"
    )


def test_an_unknown_code_converts_to_nothing_rather_than_being_echoed():
    """A code that reached a reader unconverted is a leak of an internal
    vocabulary, which is the same failure a raw score would be."""
    assert evidence_confidence.display_word("medium") is None
    assert evidence_confidence.display_word(None) is None
    assert evidence_confidence.display_word("") is None


# -- The source registry -----------------------------------------------------

def test_every_ledger_source_type_is_named_in_the_registry():
    """A source the product can record and the report cannot name would be
    evidence a reader is told nothing about."""
    named = {source.key for source in evidence_confidence.SOURCE_REGISTRY}
    assert ledger.SOURCE_TYPES <= named


def test_the_registry_uses_the_products_one_independence_grouping():
    """The originator of every ledger source is `miti.tiering`'s, not a second
    table maintained by hand beside it.

    This is the assertion that makes the registry a re-use rather than a
    re-implementation: the grouping rule lives in one place and this proves
    the copy agrees with it, in both directions, for every source the ledger
    accepts.
    """
    for source in evidence_confidence.SOURCE_REGISTRY:
        if source.key not in ledger.SOURCE_TYPES:
            continue
        assert source.originator == tiering.independence_group_for(source.key), (
            source.key
        )


def test_bgv_is_the_one_source_outside_the_ledgers_closed_vocabulary():
    """A confirmation lives in `bgv_verifications` and is never written to
    `evidence_items`, so adding it to the ledger's CHECK would create a
    vocabulary entry with no producer."""
    assert evidence_confidence.SOURCE_BGV not in ledger.SOURCE_TYPES
    entry = next(
        source
        for source in evidence_confidence.SOURCE_REGISTRY
        if source.key == evidence_confidence.SOURCE_BGV
    )
    assert entry.originator == tiering.GROUP_EMPLOYER
    assert entry.evidences_candidate is True


def test_the_bgv_source_is_named_after_who_spoke_not_after_what_they_said():
    """"Employer Confirmed" is the product's display language for a CONFIRMED
    check. Used as the name of the source it would print the word Confirmed
    beside an employment the employer had just declined to confirm."""
    labels = evidence_confidence.source_labels([evidence_confidence.SOURCE_BGV])
    assert labels == ("Previous employer",)
    assert "Confirmed" not in labels[0]


def test_the_role_description_and_the_requirement_evidence_nothing():
    """They are the hiring side's account of the ROLE. If either could
    corroborate a person, every candidate's confidence would rise the moment a
    hiring manager wrote a requirement down."""
    for key in (ledger.SOURCE_JD, ledger.SOURCE_SWOT):
        entry = next(
            source
            for source in evidence_confidence.SOURCE_REGISTRY
            if source.key == key
        )
        assert entry.evidences_candidate is False


# -- The derivation ----------------------------------------------------------

def test_nothing_recorded_reports_insufficient():
    assert (
        evidence_confidence.confidence_word([])
        == evidence_confidence.CONFIDENCE_INSUFFICIENT
    )


def test_a_requirement_on_its_own_is_still_insufficient():
    """The hiring manager defining the bar is not evidence about the person,
    so a line that carries only that carries nothing."""
    assert (
        evidence_confidence.confidence_word(
            [ledger.SOURCE_SWOT, ledger.SOURCE_JD]
        )
        == evidence_confidence.CONFIDENCE_INSUFFICIENT
    )


def test_the_candidates_own_unprompted_account_is_low():
    assert (
        evidence_confidence.confidence_word([ledger.SOURCE_RESUME])
        == evidence_confidence.CONFIDENCE_LOW
    )


def test_a_resume_and_the_candidate_repeating_it_is_still_low():
    """THE CORE RULE. Independence is counted by ORIGINATOR, never by document.

    Three documents here, all of them the same person. A derivation that
    counted documents would report High on the single most common shape of
    evidence in the product, which is a well written resume and its author.
    """
    kinds = [
        ledger.SOURCE_RESUME,
        ledger.SOURCE_VALIDATION,
        ledger.SOURCE_MEMORY,
    ]
    assert (
        evidence_confidence.confidence_word(kinds)
        == evidence_confidence.CONFIDENCE_LOW
    )


def test_platform_memory_can_never_corroborate_what_it_was_derived_from():
    """It is the product's own earlier reading of the same person. Counting it
    as a second originator would let the product corroborate a claim with
    itself."""
    entry = next(
        source
        for source in evidence_confidence.SOURCE_REGISTRY
        if source.key == ledger.SOURCE_MEMORY
    )
    assert entry.originator == tiering.GROUP_CANDIDATE


def test_being_asked_and_answering_is_moderate_not_low():
    """Moderate is a separable state, not a rounding between the other two.
    "We asked about this and they answered" is a materially stronger record
    than "it is on their resume and nothing tested it"."""
    assert (
        evidence_confidence.confidence_word(
            [ledger.SOURCE_RESUME, ledger.SOURCE_ANSWER]
        )
        == evidence_confidence.CONFIDENCE_MODERATE
    )


def test_a_second_originator_is_what_makes_it_high():
    assert (
        evidence_confidence.confidence_word(
            [ledger.SOURCE_VALIDATION, evidence_confidence.SOURCE_BGV]
        )
        == evidence_confidence.CONFIDENCE_HIGH
    )


def test_an_unrecognised_source_never_manufactures_corroboration():
    """Deny by default, the same direction `independence_group_for` takes for
    an unknown source: assuming a new kind is independent would invent exactly
    the corroboration this grouping exists to prevent."""
    assert (
        evidence_confidence.confidence_word([ledger.SOURCE_RESUME, "linkedin"])
        == evidence_confidence.CONFIDENCE_LOW
    )
    assert (
        evidence_confidence.confidence_word(["linkedin"])
        == evidence_confidence.CONFIDENCE_INSUFFICIENT
    )


def test_describe_names_only_sources_that_were_actually_present():
    described = evidence_confidence.describe(
        [ledger.SOURCE_ANSWER, ledger.SOURCE_SWOT, "linkedin"]
    )
    assert described.sources == (ledger.SOURCE_ANSWER, ledger.SOURCE_SWOT)
    assert described.confidence == evidence_confidence.CONFIDENCE_MODERATE
    assert described.labels == ("Assessment responses", "Hiring manager defined requirement")


def test_the_word_and_the_sources_beside_it_come_from_one_pass():
    """Two entry points would let a report print High above a source list that
    cannot support it."""
    for kinds in (
        (),
        (ledger.SOURCE_RESUME,),
        (ledger.SOURCE_RESUME, ledger.SOURCE_ANSWER),
        (ledger.SOURCE_VALIDATION, evidence_confidence.SOURCE_BGV),
        (ledger.SOURCE_SWOT,),
    ):
        described = evidence_confidence.describe(kinds)
        assert described.confidence == evidence_confidence.confidence_word(
            described.sources
        )


def test_source_labels_are_ordered_by_the_registry_not_by_the_stored_list():
    """Two reports naming the same sources in a different order is a
    difference a recruiter comparing candidates reads as meaning something."""
    forwards = evidence_confidence.source_labels(
        [ledger.SOURCE_ANSWER, ledger.SOURCE_RESUME]
    )
    backwards = evidence_confidence.source_labels(
        [ledger.SOURCE_RESUME, ledger.SOURCE_ANSWER]
    )
    assert forwards == backwards


# -- The property the whole feature rests on ---------------------------------

_ALL_KINDS = (
    ledger.SOURCE_RESUME,
    ledger.SOURCE_ANSWER,
    ledger.SOURCE_VALIDATION,
    ledger.SOURCE_MEMORY,
    ledger.SOURCE_SWOT,
    ledger.SOURCE_JD,
    evidence_confidence.SOURCE_BGV,
)


def _rows() -> list[dict]:
    return [
        {
            "category": "must_have",
            "name": "Distributed Systems",
            "score": 82,
            "required_level": 75,
            "ordinal": 1,
            "remark": "x",
        },
        {
            "category": "behavioural",
            "name": "Judgement under pressure",
            "score": 58,
            "required_level": 75,
            "ordinal": 1,
            "remark": "y",
        },
        {
            "category": "matching",
            "name": "Skills present",
            "score": 91,
            "required_level": None,
            "ordinal": 1,
            "remark": "z",
        },
    ]


@pytest.mark.parametrize("width", range(len(_ALL_KINDS) + 1))
def test_confidence_never_moves_a_grade(width):
    """THE ASSERTION THE FEATURE IS BUILT AROUND, swept over every prefix of
    the source vocabulary.

    `apply_evidence_confidence` runs after scoring, on rows that already carry
    their score, and the comparison is on the rows themselves rather than on a
    docstring: every key except the two it is allowed to add is byte identical
    afterwards, whatever confidence it derived.
    """
    from app.services.functional_assessment import apply_evidence_confidence

    kinds = _ALL_KINDS[:width]
    rows = _rows()
    before = [dict(row) for row in rows]
    apply_evidence_confidence(
        rows,
        competency_sources={row["name"]: kinds for row in rows},
        evidence_by_item={},
    )
    for original, after in zip(before, rows):
        assert set(after) - set(original) == {
            "evidence_confidence",
            "evidence_sources",
        }
        for key, value in original.items():
            assert after[key] == value, key
        assert after["evidence_confidence"] in evidence_confidence.CONFIDENCE_WORDS


def test_the_module_imports_no_scorer():
    """Structural, by AST, the same technique the Miti aggregator's
    determinism is asserted with. A confidence derivation that could reach a
    scorer is one somebody will eventually wire into a score."""
    import ast
    import pathlib

    source = pathlib.Path(evidence_confidence.__file__).read_text(encoding="utf-8")
    imported: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    forbidden = {
        "app.services.rating",
        "app.services.matching",
        "app.services.miti.aggregation",
        "app.services.functional_assessment",
    }
    assert not (imported & forbidden), sorted(imported & forbidden)
