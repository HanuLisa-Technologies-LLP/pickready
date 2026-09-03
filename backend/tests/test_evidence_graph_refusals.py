"""Every place the evidence graph refuses, and why refusing is the product.

`test_runbook_reconciliation` checks that the graph's VALUES match the Runbook.
This file checks the other half, which no value comparison can reach: what
happens when a lookup finds nothing.

THE REFUSALS ARE THE FEATURE. Each one below has a plausible-looking fallback
that would make the module quieter and the product wrong:

  * A department nobody can resolve could be graded against the nearest menu.
    That produces a plausible grade against the wrong function, and NOTHING
    DOWNSTREAM CAN DETECT IT -- there is no inconsistency to find. So a near
    tie is refused as firmly as no match at all.
  * A competency the Part VI menu has no row for could take the closest row.
    That relabels a role-specific item as something the menu already knew
    about, which looks like traceability and is not. None is the honest answer,
    and the caller falls back to the item's own observable evidence.
  * Sutra's matrix being unreadable could return "no sources required", which
    is a silent fallback wearing a successful return value: every competency in
    the product would report that nothing needs corroborating.
  * A corroboration group the platform cannot reach could be dropped. Dropping
    it treats a well-argued answer as corroborated; returning it is what lets
    Miti hold confidence down for the right reason.

Pure functions plus one async lookup. No network and no model.
"""
from __future__ import annotations

import pytest

from app.services.hiring import evidence_graph, layers


# ── Resolving a department ───────────────────────────────────────────────────


def test_a_clear_role_resolves_to_one_department() -> None:
    """The happy path, so the refusals below mean something."""
    resolved = evidence_graph.resolve_department(
        "Senior Software Engineer", "Engineering", "Builds backend services."
    )
    assert resolved in evidence_graph.department_keys()


def test_the_same_hints_always_resolve_the_same_way() -> None:
    """Deterministic and pure, which is what lets two candidates on one job be
    probed against the same graph."""
    hints = ("Mechanical Design Engineer", "Product engineering")
    assert evidence_graph.resolve_department(*hints) == evidence_graph.resolve_department(
        *hints
    )


def test_no_hints_at_all_is_refused() -> None:
    with pytest.raises(evidence_graph.DepartmentUnmapped):
        evidence_graph.resolve_department()


def test_blank_and_none_hints_are_refused_like_no_hints() -> None:
    """A caller passing three empty strings has supplied nothing, and treating
    that as a resolvable role would grade against whichever department happens
    to sort first."""
    with pytest.raises(evidence_graph.DepartmentUnmapped):
        evidence_graph.resolve_department(None, "", "   ")


def test_a_role_matching_no_department_is_refused_by_name() -> None:
    """Section 36 has a procedure for adding a department. Grading against a
    menu written for another function is not it."""
    with pytest.raises(evidence_graph.DepartmentUnmapped) as excinfo:
        evidence_graph.resolve_department("Zamboni")
    assert "Zamboni" in str(excinfo.value)


def test_a_title_carrying_only_a_generic_word_is_refused() -> None:
    """"Engineer" alone names no function. Resolving it would grade a role
    against whichever of the fifteen menus happens to claim the word."""
    for title in ("Engineer", "Manager", "Analyst"):
        with pytest.raises(evidence_graph.DepartmentUnmapped):
            evidence_graph.resolve_department(title)


def test_a_near_tie_is_refused_rather_than_broken() -> None:
    """The branch that matters most. A coin toss between two departments
    produces a plausible grade against the wrong menu, and there is no
    inconsistency downstream for anything to detect. The refusal names both
    candidates so a human can settle it."""
    with pytest.raises(evidence_graph.DepartmentUnmapped) as excinfo:
        evidence_graph.resolve_department("Product Design Manager")
    message = str(excinfo.value)
    assert "does not separate" in message
    named = [key for key in evidence_graph.department_keys() if key in message]
    assert len(named) >= 2, message


# ── Reading the graph ────────────────────────────────────────────────────────


