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
from tests import miti_fixtures as mf


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


def test_every_band_sits_inside_its_runbook_row() -> None:
    """The six band scores are checked against a citation, not typed by feel.

    Sections 9.1 to 9.5 state one six-row rubric over 0 to 100, and
    `rubric_anchor_text` puts that exact table in front of the evaluator. Each
    word must therefore name one row and score inside it, or the prompt shows
    one scale and the arithmetic uses another -- which is precisely the defect
    Q24 records.
    """
    from app.services.hiring import runbook_data

    rows = runbook_data.load("dimensions")["dimensions"]["D4"]["rubric_anchors"]
    assert len(rows) == len(dimensions.BANDS), (
        "a Runbook row with no word is a row no evaluator can ever report"
    )
    ranges = sorted(((r["low"], r["high"]) for r in rows), reverse=True)
    scores = [value for _band, value in dimensions.BANDS]
    assert scores == sorted(scores, reverse=True), "bands must run best first"
    for (low, high), (band, score) in zip(ranges, dimensions.BANDS):
        assert low <= score <= high, (band, score, low, high)


def test_every_runbook_control_can_now_be_reached() -> None:
    """The inverse of what this file asserted until 2026-09-02. See Q24.

    Three controls could not fire, because two rows of the section 9.x rubric
    had no word: the 0-24 HOLD row, and the 45-59 row section 10.5 prices at
    0.70 to 0.90. Adding the words made all of them reachable. This test fails
    if a word is removed again.
    """
    scores = [value for _band, value in dimensions.BANDS]

    for row in caps._floor_rows():
        floor = caps._floor_value(row)
        assert any(score < floor for score in scores), (
            row["dimension"], floor, row["effect_if_breached"]
        )

    for row in aggregation._authenticity_branches():
        low, high = row.get("d4_low"), row.get("d4_high_exclusive")
        assert any(
            (low is None or score >= low) and (high is None or score < high)
            for score in scores
        ), row["condition"]


def test_the_bottom_row_is_the_hold_and_it_fires() -> None:
    """Section 9.4's own text for the 0-24 row: "severe contradiction or
    verified misrepresentation -> integrity flag, mandatory human review,
    candidate not delivered without HR Manager decision". It is the only control
    in the product that stops a delivery on integrity grounds."""
    from app.services.miti.dimensions import DIM_AUTHENTICITY

    bottom = dimensions.BANDS[-1][0]
    score = float(dimensions.band_for(bottom))
    assert caps.hold_reason({DIM_AUTHENTICITY: score}) is not None
    assert aggregation.authenticity_multiplier_for_score(score)[0] is None

    for band, value in dimensions.BANDS[:-1]:
        assert caps.hold_reason({DIM_AUTHENTICITY: float(value)}) is None, band


def test_the_four_original_words_kept_their_exact_scores() -> None:
    """The fix is ADDITIVE. Every band that could be reported before scores
    identically now, so nothing already graded is graded differently -- which
    is what made this fixable without an owner decision about re-grading."""
    assert dict(dimensions.BANDS)["strong"] == 92
    assert dict(dimensions.BANDS)["solid"] == 80
    assert dict(dimensions.BANDS)["partial"] == 66
    assert dict(dimensions.BANDS)["absent"] == 40


def test_a_band_written_before_the_two_rows_were_added_still_reads() -> None:
    """`Evaluation.dimension_bands` persists the WORD and `calibration` reads
    historical rows back through `band_for`. Dropping a word would raise on a
    row written last month."""
    for band in ("strong", "solid", "partial", "absent"):
        assert dimensions.band_for(band) > 0, band


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
#
# WP5-B: a bucket's score is the priority-weighted mean of Miti's SKILL grades,
# weight `1 / priority` (Sutra's hidden per-bucket rank, 1 = highest). The
# derived per-competency weights this section used to test came from the frozen
# matrix, which Miti no longer reads.


def test_the_top_priority_skill_counts_most() -> None:
    out = aggregation.aggregate(
        [],
        skill_grades=(
            mf.skill("a", "must_have", 100, priority=1),
            mf.skill("b", "must_have", 40, priority=2),
            mf.skill("c", "behavioural", 80),
        ),
    )
    # (100 * 1 + 40 * 0.5) / 1.5 = 80
    assert out.category_scores["must_have"] == pytest.approx(80.0)


