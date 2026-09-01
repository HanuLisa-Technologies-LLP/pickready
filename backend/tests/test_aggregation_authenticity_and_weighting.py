"""The authenticity multiplier's edges, and what happens when weights fail.

THE AGGREGATOR IS THE STEP THAT TURNS FIVE BANDS INTO A GRADE A CLIENT READS,
and it makes no model call. Two runs over identical inputs producing different
grades would make a rubric problem indistinguishable from noise, so every
branch here is arithmetic and every one of them has a wrong answer that looks
reasonable.

NONE MEANS HOLD, NOT ZERO. Below section 10.5's floor the Runbook says "not
scored, mandatory human review". Returning a harsh multiplier instead would
deliver the candidate with a suppressed number, which is a quieter outcome than
the document asks for and hides the exact case it wants a person to look at.

A STAGE THAT DID NOT RUN IS NOT A FINDING. An absent authenticity result and an
insufficient-evidence one both yield 1.0 and say so in words, because
penalising a candidate for a stage that did not run makes an infrastructure
failure look like a conclusion about them.

WEIGHTS THAT FAILED TO DERIVE FALL BACK TO THE PLAIN MEAN, NOT TO ZERO. A
matrix with no usable weights should produce a slightly less discriminating
grade; falling to zero would produce Not Matching for everybody on that job.

Pure arithmetic over Runbook data. No database, no network, no model.
"""
from __future__ import annotations

import pytest

from app.services.miti import aggregation, caps, dimensions
from app.services.miti.dimensions import DimensionResult


def _result(band: str, *, insufficient: bool = False) -> DimensionResult:
    """`score` is DERIVED from the band, never set: a result whose number and
    whose word could disagree is one nobody could audit."""
    return DimensionResult(
        dimension="authenticity_consistency",
        band=band,
        evidence_refs=("ref:1",),
        rationale="",
        insufficient_evidence=insufficient,
    )


# ── The multiplier across the whole range ────────────────────────────────────


def test_the_top_of_the_range_never_exceeds_the_ceiling() -> None:
    """The outer cap is applied unconditionally and is not redundant: it makes
    a ceiling breach unreachable if anybody re-rounds a slope, which is a
    failure this branch has already had once."""
    table = caps.bands_data().get("authenticity_multiplier") or {}
    ceiling = float(table.get("caps_at", 1.0))
    for score in range(0, 101, 5):
        multiplier, _reason = aggregation.authenticity_multiplier_for_score(float(score))
        if multiplier is not None:
            assert multiplier <= ceiling, score


def test_the_multiplier_never_rises_as_the_score_falls() -> None:
    """Monotone, or a candidate could improve their consistency and be
    penalised for it."""
    seen = [
        aggregation.authenticity_multiplier_for_score(float(score))[0]
        for score in range(0, 101)
    ]
    graded = [value for value in seen if value is not None]
    assert graded == sorted(graded), "the multiplier is not monotone in the score"


def test_every_score_in_the_range_is_answered() -> None:
    """Total. A gap between two branches would leave a real candidate with no
    multiplier at all, and the caller has nothing to substitute."""
    for score in range(0, 101):
        multiplier, reason = aggregation.authenticity_multiplier_for_score(float(score))
        assert reason, score
        assert multiplier is None or multiplier > 0.0, score


def test_a_score_below_the_floor_is_held_rather_than_suppressed() -> None:
    """None, not 0.4. Returning a number would deliver the candidate with a
    quiet grade instead of routing them to a person."""
    multiplier, reason = aggregation.authenticity_multiplier_for_score(0.0)
    assert multiplier is None
    assert "human disposition" in reason or "floor" in reason


def test_a_clean_account_is_not_penalised() -> None:
    multiplier, reason = aggregation.authenticity_multiplier_for_score(100.0)
    assert multiplier is not None
    assert multiplier == pytest.approx(1.0)
    assert "internally consistent" in reason


def test_every_answer_carries_a_reason_a_person_can_read() -> None:
    """Both are returned so the reason is always recordable. A multiplier with
    no reason beside it is a number nobody can argue with."""
    for score in (0.0, 25.0, 50.0, 75.0, 100.0):
        _multiplier, reason = aggregation.authenticity_multiplier_for_score(score)
        assert reason.strip()
        assert not any(char.isdigit() for char in reason), (score, reason)


