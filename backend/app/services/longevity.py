"""Service-longevity signal: deterministic tenure statistics from job history.

PROVENANCE. This implements the "Candidate Scoring Signals - Service-longevity
signal" feature of the 2026-09-05 add-features spec ("longevity from job-tenure
history", extending Sutra and Yukti). THERE IS NO RUNBOOK SECTION FOR THIS
SIGNAL: RPN-PHIL-001 defines no tenure bands and no stint thresholds, so every
constant below is this module's own, stated once, named, and cited to the spec
rather than wearing a Runbook citation it did not earn. If the Runbook ever
gains a tenure section, these constants move to `runbook_data/` with it.

WHAT THIS IS, AND WHAT IT IS NOT
----------------------------------
It is an INTERNAL ordering prior, the same standing retrieval has: it may
refine where a candidate sits in an internal ordering and it may never decide
anything. Three hard properties, each pinned in `tests/test_longevity.py`:

  * NO NUMBER AND NO BAND REACHES A CLIENT AS A SCORE. The statistics stay in
    this module and in its caller's local variables; what the integration
    writes beside the stored breakdown is the WORD band alone, as provenance,
    exactly as `scoring_mode` already travels. No schema carries a longevity
    field.
  * THE SIGNAL NEVER MOVES A GRADE. `adjusted_match_score` refuses any
    adjustment that would change the four-word tier the score converts to, so
    the signal orders candidates WITHIN a band and never across one. The word
    a client reads is byte-identical with and without the signal.
  * INSUFFICIENT HISTORY IS NEVER PENALISED. The same principle as "projects
    are optional and absence is never penalised" and prescreen's "an absent
    requirement is UNKNOWN, never a zero": a history too thin to read
    contributes exactly nothing, so a fresher, a career changer or a candidate
    whose resume simply has no dates ranks identically with and without this
    module. Every adjustment is non-negative for the same reason - the signal
    can lift demonstrated stability, it cannot push anybody below the position
    their evidence earned.

WHY THE PRE-SCREEN SCORE IS DELIBERATELY NOT TOUCHED. `hiring/prescreen`
states, and `test_yukti_live` pins, that nothing in the resume-stage grade
reads an employment gap or the length of a career (RPN-PHIL-001 sections 8.6
and 40.2: recorded, never scored). Feeding tenure into that score would break a
standing fairness invariant. The integration point is `matching.run_matching`'s
internal `match_score` instead, which orders lists and never crosses the API
boundary.

No model call anywhere. Two runs over one history produce one answer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Callable

# One date parser per concept: the employment-history date formats are already
# read by Yukti's pre-screen adapter, and a second parser here would accept a
# slightly different set of spellings and the two modules would silently
# disagree about which roles are dated.
from app.services.hiring.prescreen import _as_date as parse_history_date

__all__ = [
    "BAND_STEADY",
    "BAND_TYPICAL",
    "BAND_FREQUENT_MOVER",
    "BAND_INSUFFICIENT",
    "BANDS",
    "SHORT_STINT_MONTHS",
    "MIN_DATED_ROLES",
    "STEADY_MIN_AVERAGE_MONTHS",
    "FREQUENT_MOVER_MAX_AVERAGE_MONTHS",
    "FREQUENT_MOVER_MIN_SHORT_STINTS",
    "ORDERING_LIFT",
    "TenureStats",
    "tenure_statistics",
    "band_for_history",
    "ordering_adjustment",
    "adjusted_match_score",
]

# ── The band vocabulary: WORDS, machine-spelled, never a number ──────────────

BAND_STEADY = "steady"
BAND_TYPICAL = "typical"
BAND_FREQUENT_MOVER = "frequent_mover"
BAND_INSUFFICIENT = "insufficient_history"
BANDS: tuple[str, ...] = (
    BAND_STEADY,
    BAND_TYPICAL,
    BAND_FREQUENT_MOVER,
    BAND_INSUFFICIENT,
)

# ── Thresholds (2026-09-05 spec; no Runbook section exists, see module doc) ──

#: A completed role shorter than this many months counts as a short stint.
#: Twelve months is the spec task's own boundary ("count of sub-12-month
#: stints") and the conventional reading of "less than a year in the seat".
SHORT_STINT_MONTHS = 12

#: How many roles with readable durations a history needs before a tenure
#: PATTERN can honestly be claimed. One dated role is a fact about one job,
#: not a pattern about a person, so anything below two reads as
#: `insufficient_history` and contributes nothing.
MIN_DATED_ROLES = 2

#: Average tenure at or above this many months reads as `steady` (three
#: years per role). Inclusive upward, matching claude.md rule 8's direction
#: for every boundary in this product.
STEADY_MIN_AVERAGE_MONTHS = 36

#: `frequent_mover` requires BOTH an average tenure strictly below this many
#: months (a year and a half per role) AND at least
#: `FREQUENT_MOVER_MIN_SHORT_STINTS` completed sub-year stints. Both, because
#: either alone mislabels an honest history: two 16-month roles average below
#: the line with no short stint anywhere, and one 3-month misfire beside two
#: long tenures is one misfire, not a pattern.
FREQUENT_MOVER_MAX_AVERAGE_MONTHS = 18
FREQUENT_MOVER_MIN_SHORT_STINTS = 2

# ── The internal ordering adjustment ─────────────────────────────────────────
#
# NON-NEGATIVE BY DESIGN, and deliberately not called WEIGHTS: this is not a
# client-visible weighting between assessment parameters (that table was
# removed on 2026-07-30 and must not come back), it is an ordering prior on
# the internal score, in the units of `match_score` (0-100). The lifts are
# smaller than the 1.0 step the score moves in, so the signal breaks ties and
# refines order without ever outranking a real scoring difference.
#
# `frequent_mover` sits at 0.0, THE SAME AS `insufficient_history`, on
# purpose. The neutrality rule ("insufficient contributes nothing, not a
# negative") fixes the floor at zero, and a negative entry anywhere would put
# a mover BELOW a candidate whose resume merely omitted its dates - punishing
# the candidate who documented their history for having documented it. The
# signal still works: steady tenure lifts, and relative order between a
# steady candidate and a mover moves exactly as the feature asks.
ORDERING_LIFT: dict[str, float] = {
    BAND_STEADY: 0.4,
    BAND_TYPICAL: 0.2,
    BAND_FREQUENT_MOVER: 0.0,
    BAND_INSUFFICIENT: 0.0,
}

#: An `end` value that means "still in the role" rather than a date. Matched
#: so a parse failure on "Present" is read as the ongoing role it is, while a
#: parse failure on garbage stays a parse failure.
_ONGOING = re.compile(
    r"^\s*(?:present|current|currently|ongoing|now|till\s+(?:date|now)|"
    r"to\s+date|till\s+today|today)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TenureStats:
    """Tenure statistics for one candidate's documented job history.

    INTERNAL ONLY. Nothing on this type may be serialised toward a client;
    the one value that travels beyond this module's callers is `band`, a word.
    """

    #: Entries the history listed, readable or not.
    role_count: int
    #: Roles whose duration could honestly be computed (see the exclusion
    #: rules in `tenure_statistics`). The band rests on these alone.
    dated_role_count: int
    #: Mean months per dated role, one decimal; None when nothing is dated.
    average_tenure_months: float | None
    #: The longest single dated tenure in months; None when nothing is dated.
    longest_tenure_months: int | None
    #: Completed roles shorter than SHORT_STINT_MONTHS. An ongoing role is
    #: never counted here: being three months into a job is being new to it,
    #: not evidence of leaving it.
    short_stint_count: int
    #: One of BANDS. The only field that may travel as provenance.
    band: str


def _months_between(earlier: date, later: date) -> int:
    return (later.year - earlier.year) * 12 + (later.month - earlier.month)


def tenure_statistics(
    employment_history: Any, *, today: date | None = None
) -> TenureStats:
    """Pure, deterministic tenure statistics from a parsed employment history.

    Total over hostile input: a non-list, entries that are not dicts, and
    unparseable dates all reduce the dated set rather than raising, and a
    history too thin to read produces `insufficient_history` - NEVER a guess
    and never a penalty.

    The honesty rules, each the anti-guess direction:

      * BOTH DATES READABLE and end >= start: the role is dated; tenure is the
        calendar-month difference.
      * INVERTED DATES (end before start): excluded. A span of minus seven
        months is not a tenure, and picking an end to trust would be a guess.
      * ONGOING ROLE (start readable, end absent or an ongoing word): counted
        only once its elapsed time reaches SHORT_STINT_MONTHS, and never as a
        short stint. A candidate three months into their current job has not
        left it; reading their newness as instability would penalise exactly
        the people who just moved TO a role.
      * ONE-SIDED OR UNPARSEABLE DATES: excluded. An undated role has no
        duration to compute, and an `end` that is neither a date nor an
        ongoing word is a parse failure, not an open-ended role.
      * OVERLAPPING ROLES: each measured independently, neither merged nor
        clipped. A person who held two roles at once served both tenures, and
        deciding which one to shorten would be a guess about their calendar.
    """
    now = today or datetime.now(timezone.utc).date()
    entries = employment_history if isinstance(employment_history, (list, tuple)) else ()

    role_count = 0
    tenures: list[int] = []
    short_stints = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        role_count += 1
        start = parse_history_date(entry.get("start"))
        if start is None or start > now:
            continue
        raw_end = entry.get("end")
        end = parse_history_date(raw_end)
        if end is None:
            ongoing = raw_end is None or not str(raw_end).strip() or bool(
                _ONGOING.match(str(raw_end))
            )
            if not ongoing:
                continue
            elapsed = _months_between(start, now)
            if elapsed >= SHORT_STINT_MONTHS:
                tenures.append(elapsed)
            continue
        if end < start:
            continue
        months = _months_between(start, end)
        tenures.append(months)
        if months < SHORT_STINT_MONTHS:
            short_stints += 1

    dated = len(tenures)
    average = round(sum(tenures) / dated, 1) if dated else None
    longest = max(tenures) if dated else None

    if dated < MIN_DATED_ROLES:
        band = BAND_INSUFFICIENT
    elif average >= STEADY_MIN_AVERAGE_MONTHS:
        band = BAND_STEADY
    elif (
        average < FREQUENT_MOVER_MAX_AVERAGE_MONTHS
        and short_stints >= FREQUENT_MOVER_MIN_SHORT_STINTS
    ):
        band = BAND_FREQUENT_MOVER
    else:
        band = BAND_TYPICAL

    return TenureStats(
        role_count=role_count,
        dated_role_count=dated,
        average_tenure_months=average,
        longest_tenure_months=longest,
        short_stint_count=short_stints,
        band=band,
    )


def band_for_history(employment_history: Any, *, today: date | None = None) -> str:
    """The longevity band for a parsed employment history. Always a word."""
    return tenure_statistics(employment_history, today=today).band


def ordering_adjustment(band: str) -> float:
    """The non-negative internal ordering lift for a band.

    Raises on a band this module did not produce: the vocabulary is closed,
    and a silent 0.0 for a typo would make a wired-up signal and a misspelled
    one indistinguishable.
    """
    if band not in ORDERING_LIFT:
        raise ValueError(f"unknown longevity band {band!r}")
    return ORDERING_LIFT[band]


def adjusted_match_score(
    score: float, band: str, *, grade_of: Callable[[float], Any]
) -> float:
    """`score` with the band's ordering lift applied, GRADE-PRESERVINGLY.

    `grade_of` is the conversion the caller's score feeds (for `match_score`
    that is `tiers.assign_tier`). If the lifted score would convert to a
    different grade than the unlifted one, the lift is DROPPED and the score
    returned unchanged: the signal orders candidates within a band, and the
    word a client reads must be identical with and without it. Capped at 100
    so the score stays inside its own 0-100 contract.

    A zero-lift band returns `score` untouched - bit-identical, which is what
    the insufficient-history neutrality test asserts.
    """
    lift = ordering_adjustment(band)
    if lift == 0.0:
        return score
    adjusted = min(100.0, round(float(score) + lift, 1))
    if grade_of(adjusted) != grade_of(score):
        return score
    return adjusted
