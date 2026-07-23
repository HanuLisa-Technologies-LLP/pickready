"""Pure unit tests for the 4-parameter weighted scoring math (Track B).

Covers: the weighted average (skills 0.35 / experience 0.30 / role 0.20 /
education 0.15), rounding to 1 decimal, LLM-score validation, and the
overall×10 → tier mapping including the inclusive-upward 90 boundary
(claude.md rule 8). No DB, no LLM — everything here is pure Python.

Rounding note: compute_overall_score rounds IEEE-754 doubles. Python 3.12's
sum() uses compensated (Neumaier) summation, so the pre-round value is the
correctly-rounded double of the exact decimal sum. Cases like 4.15 and 2.35
have nearest doubles slightly ABOVE the exact decimal, so they round UP to
4.2 / 2.4 — these are not round-half-even ties (no exact .X5 tie exists in
binary floats). The assertions below pin that exact behaviour.
"""
import pytest

from app.models.enums import Tier
from app.services.matching import (
    PARAMETERS,
    WEIGHTS,
    _coerce_param_score,
    _validate_entry,
    compute_overall_score,
)
from app.services.tiers import assign_tier


def scores(skills: int, experience: int, role: int, education: int) -> dict[str, int]:
    return {
        "skills_match": skills,
        "experience_relevance": experience,
        "role_alignment": role,
        "education_fit": education,
    }


# ── Weights contract ─────────────────────────────────────────────────────────

def test_weights_match_contract():
    assert WEIGHTS == {
        "skills_match": 0.35,
        "experience_relevance": 0.30,
        "role_alignment": 0.20,
        "education_fit": 0.15,
    }
    assert PARAMETERS == (
        "skills_match", "experience_relevance", "role_alignment", "education_fit",
    )


def test_weights_sum_to_one():
    assert round(sum(WEIGHTS.values()), 10) == 1.0


# ── Weighted average ─────────────────────────────────────────────────────────

def test_all_tens_is_ten():
    assert compute_overall_score(scores(10, 10, 10, 10)) == 10.0


def test_all_ones_is_one():
    assert compute_overall_score(scores(1, 1, 1, 1)) == 1.0


def test_uniform_scores_pass_through():
    assert compute_overall_score(scores(5, 5, 5, 5)) == 5.0
    assert compute_overall_score(scores(9, 9, 9, 9)) == 9.0


def test_skills_ten_others_one():
    # 10*.35 + 1*.30 + 1*.20 + 1*.15 = 4.15 exactly in decimal; the nearest
    # double is slightly above 4.15, so round(..., 1) gives 4.2.
    assert compute_overall_score(scores(10, 1, 1, 1)) == 4.2


def test_each_single_high_parameter_reflects_its_weight():
    assert compute_overall_score(scores(1, 10, 1, 1)) == 3.7   # 1 + 9*.30
    assert compute_overall_score(scores(1, 1, 10, 1)) == 2.8   # 1 + 9*.20
    # 2.35 in decimal — nearest double is slightly above, rounds to 2.4.
    assert compute_overall_score(scores(1, 1, 1, 10)) == 2.4   # 1 + 9*.15


def test_weight_ordering_skills_dominates():
    # Raising one parameter from 1 to 10 moves the overall by ~9×its weight —
    # the ordering skills > experience > role > education must hold (a
    # single-point step is invisible at 1-decimal rounding, so use the full
    # 1→10 swing).
    base = compute_overall_score(scores(1, 1, 1, 1))  # 1.0
    deltas = {
        "skills_match": compute_overall_score(scores(10, 1, 1, 1)) - base,        # 3.2
        "experience_relevance": compute_overall_score(scores(1, 10, 1, 1)) - base,  # 2.7
        "role_alignment": compute_overall_score(scores(1, 1, 10, 1)) - base,       # 1.8
        "education_fit": compute_overall_score(scores(1, 1, 1, 10)) - base,        # 1.4
    }
    assert (
        deltas["skills_match"]
        > deltas["experience_relevance"]
        > deltas["role_alignment"]
        > deltas["education_fit"]
    )


