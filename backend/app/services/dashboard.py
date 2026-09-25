"""The Candidate Dashboard: eight columns, and the rules that fill them.

WHAT THIS MODULE IS
-------------------
`docs/spec/CANDIDATE_DASHBOARD_SPECIFICATION.md` (precedence rank 4) is the
authority for the candidate list surface, and spec-doc6 §8 (rank 3) carries the
reconciliations that outrank it. This module holds the READING half: the
vocabularies each column renders, the SQL that produces one page of rows, and
the assembly that turns a row into the eight cells. Nothing here writes, and
nothing here calls a model.

THE EIGHT COLUMNS, IN THE SPECIFIED ORDER
-----------------------------------------
Candidate, Source, AI Match, Vivekium Grade, Vivekium Note, Vivekium Profile,
Team Review, Stage. `COLUMNS` below is that order as data, so the API, the
table and the tab-order test all read one list rather than three copies of it.

NO NUMBER, AND NO LETTER, REACHES THIS SURFACE (Vivekium release, D3, C8)
-------------------------------------------------------------------------
SUPERSEDES spec-doc6 D8. Column 4 used to render the Vivekium Score as a
0-100 number with a fifth band vocabulary (Ready to Pick, Strong / Ready to
Pick / Consider with Reservations / Not Recommended, cut at 85 / 72 / 60), and
column 3 rendered the pre-screen as a LETTER (A / B / C / Hold). The owner
ruling that the Vivekium brief is final removed the one sanctioned number with
no exception (D3), and spec v4 had already forbidden letter grades. So:

  * Column 3, AI MATCH, is Yukti's reading of the RESUME alone: one of the
    four `services/rating` words, or a status word when Yukti has not read the
    resume or could not. It is an early signal and renders muted.
  * Column 4, VIVEKIUM GRADE, is the word for the ONE rank the recruiter's
    ranked table also sorts by: `yukti.ranking.rank_score_sql`, the resume
    check blended with the Tatva Assessment and capped by a failed Must-have.
    Reading the same expression is what keeps the dashboard and the job page
    from ever ordering one job's candidates two different ways.

Both words are chosen HERE from the internal score, server-side, and the score
itself never leaves this module: `DashboardRow` has no numeric assessment
field, and `tests/test_dashboard_numbers.py` walks every response schema to
keep it that way. The four words are the product's one scale, so the band
vocabulary and its separate cut-points are gone rather than kept beside it.

A ROW UNDER INTEGRITY REVIEW WITHHOLDS COLUMN 4
-----------------------------------------------
An open G3 finding (failed, with no human disposition) still locks the stage
control and still withholds the grade, as it withheld the number: a grade
printed beside "Under Review" invites a recruiter to act on exactly what the
lock is holding. It sorts with the other rows that show no grade.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import assessment_video_access as video_access
from app.services import hiring_pipeline, rating
from app.services.hiring import gates as hiring_gates
from app.services.miti import aggregation as miti_aggregation
from app.services.miti import dimensions as miti_dimensions
from app.services.yukti import config as yukti_config
from app.services.yukti import ranking as yukti_ranking

__all__ = [
    "COLUMNS",
    "COLUMN_CANDIDATE",
    "COLUMN_SOURCE",
    "COLUMN_AI_MATCH",
    "COLUMN_READY_PICK_GRADE",
    "COLUMN_READY_PICK_NOTE",
    "COLUMN_READY_PICK_PROFILE",
    "COLUMN_TEAM_REVIEW",
    "COLUMN_STAGE",
    "AI_MATCH_GRADES",
    "GRADE_STATES",
    "MatchWord",
    "ai_match_word",
    "ranking_word",
    "CONFIDENCE_INDICATORS",
    "confidence_indicator",
    "confidence_label",
    "SOURCE_TYPES",
    "SORT_KEYS",
    "PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "ReadyPickProfileRef",
    "PrismReportRef",
    "DashboardRow",
    "DashboardPage",
    "READY_PICK_NOTE_KEY",
    "normalize_page",
    "candidates_page",
    "assemble_row",
    "profile_panel",
]


# ── The eight columns, as data ───────────────────────────────────────────────
#
# Scanning order follows decision logic, not backend computation order. The
# specification says so in its own words and then repeats the order twice more
# (the reference card, and the keyboard tab order), so it is written ONCE here
# and read by everything that has to agree with it.

COLUMN_CANDIDATE = "candidate"
COLUMN_SOURCE = "source"
COLUMN_AI_MATCH = "ai_match"
COLUMN_READY_PICK_GRADE = "ready_pick_grade"
COLUMN_READY_PICK_NOTE = "ready_pick_note"
COLUMN_READY_PICK_PROFILE = "ready_pick_profile"
COLUMN_TEAM_REVIEW = "team_review"
COLUMN_STAGE = "stage"

COLUMNS: tuple[str, ...] = (
    COLUMN_CANDIDATE,
    COLUMN_SOURCE,
    COLUMN_AI_MATCH,
    COLUMN_READY_PICK_GRADE,
    COLUMN_READY_PICK_NOTE,
    COLUMN_READY_PICK_PROFILE,
    COLUMN_TEAM_REVIEW,
    COLUMN_STAGE,
)

#: Screen-reader headers. The specification asks for "Candidate Code Name"
#: rather than "Candidate" on column 1, because a blind reader reaching a cell
#: with a name and a monospace code needs to know both are there before they
#: hear them. Column 3 says what it is read from, because "AI Match" alone
#: does not tell a listener that no assessment has touched it.
COLUMN_SCREEN_READER_LABELS: dict[str, str] = {
    COLUMN_CANDIDATE: "Candidate Code Name",
    COLUMN_SOURCE: "Source",
    COLUMN_AI_MATCH: "AI Match, from the resume only",
    COLUMN_READY_PICK_GRADE: "Vivekium Grade",
    COLUMN_READY_PICK_NOTE: "Vivekium Note",
    COLUMN_READY_PICK_PROFILE: "Vivekium Profile",
    COLUMN_TEAM_REVIEW: "Team Review",
    COLUMN_STAGE: "Stage",
}


# ── Columns 3 and 4: one scale, and the states that are not a grade ──────────
#
# THE VOCABULARY IS NOT DEFINED HERE. The four grades are `rating.GRADES`, the
# one scale the whole product reads, and Yukti's statuses are
# `yukti.config.STATUSES`. This module imports both. A second copy of either
# in a rendering layer is how `services/tiers.py` came to disagree with
# `rating.py` on 69.5% of its rows.
#
# A STATE is what the browser styles by: the grade words as codes, plus the
# three states that carry no grade. The browser never derives a state from a
# word or a word from a number; it is sent both.

STATE_HIGHLY = "highly_matching"
STATE_MATCHING = "matching"
STATE_MODERATELY = "moderately_matching"
STATE_NOT = "not_matching"
STATE_NOT_CHECKED = "not_checked"
STATE_NOT_ASSESSED = "not_assessed"
STATE_UNDER_REVIEW = "under_review"

#: The grade word to its state code, best first, in `rating.GRADES` order.
GRADE_STATES: dict[str, str] = {
    rating.GRADE_HIGHLY: STATE_HIGHLY,
    rating.GRADE_MATCHING: STATE_MATCHING,
    rating.GRADE_MODERATELY: STATE_MODERATELY,
    rating.GRADE_NOT: STATE_NOT,
}

#: The AI Match filter's whole domain: the four words, served to the browser.
AI_MATCH_GRADES: tuple[str, ...] = rating.GRADES

#: Statuses whose stored pre-assessment score is a real reading. `legacy` is a
#: link ranked by the retired matcher before Yukti existed (WP-B's migration
#: carries its old score across so no live table goes unordered on deploy
#: day); it reads as a grade with a note saying so.
_READ_STATUSES: tuple[str, ...] = (
    yukti_config.STATUS_SCORED,
    yukti_config.STATUS_LEGACY,
)

NOT_CHECKED_LABEL = "Not checked yet"
NOT_CHECKED_NOTE = "AI Matching has not read this resume yet."
NOT_ASSESSED_LABEL = "Not assessed"
UNDER_REVIEW_LABEL = "Under Review"
UNDER_REVIEW_SCREEN_READER = "Status: Under Review, awaiting integrity disposition"

RESUME_CHECK_NOTE = "Resume check only. Real skills are tested in the assessment."
LEGACY_NOTE = "Checked before evidence tags existed. Run AI Matching to refresh."

#: Why a resume could not be read, in words. Keyed by EVERY reason Yukti can
#: store (`test_dashboard_columns` pins the key set to
#: `yukti.config.FAILURE_REASONS`), so a reason added there cannot reach a row
#: that has no sentence for it.
NOT_ASSESSED_NOTES: dict[str, str] = {
    yukti_config.FAILURE_NO_RESUME: "There is no resume on this application.",
    yukti_config.FAILURE_NO_RESUME_TEXT: "No readable resume text.",
    yukti_config.FAILURE_MODEL_UNAVAILABLE: (
        "The AI check could not be completed. It is retried on the next AI "
        "Matching run."
    ),
    yukti_config.FAILURE_MODEL_OUTPUT_INVALID: (
        "The AI check could not be completed. It is retried on the next AI "
        "Matching run."
    ),
    yukti_config.FAILURE_NO_GROUNDED_EVIDENCE: (
        "Nothing on the resume could be checked against the skills on this job."
    ),
}

BASIS_RESUME_ONLY = "Resume check only"
BASIS_WITH_ASSESSMENT = "Tatva Assessment and resume check"
BASIS_ASSESSMENT_ONLY = "Tatva Assessment only"
MUST_HAVE_CAPPED_NOTE = (
    "Capped: a Must-have skill was not demonstrated in the assessment."
)


@dataclass(frozen=True)
class MatchWord:
    """One of columns 3 or 4, as words. There is no number on it."""

    state: str
    label: str
    screen_reader_label: str
    note: str


def ai_match_word(
    status: str | None, pre_score: float | None, failure_reason: str | None
) -> MatchWord:
    """Column 3: Yukti's resume-only reading, as a word or a status word.

    RAISES on a status or a reason nobody defined, rather than falling through
    to a neutral string. The CHECK on `yukti_status` and the writer in
    `yukti.scoring` refuse both, so arriving here with one means the
    vocabularies have diverged, and a dashboard that quietly renders it is how
    that goes unnoticed. A read status with no score is the same divergence.
    """
    if status == yukti_config.STATUS_PENDING:
        return MatchWord(
            state=STATE_NOT_CHECKED,
            label=NOT_CHECKED_LABEL,
            screen_reader_label=f"{NOT_CHECKED_LABEL}. {NOT_CHECKED_NOTE}",
            note=NOT_CHECKED_NOTE,
        )
    if status == yukti_config.STATUS_NOT_ASSESSED:
        if failure_reason not in NOT_ASSESSED_NOTES:
            raise ValueError(
                f"{failure_reason!r} is not one of yukti.config.FAILURE_REASONS, "
                "so a not-assessed row has no sentence to explain it."
            )
        note = NOT_ASSESSED_NOTES[failure_reason]
        return MatchWord(
            state=STATE_NOT_ASSESSED,
            label=NOT_ASSESSED_LABEL,
            screen_reader_label=f"{NOT_ASSESSED_LABEL}. {note}",
            note=note,
        )
    if status in _READ_STATUSES:
        word = yukti_ranking.grade_word(pre_score)
        if word is None:
            raise ValueError(
                f"a {status!r} Yukti reading carries no score, so it has no word."
            )
        note = LEGACY_NOTE if status == yukti_config.STATUS_LEGACY else RESUME_CHECK_NOTE
        return MatchWord(
            state=GRADE_STATES[word],
            label=word,
            screen_reader_label=f"{word}, from the resume only. {note}",
            note=note,
        )
    raise ValueError(
        f"{status!r} is not one of yukti.config.STATUSES "
        f"{list(yukti_config.STATUSES)}, so it has no AI Match word."
    )


def ranking_word(
    rank_score: float | None,
    *,
    under_review: bool,
    has_assessment_grade: bool,
    must_have_failed: bool,
    ai_match: MatchWord,
) -> MatchWord:
    """Column 4: the word for the ONE rank the ranked table also sorts by.

    `rank_score` is `yukti.ranking.rank_score_sql` read from the database, so
    the grade here and the order on the job page cannot disagree. When there
    is no rank at all (no usable resume reading and no graded assessment) the
    cell says why in column 3's own words rather than inventing a pending
    grade: "not checked" and "not assessed" are different sentences.
    """
    if under_review:
        return MatchWord(
            state=STATE_UNDER_REVIEW,
            label=UNDER_REVIEW_LABEL,
            screen_reader_label=UNDER_REVIEW_SCREEN_READER,
            note=(
                "The grade is withheld while an integrity finding awaits a "
                "human disposition."
            ),
        )
    word = yukti_ranking.grade_word(rank_score)
    if word is None:
        return MatchWord(
            state=ai_match.state,
            label=ai_match.label,
            screen_reader_label=ai_match.screen_reader_label,
            note=ai_match.note,
        )
    resume_read = ai_match.state in GRADE_STATES.values()
    if not has_assessment_grade:
        basis = BASIS_RESUME_ONLY
    elif resume_read:
        basis = BASIS_WITH_ASSESSMENT
    else:
        basis = BASIS_ASSESSMENT_ONLY
    note = basis + "."
    if has_assessment_grade and must_have_failed:
        note = f"{note} {MUST_HAVE_CAPPED_NOTE}"
    return MatchWord(
        state=GRADE_STATES[word],
        label=word,
        screen_reader_label=f"{word}, {basis.lower()}.",
        note=note,
    )


#: The confidence dot beside column 4. The specification gives four visual
#: states and three of them collapse onto the aggregator's confidence words;
#: the fourth (`grayed`) belongs to the states that carry no grade and to an
#: assessment the aggregator itself called insufficient.
CONFIDENCE_FILLED = "filled"
CONFIDENCE_OUTLINE = "outline"
CONFIDENCE_GRAYED = "grayed"

CONFIDENCE_INDICATORS: tuple[str, ...] = (
    CONFIDENCE_FILLED,
    CONFIDENCE_OUTLINE,
    CONFIDENCE_GRAYED,
)

#: Said in words beside the dot, always. A dot is a colour-and-shape signal and
#: colour is never the sole carrier of meaning here.
CONFIDENCE_LABELS: dict[str, str] = {
    CONFIDENCE_FILLED: "High confidence",
    CONFIDENCE_OUTLINE: "Low confidence",
    CONFIDENCE_GRAYED: "Insufficient confidence",
}

#: No evaluation exists: a resume check is not an assessment, and calling its
#: confidence "insufficient" would read as a verdict on evidence nobody has
#: gathered yet.
CONFIDENCE_NOT_ASSESSED_LABEL = "No assessment yet"

#: The aggregator's own words (`miti.aggregation`, and the CHECK migration 0106
#: put on `evaluations.confidence`), each to its dot. `moderate` is FILLED,
#: which is the specification's grouping: it already means most dimensions
#: were judged on real evidence, a result a recruiter may act on. Before this
#: table the dot read `medium`, a word 0106 rewrote out of the column, so every
#: moderate assessment rendered as grayed "Insufficient confidence".
_CONFIDENCE_DOTS: dict[str, str] = {
    miti_aggregation.CONFIDENCE_HIGH: CONFIDENCE_FILLED,
    miti_aggregation.CONFIDENCE_MODERATE: CONFIDENCE_FILLED,
    miti_aggregation.CONFIDENCE_LOW: CONFIDENCE_OUTLINE,
    miti_aggregation.CONFIDENCE_INSUFFICIENT: CONFIDENCE_GRAYED,
}


def confidence_indicator(confidence: str | None) -> str:
    """Filled / outline / grayed, from the aggregator's confidence word.

    Raises on a word the aggregator does not write, for the reason
    `ai_match_word` does: the column carries a CHECK, so an unknown word is a
    diverged vocabulary rather than a candidate.
    """
    if confidence is None:
        return CONFIDENCE_GRAYED
    try:
        return _CONFIDENCE_DOTS[str(confidence).lower()]
    except KeyError as exc:
        raise ValueError(
            f"{confidence!r} is not a confidence word the aggregator writes."
        ) from exc


def confidence_label(confidence: str | None) -> str:
    if confidence is None:
        return CONFIDENCE_NOT_ASSESSED_LABEL
    return CONFIDENCE_LABELS[confidence_indicator(confidence)]


# ── Column 5: the Vivekium Note ────────────────────────────────────────────
#
# ONE PRODUCER, TWO CONSUMERS. `siddhi/synthesis.ready_pick_note` computes the
# sentence ONCE and writes it to `evaluations.aggregate_json` under this key.
# The dashboard renders the sentence. The immutable PRISM Report renders the
# same sentence WITH its citations.
#
# The dashboard must never render those citations and must never compute a note
# of its own, in either direction: a second producer means a recruiter reading
# the row can see something the delivered document does not say, and a citation
# on a triage row is engineering detail in a place built for speed.
#
# The key is stated in both modules rather than imported across them, so
# neither service acquires an import edge to the other, and
# `test_siddhi_live.py` and `test_dashboard_columns.py` each assert the two
# agree. Two constants and two tests is the cheaper arrangement here than one
# constant and an import cycle.
#
# Read from the EVALUATION and never from `functional_skills_reports`, which is
# the delivered PRISM Report (spec-doc6 C10/C15). Sourcing a dashboard cell
# from the delivered document would make the row's pending state a statement
# about the report rather than about the profile, which is the exact confusion
# C15 exists to settle. Nothing in this package imports `schemas/reports.py`.
READY_PICK_NOTE_KEY = "why_this_candidate"

NOTE_PENDING = "Vivekium Profile not written yet."
NOTE_UNDER_REVIEW = (
    "Held for integrity review. No note is written until a person has "
    "dispositioned the finding."
)


# ── Column 2: Source ─────────────────────────────────────────────────────────
#
# spec-doc6 C40: the Dashboard document lists TWO values (Databank / Applied)
# and this repository has THREE (`applied | sourced | databank`, migration
# 0022). A two-value filter silently hides every `sourced` candidate, which is
# every applicant who arrived through an externally shared job link. All three
# render, and `SOURCE_TYPES` is the filter's whole domain.

SOURCE_APPLIED = "applied"
SOURCE_SOURCED = "sourced"
SOURCE_DATABANK = "databank"

SOURCE_TYPES: tuple[str, ...] = (SOURCE_APPLIED, SOURCE_SOURCED, SOURCE_DATABANK)

SOURCE_LABELS: dict[str, str] = {
    SOURCE_APPLIED: "Applied",
    SOURCE_SOURCED: "Sourced",
    SOURCE_DATABANK: "Databank",
}


# ── Sorting, filtering and pagination ────────────────────────────────────────

#: 25 per page (claude.md), and every sort and filter runs in SQL BEFORE the
#: page is cut. Filtering a fetched page in the browser makes the match count
#: depend on which page happened to be loaded.
PAGE_SIZE = 25
MAX_PAGE_SIZE = 100

SORT_GRADE = "grade"
SORT_NAME = "name"
SORT_ADDED = "added"
SORT_SOURCE = "source"
SORT_AI_MATCH = "ai_match"
SORT_STAGE = "stage"

SORT_KEYS: tuple[str, ...] = (
    SORT_GRADE,
    SORT_NAME,
    SORT_ADDED,
    SORT_SOURCE,
    SORT_AI_MATCH,
    SORT_STAGE,
)

#: The leading expression per sort key. Every clause is completed with a TOTAL
#: order (`link.created_at, link.id`) by `_order_by`: without it, two rows
#: sharing a grade can swap between two page fetches, and a candidate then
#: appears on two pages or on none. The two grade sorts order by the internal
#: score UNDER the word, which is the ranked table's own rule: the word is what
#: a reader sees, and the score only breaks ties inside it.
_SORT_EXPRESSIONS: dict[str, str] = {
    SORT_GRADE: "ready_pick_rank",
    SORT_NAME: "lower(cand.full_name)",
    SORT_ADDED: "link.created_at",
    SORT_SOURCE: "link.source_type",
    SORT_AI_MATCH: "ai_match_rank",
    SORT_STAGE: "stage_rank",
}

_DIRECTIONS: tuple[str, ...] = ("asc", "desc")

#: Descending by default for the two grades (the specification's fast-triage
#: workflow opens with the strongest first); ascending for everything else,
#: where the natural reading is A before B and older before newer.
_DESCENDING_BY_DEFAULT = frozenset({SORT_GRADE, SORT_AI_MATCH})


def normalize_page(page: int | None, page_size: int | None) -> tuple[int, int]:
    """Clamp to a real page number and a sane page size."""
    resolved_page = max(1, int(page or 1))
    resolved_size = int(page_size or PAGE_SIZE)
    resolved_size = max(1, min(resolved_size, MAX_PAGE_SIZE))
    return resolved_page, resolved_size


def _order_by(sort: str | None, direction: str | None) -> str:
    key = sort if sort in _SORT_EXPRESSIONS else SORT_GRADE
    order = (direction or "").lower()
    if order not in _DIRECTIONS:
        order = "desc" if key in _DESCENDING_BY_DEFAULT else "asc"
    expression = _SORT_EXPRESSIONS[key]
    # NULLs LAST in both directions. An ungraded candidate is not the worst
    # candidate and must not head an ascending list, and they must not head a
    # descending one either.
    return f"{expression} {order.upper()} NULLS LAST, link.created_at DESC, link.id"


def _grade_floors() -> dict[str, int]:
    """The inclusive lower bound of each grade, READ OFF `rating`, not retyped.

    `rating.grade_for_percent` is the one place the cut-points live. The AI
    Match filter needs them as SQL ranges, so they are recovered here by
    asking that function at every whole percent, and
    `test_dashboard_columns` sweeps a fine grid to prove the ranges and the
    function agree everywhere, which also catches a cut-point moved off a whole
    number.
    """
    floors: dict[str, int] = {}
    for percent in range(0, 101):
        word = rating.grade_for_percent(percent)
        floors.setdefault(word, percent)
    missing = set(rating.GRADES) - set(floors)
    if missing:
        raise RuntimeError(f"grades with no whole-percent floor: {sorted(missing)}")
    return floors


_GRADE_FLOORS = _grade_floors()


def grade_range(grade: str) -> tuple[int, int | None]:
    """[floor, ceiling) of a grade on the 0-100 line; ceiling None at the top."""
    floor = _GRADE_FLOORS[grade]
    above = [value for value in _GRADE_FLOORS.values() if value > floor]
    return floor, (min(above) if above else None)


# ── The two artefacts, as two types (spec-doc6 C10) ──────────────────────────
#
# "Vivekium Profile" is the dashboard's evidence panel over an `Evaluation`.
# "PRISM Report" is the delivered, immutable, employer-facing document, a
# `functional_skills_reports` row. spec-doc6 §8.2 requires the codebase to stop
# using the names interchangeably and to enforce the distinction with types
# rather than with convention, so here are the two types. Neither carries a
# score: D8's licence for one on the profile reference went with D3.


@dataclass(frozen=True)
class ReadyPickProfileRef:
    """The dashboard's evidence panel. Points at an `evaluations` row."""

    evaluation_id: uuid.UUID

    @property
    def artifact(self) -> str:
        return "ready_pick_profile"