def test_a_table_that_covers_nothing_is_refused_by_name(monkeypatch) -> None:
    """No silent fallthrough. A section 10.5 table with no branch for a real
    score has to say so: substituting 1.0 would report an unevaluated
    authenticity check as a clean one."""
    monkeypatch.setattr(aggregation, "_authenticity_branches", lambda: [])
    with pytest.raises(caps.CapDataError) as excinfo:
        aggregation.authenticity_multiplier_for_score(50.0)
    assert "10.5" in str(excinfo.value)


# ── The wrapper, and the two absences ────────────────────────────────────────


def test_no_authenticity_result_at_all_is_neutral_and_says_so() -> None:
    multiplier, reason = aggregation.authenticity_multiplier(None)
    assert multiplier == 1.0
    assert reason == "authenticity was not evaluated"


def test_insufficient_evidence_is_neutral_and_says_so_differently() -> None:
    """Distinct wording on purpose: "we did not run it" and "we ran it and
    could not tell" are different facts, and only one of them is about the
    pipeline."""
    multiplier, reason = aggregation.authenticity_multiplier(
        _result("absent", insufficient=True)
    )
    assert multiplier == 1.0
    assert "insufficient evidence" in reason


def test_the_band_scale_cannot_reach_three_runbook_controls() -> None:
    """THE MISMATCH, PINNED SO CLOSING IT IS DELIBERATE. See
    RUNBOOK_OPEN_QUESTIONS.md Q24.

    Sections 10.5 and 12.2 place their control points on a continuous 0-100
    dimension score. Miti's evaluators return one of four BANDS, converted by a
    code literal with no Runbook citation whose values were chosen against a
    different axis -- `rating`'s product-grade cuts of 90/75/60. The two were
    never reconciled, and three controls fall in the gap.

    This test states the gap as it is TODAY. It fails if somebody lowers a band,
    raises a floor, or otherwise closes the mismatch -- which is the point: any
    of those re-grades the existing population or edits the Runbook, and both
    are owner decisions rather than a side effect of tuning a number. A green
    test here is not an endorsement, it is a record that the question is still
    open.
    """
    scores = [value for _band, value in dimensions.BANDS]
    lowest = min(scores)

    # 1 and 2. Two of section 12.2's four floors cannot be breached.
    unreachable_floors = [
        (row["dimension"], caps._floor_value(row))
        for row in caps._floor_rows()
        if not any(score < caps._floor_value(row) for score in scores)
    ]
    assert sorted(unreachable_floors) == [("D3", 40.0), ("D4", 25.0)], (
        unreachable_floors
    )
    # The D3 one is missed by a single point, because the test is `<`.
    assert lowest == 40.0

    # 3 and 4. Two of section 10.5's five branches cannot be entered.
    def entered(row):
        low, high = row.get("d4_low"), row.get("d4_high_exclusive")
        return [
            score for score in scores
            if (low is None or score >= low)
            and (high is None or score < high)
        ]

    unreachable_branches = [
        row["condition"] for row in aggregation._authenticity_branches()
        if not entered(row)
    ]
    assert unreachable_branches == ["45 <= D4 < 60", "D4 < 25"], unreachable_branches


def test_the_d4_hold_cannot_fire_from_any_band() -> None:
    """The consequential half of Q24, stated on its own.

    The HOLD is the only control in this product that stops a delivery on
    integrity grounds. Both implementations of it are correct and both agree on
    the floor; neither can be reached.
    """
    from app.services.hiring.department_models import DIM_AUTHENTICITY

    for band, score in dimensions.BANDS:
        assert caps.hold_reason({DIM_AUTHENTICITY: float(score)}) is None, band
        multiplier, _reason = aggregation.authenticity_multiplier_for_score(
            float(score)
        )
        assert multiplier is not None, band