def test_mixed_scores_round_to_one_decimal():
    assert compute_overall_score(scores(8, 7, 9, 6)) == 7.6
    assert compute_overall_score(scores(7, 8, 6, 9)) == 7.4


def test_result_always_one_decimal():
    for a in range(1, 11):
        for b in range(1, 11):
            val = compute_overall_score(scores(a, b, 10 - min(9, b - 1), a))
            assert val == round(val, 1)
            assert 1.0 <= val <= 10.0


# ── overall×10 → tier mapping (boundary rule preserved) ─────────────────────

def test_overall_nine_is_highly_matching_boundary():
    overall = compute_overall_score(scores(9, 9, 9, 9))
    assert overall == 9.0
    match_score = round(overall * 10, 1)
    assert match_score == 90.0
    # Exactly 90 is Highly Matching — inclusive upward (claude.md rule 8).
    assert assign_tier(match_score) == Tier.highly_matching


def test_tier_mapping_below_boundaries():
    assert assign_tier(round(8.9 * 10, 1)) == Tier.moderately_matching   # 89
    assert assign_tier(round(7.0 * 10, 1)) == Tier.moderately_matching   # 70 boundary
    assert assign_tier(round(6.9 * 10, 1)) == Tier.matching              # 69
    assert assign_tier(round(5.0 * 10, 1)) == Tier.matching              # 50 boundary
    assert assign_tier(round(4.9 * 10, 1)) == Tier.not_matching          # 49
    assert assign_tier(round(compute_overall_score(scores(10, 10, 10, 10)) * 10, 1)) == Tier.highly_matching


# ── LLM output validation (scores must be ints 1-10) ────────────────────────

@pytest.mark.parametrize(
    "value,expected",
    [
        (1, 1), (10, 10), (7, 7),
        (8.0, 8),          # integral JSON float accepted
        (0, None), (11, None), (-3, None),
        (7.5, None),       # fractional rejected
        (True, None), (False, None),  # bools are not scores
        ("8", None), (None, None), ([8], None),
    ],
)
def test_coerce_param_score(value, expected):
    assert _coerce_param_score(value) == expected


def _entry(**overrides):
    entry = {
        "profile_id": "3f0e8a34-1111-4222-8333-444455556666",
        "skills_match": {"score": 8, "comment": "strong overlap"},
        "experience_relevance": {"score": 7, "comment": "same function"},
        "role_alignment": {"score": 9, "comment": "duties align"},
        "education_fit": {"score": 6, "comment": "adjacent degree"},
        "overall_comment": "A credible fit overall with minor education gaps.",
    }
    entry.update(overrides)
    return entry


def test_validate_entry_builds_breakdown_with_python_overall():
    breakdown = _validate_entry(_entry())
    assert breakdown is not None
    assert breakdown["overall"]["score"] == compute_overall_score(
        scores(8, 7, 9, 6)
    ) == 7.6
    assert breakdown["overall"]["comment"] == (
        "A credible fit overall with minor education gaps."
    )
    for param in PARAMETERS:
        assert breakdown[param]["score"] == _entry()[param]["score"]
        assert isinstance(breakdown[param]["comment"], str)


def test_validate_entry_ignores_llm_supplied_overall_score():
    # Even if the LLM volunteers an overall block, the Python-computed
    # weighted average wins — the LLM's number is never trusted.
    breakdown = _validate_entry(_entry(overall={"score": 1, "comment": "lies"}))
    assert breakdown is not None
    assert breakdown["overall"]["score"] == 7.6
    assert breakdown["overall"]["comment"] != "lies"


@pytest.mark.parametrize(
    "bad",
    [
        _entry(skills_match={"score": 0, "comment": "x"}),        # out of range
        _entry(skills_match={"score": 7.5, "comment": "x"}),      # fractional
        _entry(skills_match={"score": "8", "comment": "x"}),      # string score
        _entry(education_fit="9"),                                # not an object
        _entry(overall_comment=""),                               # empty holistic
        _entry(overall_comment=None),                             # missing holistic
        {k: v for k, v in _entry().items() if k != "role_alignment"},  # missing param
        "not a dict",
        None,
    ],
)
def test_validate_entry_rejects_malformed(bad):
    assert _validate_entry(bad) is None