@dataclass(frozen=True)
class PrismReportRef:
    """The delivered document. Points at a `functional_skills_reports` row.

    `test_dashboard_numbers.py` asserts the field set, so a field called
    `value` could not slip through a narrower check.
    """

    report_id: uuid.UUID

    @property
    def artifact(self) -> str:
        return "prism_report"


# ── One assembled row ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DashboardRow:
    """Everything the eight columns of one row need, and nothing else.

    Note what is NOT here, and the specification lists them explicitly under
    "What's never displayed": individual dimension scores, evidence source
    counts, confidence reasoning detail, and any other reviewer's Team Review
    remark. Nor is any score: the two grade columns are words and states, and
    the internal numbers they came from stay in `candidates_page`.
    """

    link_id: uuid.UUID
    job_id: uuid.UUID
    job_title: str
    candidate_id: uuid.UUID
    full_name: str
    system_id: str
    source_type: str
    source_label: str
    ai_match_state: str
    ai_match_label: str
    ai_match_screen_reader_label: str
    ai_match_note: str
    ranking_state: str
    ranking_label: str
    ranking_screen_reader_label: str
    ranking_note: str
    confidence: str | None
    confidence_indicator: str
    confidence_label: str
    note: str
    note_is_pending: bool
    profile: ReadyPickProfileRef | None
    profile_pending_reason: str | None
    stage: str | None
    stage_label: str
    stage_on_hold: bool
    stored_status: str
    under_integrity_review: bool
    archived: bool
    team_review_count: int
    own_verdict: str | None
    own_verdict_at: Any | None

    # ── Assessment/video metadata (2026-09-05 dashboard/video spec §3-5) ─────
    # Availability WORDS beside the eight columns, never inside them:
    # `COLUMNS` is the specification's fixed scanning order and stays eight.
    # Defaults are the honest empty state (no session, no recording), which is
    # also what a queried row from before these columns existed reads as.
    assessment_mode: str | None = None
    assessment_mode_label: str = video_access.MODE_NOT_STARTED_LABEL
    prism_report_status: str = video_access.REPORT_NOT_AVAILABLE
    proctoring_report_status: str = video_access.REPORT_NOT_AVAILABLE
    video_status: str = video_access.VIDEO_NONE