def test_every_declared_department_has_a_graph_with_nodes() -> None:
    keys = evidence_graph.department_keys()
    assert len(keys) == 15, "Part VI has fifteen department models"
    for key in keys:
        assert evidence_graph.nodes_for(key), key


def test_an_unknown_department_key_has_no_graph() -> None:
    """Returning an empty graph would report that the department requires no
    evidence, which reads as a clean result."""
    with pytest.raises(evidence_graph.DepartmentUnmapped):
        evidence_graph.graph_for("not_a_department")


def test_every_node_carries_the_words_a_probe_is_written_from() -> None:
    key = evidence_graph.department_keys()[0]
    for node in evidence_graph.nodes_for(key):
        assert node.competency_id
        assert node.name
        assert node.key


# ── Matching a competency to a node ──────────────────────────────────────────


def test_a_competency_named_exactly_finds_its_node() -> None:
    key = evidence_graph.department_keys()[0]
    node = evidence_graph.nodes_for(key)[0]
    assert (
        evidence_graph.node_for_competency(node.competency_id, key) is node
        or evidence_graph.node_for_competency(node.name, key) is node
    )


def test_a_competency_the_menu_has_no_row_for_returns_none() -> None:
    """None is a real answer. The caller falls back to the matrix item's own
    observable-evidence statement, which Sutra's stage 2 guaranteed exists."""
    key = evidence_graph.department_keys()[0]
    assert (
        evidence_graph.node_for_competency("Zamboni resurfacing cadence", key) is None
    )


def test_an_empty_competency_returns_none_rather_than_the_first_node() -> None:
    key = evidence_graph.department_keys()[0]
    assert evidence_graph.node_for_competency("", key) is None
    assert evidence_graph.node_for_competency("   ", key) is None


# ── Corroboration, including what the platform cannot reach ──────────────────


def test_a_group_the_platform_cannot_reach_is_reported_not_dropped() -> None:
    """The out-of-band list is what Miti reads as a reason to hold confidence
    down. Dropping it would treat a well-argued answer as corroborated."""
    key = evidence_graph.department_keys()[0]
    for node in evidence_graph.nodes_for(key):
        inside, outside = evidence_graph.corroboration_targets(node)
        assert set(inside) | set(outside) == set(node.corroborated_by), node.name
        for group in inside:
            assert group in evidence_graph.REACHABLE_GROUPS


def test_the_self_written_group_is_never_its_own_corroboration() -> None:
    """Section 38.1 source 1. A candidate's claim corroborating itself is the
    manufactured-corroboration failure in its simplest form."""
    assert evidence_graph.SELF_WRITTEN_GROUP not in evidence_graph.REACHABLE_GROUPS


def test_the_reachable_set_can_be_narrowed_by_the_caller() -> None:
    """A conversation that has not started yet reaches fewer groups than one
    that has."""
    key = evidence_graph.department_keys()[0]
    node = next(
        (n for n in evidence_graph.nodes_for(key) if n.corroborated_by), None
    )
    assert node is not None
    inside, outside = evidence_graph.corroboration_targets(
        node, available_sources=()
    )
    assert inside == []
    assert list(outside) == list(node.corroborated_by)


# ── 38.3's gradient ──────────────────────────────────────────────────────────


def test_the_gradient_climbs_and_then_exhausts() -> None:
    """"until either the candidate demonstrates participatory knowledge or the
    probe exhausts". The exhaustion is what makes probing one claim provably
    finite."""
    levels = evidence_graph.specificity_levels()
    reached = 0
    seen = 0
    while (nxt := evidence_graph.next_specificity_level(reached)) is not None:
        assert nxt.level > reached
        reached = nxt.level
        seen += 1
        assert seen <= len(levels) + 1, "the gradient did not terminate"
    assert seen == len(levels)
    assert evidence_graph.next_specificity_level(levels[-1].level) is None


def test_the_session_extension_is_the_gradient_and_not_a_typed_number() -> None:
    """The Runbook states no conversation length anywhere. The ceiling is
    DERIVED from the one thing it does bound."""
    assert evidence_graph.extension_ceiling() == len(
        evidence_graph.specificity_levels()
    )


