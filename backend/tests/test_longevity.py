"""Pure unit tests for the service-longevity signal (2026-09-05 add-features
spec, Candidate Scoring Signals).

No DB, no LLM: the module under test is deterministic arithmetic over parsed
dates, and these tests pin the four properties that make it safe to have:

  * the tenure arithmetic is honest over messy histories (overlaps, ongoing
    roles, inverted and one-sided dates),
  * the band boundaries sit where the constants say and are inclusive upward,
  * insufficient history is NEUTRAL - a candidate with no readable tenure
    ranks bit-identically with and without the signal, and no band ever
    pushes anybody below their unadjusted position,
  * no longevity number can reach a client: the adjustment is grade-preserving,
    the only value recorded beside the breakdown is a WORD, and no schema
    module knows the signal exists.
"""
from __future__ import annotations

import pathlib
import re
from datetime import date

import pytest

import app.schemas
import app.services.matching as matching
from app.services import longevity
from app.services.longevity import (
    BAND_FREQUENT_MOVER,
    BAND_INSUFFICIENT,
    BAND_STEADY,
    BAND_TYPICAL,
    BANDS,
    FREQUENT_MOVER_MIN_SHORT_STINTS,
    MIN_DATED_ROLES,
    ORDERING_LIFT,
    SHORT_STINT_MONTHS,
    STEADY_MIN_AVERAGE_MONTHS,
    adjusted_match_score,
    band_for_history,
    ordering_adjustment,
    tenure_statistics,
)
from app.services.tiers import assign_tier

#: Fixed so every assertion below is reproducible; passed to every call that
#: reads an ongoing role's elapsed time.
TODAY = date(2026, 9, 5)


def role(start, end, **extra):
    return {"company": "Acme", "title": "Engineer", "start": start, "end": end, **extra}


def stats(history):
    return tenure_statistics(history, today=TODAY)


# ── Tenure arithmetic ────────────────────────────────────────────────────────

def test_multiple_completed_roles():
    s = stats(
        [
            role("2015-01", "2018-01"),  # 36 months
            role("2018-02", "2021-02"),  # 36 months
            role("2021-03", "2024-03"),  # 36 months
        ]
    )
    assert s.role_count == 3
    assert s.dated_role_count == 3
    assert s.average_tenure_months == 36.0
    assert s.longest_tenure_months == 36
    assert s.short_stint_count == 0
    assert s.band == BAND_STEADY  # exactly 36 is steady: inclusive upward


def test_open_ended_current_role_counts_once_it_is_a_year_old():
    s = stats([role("2019-01", "2022-01"), role("2022-09", None)])  # 36, then 48 ongoing
    assert s.dated_role_count == 2
    assert s.average_tenure_months == 42.0
    assert s.longest_tenure_months == 48
    assert s.band == BAND_STEADY


def test_present_spellings_read_as_ongoing():
    for spelling in ("Present", "current", "till date", "  NOW "):
        s = stats([role("2019-01", "2022-01"), role("2022-09", spelling)])
        assert s.dated_role_count == 2, spelling


def test_a_young_ongoing_role_contributes_nothing():
    # Four months into the current job: not a stint, not a tenure, not data.
    s = stats([role("2019-01", "2022-01"), role("2026-05", None)])
    assert s.dated_role_count == 1
    assert s.band == BAND_INSUFFICIENT


def test_an_ongoing_role_is_never_a_short_stint():
    # Thirteen months in: counted as tenure, never as a sub-year stint.
    s = stats([role("2025-08", None), role("2019-01", "2022-01")])
    assert s.dated_role_count == 2
    assert s.short_stint_count == 0


def test_overlapping_roles_are_measured_independently():
    # Two concurrent 24-month roles: both tenures stand, neither is clipped.
    s = stats([role("2020-01", "2022-01"), role("2020-06", "2022-06")])
    assert s.dated_role_count == 2
    assert s.average_tenure_months == 24.0


def test_inverted_dates_are_excluded_not_negative():
    s = stats([role("2022-01", "2020-01")])
    assert s.role_count == 1
    assert s.dated_role_count == 0
    assert s.band == BAND_INSUFFICIENT


