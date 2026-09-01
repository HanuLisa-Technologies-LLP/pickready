"""What the live path refuses when the shape it was handed is not the shape.

THIS IS THE MODULE THAT PUT PART A ON THE LIVE PATH. Before it, every gate was
real and every gate guarded nothing: the whole framework was reachable from one
script and from no route or worker. So the contract checks here are the ones
standing between an approved scorecard and a candidate's report, and each has a
quieter wrong answer available to it.

G1 ASKS THE TABLE FIRST AND THE STAMP SECOND, in that order, because this
codebase has already paid for believing a timestamp: nineteen of thirty-five
live jobs carried a generation stamp and zero competency rows, and every health
check asked the stamp. Both are checked here, and a matrix that can supply
neither name for the stamp is reported as a CONTRACT MISMATCH rather than as an
unapproved scorecard -- two different problems for two different people.

THE `items` SHAPE IS CHECKED RATHER THAN ASSUMED. `items` on a Mapping is a
method and on a FrozenMatrix is a sequence. Calling one and indexing the other
are both wrong, and the failure of assuming would be a matrix that iterates as
zero items and a gate that passes.

A GRADE IS NEVER WRITTEN FROM EVIDENCE NOBODY READ. A ledger row whose text
could not be fetched is EXCLUDED and NAMED, not passed along as an empty
string. Excluding it lowers coverage, lowers confidence and can trip section
14.1 -- all visible. Passing an empty excerpt would be invisible.

Pure functions over duck-typed stubs. No database, no network, no model.
"""
from __future__ import annotations

import pytest

from app.services.miti import live, pipeline


class _Item:
    def __init__(self, competency="Stream processing", category="must_have"):
        self.competency = competency
        self.category = category
        self.dimension = "verified_competence"
        self.assessment_method = "structured_probe"


class _Matrix:
    def __init__(self, items=(), approved_at="2026-08-01", frozen_at=None):
        self.items = list(items)
        if approved_at is not None:
            self.approved_at = approved_at
        if frozen_at is not None:
            self.frozen_at = frozen_at


# ── The matrix's items ───────────────────────────────────────────────────────


def test_a_real_matrix_yields_its_items() -> None:
    items = [_Item(), _Item("Ownership", "behavioural")]
    assert live._matrix_items(_Matrix(items)) == items


def test_a_matrix_with_no_items_attribute_is_refused() -> None:
    class _Nothing:
        pass

    with pytest.raises(live.ScorecardUnavailable) as excinfo:
        live._matrix_items(_Nothing())
    assert "nothing to score" in str(excinfo.value)


def test_a_mapping_passed_where_a_matrix_belongs_is_refused() -> None:
    """The load-bearing shape check. A dict's `items` is a METHOD: iterating it
    without calling it yields nothing, so the matrix would read as empty and
    G1 would pass on a scorecard with no criteria in it."""
    with pytest.raises(live.ScorecardUnavailable) as excinfo:
        live._matrix_items({"Stream processing": "must_have"})
    assert "callable" in str(excinfo.value)


def test_an_empty_but_well_shaped_matrix_is_not_a_contract_error() -> None:
    """Zero items is a scorecard problem for G1 to answer, not a shape problem.
    Conflating them would report a configuration error as a bug."""
    assert live._matrix_items(_Matrix([])) == []


# ── G1's second question ─────────────────────────────────────────────────────


def test_either_name_for_the_stamp_is_accepted() -> None:
    assert live._approved_at(_Matrix(approved_at="2026-08-01")) == "2026-08-01"
    assert live._approved_at(
        _Matrix(approved_at=None, frozen_at="2026-08-02")
    ) == "2026-08-02"


def test_approved_at_wins_when_both_are_present() -> None:
    """One order, stated once, so two matrices with both stamps cannot disagree
    about which one G1 read."""
    matrix = _Matrix(approved_at="2026-08-01", frozen_at="2026-08-02")
    assert live._approved_at(matrix) == "2026-08-01"


def test_an_unapproved_matrix_reads_as_unapproved_rather_than_broken() -> None:
    """A present-but-null stamp is a draft. That is G1's own answer and it must
    not be dressed up as a contract mismatch."""
    class _Draft:
        approved_at = None
        frozen_at = None

    with pytest.raises(live.ScorecardUnavailable):
        live._approved_at(_Draft())


def test_a_matrix_carrying_neither_name_says_it_is_a_contract_mismatch() -> None:
    """Different problem, different person. "Gate G1 cannot tell an approved
    scorecard from a draft" goes to an engineer; "this scorecard is not
    approved" goes to a recruiter."""
    class _Neither:
        pass

    with pytest.raises(live.ScorecardUnavailable) as excinfo:
        live._approved_at(_Neither())
    assert "neither" in str(excinfo.value)