def test_no_probe_opens_at_the_bottom_rung() -> None:
    """"What did you do?" is answerable by anyone, which 38.3 says in as many
    words, and the resume already answered it."""
    bottom = evidence_graph.specificity_levels()[0].level
    for ordinal in range(20):
        assert evidence_graph.probe_level(ordinal=ordinal).level > bottom, ordinal


def test_the_discriminator_share_is_met_at_every_realistic_length() -> None:
    """CEILING, NOT FLOOR. With a floor, sixteen questions get six
    discriminators and 6/16 is 0.375, so "at least 40%" fails on exactly the
    interview lengths this product uses."""
    fraction = evidence_graph.minimum_discriminator_fraction()
    discriminators = set(evidence_graph.discriminator_levels())
    for length in (10, 12, 15, 16, 17, 20, 22, 25):
        opened = [
            evidence_graph.probe_level(ordinal=i).level for i in range(length)
        ]
        share = sum(1 for level in opened if level in discriminators) / length
        assert share >= fraction - 1e-9, (length, share)


def test_a_repeat_probe_of_one_item_sits_above_the_first() -> None:
    """"A claim is probed at increasing specificity"."""
    first = evidence_graph.probe_level(ordinal=0, prior_substantive=0)
    second = evidence_graph.probe_level(ordinal=0, prior_substantive=1)
    assert second.level >= first.level


def test_the_climb_stops_at_the_top_rather_than_running_off_the_gradient() -> None:
    top = evidence_graph.specificity_levels()[-1].level
    assert (
        evidence_graph.probe_level(ordinal=3, prior_substantive=99).level == top
    )


def test_a_negative_position_is_read_as_the_first_one() -> None:
    """Total, so a caller's off-by-one cannot produce an index error in the
    middle of a live conversation."""
    assert evidence_graph.probe_level(ordinal=-5).level == evidence_graph.probe_level(
        ordinal=0
    ).level


def test_a_gradient_with_no_discriminators_is_refused(monkeypatch) -> None:
    """Levels 4 and 5 are the mechanism: they are what a generative model
    produces generically and a participant produces specifically. Without them
    the instrument cannot discriminate and must say so."""
    flat = tuple(
        evidence_graph.SpecificityLevel(
            level=level.level,
            question=level.question,
            answerable_by=level.answerable_by,
            discriminating=False,
        )
        for level in evidence_graph.specificity_levels()
    )
    monkeypatch.setattr(evidence_graph, "specificity_levels", lambda: flat)
    with pytest.raises(layers.RunbookDataUnavailable):
        evidence_graph.probe_level(ordinal=0)


def test_a_gradient_the_data_does_not_carry_is_refused(monkeypatch) -> None:
    """Section 38.3's five levels are not restated in code, so an absent table
    cannot be defaulted around."""
    evidence_graph.specificity_levels.cache_clear()
    monkeypatch.setattr(
        evidence_graph.layers, "runbook_value", lambda *path: {"levels": []}
    )
    try:
        with pytest.raises(layers.RunbookDataUnavailable):
            evidence_graph.specificity_levels()
    finally:
        evidence_graph.specificity_levels.cache_clear()


def test_independence_groups_are_refused_when_absent(monkeypatch) -> None:
    evidence_graph.independence_groups.cache_clear()
    monkeypatch.setattr(
        evidence_graph.layers, "runbook_value", lambda *path: None
    )
    try:
        with pytest.raises(layers.RunbookDataUnavailable):
            evidence_graph.independence_groups()
    finally:
        evidence_graph.independence_groups.cache_clear()


def test_the_six_independence_groups_are_read_from_the_data() -> None:
    evidence_graph.independence_groups.cache_clear()
    groups = evidence_graph.independence_groups()
    assert len(groups) == 6, "section 38.1 states six"
    assert evidence_graph.SELF_WRITTEN_GROUP in groups