@dataclass(frozen=True)
class DashboardPage:
    rows: tuple[DashboardRow, ...]
    total: int
    page: int
    page_size: int


#: Rendered when the profile button is disabled. The specification's column 6
#: says "Awaiting Profile"; this is the sentence behind it, which is what a
#: screen reader announces and what a tooltip shows.
PROFILE_PENDING_REASON = (
    "The Vivekium Profile has not been written yet. This says nothing about "
    "the PRISM Report, which is a different document."
)
PROFILE_PENDING_UNDER_REVIEW = (
    "The Vivekium Profile is held while an integrity finding awaits a human "
    "disposition."
)


# ── The query ────────────────────────────────────────────────────────────────
#
# One statement, filtered, sorted and counted in SQL. Written as `text()`
# rather than assembled from the ORM because several columns need JSON
# extraction and a lateral join to the newest evaluation, and the shape of
# that is easier to review as SQL than as a chain of query-builder calls.
#
# EVERY FRAGMENT INTERPOLATED INTO IT IS FROM A CLOSED SET. `_order_by` reads
# from `_SORT_EXPRESSIONS`, whose keys are matched against `SORT_KEYS` first;
# the rank expression and the status literals come from modules, never from a
# caller; every value a caller supplies travels as a bound parameter.

#: The newest evaluation for the link, and only that one. LATERAL rather than a
#: window function because the row set is one page and the correlated read is
#: indexed by `ix_evaluations_link (link_id, created_at)`.
_LATEST_EVALUATION = """
    LEFT JOIN LATERAL (
        SELECT e.id,
               e.aggregate_json,
               e.dimension_scores,
               e.confidence,
               e.needs_human_review,
               e.gate_results_json,
               e.completed_at
        FROM evaluations e
        WHERE e.link_id = link.id
        ORDER BY e.created_at DESC, e.id DESC
        LIMIT 1
    ) eval ON true
"""