def test_one_sided_and_garbage_dates_are_excluded():
    s = stats(
        [
            role(None, "2022-01"),          # no start
            role("not a date", "2022-01"),  # unreadable start
            role("2020-01", "afterwards"),  # end neither a date nor ongoing
        ]
    )
    assert s.role_count == 3
    assert s.dated_role_count == 0
    assert s.band == BAND_INSUFFICIENT


def test_no_history_is_insufficient_never_a_guess():
    for history in (None, [], "nonsense", [42, "x"], [{"company": "Acme"}]):
        s = stats(history)
        assert s.band == BAND_INSUFFICIENT
        assert s.average_tenure_months is None
        assert s.longest_tenure_months is None
        assert band_for_history(history, today=TODAY) == BAND_INSUFFICIENT


def test_one_dated_role_is_a_fact_not_a_pattern():
    assert MIN_DATED_ROLES == 2
    s = stats([role("2015-01", "2024-01")])
    assert s.dated_role_count == 1
    assert s.average_tenure_months == 108.0
    assert s.band == BAND_INSUFFICIENT


# ── Band boundaries ──────────────────────────────────────────────────────────

def test_frequent_mover_needs_both_a_low_average_and_repeated_short_stints():
    # Three six-month roles: average 6.0, three short stints.
    s = stats([role("2023-01", "2023-07"), role("2023-08", "2024-02"), role("2024-03", "2024-09")])
    assert s.short_stint_count == 3 >= FREQUENT_MOVER_MIN_SHORT_STINTS
    assert s.band == BAND_FREQUENT_MOVER


def test_average_exactly_at_the_mover_ceiling_is_typical():
    # 6 + 6 + 42 months: average exactly 18.0 -> the strict < does not fire.
    s = stats([role("2020-01", "2020-07"), role("2020-08", "2021-02"), role("2021-03", "2024-09")])
    assert s.average_tenure_months == 18.0
    assert s.short_stint_count == 2
    assert s.band == BAND_TYPICAL


def test_one_misfire_beside_long_tenures_is_not_a_mover():
    # 3 + 40 + 40 months: one short stint is one misfire, not a pattern.
    s = stats([role("2016-01", "2016-04"), role("2016-05", "2019-09"), role("2019-10", "2023-02")])
    assert s.short_stint_count == 1
    assert s.band == BAND_TYPICAL


def test_just_below_steady_is_typical():
    # 35 + 36 months: average 35.5, below the inclusive 36 line.
    s = stats([role("2018-01", "2020-12"), role("2021-01", "2024-01")])
    assert s.average_tenure_months == 35.5 < STEADY_MIN_AVERAGE_MONTHS
    assert s.band == BAND_TYPICAL


# ── Insufficient-history neutrality (the fairness pin) ───────────────────────

def test_insufficient_history_contributes_exactly_nothing():
    assert ordering_adjustment(BAND_INSUFFICIENT) == 0.0
    for score in (0.0, 44.4, 59.9, 60.0, 74.9, 75.0, 89.9, 90.0, 100.0):
        assert adjusted_match_score(score, BAND_INSUFFICIENT, grade_of=assign_tier) == score


def test_no_band_ever_pushes_a_candidate_down():
    # The floor is the unadjusted score: every lift is non-negative, so a
    # documented mover is never ranked below a candidate whose resume merely
    # omitted its dates.
    assert set(ORDERING_LIFT) == set(BANDS)
    assert all(lift >= 0.0 for lift in ORDERING_LIFT.values())
    assert ordering_adjustment(BAND_FREQUENT_MOVER) == 0.0
    for band in BANDS:
        for score in (0.0, 33.3, 75.0, 100.0):
            assert adjusted_match_score(score, band, grade_of=assign_tier) >= score


def test_a_candidate_with_no_parseable_history_ranks_identically():
    # End to end: no history -> insufficient band -> the adjusted score is the
    # bit-identical input, so the ordering with and without the signal agrees.
    band = band_for_history(None, today=TODAY)
    assert band == BAND_INSUFFICIENT
    scores = [88.0, 72.5, 61.0, 40.0]
    adjusted = [adjusted_match_score(s, band, grade_of=assign_tier) for s in scores]
    assert adjusted == scores


