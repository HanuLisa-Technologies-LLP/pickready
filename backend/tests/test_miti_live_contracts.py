"""What the live path refuses when the contract it was handed is not provable.

REWRITTEN IN WP5-B (the Vivekium release). This file used to pin the helpers
that read a frozen Tatva matrix (`_matrix_items`, `_approved_at`,
`_competency_categories`, `_as_dict`). Miti no longer reads a matrix: gate G1
asks the LOCKED ASSESSMENT CONTRACT the conversation is bound to, through the
one read API. What survives is the shape of the questions, asked of the new
input:

G1 ASKS THE CONTRACT, NOT A STAMP. A contract is locked when a snapshot row
exists, and `locked_at` must be stated; an unlocked contract is the live rows,
which may move after a candidate answered. An empty contract, or one missing a
Must-have or a Behavioural skill, is refused by name.

A GRADE IS NEVER WRITTEN FROM EVIDENCE NOBODY READ, and a result that could
not be completed says so rather than raising from a property.

Pure functions over values. No database, no network, no model.
"""
from __future__ import annotations

from app.services import rating
from app.services.hiring import gates
from app.services.miti import aggregation, live, pipeline
from tests import miti_fixtures as mf

# ── G1 on the contract ───────────────────────────────────────────────────────


def test_a_locked_contract_with_both_required_buckets_passes_g1() -> None:
    result = gates.contract_gate(mf.contract())
    assert result.gate == gates.G1
    assert result.passed
    assert result.blocking


def test_no_contract_at_all_is_refused() -> None:
    result = gates.contract_gate(None)
    assert not result.passed and result.blocking


def test_an_unlocked_contract_is_refused() -> None:
    """The live rows may change after the candidate answered. Grading against
    them is grading against criteria nobody froze."""
    result = gates.contract_gate(mf.contract(locked=False))
    assert not result.passed
    assert any("not locked" in reason for reason in result.reasons)


def test_a_locked_contract_that_states_no_lock_time_is_refused() -> None:
    """`locked` and `locked_at` must agree; a snapshot that cannot say when it
    was frozen is not treated as one."""
    import dataclasses

    contract = dataclasses.replace(mf.contract(), locked_at=None)
    assert not gates.contract_gate(contract).passed


def test_an_empty_contract_is_refused_and_says_the_gate_read_the_contract() -> None:
    result = gates.contract_gate(mf.contract(()))
    assert not result.passed
    assert any("no skills" in reason for reason in result.reasons)


def test_a_contract_with_no_must_have_or_no_behavioural_is_refused_by_name() -> None:
    no_must = gates.contract_gate(mf.contract((("Ownership", "behavioural"),)))
    no_behaviour = gates.contract_gate(mf.contract((("Kafka", "must_have"),)))
    assert any("Must-have" in reason for reason in no_must.reasons)
    assert any("Behavioural" in reason for reason in no_behaviour.reasons)


# ── The role sentence ────────────────────────────────────────────────────────


class _Job:
    def __init__(self, title="Staff Engineer"):
        self.title = title


def test_the_role_sentence_carries_the_title_and_the_locked_grade() -> None:
    """Role and Context Fit is unanswerable without it. The band is the
    CONTRACT's grade, locked with the skills, never the job row's current
    value."""
    context = live._role_context(_Job(), "non_managerial")
    assert "Staff Engineer" in context
    assert "non managerial" in context


def test_the_band_is_spelled_for_a_reader_rather_than_as_a_key() -> None:
    assert "_" not in live._role_context(_Job(), "non_managerial")


def test_a_contract_with_no_grade_still_produces_a_sentence() -> None:
    assert live._role_context(_Job(), "") == "Staff Engineer"


def test_a_job_with_nothing_on_it_produces_an_empty_sentence() -> None:
    """An empty string, not the word "None"."""
    assert live._role_context(_Job(title=""), "") == ""
    assert live._role_context(_Job(title=None), "") == ""


# ── The result ───────────────────────────────────────────────────────────────


def _result(skills=None, unresolved=(), aggregate=None) -> live.MitiResult:
    outcome = pipeline.EvaluationOutcome()
    outcome.aggregate = aggregate
    return live.MitiResult(
        contract=mf.contract(),
        skills=tuple(skills if skills is not None else mf.strong_skills()),
        outcome=outcome,
        unresolved_evidence=list(unresolved),
    )


def test_unreadable_evidence_is_named_in_the_review_reasons() -> None:
    reasons = _result(unresolved=["ref:1", "ref:2"]).review_reasons
    assert any("could not be read back" in reason for reason in reasons)