#: The delivered report (`uq_functional_report_link`: at most one per link, so
#: this join never multiplies a row) and the tenant, whose assessment weight
#: the rank expression reads. Aliased `rep` and `t` because those are the
#: names `yukti.ranking.rank_score_sql` is called with below.
_RANK_JOINS = """
    JOIN tenants t ON t.id = link.tenant_id
    LEFT JOIN functional_skills_reports rep ON rep.job_candidate_link_id = link.id
"""

#: An open integrity finding is G3 recorded as failed with no disposition
#: against that evaluation. Both halves are required: G3 failing is a finding,
#: and a recorded human decision is what closes it. No flag auto-clears, and
#: nothing here rejects anybody, it only locks the stage control.
_UNDER_REVIEW_SQL = """
    (
        eval.id IS NOT NULL
        AND EXISTS (
            SELECT 1
            FROM jsonb_array_elements(eval.gate_results_json) AS gate
            WHERE gate->>'gate' = :g3_gate
              AND (gate->>'passed') = 'false'
        )
        AND NOT EXISTS (
            SELECT 1 FROM review_dispositions rd WHERE rd.evaluation_id = eval.id
        )
    )
"""

#: Assessment mode + latest recording, one lateral each (2026-09-05
#: dashboard/video spec §3-5). METADATA ONLY: rows, never S3, never media
#: work, fetched inside the same single statement as everything else so the
#: page stays one query with no per-row read.
_ASSESSMENT_VIDEO_JOINS = """
    LEFT JOIN LATERAL (
        SELECT ac.mode, ac.status
        FROM assessment_conversations ac
        WHERE ac.job_candidate_link_id = link.id
        ORDER BY ac.created_at DESC, ac.id DESC
        LIMIT 1
    ) conv ON true
    LEFT JOIN LATERAL (
        SELECT vr.status, vr.media_deleted_at
        FROM video_recordings vr
        WHERE vr.job_candidate_link_id = link.id
        ORDER BY vr.created_at DESC, vr.id DESC
        LIMIT 1
    ) vid ON true
"""

