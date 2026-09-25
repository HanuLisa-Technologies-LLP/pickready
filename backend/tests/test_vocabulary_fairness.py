"""Vocabulary must not decide whether a resume evidences a skill (Runbook §58).

WHAT IS BEING ASSERTED, AND WHY IT IS A FAIRNESS TEST RATHER THAN A QUALITY ONE
--------------------------------------------------------------------------------
RPN-PHIL-001 §58 does not say vocabulary mismatch costs a little accuracy. It
says pure vector similarity "will systematically undervalue candidates who
describe their work in non-standard vocabulary, which correlates with
non-standard backgrounds". The failure is therefore directional and it lands on
one group: the person who learnt the craft at an Indian services firm, in
academia, in a regional business or outside English, and who calls the work by
the name they were taught.

WHAT IT IS ASSERTED AGAINST NOW (Vivekium release, Phase 2 WP-F)
-----------------------------------------------------------------
This file used to grade the corpus through the deterministic pre-screen, which
is DELETED with the A/B/C/Hold grade. The deterministic step that decides
evidence on the live path is Yukti's grounding (`yukti/grounding.py`):
`skill_term_line` is what upgrades an unquoted model verdict to "some" when
the resume literally names the skill, and `skill_mentioned` is what stops a
"Not evidenced" tag being shown to a recruiter about a skill the resume
names. Both read `hiring.ontology`. So the directional assertion is now: the
candidate using the NON-STANDARD word is found to evidence the skill exactly
when the candidate using the job description's own word is, and is never
told they lack it.

HOW THE CORPUS ISOLATES THE VARIABLE
--------------------------------------
Every pair is substituted into ONE identical sentence template, so the two
readings differ in exactly one word. Both directions are run: equivalence is
symmetric, and a table that resolved one way only would have picked a winning
vocabulary rather than stopped vocabulary deciding.

THE PHRASING HALF
------------------
Grammar is not vocabulary. What is asserted is that a claim written in Indian
business English is found to evidence its skill exactly as the same claim in
standard English is.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from app.services.hiring import ontology
from app.services.yukti import grounding

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


def _evidenced(requirement: str, resume: str) -> bool:
    """Yukti's deterministic verdict: does this resume name the skill on a line
    that can serve as the quote?"""
    return grounding.skill_term_line(requirement, grounding.ResumeIndex.of(resume)) is not None


def _mentioned(requirement: str, resume: str) -> bool:
    """Would Yukti suppress a "Not evidenced" tag for this skill?"""
    return grounding.skill_mentioned(requirement, grounding.ResumeIndex.of(resume))


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
    the other name for the work must be found to evidence it, and must never
    be told they lack it.
    """
    standard = TEMPLATE.format(term=row["requirement"])
    variant = TEMPLATE.format(term=row["variant"])

    assert _evidenced(row["requirement"], standard), row
    assert _evidenced(row["requirement"], variant), row
    assert _mentioned(row["requirement"], variant), row


@pytest.mark.parametrize("row", PAIRS, ids=lambda r: f"{r['variant']}|{r['requirement']}")
def test_equivalence_resolves_in_both_directions(row):
    """The mirror case: the JOB uses the non-standard word and the candidate
    uses the standard one. A table that only resolved one way would have picked
    a winning vocabulary rather than stopped vocabulary from deciding."""
    crossed = TEMPLATE.format(term=row["requirement"])

    assert _evidenced(row["variant"], TEMPLATE.format(term=row["variant"])), row
    assert _evidenced(row["variant"], crossed), row
    assert _mentioned(row["variant"], crossed), row


def test_a_word_the_ontology_has_never_heard_of_still_matches_itself():
    """Expansion is ADDITIVE. An unknown term must stand on its own rather than
    vanish, or adding a group to the table could remove a match that used to
    work."""
    assert _evidenced(
        "zermatt reconciliation",
        TEMPLATE.format(term="zermatt reconciliation"),
    )


def test_expansion_never_manufactures_a_match_out_of_nothing():
    """The failure in the other direction, which is the more dangerous one.

    A near-miss credits a candidate with work they did not do, and an ontology
    that says yes to everything is not fairer than one that says no to
    everything. A resume about supply chain does not evidence a graph database
    requirement however generously the table is read.
    """
    resume = TEMPLATE.format(term="supply chain and materials management")
    assert not _evidenced("graph database", resume)
    assert not _mentioned("graph database", resume)


# ── Non-standard English ────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "row", PHRASINGS, ids=lambda r: r["requirement"].replace(" ", "_")
)
def test_non_standard_english_is_read_as_the_same_evidence(row):
    """Grammar is not evidence.

    The same claim, written once in standard English and once in the Indian
    business English a great many resumes in this market are written in. A
    reader that found the skill in the first and not the second would be
    scoring fluency, which §52.4's proxy audit names as a common exclusion
    mechanism.
    """
    assert _evidenced(row["requirement"], row["standard"]), row
    assert _evidenced(row["requirement"], row["variant"]), row
    assert _mentioned(row["requirement"], row["variant"]), row


# ── One ontology, not two (spec-doc6 §4.6, §10.1 rule 12) ───────────────────

def test_matching_job_relevance_and_yukti_share_one_ontology():
    """Three surfaces read the same table, and none of them carries a copy.

    A second equivalence table would drift from the first, and a fairness
    artefact that disagrees with itself between the recruiter's ranked list and
    the candidate's own job board is worse than one that is simply wrong,
    because only one of those is findable.
    """
    from app.services import job_relevance, matching

    assert matching.ontology is ontology
    assert job_relevance.ontology is ontology
    assert grounding.ontology is ontology


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