def test_nothing_unreadable_adds_no_reason() -> None:
    assert _result().review_reasons == []


def test_a_result_with_no_aggregate_has_no_grade_and_does_not_raise() -> None:
    """A run stopped after the item stage. The caller asks `review_reasons`
    before it knows whether there is an aggregate."""
    result = live.MitiResult(contract=mf.contract(), skills=mf.strong_skills())
    assert result.aggregate is None
    assert result.review_reasons == []


def test_completeness_is_read_off_the_skill_statuses() -> None:
    complete = _result()
    incomplete = _result(skills=(mf.skill("Kafka"), mf.skill("Ownership", "behavioural", None)))
    assert complete.complete
    assert not incomplete.complete
    assert incomplete.not_assessed_skills == ["Ownership"]


def test_must_have_failed_is_the_one_predicate_and_ignores_not_assessed() -> None:
    """Graded or unanswered Not Matching on a Must-have is failed (O5-1); a
    Must-have Miti could not assess is NOT failed, because an outage is not a
    finding about a candidate."""
    assert _result(skills=(mf.unanswered("Kafka"),)).must_have_failed
    assert _result(skills=(mf.skill("Kafka", score=40),)).must_have_failed
    assert not _result(skills=(mf.skill("Kafka", score=None),)).must_have_failed
    assert not _result(skills=(mf.skill("Go", "nice_to_have", 40),)).must_have_failed


def test_the_result_names_its_contract_version_and_digest() -> None:
    result = _result()
    assert result.contract_version == result.contract.version
    assert result.contract_digest == result.contract.digest


def test_the_aggregate_agrees_with_the_result_about_a_failed_must_have() -> None:
    """Two readers of one rule: the aggregate's flag and the result's property
    must never disagree."""
    skills = (mf.unanswered("Kafka"), mf.skill("Ownership", "behavioural", 90))
    aggregate = aggregation.aggregate([], skill_grades=skills)
    assert aggregate.must_have_failed is _result(skills=skills).must_have_failed is True
    assert aggregate.overall_grade in (rating.GRADE_MODERATELY, rating.GRADE_NOT)


# ── G1 is stated once, in the gates module ───────────────────────────────────


def test_g1_for_grading_is_the_gates_modules_contract_gate() -> None:
    """One implementation per concept. The contract form of G1 lives beside
    G2 to G4 in `hiring/gates.py`; `run_gate(G1)` dispatches to it; and the
    Miti package defines no gate of its own that could drift from it."""
    import ast
    from pathlib import Path

    assert gates.run_gate(gates.G1, contract=mf.contract()).passed
    assert not gates.run_gate(gates.G1, contract=mf.contract(locked=False)).passed
    assert not hasattr(pipeline, "contract_gate")
    miti_root = Path(pipeline.__file__).parent
    for module in sorted(miti_root.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        defined = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert not {name for name in defined if name.endswith("_gate")}, module.name
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "GateResult":
                raise AssertionError(f"{module.name} builds a GateResult of its own")


# ── The bucket routing agrees with the contract's buckets ────────────────────


def test_every_contract_bucket_is_routed_to_exactly_one_evaluator() -> None:
    from app.services import assessment_contract
    from app.services.miti import dimensions

    assert set(dimensions.BUCKET_DIMENSION) == set(assessment_contract.BUCKETS)
    for bucket in assessment_contract.BUCKETS:
        assert dimensions.dimension_for_bucket(bucket) in dimensions.DIMENSIONS
        assert dimensions.dimension_for_bucket(bucket) not in dimensions.CROSS_CUTTING


# ── A withheld overall has no score to write ─────────────────────────────────


def test_a_withheld_overall_states_no_score() -> None:
    """`delivered_score` is the recorded working; `stated_score` is what a
    report may write, and it is None exactly when the overall is withheld."""
    graded = aggregation.aggregate([], skill_grades=mf.strong_skills())
    assert graded.overall_status == aggregation.OVERALL_GRADED
    assert graded.stated_score == graded.delivered_score

    withheld = aggregation.aggregate(
        [],
        skill_grades=(
            mf.skill("Kafka", "must_have", None),
            mf.skill("Go", "nice_to_have", 95),
            mf.skill("Ownership", "behavioural", 95),
        ),
    )
    assert withheld.overall_status == aggregation.OVERALL_NOT_ASSESSED
    assert withheld.overall_grade == ""
    assert withheld.delivered_score > 0, "the working is still recorded"
    assert withheld.stated_score is None