def test_priority_weights_never_favour_a_lower_ranked_skill() -> None:
    weights = [aggregation.priority_weight(rank) for rank in range(1, 6)]
    assert weights == sorted(weights, reverse=True)
    assert weights[0] == 1.0


def test_a_priority_below_one_is_a_contract_defect_not_a_weight() -> None:
    with pytest.raises(ValueError):
        aggregation.priority_weight(0)


def test_a_not_assessed_skill_is_excluded_from_its_bucket_and_named() -> None:
    """Insufficient is not negative: an outage never reads as a low score."""
    out = aggregation.aggregate(
        [],
        skill_grades=(
            mf.skill("a", "must_have", 90),
            mf.skill("b", "nice_to_have", None),
            mf.skill("c", "behavioural", 90),
        ),
    )
    assert "nice_to_have" not in out.category_scores
    assert out.insufficient_skills == ["b"]
    assert out.needs_human_review
    # A Nice-to-have hole leaves the overall graded.
    assert out.overall_status == aggregation.OVERALL_GRADED


def test_a_not_assessed_must_have_withholds_the_overall() -> None:
    out = aggregation.aggregate(
        [],
        skill_grades=(
            mf.skill("a", "must_have", None),
            mf.skill("c", "behavioural", 90),
        ),
    )
    assert out.overall_status == aggregation.OVERALL_NOT_ASSESSED
    assert out.overall_grade == ""
    assert out.client_projection()["overall_grade"] == ""


def test_nothing_assessed_at_all_is_not_a_grade() -> None:
    out = aggregation.aggregate([], skill_grades=())
    assert out.category_scores == {}
    assert out.overall_status == aggregation.OVERALL_NOT_ASSESSED
    assert out.overall_grade == ""



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


def test_a_held_candidate_is_not_ranked() -> None:
    """Section 10.8: a hold is "not ranked pending human disposition".

    THE TRAP THIS CLOSES. Below section 10.5's floor the multiplier is None, not
    a suppression, so the composite passes through UNSUPPRESSED -- and a
    `contradicted` account would otherwise grade ABOVE an `absent` one, which is
    the worst possible reading of a worse result. Nothing downstream could catch
    it, because a grade is a plausible word whatever produced it.
    """
    from app.services.miti.dimensions import DIM_AUTHENTICITY
    from app.services.miti.dimensions import DimensionResult

    def run(band: str):
        results = [
            DimensionResult(
                dimension=dimension,
                band="strong" if dimension != DIM_AUTHENTICITY else band,
                evidence_refs=("ref:1",),
                rationale="",
            )
            for dimension in dimensions.DIMENSIONS
        ]
        return aggregation.aggregate(results, skill_grades=mf.strong_skills())

    held = run("contradicted")
    assert held.hold is True
    assert held.needs_human_review is True
    assert held.review_reasons, "a hold must always say why"

    projection = held.client_projection()
    assert projection["held_for_integrity_review"] is True
    assert projection["overall_grade"] == ""
    assert projection["category_grades"] == {}

    # And the ordinary paths still rank normally.
    graded = run("absent")
    assert graded.hold is False
    assert graded.client_projection()["overall_grade"]


def test_the_client_projection_still_carries_no_number() -> None:
    """Both branches of it, because the held one is a second construction site
    and a number added there would reach a client just as fast."""
    from app.services.miti.dimensions import DIM_AUTHENTICITY
    from app.services.miti.dimensions import DimensionResult

    for band in ("strong", "contradicted"):
        results = [
            DimensionResult(
                dimension=dimension,
                band="strong" if dimension != DIM_AUTHENTICITY else band,
                evidence_refs=("ref:1",),
                rationale="",
            )
            for dimension in dimensions.DIMENSIONS
        ]
        projection = aggregation.aggregate(
            results, skill_grades=mf.strong_skills()
        ).client_projection()
        for key, value in projection.items():
            assert not isinstance(value, (int, float)) or isinstance(value, bool), (
                band,
                key,
                value,
            )