#: `yukti_status IN (<the read statuses>)`, from `yukti.config`, never retyped.
_READ_STATUS_SQL = "link.yukti_status IN ({})".format(
    ", ".join(f"'{status}'" for status in _READ_STATUSES)
)

#: Column 3's sort key: the resume-only score, NULL when there is no reading.
_AI_MATCH_RANK_SQL = (
    f"CASE WHEN {_READ_STATUS_SQL} THEN link.yukti_pre_score END"
)


def _ready_pick_rank_sql() -> str:
    """Column 4's sort key: THE rank, or NULL while the row is under review.

    THE SORT KEY IS WHAT THE READER CAN SEE. A row under integrity review
    withholds its grade, so sorting it by the hidden rank would drop a
    gradeless row into the middle of a descending list with nothing to explain
    its position. Nulled here instead, so it sorts with the other rows that
    show no grade, and `NULLS LAST` puts them at the end in both directions.
    """
    rank = yukti_ranking.rank_score_sql(link="link", report="rep", tenant="t")
    return f"CASE WHEN {_UNDER_REVIEW_SQL} THEN NULL ELSE {rank} END"


def _stage_rank_sql() -> str:
    """Rank the coarse dashboard stages in pipeline order.

    Built from `hiring_pipeline.DASHBOARD_STAGE` rather than restated, so a
    stage added to the FSM cannot acquire a silent rank of NULL here while
    passing the separation test over there.
    """
    order = {
        stage.value: index
        for index, stage in enumerate(hiring_pipeline.CandidatePipelineStage, start=1)
    }
    whens = " ".join(
        f"WHEN '{status}' THEN {order[stage.value]}"
        for status, stage in sorted(hiring_pipeline.DASHBOARD_STAGE.items())
    )
    # `hold` is deliberately absent from DASHBOARD_STAGE: it is an action, not
    # a stage. It ranks NULL, which `_order_by` puts last in both directions.
    return f"CASE link.status {whens} END"


