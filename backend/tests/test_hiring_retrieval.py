"""Yukti's ontology.

Both exist for the same reason and it is the one spec-doc5 states about the
ontology: vocabulary mismatch is a FAIRNESS problem, not only a quality one.
Vocabulary is not randomly distributed -- it tracks employer, country and
training -- so a retrieval layer that scores "semantic technologies" below
"graph database" is measuring which words somebody was taught, not what they can
do.

Vaada's Department Evidence Graph is the same idea applied to the conversation
and is tested in `test_vaada_live.py`, beside the live path that reads it.
"""
from __future__ import annotations

import pytest

from app.services.hiring import ontology


# ── The ontology ─────────────────────────────────────────────────────────────


def test_the_two_examples_spec_doc5_names_are_covered() -> None:
    """spec-doc5 gives both literally: "graph database" / "semantic
    technologies", and "GD&T" / "geometric tolerancing"."""
    assert "semantic technologies" in ontology.equivalent("graph database")
    assert "graph database" in ontology.equivalent("semantic technologies")
    assert "geometric tolerancing" in ontology.equivalent("gd&t")
    assert "gd&t" in ontology.equivalent("geometric tolerancing")


def test_equivalence_is_symmetric() -> None:
    for group in ontology.EQUIVALENCE_GROUPS:
        members = {ontology.normalise(term) for term in group}
        for term in members:
            assert members <= ontology.equivalent(term), term


def test_expansion_is_additive_and_never_substitutive() -> None:
    """Replacing "GD&T" with "geometric tolerancing" would stop matching the
    candidates who wrote "GD&T" -- the identical failure pointed the other
    way."""
    expanded = ontology.expand(["gd&t"])
    assert "gd&t" in expanded
    assert "geometric tolerancing" in expanded


def test_the_input_terms_come_first_so_truncation_keeps_what_was_asked() -> None:
    expanded = ontology.expand(["kubernetes", "kafka"])
    assert expanded[:2] == ["kubernetes", "kafka"]


def test_an_unknown_term_stands_on_its_own_rather_than_vanishing() -> None:
    """A caller unioning results must never have a term disappear because the
    ontology had not heard of it."""
    assert ontology.equivalent("quantum basket weaving") == frozenset(
        {"quantum basket weaving"}
    )
    assert "quantum basket weaving" in ontology.expand(["quantum basket weaving"])


def test_a_term_in_two_groups_gets_the_union_not_the_last_write() -> None:
    """Silently taking one group would make expansion depend on table order."""
    counts: dict[str, int] = {}
    for group in ontology.EQUIVALENCE_GROUPS:
        for term in group:
            key = ontology.normalise(term)
            counts[key] = counts.get(key, 0) + 1
    shared = [term for term, count in counts.items() if count > 1]
    for term in shared:
        expanded = ontology.equivalent(term)
        for group in ontology.EQUIVALENCE_GROUPS:
            members = {ontology.normalise(t) for t in group}
            if term in members:
                assert members <= expanded, term


def test_separators_are_collapsed_but_words_are_not_joined() -> None:
    """"ci/cd", "ci-cd" and "ci cd" must agree; "cicd" -- which nobody writes --
    must not be manufactured."""
    assert ontology.normalise("CI/CD") == ontology.normalise("ci-cd")
    assert ontology.normalise("CI CD") == ontology.normalise("ci/cd")
    assert ontology.normalise("ci/cd") != "cicd"


def test_the_fairness_case_scores_as_an_overlap() -> None:
    """THE point of the module. A candidate who wrote "semantic technologies"
    against a JD asking for "graph database" currently scores zero overlap; the
    correct answer is one."""
    matched = ontology.overlap(
        ["graph database", "kubernetes"], ["semantic technologies", "k8s"]
    )
    assert len(matched) == 2


def test_an_honest_non_match_stays_a_non_match() -> None:
    """The ontology must not manufacture overlap. A near-miss is worse than an
    absence: an absence costs a candidate a little ranking, a near-miss credits
    them with something they did not do."""
    assert ontology.overlap(["kubernetes"], ["financial reporting"]) == frozenset()


def test_data_science_is_not_folded_into_data_engineering() -> None:
    """A specific near-miss worth naming: they are different jobs, and a table
    that conflated them would credit a data scientist with pipeline work."""
    assert "data science" not in ontology.equivalent("data engineering")


def test_canonical_is_for_display_only() -> None:
    """Canonicalising on the way IN would rewrite what a candidate actually
    wrote, and an answer is never re-worded in this product."""
    assert ontology.canonical("k8s") == "container orchestration"
    # The original still expands, so nothing depends on having been rewritten.
    assert "k8s" in ontology.expand(["k8s"])


def test_no_group_has_a_duplicate_member() -> None:
    for group in ontology.EQUIVALENCE_GROUPS:
        normalised = [ontology.normalise(t) for t in group]
        assert len(normalised) == len(set(normalised)), group
