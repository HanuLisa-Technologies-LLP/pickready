"""The vocabulary table's edges, where a matcher quietly starts saying yes.

`test_vocabulary_fairness` asserts the directional property section 58 is about:
the candidate who wrote the non-standard word is never scored below the one who
wrote the job description's own word. That test only exercises real pairs in
whole sentences, which is the right shape for a fairness claim and the wrong
shape for finding the two ways this module can go wrong at the margin.

BOTH FAILURES ARE SILENT AND POINT IN OPPOSITE DIRECTIONS.

A matcher that says yes too easily is the worse of the two, because nothing
downstream can detect it: "rag" is a term in this table and a substring of
"storage", "fragment" and "average", so a plain `in` finds retrieval-augmented
generation in a sentence about disk storage. The grade that follows is not
wrong-looking, it is just wrong.

A matcher that says no too easily is the fairness failure itself, one layer in:
a term the table has never heard of must stand on its own rather than VANISH,
or a caller unioning results silently loses the skill the candidate actually
named.

Pure functions over a Python constant. No database, no network, no model.
"""
from __future__ import annotations

import pytest

from app.services.hiring import ontology


# ── Normalising ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize("written", ["ci/cd", "CI-CD", "  ci   cd  ", "Ci Cd"])
def test_the_separators_people_actually_type_all_agree(written: str) -> None:
    """Collapsed rather than removed: "cicd", which nobody writes, is not
    manufactured, so it cannot match something that is not there."""
    assert ontology.normalise(written) == ontology.normalise("ci cd")


def test_normalising_nothing_is_the_empty_string_rather_than_an_error() -> None:
    assert ontology.normalise("") == ""
    assert ontology.normalise("   ") == ""


# ── Equivalence ──────────────────────────────────────────────────────────────


def test_an_unknown_term_stands_on_its_own_rather_than_vanishing() -> None:
    """A caller unioning results must never lose a skill because the table had
    not heard of it. That would be the fairness failure the table exists to
    prevent, arriving through the table."""
    assert ontology.equivalent("wafer fab metrology") == frozenset(
        {"wafer fab metrology"}
    )


def test_an_empty_term_is_equivalent_to_nothing() -> None:
    """Distinct from the unknown case on purpose: an unknown term is something
    somebody wrote, and a blank is not."""
    assert ontology.equivalent("") == frozenset()
    assert ontology.equivalent("   ") == frozenset()


def test_equivalence_is_symmetric() -> None:
    """A table resolving one way only would have picked a winning vocabulary
    rather than stopped vocabulary deciding."""
    for group in ontology.EQUIVALENCE_GROUPS:
        members = [ontology.normalise(term) for term in group if term.strip()]
        if len(members) < 2:
            continue
        first, second = members[0], members[1]
        assert second in ontology.equivalent(first), group
        assert first in ontology.equivalent(second), group


def test_a_term_in_two_groups_keeps_both_sets_of_siblings(monkeypatch) -> None:
    """The union, never the last write.

    No term in today's table sits in two groups, so this builds one that does.
    Asserting over the live table would pass while testing nothing, and the
    property is about the index BUILDER rather than about today's rows: the day
    somebody adds "kubernetes" to a second group, taking one group would make
    expansion depend on the order rows happen to sit in a Python constant.
    """
    monkeypatch.setattr(
        ontology,
        "EQUIVALENCE_GROUPS",
        (("kubernetes", "k8s"), ("kubernetes", "container orchestration")),
    )
    index = ontology._build_index()
    assert index["kubernetes"] == frozenset(
        {"kubernetes", "k8s", "container orchestration"}
    )
    # And the siblings-of-a-sibling stay in their own group: the union is taken
    # for the shared term, not merged across the whole table.
    assert index["k8s"] == frozenset({"kubernetes", "k8s"})


# ── Canonical form, which is for display only ────────────────────────────────


def test_an_unknown_term_is_its_own_canonical_form() -> None:
    assert ontology.canonical("Wafer Fab Metrology") == "wafer fab metrology"


def test_every_member_of_a_group_shares_one_canonical_form() -> None:
    for group in ontology.EQUIVALENCE_GROUPS:
        forms = {ontology.canonical(term) for term in group if term.strip()}
        assert len(forms) == 1, group