def _ai_match_clause(grades: Sequence[str]) -> tuple[str, dict[str, Any]]:
    """The AI Match filter: each word to its range on the resume-only score.

    A caller's word never reaches the SQL text. It is looked up in
    `_GRADE_FLOORS` (an unknown one is a KeyError the route has already
    refused with a 422), and only the bounds travel, as bound parameters.
    """
    ranges: list[str] = []
    params: dict[str, Any] = {}
    for index, grade in enumerate(dict.fromkeys(grades)):
        floor, ceiling = grade_range(grade)
        params[f"ai_match_lo_{index}"] = floor
        clause = f"link.yukti_pre_score >= :ai_match_lo_{index}"
        if ceiling is not None:
            params[f"ai_match_hi_{index}"] = ceiling
            clause += f" AND link.yukti_pre_score < :ai_match_hi_{index}"
        ranges.append(f"({clause})")
    return f"({_READ_STATUS_SQL} AND ({' OR '.join(ranges)}))", params


def _scope_clause(
    *,
    scoped_to_assignments: bool,
    job_id: uuid.UUID | str | None,
    source_types: Sequence[str] | None,
    stages: Sequence[str] | None,
    ai_match_grades: Sequence[str] | None,
    search: str | None,
    include_archived: bool,
) -> tuple[str, dict[str, Any]]:
    clauses = ["link.tenant_id = :tenant_id"]
    params: dict[str, Any] = {}

    if not include_archived:
        clauses.append("link.archived_at IS NULL")

    if scoped_to_assignments:
        # RBAC 9.2 and 23: holding a role is not owning a job. A scoped role
        # sees the candidates on the jobs it is ASSIGNED to, and the assignment
        # lives in `job_assignments` (migration 0061), never in `jobs.created_by`
        # and never inferred from `users.role`.
        clauses.append(
            "EXISTS (SELECT 1 FROM job_assignments ja "
            "WHERE ja.job_id = link.job_id AND ja.user_id = :viewer_id "
            "AND ja.active)"
        )

    if job_id is not None:
        clauses.append("link.job_id = :job_id")
        params["job_id"] = str(job_id)

    if source_types:
        clauses.append("link.source_type = ANY(:source_types)")
        params["source_types"] = list(source_types)

    if stages:
        # The filter names a COARSE dashboard stage; the stored value is one of
        # the ten FSM statuses. Translated here rather than in the browser, so
        # the count is the whole match and not the part on this page.
        statuses = [
            status
            for status, stage in hiring_pipeline.DASHBOARD_STAGE.items()
            if stage.value in stages
        ]
        clauses.append("link.status = ANY(:stage_statuses)")
        params["stage_statuses"] = statuses

    if ai_match_grades:
        clause, ai_params = _ai_match_clause(ai_match_grades)
        clauses.append(clause)
        params.update(ai_params)

    if search:
        clauses.append("cand.full_name ILIKE :search")
        params["search"] = f"%{search.strip()}%"

    return " AND ".join(clauses), params