# ── Competency categories ────────────────────────────────────────────────────


def test_categories_are_read_off_the_items() -> None:
    """FROM THE ITEM, never derived from the dimension. Keying the composite on
    a dimension table once produced an empty Must-have grade with nothing for
    the hard cap to bind against."""
    categories = live._competency_categories(
        [_Item("Stream processing", "must_have"), _Item("Ownership", "behavioural")]
    )
    assert categories == {
        "Stream processing": "must_have",
        "Ownership": "behavioural",
    }


def test_an_item_with_no_category_is_refused_by_name() -> None:
    """Section 14.1: a competency with no defined assessment route is a
    configuration rejected back to the recruiter, not something to guess at."""
    with pytest.raises(live.ScorecardUnavailable) as excinfo:
        live._competency_categories([_Item("Stream processing", "")])
    assert "section 14.1" in str(excinfo.value)


def test_an_item_with_no_competency_name_is_refused() -> None:
    with pytest.raises(live.ScorecardUnavailable):
        live._competency_categories([_Item("", "must_have")])


def test_no_items_at_all_is_an_empty_map_rather_than_a_refusal() -> None:
    assert live._competency_categories([]) == {}


# ── The role sentence, which says nothing about the person ───────────────────


class _Job:
    def __init__(self, title="Staff Engineer", grade="non_managerial"):
        self.title = title
        self.assessment_grade = grade


def test_the_role_sentence_carries_the_title_and_the_band() -> None:
    """Role and Context Fit is unanswerable without it, and the other four are
    sharper with it."""
    context = live._role_context(_Job())
    assert "Staff Engineer" in context
    assert "non managerial" in context


def test_the_band_is_spelled_for_a_reader_rather_than_as_a_key() -> None:
    assert "_" not in live._role_context(_Job(grade="non_managerial"))


def test_a_job_with_no_grade_still_produces_a_sentence() -> None:
    assert live._role_context(_Job(grade="")) == "Staff Engineer"


def test_a_job_with_nothing_on_it_produces_an_empty_sentence() -> None:
    """An empty string, not the word "None". A role line reading "None" would
    be handed to five evaluators as though it described the job."""
    assert live._role_context(_Job(title="", grade="")) == ""
    assert live._role_context(_Job(title=None, grade=None)) == ""


# ── Projecting an item for the gate ──────────────────────────────────────────


def test_an_item_that_knows_its_own_shape_is_asked() -> None:
    """The owning module knows its shape better than this one does."""
    class _Knows:
        def as_dict(self):
            return {"competency": "Stream processing", "category": "must_have"}

    assert live._as_dict(_Knows()) == {
        "competency": "Stream processing",
        "category": "must_have",
    }


def test_an_item_that_does_not_gets_a_minimal_projection() -> None:
    """Explicit fields rather than an empty dict, so a contract change on the
    other side does not silently produce empty items and a PASSING gate."""
    projected = live._as_dict(_Item())
    assert projected["competency"] == "Stream processing"
    assert projected["category"] == "must_have"
    assert projected["dimension"] == "verified_competence"


def test_an_item_with_nothing_on_it_projects_empty_strings_not_the_word_none() -> None:
    class _Bare:
        pass

    projected = live._as_dict(_Bare())
    assert set(projected.values()) == {""}


# ── Evidence that could not be read back ─────────────────────────────────────


def _evaluation(unresolved=(), aggregate=None) -> live.LiveEvaluation:
    outcome = pipeline.EvaluationOutcome()
    outcome.aggregate = aggregate
    return live.LiveEvaluation(
        outcome=outcome, unresolved_evidence=list(unresolved)
    )


def test_unreadable_evidence_is_named_in_the_review_reasons() -> None:
    """Named rather than dropped. An excluded piece of evidence lowers
    coverage, lowers confidence and can trip section 14.1, and a person reading
    the report has to be able to see why."""
    reasons = _evaluation(unresolved=["ref:1", "ref:2"]).review_reasons
    assert reasons
    assert any("could not be read back" in reason for reason in reasons)


def test_nothing_unreadable_adds_no_reason() -> None:
    assert _evaluation().review_reasons == []


def test_an_evaluation_with_no_aggregate_has_no_grade_and_does_not_raise() -> None:
    """A run blocked at G1. The caller asks `review_reasons` before it knows
    whether there is an aggregate, and raising here would turn a blocked
    assessment into a crash."""
    evaluation = _evaluation(unresolved=["ref:1"])
    assert evaluation.aggregate is None
    assert evaluation.review_reasons
