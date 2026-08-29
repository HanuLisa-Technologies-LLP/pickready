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


# ── Parameter contract ───────────────────────────────────────────────────────

def test_the_four_parameters_are_the_contract():
    assert PARAMETERS == (
        "skills_match", "experience_relevance", "role_alignment", "education_fit",
    )


def test_no_weighting_module_survives():
    """The four parameters carry NO mathematical weightage (spec 2026-07-30).

    Asserted by absence: a reintroduced WEIGHTS table is exactly how the old
    "35% role-fit weighting" copy would come back.
    """
    import app.services.matching as matching

    assert not hasattr(matching, "WEIGHTS")


# ── Unweighted mean ──────────────────────────────────────────────────────────

def test_all_tens_is_ten():
    assert compute_overall_score(scores(10, 10, 10, 10)) == 10.0


def test_all_ones_is_one():
    assert compute_overall_score(scores(1, 1, 1, 1)) == 1.0


def test_uniform_scores_pass_through():
    assert compute_overall_score(scores(5, 5, 5, 5)) == 5.0
    assert compute_overall_score(scores(9, 9, 9, 9)) == 9.0


def test_one_high_parameter_moves_the_mean_by_its_own_share():
    # Unweighted: raising one parameter from 1 to 10 adds 9/4 = 2.25 to the
    # mean of 1.0. The nearest double rounds to 3.2 at one decimal.
    assert compute_overall_score(scores(10, 1, 1, 1)) == 3.2


def test_every_parameter_moves_the_overall_identically():
    """No parameter outranks another (spec 2026-07-30). This is the assertion
    that would have caught the old 0.35/0.30/0.20/0.15 table."""
    base = compute_overall_score(scores(1, 1, 1, 1))
    deltas = {
        "skills_match": compute_overall_score(scores(10, 1, 1, 1)) - base,
        "experience_relevance": compute_overall_score(scores(1, 10, 1, 1)) - base,
        "role_alignment": compute_overall_score(scores(1, 1, 10, 1)) - base,
        "education_fit": compute_overall_score(scores(1, 1, 1, 10)) - base,
    }
    assert len({round(value, 6) for value in deltas.values()}) == 1


def test_mixed_scores_round_to_one_decimal():
    assert compute_overall_score(scores(8, 7, 9, 6)) == 7.5
    # The same four numbers in any order now give the same overall, which is
    # precisely what "no weightage" means.
    assert compute_overall_score(scores(7, 8, 6, 9)) == 7.5


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
    # Exactly 90 is Highly Matching, inclusive upward (claude.md rule 8).
    assert assign_tier(match_score) == Tier.highly_matching


def test_tier_mapping_below_boundaries():
    """The 90 / 75 / 60 cut-points of `services/rating.py`, reached through the
    x10 conversion the AI Score applies to a 1-10 parameter mean.

    This block asserted 90 / 70 / 50 with `matching` and `moderately_matching`
    transposed, which is how `services/tiers.py` kept a third, inverted scale
    through a full green suite. `tests/test_grade_scale_consistency.py` now
    sweeps the whole range; these named points stay for readability.
    """
    assert assign_tier(round(8.9 * 10, 1)) == Tier.matching               # 89
    assert assign_tier(round(7.5 * 10, 1)) == Tier.matching               # 75 boundary
    assert assign_tier(round(7.4 * 10, 1)) == Tier.moderately_matching    # 74
    assert assign_tier(round(6.0 * 10, 1)) == Tier.moderately_matching    # 60 boundary
    assert assign_tier(round(5.9 * 10, 1)) == Tier.not_matching           # 59
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
    ) == 7.5
    assert breakdown["overall"]["comment"] == (
        "A credible fit overall with minor education gaps."
    )
    for param in PARAMETERS:
        assert breakdown[param]["score"] == _entry()[param]["score"]
        assert isinstance(breakdown[param]["comment"], str)


def test_validate_entry_ignores_llm_supplied_overall_score():
    # Even if the LLM volunteers an overall block, the Python-computed mean
    # wins — the LLM's number is never trusted.
    breakdown = _validate_entry(_entry(overall={"score": 1, "comment": "lies"}))
    assert breakdown is not None
    assert breakdown["overall"]["score"] == 7.5
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