async def candidates_page(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | str,
    viewer_id: uuid.UUID | str,
    scoped_to_assignments: bool,
    job_id: uuid.UUID | str | None = None,
    source_types: Sequence[str] | None = None,
    stages: Sequence[str] | None = None,
    ai_match_grades: Sequence[str] | None = None,
    search: str | None = None,
    include_archived: bool = False,
    sort: str | None = None,
    direction: str | None = None,
    page: int | None = None,
    page_size: int | None = None,
) -> DashboardPage:
    """One page of the candidate dashboard, filtered and sorted in SQL.

    Runs under whatever session the caller opened, which for every request path
    is the RLS-aware tenant session. The explicit `link.tenant_id` predicate is
    defence in depth, not the boundary (claude.md rule 1).
    """
    resolved_page, resolved_size = normalize_page(page, page_size)
    where, params = _scope_clause(
        scoped_to_assignments=scoped_to_assignments,
        job_id=job_id,
        source_types=source_types,
        stages=stages,
        ai_match_grades=ai_match_grades,
        search=search,
        include_archived=include_archived,
    )
    params.update(
        {
            "tenant_id": str(tenant_id),
            "viewer_id": str(viewer_id),
            "g3_gate": hiring_gates.G3,
            "limit": resolved_size,
            "offset": (resolved_page - 1) * resolved_size,
            "note_key": READY_PICK_NOTE_KEY,
        }
    )

    base = f"""
        FROM job_candidate_links link
        JOIN candidates cand ON cand.id = link.candidate_id
        JOIN jobs job ON job.id = link.job_id
        {_RANK_JOINS}
        {_LATEST_EVALUATION}
        {_ASSESSMENT_VIDEO_JOINS}
        WHERE {where}
    """

    total = (
        await session.execute(text(f"SELECT count(*) {base}"), params)
    ).scalar_one()

    rows = (
        await session.execute(
            text(
                f"""
                SELECT
                    link.id                AS link_id,
                    link.tenant_id         AS tenant_id,
                    link.job_id            AS job_id,
                    job.title              AS job_title,
                    link.candidate_id      AS candidate_id,
                    cand.full_name         AS full_name,
                    link.source_type       AS source_type,
                    link.status            AS status,
                    link.created_at        AS created_at,
                    link.archived_at       AS archived_at,
                    -- Column 3's inputs. The score is read to choose a word
                    -- and never leaves `assemble_row`.
                    link.yukti_status          AS ai_match_status,
                    link.yukti_pre_score       AS ai_match_score,
                    link.yukti_failure_reason  AS ai_match_failure_reason,
                    {_AI_MATCH_RANK_SQL}       AS ai_match_rank,
                    -- Column 4's inputs, and the rank the ranked table sorts by.
                    (rep.id IS NOT NULL AND rep.overall_score IS NOT NULL)
                                           AS has_assessment_grade,
                    COALESCE(rep.must_have_failed, false)
                                           AS must_have_failed,
                    {_ready_pick_rank_sql()} AS ready_pick_rank,
                    eval.id                AS evaluation_id,
                    eval.confidence        AS confidence,
                    eval.completed_at      AS evaluated_at,
                    eval.aggregate_json->>:note_key
                                           AS ready_pick_note,
                    {_stage_rank_sql()}    AS stage_rank,
                    {_UNDER_REVIEW_SQL}    AS under_integrity_review,
                    (SELECT count(*) FROM candidate_team_reviews tr
                      WHERE tr.job_candidate_link_id = link.id)
                                           AS team_review_count,
                    (SELECT tr.rating FROM candidate_team_reviews tr
                      WHERE tr.job_candidate_link_id = link.id
                        AND tr.reviewer_user_id = :viewer_id)
                                           AS own_verdict,
                    (SELECT tr.updated_at FROM candidate_team_reviews tr
                      WHERE tr.job_candidate_link_id = link.id
                        AND tr.reviewer_user_id = :viewer_id)
                                           AS own_verdict_at,
                    -- Assessment/video metadata (2026-09-05 dashboard/video
                    -- spec §3-5): presence facts the assembly turns into the
                    -- availability WORDS. No media, no S3, same statement.
                    conv.mode              AS assessment_mode,
                    conv.status            AS conversation_status,
                    vid.status             AS video_recording_status,
                    vid.media_deleted_at   AS video_media_deleted_at,
                    (rep.id IS NOT NULL)   AS has_prism_report,
                    EXISTS (
                        SELECT 1 FROM proctoring_reports pr
                        JOIN proctoring_sessions psess
                          ON psess.id = pr.proctoring_session_id
                         WHERE psess.job_candidate_link_id = link.id
                    )                      AS has_proctoring_report,
                    EXISTS (
                        SELECT 1 FROM proctoring_sessions psess2
                         WHERE psess2.job_candidate_link_id = link.id
                    )                      AS has_proctoring_session
                {base}
                ORDER BY {_order_by(sort, direction)}
                LIMIT :limit OFFSET :offset
                """
            ),
            params,
        )
    ).mappings().all()

    return DashboardPage(
        rows=tuple(assemble_row(row) for row in rows),
        total=int(total or 0),
        page=resolved_page,
        page_size=resolved_size,
    )


