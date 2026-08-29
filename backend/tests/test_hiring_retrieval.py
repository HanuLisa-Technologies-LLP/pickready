"""Yukti's ontology and Vaada's evidence graph.

Both exist for the same reason and it is the one spec-doc5 states about the
ontology: vocabulary mismatch is a FAIRNESS problem, not only a quality one.
Vocabulary is not randomly distributed -- it tracks employer, country and
training -- so a retrieval layer that scores "semantic technologies" below
"graph database" is measuring which words somebody was taught, not what they can
do.

The evidence graph is the same idea applied to the conversation: without a
coverage structure, a fluent interviewer can spend five questions establishing
one thing five ways and never ask what would corroborate it.
"""
from __future__ import annotations

import pytest

from app.services.hiring import evidence_graph, ontology
from app.services.hiring.department_models import DEPARTMENTS


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


# ── The evidence graph ───────────────────────────────────────────────────────


def test_every_department_has_a_graph() -> None:
    for key in DEPARTMENTS:
        assert evidence_graph.nodes_for(key), key


def test_every_node_points_at_a_real_competency() -> None:
    """A node serving a competency no department model has is a node nothing
    will ever reach."""
    for department, nodes in evidence_graph.GRAPHS.items():
        model = DEPARTMENTS[department]
        known = {c.key for c in model.competencies}
        for node in nodes:
            assert node.competency_key in known, (department, node.competency_key)


def test_every_node_names_a_hollow_tell_and_a_corroboration() -> None:
    for department, nodes in evidence_graph.GRAPHS.items():
        for node in nodes:
            assert node.establishes.strip(), (department, node.key)
            assert node.hollow_tell.strip(), (department, node.key)
            assert node.corroborated_by, (department, node.key)


def test_a_node_establishes_a_fact_rather_than_asking_a_question() -> None:
    """A question is what Vaada writes fresh, per candidate. Putting questions
    here would rebuild the preset bank this codebase deleted on 2026-08-06."""
    for nodes in evidence_graph.GRAPHS.values():
        for node in nodes:
            assert not node.establishes.strip().endswith("?"), node.key


def test_every_unlocked_node_exists_in_its_own_graph() -> None:
    """A dangling edge would silently never fire."""
    for department, nodes in evidence_graph.GRAPHS.items():
        keys = {node.competency_key for node in nodes}
        for node in nodes:
            for unlocked in node.unlocks:
                assert unlocked in keys, (department, node.key, unlocked)


def test_the_next_target_is_deterministic() -> None:
    """`interviewer` keeps its COVERAGE PLAN deterministic while letting the
    WORDS vary per candidate, because a fixed plan is what keeps two candidates
    on one job comparable. Same rule here."""
    matrix = ["systems_design", "production_ownership", "code_quality"]
    first = evidence_graph.next_target(department="engineering", matrix_keys=matrix)
    again = evidence_graph.next_target(department="engineering", matrix_keys=matrix)
    assert first is not None
    assert first.key == again.key


def test_the_next_target_respects_sutras_weights() -> None:
    matrix = ["systems_design", "code_quality"]
    default = evidence_graph.next_target(department="engineering", matrix_keys=matrix)
    weighted = evidence_graph.next_target(
        department="engineering",
        matrix_keys=matrix,
        weights={"code_quality": 5.0},
    )
    assert default is not None and weighted is not None
    assert default.key == "systems_design"
    assert weighted.key == "code_quality"


def test_an_established_node_is_not_targeted_again() -> None:
    matrix = ["systems_design", "production_ownership"]
    first = evidence_graph.next_target(department="engineering", matrix_keys=matrix)
    second = evidence_graph.next_target(
        department="engineering", matrix_keys=matrix, established=[first.key]
    )
    assert second is not None and second.key != first.key


def test_the_conversation_follows_the_graphs_edges() -> None:
    """Following the edges is the difference between an interview and a
    questionnaire."""
    matrix = ["systems_design", "production_ownership", "code_quality", "collaboration"]
    after_design = evidence_graph.next_target(
        department="engineering", matrix_keys=matrix, established=["systems_design"]
    )
    # `systems_design` unlocks `production_ownership`.
    assert after_design is not None
    assert after_design.key == "production_ownership"


def test_an_exhausted_graph_returns_none() -> None:
    matrix = ["systems_design"]
    assert (
        evidence_graph.next_target(
            department="engineering", matrix_keys=matrix, established=["systems_design"]
        )
        is None
    )


def test_a_competency_with_no_node_returns_none_not_a_substitute() -> None:
    """A role-specific competency has no graph node, and Vaada falls back to the
    matrix item's own observable-evidence statement -- which stage 2 guaranteed
    exists."""
    assert (
        evidence_graph.node_for_competency("speaks_japanese", "engineering") is None
    )


def test_out_of_band_corroboration_is_reported_rather_than_dropped() -> None:
    """A competency the assessment can probe and cannot confirm is one Miti
    should hold confidence down on. Saying so is more honest than treating a
    well-argued answer as corroborated."""
    node = evidence_graph.node_for_competency("people_leadership", "generic")
    assert node is not None
    reachable, out_of_band = evidence_graph.corroboration_targets(
        node, available_sources=["answer"]
    )
    assert any("reference" in target for target in out_of_band)


def test_no_available_sources_means_everything_is_out_of_band() -> None:
    node = evidence_graph.node_for_competency("core_craft", "generic")
    assert node is not None
    reachable, out_of_band = evidence_graph.corroboration_targets(node)
    assert reachable == []
    assert out_of_band
