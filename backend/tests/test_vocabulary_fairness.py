"""Vocabulary must not decide a pre-screen grade (spec-doc6 §4.4, Runbook §58).

WHAT IS BEING ASSERTED, AND WHY IT IS A FAIRNESS TEST RATHER THAN A QUALITY ONE
--------------------------------------------------------------------------------
RPN-PHIL-001 §58 does not say vocabulary mismatch costs a little accuracy. It
says pure vector similarity "will systematically undervalue candidates who
describe their work in non-standard vocabulary, which correlates with
non-standard backgrounds". The failure is therefore directional and it lands on
one group: the person who learnt the craft at an Indian services firm, in
academia, in a regional business or outside English, and who calls the work by
the name they were taught.

So the assertion here is not "the two words score similarly". It is that the
candidate using the NON-STANDARD word is never scored BELOW the candidate using
the job description's own word for the same work. A regression that hurt both
equally would be a bug; a regression that hurt only one of them is the thing
§58 is about, and only the directional assertion catches it.

HOW THE CORPUS ISOLATES THE VARIABLE
--------------------------------------
Every pair is substituted into ONE identical sentence template, so the two
gradings differ in exactly one word. Hand-written sentence pairs would have let
sentence quality vary alongside the vocabulary, and the test would then have
been measuring prose while reporting on fairness.

Both directions are run. Equivalence is symmetric, and a table that resolved
"semantic technologies" toward "graph database" but not back would have picked a
winning vocabulary rather than stopped vocabulary deciding, which is the failure
this module's own docstring warns about.

THE PHRASING HALF
------------------
spec-doc6 §4.4 also asks for "candidates whose resumes are written in
non-standard English". No ontology entry can help with that, because it is
grammar and not vocabulary. What is asserted instead is that the claim reader
finds the mechanism, the number and the act of ownership in a sentence whatever
construction carries them, so a resume written in Indian business English is
tiered identically to the same claim in standard English.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.services.hiring import ontology, prescreen

CORPUS = (
    pathlib.Path(__file__).resolve().parent / "fixtures" / "vocabulary" / "mismatch_pairs.json"
)

#: One sentence, one hole. Everything that could move a grade other than the
#: term itself is held constant: the mechanism verb, the checkable numbers, the
#: ownership phrase and the scale marker are identical in both renderings.
TEMPLATE = (
    "I owned the {term} programme end to end, migrating 40 systems and cutting "
    "the cycle from 900 minutes to 120 minutes for a team of 30."
)


def _corpus() -> dict:
    return json.loads(CORPUS.read_text(encoding="utf-8"))


def _grade(requirement: str, resume: str) -> prescreen.PreScreenResult:
    return prescreen.grade(
        prescreen.PreScreenInput(
            requirements=(requirement,),
            requirement_source=prescreen.REQUIREMENTS_FROM_JD,
            claims=prescreen.claims_from_resume(resume),
        )
    )


PAIRS = _corpus()["pairs"]
PHRASINGS = _corpus()["phrasings"]


# ── The corpus itself ───────────────────────────────────────────────────────

def test_the_corpus_meets_the_size_and_coverage_the_spec_asks_for():
    """spec-doc6 §4.4 asks for at least 40 pairs, and names two coverage areas
    by hand: Indian and non-Indian job-title conventions, and non-standard
    English. A corpus of 40 rows all drawn from one engineering vocabulary would
    satisfy the count and none of the intent."""
    assert len(PAIRS) >= 40, len(PAIRS)
    categories = {row["category"] for row in PAIRS}
    for required in (
        "runbook_named",
        "skill_vocabulary",
        "title_convention_in",
        "title_convention_intl",
        "non_standard_english",
    ):
        assert required in categories, required
    # No category may be a token single row.
    for category in categories:
        count = sum(1 for row in PAIRS if row["category"] == category)
        assert count >= 3, (category, count)


def test_the_three_pairings_the_runbook_names_itself_are_present():
    """§58 gives three worked examples. One of them, FP&A against business
    finance, was absent from the table for a whole phase, and it is the pairing
    most likely to matter in this product's primary market. It is pinned by name
    so it cannot go missing again."""
    named = {
        (row["requirement"], row["variant"])
        for row in PAIRS
        if row["category"] == "runbook_named"
    }
    assert ("graph database", "semantic technologies") in named
    assert ("gd&t", "geometric tolerancing") in named
    assert ("fp&a", "business finance") in named


def test_every_pair_actually_resolves_in_the_ontology():
    """A corpus row whose two terms the table has never heard of would pass the
    fairness assertions trivially, by scoring zero on both sides. This is the
    check that keeps the corpus honest rather than merely green."""
    unresolved = [
        row
        for row in PAIRS
        if not ontology.matches(row["requirement"], row["variant"])
        or not ontology.matches(row["variant"], row["requirement"])
    ]
    assert not unresolved, unresolved


# ── The fairness assertion ──────────────────────────────────────────────────

@pytest.mark.parametrize("row", PAIRS, ids=lambda r: f"{r['requirement']}|{r['variant']}")
def test_the_non_standard_word_is_never_penalised(row):
    """The corpus assertion, in the direction §58 states.

    Same requirement, same sentence, one word different. The candidate who used
    the other name for the work must not score lower, must not grade lower, and
    must not be recorded as having left the requirement unassessed.
    """
    standard = _grade(row["requirement"], TEMPLATE.format(term=row["requirement"]))
    variant = _grade(row["requirement"], TEMPLATE.format(term=row["variant"]))

    assert variant.internal.assessed_requirements == 1, row
    assert variant.internal.value >= standard.internal.value, row
    assert variant.named.grade == standard.named.grade, row
    assert variant.internal.confidence >= standard.internal.confidence, row


@pytest.mark.parametrize("row", PAIRS, ids=lambda r: f"{r['variant']}|{r['requirement']}")
def test_equivalence_resolves_in_both_directions(row):
    """The mirror case: the JOB uses the non-standard word and the candidate
    uses the standard one. A table that only resolved one way would have picked
    a winning vocabulary rather than stopped vocabulary from deciding."""
    standard = _grade(row["variant"], TEMPLATE.format(term=row["variant"]))
    crossed = _grade(row["variant"], TEMPLATE.format(term=row["requirement"]))

    assert crossed.internal.assessed_requirements == 1, row
    assert crossed.internal.value >= standard.internal.value, row
    assert crossed.named.grade == standard.named.grade, row


def test_the_whole_corpus_is_scored_identically_not_merely_no_worse():
    """The stronger statement, measured over the corpus rather than per row.

    `>=` is the assertion that matters because it is the direction §58 names,
    but a change that quietly started scoring the non-standard word HIGHER would
    also be a vocabulary preference, just an easier one to feel good about. The
    template holds every other input constant, so the correct number of
    non-identical scores is zero.
    """
    differing = []
    for row in PAIRS:
        standard = _grade(row["requirement"], TEMPLATE.format(term=row["requirement"]))
        variant = _grade(row["requirement"], TEMPLATE.format(term=row["variant"]))
        if variant.internal.value != standard.internal.value:
            differing.append((row, standard.internal.value, variant.internal.value))
    assert not differing, differing


def test_a_word_the_ontology_has_never_heard_of_still_matches_itself():
    """Expansion is ADDITIVE. An unknown term must stand on its own rather than
    vanish, or adding a group to the table could remove a match that used to
    work."""
    result = _grade(
        "zermatt reconciliation",
        TEMPLATE.format(term="zermatt reconciliation"),
    )
    assert result.internal.assessed_requirements == 1


def test_expansion_never_manufactures_a_match_out_of_nothing():
    """The failure in the other direction, which is the more dangerous one.

    A near-miss credits a candidate with work they did not do, and an ontology
    that says yes to everything is not fairer than one that says no to
    everything. A resume about supply chain does not evidence a graph database
    requirement however generously the table is read.
    """
    result = _grade(
        "graph database",
        TEMPLATE.format(term="supply chain and materials management"),
    )
    assert result.internal.assessed_requirements == 0
    assert result.named.grade == prescreen.GRADE_HOLD


# ── Non-standard English ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "row", PHRASINGS, ids=lambda r: r["requirement"].replace(" ", "_")
)
def test_non_standard_english_is_read_at_the_same_evidence_tier(row):
    """Grammar is not evidence.

    The same claim, to the same depth, written once in standard English and once
    in the Indian business English a great many resumes in this market are
    written in. The mechanism, the numbers and the ownership are present in
    both, so RPN-PHIL-001 §6.1's tier and §6.4's modifiers must read them the
    same way. A grader that scored the second one lower would be scoring
    fluency, which §52.4's proxy audit names as a common exclusion mechanism.
    """
    standard = _grade(row["requirement"], row["standard"])
    variant = _grade(row["requirement"], row["variant"])

    assert variant.internal.best_tier == standard.internal.best_tier, row
    assert variant.named.grade == standard.named.grade, row
    assert variant.internal.value >= standard.internal.value, row


def test_the_phrasing_corpus_carries_real_evidence_on_both_sides():
    """Guards the guard. If both sentences of a phrasing pair tiered at E0 the
    parity assertion above would pass while measuring nothing at all."""
    for row in PHRASINGS:
        for key in ("standard", "variant"):
            result = _grade(row["requirement"], row[key])
            assert result.internal.best_tier == prescreen.TIER_SPECIFIC, (row, key)


# ── One ontology, not two (spec-doc6 §4.6, §10.1 rule 12) ───────────────────

def test_matching_job_relevance_and_the_pre_screen_share_one_ontology():
    """Three surfaces read the same table, and none of them carries a copy.

    A second equivalence table would drift from the first, and a fairness
    artefact that disagrees with itself between the recruiter's ranked list and
    the candidate's own job board is worse than one that is simply wrong,
    because only one of those is findable.
    """
    from app.services import job_relevance, matching

    assert matching.ontology is ontology
    assert job_relevance.ontology is ontology
    assert prescreen.ontology is ontology


def test_no_second_equivalence_table_exists_anywhere_in_the_source():
    """The mechanical half of the one-implementation rule.

    `EQUIVALENCE_GROUPS` is defined once. A module that redefined it, or that
    hand-rolled its own synonym dict beside the shared one, is the dual path
    spec-doc6 §4.1 forbids, and it would be invisible in review because both
    copies would look correct on their own.
    """
    root = pathlib.Path(__file__).resolve().parents[1] / "app"
    definitions = [
        path
        for path in root.rglob("*.py")
        if "EQUIVALENCE_GROUPS: " in path.read_text(encoding="utf-8")
        or "EQUIVALENCE_GROUPS =" in path.read_text(encoding="utf-8")
    ]
    assert definitions == [root / "services" / "hiring" / "ontology.py"], definitions