def test_the_two_implementations_of_the_hold_floor_agree() -> None:
    """They are separate data entries reading separate Runbook sections, and a
    change to one without the other would deliver a held candidate with nothing
    on the record saying why: `authenticity_multiplier` returns 1.0 for a HOLD,
    so `review_reasons` never picks the reason up, and only `Aggregate.hold`
    from `caps.hold_reason` would carry it."""
    from app.services.hiring.department_models import DIM_AUTHENTICITY

    caps_floor = min(
        caps._floor_value(row)
        for row in caps._floor_rows()
        if str(row.get("dimension")) == "D4"
    )
    held = [
        score
        for score in range(0, 101)
        if aggregation.authenticity_multiplier_for_score(float(score))[0] is None
    ]
    assert held, "the multiplier has no hold branch at all"
    assert max(held) + 1 == caps_floor
    assert caps.hold_reason({DIM_AUTHENTICITY: float(max(held))}) is not None
    assert caps.hold_reason({DIM_AUTHENTICITY: caps_floor}) is None


def test_the_weakest_band_is_suppressed_rather_than_held() -> None:
    """What an `absent` authenticity result actually does today: a 0.65
    suppression, not a hold."""
    multiplier, reason = aggregation.authenticity_multiplier(_result("absent"))
    assert multiplier is not None and multiplier < 1.0
    assert "suppressed" in reason


def test_a_score_below_the_floor_is_carried_as_neutral_with_its_reason() -> None:
    """If a sub-floor D4 score ever does arrive, folding the HOLD into the
    multiplier would turn a routing decision into a number and deliver the
    candidate anyway. So the multiplier stays neutral and the reason travels."""
    multiplier, reason = aggregation.authenticity_multiplier_for_score(0.0)
    assert multiplier is None
    assert "human disposition" in reason


def test_a_scored_result_carries_its_multiplier_through() -> None:
    strong = _result("strong")
    expected, _reason = aggregation.authenticity_multiplier_for_score(float(strong.score))
    multiplier, _reason = aggregation.authenticity_multiplier(strong)
    assert expected is not None
    assert multiplier == pytest.approx(expected)


# ── Weighting ────────────────────────────────────────────────────────────────


def test_weights_are_applied() -> None:
    weighted = aggregation._weighted(
        {"a": 100.0, "b": 0.0}, {"a": 3.0, "b": 1.0}
    )
    assert weighted == pytest.approx(75.0)


def test_a_competency_with_no_stated_weight_counts_once() -> None:
    """Not zero. An item nobody derived a weight for is still a criterion the
    hiring manager declared."""
    assert aggregation._weighted({"a": 100.0, "b": 0.0}, {}) == pytest.approx(50.0)


def test_weights_that_all_failed_to_derive_fall_back_to_the_plain_mean() -> None:
    """The load-bearing case. Falling to zero would produce Not Matching for
    every candidate on that job, which reads as a hard cohort rather than a
    broken matrix."""
    assert aggregation._weighted(
        {"a": 80.0, "b": 40.0}, {"a": 0.0, "b": 0.0}
    ) == pytest.approx(60.0)


def test_a_negative_weight_is_read_as_no_weight_rather_than_a_penalty() -> None:
    """A negative multiplier would let one item subtract from the composite,
    which is not a weighting, it is a punishment nobody declared."""
    assert aggregation._weighted(
        {"a": 80.0, "b": 40.0}, {"a": -5.0, "b": -5.0}
    ) == pytest.approx(60.0)


def test_nothing_to_average_is_zero_rather_than_an_error() -> None:
    """A category with no scored items reaches this. Raising would take down
    the whole aggregate for an empty section."""
    assert aggregation._weighted({}, {"a": 1.0}) == 0.0


def test_the_hold_flag_reaches_the_client_projection() -> None:
    """Half of Q24's second finding, pinned at the half that is testable here.

    Even if the floor became reachable, the flag has to travel. It is rendered
    as `held_for_integrity_review`, a WORD-free boolean, which is what a
    recruiter needs without the arithmetic behind it.

    The other half is not testable from inside this module and is recorded in
    Q24 instead: nothing outside `aggregation.py` currently READS the
    projection, so the control would fire into nothing. That is a wiring fact
    about the callers, not a property of this function.
    """
    projected = aggregation.Aggregate().client_projection()
    assert "held_for_integrity_review" in projected
    assert projected["held_for_integrity_review"] is False
    assert not any(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in projected.values()
    ), projected