# ── Expansion, which is additive and order-stable ────────────────────────────


def test_the_terms_asked_for_come_first() -> None:
    """A caller that truncates keeps what was actually asked for, rather than a
    sibling the table volunteered."""
    expanded = ontology.expand(["graph database"])
    assert expanded[0] == "graph database"


def test_a_term_given_twice_appears_once() -> None:
    assert ontology.expand(["Kubernetes", "kubernetes", " KUBERNETES "]).count(
        "kubernetes"
    ) == 1


def test_blank_terms_are_dropped_rather_than_expanded() -> None:
    assert ontology.expand(["", "   "]) == []


def test_expansion_never_removes_the_original() -> None:
    """ADDITIVE, never substitutive. Replacing "GD&T" with the long form would
    stop matching the candidates who wrote "GD&T", which is the identical
    failure pointed the other way."""
    for term in ("graph database", "kubernetes"):
        assert term in ontology.expand([term]), term


# ── Mentions, which is phrase-aware ──────────────────────────────────────────


def test_a_multi_word_term_is_found_as_a_phrase() -> None:
    """A bare word-set intersection does not have this property, and the miss
    is the section 58 failure arriving through tokenisation."""
    found = ontology.mentions("We replaced the graph database layer last year.")
    assert "graph database" in found


def test_a_term_inside_a_longer_word_is_not_a_mention() -> None:
    """The load-bearing case. "rag" is in this table and is a substring of
    "storage"; a plain `in` finds retrieval-augmented generation in a sentence
    about disks, and nothing downstream could tell."""
    assert "rag" not in ontology.mentions(
        "Moved cold storage to a cheaper tier and cut average fragment size."
    )


def test_a_real_mention_after_a_false_one_is_still_found() -> None:
    """The scan does not stop at the first substring hit that failed the
    boundary check; it keeps going. Otherwise one earlier "storage" would hide
    every genuine mention later in the same resume."""
    assert "rag" in ontology.mentions("Cold storage first, then a RAG pipeline.")


def test_empty_text_mentions_nothing() -> None:
    assert ontology.mentions("") == frozenset()
    assert ontology.mentions("   ") == frozenset()


# ── Matching, the pre-screen question ────────────────────────────────────────


def test_a_requirement_is_met_by_the_other_word_for_the_same_work() -> None:
    assert ontology.matches("graph database", "Built the semantic technologies layer.")


def test_a_multi_word_requirement_needs_both_halves() -> None:
    """`all`, never `any`. One common word carrying a whole requirement is a
    matcher that says yes to everything, which is not fairer than one that says
    no to everything -- just wrong where it is harder to notice."""
    assert not ontology.matches(
        "stakeholder management", "Line management of a team of six."
    )
    assert ontology.matches(
        "stakeholder management",
        "Owned stakeholder relationships and the management of the rollout.",
    )


def test_a_requirement_made_only_of_noise_words_matches_nothing() -> None:
    """"Strong experience" is not a requirement; it is an adjective and a noun
    the stop list already drops. Matching it against anything would let a JD
    written in adjectives grade every candidate as meeting it."""
    assert not ontology.matches("strong experience", "Wrote a compiler.")


def test_an_empty_requirement_or_an_empty_text_never_matches() -> None:
    """Both directions. An unparsed resume must read as no evidence rather than
    as evidence of everything asked for."""
    assert not ontology.matches("", "Built the ingestion pipeline.")
    assert not ontology.matches("graph database", "")


# ── Overlap ──────────────────────────────────────────────────────────────────


def test_two_words_for_one_skill_overlap() -> None:
    """A raw set intersection scores this zero, which measures which words
    somebody was trained to use rather than what they can do."""
    assert ontology.overlap(["graph database"], ["semantic technologies"])


def test_unrelated_skills_do_not_overlap() -> None:
    assert ontology.overlap(["kubernetes"], ["wafer fab metrology"]) == frozenset()


def test_overlapping_with_nothing_is_empty_rather_than_an_error() -> None:
    assert ontology.overlap([], ["kubernetes"]) == frozenset()
    assert ontology.overlap(["kubernetes"], []) == frozenset()