def test_an_unknown_band_raises_rather_than_silently_neutralising():
    with pytest.raises(ValueError):
        ordering_adjustment("stedy")


# ── Grade preservation: the signal never moves the word a client reads ───────

def test_the_adjustment_never_changes_the_tier_anywhere_on_the_scale():
    for band in BANDS:
        for i in range(0, 1001):
            score = i / 10
            adjusted = adjusted_match_score(score, band, grade_of=assign_tier)
            assert assign_tier(adjusted) == assign_tier(score), (band, score)
            assert adjusted <= 100.0


def test_a_lift_that_would_cross_a_grade_boundary_is_dropped():
    # 89.8 is Matching; 89.8 + 0.4 would be Highly Matching, so no lift.
    assert adjusted_match_score(89.8, BAND_STEADY, grade_of=assign_tier) == 89.8
    # Away from a boundary the lift lands.
    assert adjusted_match_score(80.0, BAND_STEADY, grade_of=assign_tier) == 80.4


def test_the_signal_orders_within_a_band():
    steady = adjusted_match_score(80.0, BAND_STEADY, grade_of=assign_tier)
    typical = adjusted_match_score(80.0, BAND_TYPICAL, grade_of=assign_tier)
    mover = adjusted_match_score(80.0, BAND_FREQUENT_MOVER, grade_of=assign_tier)
    assert steady > typical > mover == 80.0
    assert assign_tier(steady) == assign_tier(mover)


# ── No number reaches a client ───────────────────────────────────────────────

def test_bands_are_words():
    for band in BANDS:
        assert isinstance(band, str)
        assert not any(ch.isdigit() for ch in band)
        assert chr(8212) not in band


def test_no_schema_module_knows_the_signal_exists():
    """No client-facing Pydantic schema carries a longevity field.

    The signal is internal; the one word that travels rides inside the stored
    breakdown as provenance, through projections that already exist. A schema
    naming it would be the first step of a number leaking, so the sweep is
    over the whole schemas package.
    """
    schemas_dir = pathlib.Path(app.schemas.__file__).parent
    for path in schemas_dir.glob("*.py"):
        assert "longevity" not in path.read_text(encoding="utf-8").casefold(), path


def test_matching_reads_only_the_band_and_the_adjuster():
    """The integration point touches no tenure number.

    `matching.py` may ask for the WORD band and the grade-preserving
    adjustment, and nothing else: not TenureStats, not the averages, not the
    stint counts. A new `longevity.` call site in matching is a reviewed
    change to this list, not a drive-by.
    """
    src = pathlib.Path(matching.__file__).read_text(encoding="utf-8")
    used = set(re.findall(r"\blongevity\.(\w+)", src))
    assert used == {"band_for_history", "adjusted_match_score"}


def test_the_breakdown_carries_the_word_and_client_projections_stay_clean():
    breakdown = {
        "skills_match": {"score": 8, "comment": "c " * 27},
        "overall": {"score": 8.0, "comment": "c " * 27},
        "scoring_mode": "llm",
        "longevity_band": BAND_STEADY,
    }
    # The review-screen projection strips every number and keeps the word.
    client = matching.client_breakdown(breakdown)
    assert client["longevity_band"] == BAND_STEADY
    assert "score" not in client["skills_match"]
    assert "score" not in client["overall"]
    # The flat ranking payload never grows a longevity key: the band is not a
    # scored category and must not render as one.
    payload = matching.ranking_payload(breakdown)
    assert not any("longevity" in key for key in payload)
    assert not any(
        "longevity" in str(entry.get("key", "")) for entry in payload["categories"]
    )


def test_tenure_statistics_calls_no_model_and_is_deterministic():
    history = [role("2019-01", "2022-01"), role("2022-02", "2026-01")]
    assert stats(history) == stats(history)
    src = pathlib.Path(longevity.__file__).read_text(encoding="utf-8")
    assert "llm_router" not in src
    assert "invoke_llm" not in src
    assert chr(8212) not in src