def assemble_row(row: Mapping[str, Any]) -> DashboardRow:
    """Turn one queried row into the eight cells.

    Pure, and separated from the query on purpose: every state in the
    specification's state tables is reachable from a plain mapping, so the
    state matrix is unit-tested without a database and the query is tested for
    the shape it produces. The two scores in the mapping are consumed here and
    appear on the returned row only as words.
    """
    from app.services import reference_code

    under_review = bool(row.get("under_integrity_review"))
    evaluation_id = row.get("evaluation_id")

    ai_match = ai_match_word(
        row.get("ai_match_status"),
        row.get("ai_match_score"),
        row.get("ai_match_failure_reason"),
    )
    ranking = ranking_word(
        row.get("ready_pick_rank"),
        under_review=under_review,
        has_assessment_grade=bool(row.get("has_assessment_grade")),
        must_have_failed=bool(row.get("must_have_failed")),
        ai_match=ai_match,
    )

    confidence = row.get("confidence")
    if under_review:
        indicator = CONFIDENCE_GRAYED
        spoken_confidence = CONFIDENCE_LABELS[CONFIDENCE_GRAYED]
    else:
        indicator = confidence_indicator(confidence)
        spoken_confidence = confidence_label(confidence)

    note_text = (row.get("ready_pick_note") or "").strip()
    if under_review:
        note, note_pending = NOTE_UNDER_REVIEW, True
    elif note_text:
        note, note_pending = note_text, False
    else:
        note, note_pending = NOTE_PENDING, True

    profile = None
    profile_pending_reason: str | None = PROFILE_PENDING_REASON
    if evaluation_id is not None:
        profile = ReadyPickProfileRef(evaluation_id=uuid.UUID(str(evaluation_id)))
        profile_pending_reason = None
    elif under_review:
        profile_pending_reason = PROFILE_PENDING_UNDER_REVIEW

    status = str(row.get("status") or hiring_pipeline.APPLIED)
    stage = hiring_pipeline.dashboard_stage(status)
    # NOT `stage is None`: a sourced candidate has no stage either, and reading
    # the absence as a pause would mark a resume nobody has contacted as an
    # application somebody deliberately paused.
    on_hold = hiring_pipeline.is_on_hold(status)

    return DashboardRow(
        link_id=uuid.UUID(str(row["link_id"])),
        job_id=uuid.UUID(str(row["job_id"])),
        job_title=str(row.get("job_title") or ""),
        candidate_id=uuid.UUID(str(row["candidate_id"])),
        full_name=str(row.get("full_name") or ""),
        system_id=reference_code.reference_code(
            row["tenant_id"], row["job_id"], row["candidate_id"]
        ),
        source_type=str(row.get("source_type") or SOURCE_APPLIED),
        source_label=SOURCE_LABELS.get(
            str(row.get("source_type") or SOURCE_APPLIED), SOURCE_LABELS[SOURCE_APPLIED]
        ),
        ai_match_state=ai_match.state,
        ai_match_label=ai_match.label,
        ai_match_screen_reader_label=ai_match.screen_reader_label,
        ai_match_note=ai_match.note,
        ranking_state=ranking.state,
        ranking_label=ranking.label,
        ranking_screen_reader_label=ranking.screen_reader_label,
        ranking_note=ranking.note,
        confidence=confidence,
        confidence_indicator=indicator,
        confidence_label=spoken_confidence,
        note=note,
        note_is_pending=note_pending,
        profile=profile,
        profile_pending_reason=profile_pending_reason,
        stage=None if stage is None else stage.value,
        # From the FSM, not inferred here: `stage is None` has two causes, and
        # `stage.value` on the second one is an AttributeError rather than a
        # wrong label. See `hiring_pipeline.dashboard_stage_label`.
        stage_label=hiring_pipeline.dashboard_stage_label(status),
        stage_on_hold=on_hold,
        stored_status=status,
        under_integrity_review=under_review,
        archived=row.get("archived_at") is not None,
        team_review_count=int(row.get("team_review_count") or 0),
        own_verdict=row.get("own_verdict"),
        own_verdict_at=row.get("own_verdict_at"),
        # Assessment/video metadata (2026-09-05 dashboard/video spec §3-5).
        # `.get()` throughout: a mapping without these keys is the honest
        # empty state (no session, no recording), never an error.
        assessment_mode=row.get("assessment_mode"),
        assessment_mode_label=video_access.mode_label(row.get("assessment_mode")),
        prism_report_status=video_access.prism_status_word(
            has_report=bool(row.get("has_prism_report")),
            conversation_status=row.get("conversation_status"),
        ),
        proctoring_report_status=video_access.proctoring_status_word(
            has_proctoring_report=bool(row.get("has_proctoring_report")),
            has_proctoring_session=bool(row.get("has_proctoring_session")),
        ),
        video_status=video_access.video_status_word(
            row.get("video_recording_status"),
            media_deleted=row.get("video_media_deleted_at") is not None,
        ),
    )


# ── The Vivekium Profile panel ─────────────────────────────────────────────


def profile_panel(
    *,
    evaluation: Mapping[str, Any],
    candidate_name: str,
    system_id: str,
    under_integrity_review: bool,
) -> dict[str, Any]:
    """The slide-over panel's payload: named ratings, never raw D1-D5 numbers.

    spec-doc6 D8 and C2. The specification's own column 6 text asks for a
    "Dimension breakdown (D1 to D5 scores)"; D8 outranks it and rules that the
    panel shows NAMED per-dimension ratings. The named rating is what the
    evaluators actually produce (`miti.dimensions.BANDS`: strong / solid /
    partial / weak / absent / contradicted, one per row of the section 9.x
    rubric), so this is not a lossy projection of a number, it is the number's
    own source.

    Raw numbers, evaluator outputs and aggregation internals reach nobody
    through this function. They are `services/calibration.py`'s business, behind
    an audited view.
    """
    dimension_scores = evaluation.get("dimension_scores") or {}
    aggregate = evaluation.get("aggregate_json") or {}
    triangulation = evaluation.get("triangulation_json") or {}
    gate_results = evaluation.get("gate_results_json") or []

    dimensions = []
    for key in miti_dimensions.DIMENSIONS:
        entry = dimension_scores.get(key) or {}
        band = entry.get("band")
        dimensions.append(
            {
                "dimension": key,
                "label": miti_dimensions.DIMENSION_LABELS[key],
                "question": miti_dimensions.DIMENSION_QUESTIONS[key],
                # The NAMED rating. Absent rather than defaulted: a dimension
                # the evaluators did not reach is not a dimension that scored
                # `absent`, and the two words would look identical in a cell.
                "rating": band,
                "rated": band is not None,
                "insufficient_evidence": bool(entry.get("insufficient_evidence")),
                "evidence_refs": list(entry.get("evidence_refs") or []),
            }
        )

    open_flags = [
        {
            "gate": result.get("gate"),
            "blocking": bool(result.get("blocking")),
            "reasons": list(result.get("reasons") or []),
        }
        for result in gate_results
        if result.get("passed") is False
    ]

    return {
        "artifact": "ready_pick_profile",
        "candidate_name": candidate_name,
        "system_id": system_id,
        "why_this_candidate": (aggregate.get(READY_PICK_NOTE_KEY) or "").strip() or None,
        "dimensions": dimensions,
        # Words only. `category_grades` is the aggregator's client projection
        # and carries no arithmetic.
        "category_ratings": dict(aggregate.get("category_grades") or {}),
        "overall_rating": aggregate.get("overall_grade") or None,
        "capped_by_must_have": bool(aggregate.get("must_have_cap_applied")),
        "confidence": evaluation.get("confidence"),
        "insufficient_dimensions": list(aggregate.get("insufficient_dimensions") or []),
        "authenticity_findings": list(triangulation.get("findings") or []),
        "open_flags": open_flags,
        "under_integrity_review": under_integrity_review,
        "needs_human_review": bool(evaluation.get("needs_human_review")),
        "scorecard_version": evaluation.get("scorecard_version"),
        "evaluated_at": evaluation.get("completed_at"),
        "scoring_mode": evaluation.get("scoring_mode"),
    }