# ── The four classes of question ─────────────────────────────────────────────


def test_a_contradiction_outranks_everything() -> None:
    """An item with evidence on both sides is the most interesting one in the
    conversation and the easiest to lose behind a rule that lets support
    outweigh disagreement."""
    assert (
        evidence_graph.question_class(
            conflicting=True, claim_present=True, substantive_answers=3, answers=3
        )
        == evidence_graph.CLASS_CONTRADICTION
    )


def test_an_item_already_answered_substantively_is_confirmed() -> None:
    assert (
        evidence_graph.question_class(substantive_answers=1)
        == evidence_graph.CLASS_CONFIRMATION
    )


def test_a_claim_with_nothing_substantive_behind_it_is_a_gap() -> None:
    assert (
        evidence_graph.question_class(claim_present=True)
        == evidence_graph.CLASS_GAP
    )
    assert evidence_graph.question_class(answers=2) == evidence_graph.CLASS_GAP


def test_a_silent_profile_is_asked_what_it_has_done() -> None:
    """THE CLASS THAT PROTECTS THE UNCONVENTIONAL CANDIDATE. Asking a gap
    question of a silent profile establishes only that the profile is silent,
    which is what an ATS already concluded. Getting this backwards reproduces
    ATS bias."""
    assert evidence_graph.question_class() == evidence_graph.CLASS_DISCOVERY


def test_every_class_returned_is_one_the_module_declares() -> None:
    for conflicting in (False, True):
        for claim_present in (False, True):
            for substantive in (0, 1):
                for answers in (0, 1):
                    assert (
                        evidence_graph.question_class(
                            conflicting=conflicting,
                            claim_present=claim_present,
                            substantive_answers=substantive,
                            answers=answers,
                        )
                        in evidence_graph.QUESTION_CLASSES
                    )


# ── Sutra's routing, which raises rather than reporting "nothing required" ────


@pytest.mark.asyncio
async def test_no_session_means_the_matrix_is_unreadable_and_says_so() -> None:
    """Not an empty tuple. "No sources required" for every competency in the
    product is a silent fallback wearing a successful return value."""
    with pytest.raises(evidence_graph.ScorecardUnavailable) as excinfo:
        await evidence_graph.required_evidence_sources(None, "job-1", "Kafka")
    assert "Kafka" in str(excinfo.value)


@pytest.mark.asyncio
async def test_a_matrix_that_will_not_read_is_raised_under_one_class(
    monkeypatch,
) -> None:
    """Includes G1 refusing. Re-raised under this module's own class, with the
    original attached, so the caller catches ONE thing and an operator still
    reads which one it was. G1 is NOT re-enforced here: a copy of a rule is the
    copy that gets forgotten."""
    from app.services.hiring import scorecard

    class _Refused(RuntimeError):
        pass

    async def _refuse(session, job_id):
        raise _Refused("not frozen")

    monkeypatch.setattr(scorecard, "require_frozen_matrix", _refuse)
    with pytest.raises(evidence_graph.ScorecardUnavailable) as excinfo:
        await evidence_graph.required_evidence_sources(object(), "job-1", "Kafka")
    assert "_Refused" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, _Refused)


@pytest.mark.asyncio
async def test_a_competency_the_matrix_does_not_carry_requires_nothing(
    monkeypatch,
) -> None:
    """Distinct from the unreadable case above, which is the whole point of
    raising there: an empty tuple here means the matrix was read and this
    competency is not in it."""
    from app.services.hiring import scorecard

    class _Item:
        competency = "Stream processing"
        evidence_sources = ("assessment",)

    class _Matrix:
        items = (_Item(),)

    async def _read(session, job_id):
        return _Matrix()

    monkeypatch.setattr(scorecard, "require_frozen_matrix", _read)
    assert (
        await evidence_graph.required_evidence_sources(
            object(), "job-1", "Origami"
        )
        == ()
    )
    assert await evidence_graph.required_evidence_sources(
        object(), "job-1", "  stream   PROCESSING "
    ) == ("assessment",)
