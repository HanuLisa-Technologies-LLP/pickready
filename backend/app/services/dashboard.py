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
Candidate, Source, Pre-Screen Grade, Ready Pick Score, Ready Pick Note,
Ready Pick Profile, Team Review, Stage. `COLUMNS` below is that order as data,
so the API, the table and the tab-order test all read one list rather than
three copies of it.

FOUR OF THE EIGHT ARE FILLED BY AGENTS THAT ARE NOT ON A LIVE PATH YET
-----------------------------------------------------------------------
Pre-Screen Grade (Yukti), Ready Pick Score and Ready Pick Profile (Miti's
`Evaluation`), Ready Pick Note (Siddhi). Every one of them has a DOCUMENTED
PENDING STATE in the specification, and this module renders exactly that state
when the data is absent. It never substitutes a default, a placeholder or a
plausible-looking value: a dashboard that shows `— · Pending Ready Pick
Profile` is telling the truth, and one that shows `50 · Consider with
Reservations` because nothing was there is the failure this whole surface
exists to prevent.

TWO NUMBERS RULES, AND THEY POINT IN OPPOSITE DIRECTIONS
---------------------------------------------------------
spec-doc6 D8 rules that the Ready Pick Score renders NUMERICALLY here, in
column 4 and its hover, and that it must be impossible for that number to
enter a delivered PRISM Report. So this module is the one place in the product
that deliberately puts a 0-100 score in front of a client, and
`schemas/dashboard.py` is where the shape of that is pinned. Everything else
stays words: the Profile panel shows NAMED per-dimension ratings, and raw
D1-D5 numbers live only in `services/calibration.py`, behind an audited view.

WHY THE BAND CUT-POINTS ARE NOT `services/rating.py`'S
------------------------------------------------------
The Dashboard specification's band is a FIFTH vocabulary at cut-points 85 / 72
/ 60, against `rating.py`'s 90 / 75 / 60. They are not two scales for one
thing, which is the mistake `services/tiers.py` made and was corrected for:
they are two different artifacts by D8's own reasoning. `rating.GRADES` is the
assessment grade that reaches the delivered report; the Ready Pick band is a
dashboard triage label that may never reach it, and the two vocabularies share
no word (`test_dashboard_vocabulary.py` asserts that, so neither can ever be
mistaken for the other on screen).

What IS carried over from the tiers.py correction is the guard: a better score
must never earn a worse band, and the band order must never invert relative to
the grade order. Both are swept across the whole 0-100 range by test.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import hiring_pipeline, rating
from app.services.hiring import gates as hiring_gates
from app.services.hiring import prescreen
from app.services.miti import dimensions as miti_dimensions

__all__ = [
    "COLUMNS",
    "COLUMN_CANDIDATE",
    "COLUMN_SOURCE",
    "COLUMN_PRE_SCREEN_GRADE",
    "COLUMN_READY_PICK_SCORE",
    "COLUMN_READY_PICK_NOTE",
    "COLUMN_READY_PICK_PROFILE",
    "COLUMN_TEAM_REVIEW",
    "COLUMN_STAGE",
    "PRE_SCREEN_GRADES",
    "PRE_SCREEN_LABELS",
    "pre_screen_label",
    "BANDS",
    "BAND_LABELS",
    "BAND_ORDER",
    "band_for_score",
    "CONFIDENCE_INDICATORS",
    "confidence_indicator",
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
COLUMN_PRE_SCREEN_GRADE = "pre_screen_grade"
COLUMN_READY_PICK_SCORE = "ready_pick_score"
COLUMN_READY_PICK_NOTE = "ready_pick_note"
COLUMN_READY_PICK_PROFILE = "ready_pick_profile"
COLUMN_TEAM_REVIEW = "team_review"
COLUMN_STAGE = "stage"

COLUMNS: tuple[str, ...] = (
    COLUMN_CANDIDATE,
    COLUMN_SOURCE,
    COLUMN_PRE_SCREEN_GRADE,
    COLUMN_READY_PICK_SCORE,
    COLUMN_READY_PICK_NOTE,
    COLUMN_READY_PICK_PROFILE,
    COLUMN_TEAM_REVIEW,
    COLUMN_STAGE,
)

#: Screen-reader headers. The specification asks for "Candidate Code Name"
#: rather than "Candidate" on column 1, because a blind reader reaching a cell
#: with a name and a monospace code needs to know both are there before they
#: hear them.
COLUMN_SCREEN_READER_LABELS: dict[str, str] = {
    COLUMN_CANDIDATE: "Candidate Code Name",
    COLUMN_SOURCE: "Source",
    COLUMN_PRE_SCREEN_GRADE: "Pre-Screen Grade, early signal",
    COLUMN_READY_PICK_SCORE: "Ready Pick Score",
    COLUMN_READY_PICK_NOTE: "Ready Pick Note",
    COLUMN_READY_PICK_PROFILE: "Ready Pick Profile",
    COLUMN_TEAM_REVIEW: "Team Review",
    COLUMN_STAGE: "Stage",
}


# ── Column 3: the Pre-Screen Grade ───────────────────────────────────────────
#
# spec-doc6 C9: the Dashboard's A / B / C / Hold and spec-doc5's "AI Score" are
# THE SAME ARTIFACT, and the named grade is the product surface.
#
# THE VOCABULARY IS NOT DEFINED HERE. It is
# `services/hiring/prescreen.GRADES`, written by Yukti's resume-stage pass and
# stored on `job_candidate_links.prescreen_grade` behind a CHECK constraint
# (migration 0065). This module IMPORTS it. A second copy of four strings in a
# rendering layer is exactly how a product ends up with two vocabularies that
# have to be kept in step by hand, which this codebase has already paid for
# once in `services/tiers.py`.
#
# A NULL GRADE IS "NOT PRE-SCREENED", AND IS NOT `Hold`.
# ------------------------------------------------------
# Migration 0065 says so and it matters on this surface more than anywhere
# else: `Hold` is a GRADED outcome meaning a person should look, and NULL means
# nothing has looked at all. Rendering them the same way would tell a recruiter
# that an ungraded backlog had been triaged. `PRE_SCREEN_PENDING_LABEL` is the
# honest cell, and column 3 renders it in the same muted treatment as a real
# grade, because the muted treatment is what says "early signal" either way.

PRE_SCREEN_GRADES: tuple[str, ...] = prescreen.GRADES

#: What a screen reader and a legend say. Colour and a bare letter are both
#: insufficient on their own: colour is never the sole carrier of meaning, and
#: `B` is not self-describing to somebody meeting this product today.
PRE_SCREEN_LABELS: dict[str, str] = {
    prescreen.GRADE_A: "A, claims backed by attached artefacts",
    prescreen.GRADE_B: "B, claims are checkable",
    prescreen.GRADE_C: "C, claims are asserted and nothing checkable stands behind them",
    prescreen.GRADE_HOLD: "Hold, there was nothing to grade; not a rejection",
}

#: What column 3 renders when Yukti has not graded this resume yet.
PRE_SCREEN_PENDING_LABEL = (
    "Not pre-screened. This application has not been graded, which is not the "
    "same as being graded Hold."
)


def pre_screen_label(grade: str | None) -> str:
    """The spoken label for a stored pre-screen grade.

    Raises on a grade nobody defined rather than falling through to a neutral
    string: the database CHECK already refuses one, so reaching here with an
    unknown value means the vocabularies have diverged, and a dashboard that
    quietly renders an unknown grade is how that goes unnoticed.
    """
    if grade is None:
        return PRE_SCREEN_PENDING_LABEL
    try:
        return PRE_SCREEN_LABELS[str(grade)]
    except KeyError as exc:
        raise ValueError(
            f"{grade!r} is not one of prescreen.GRADES {list(prescreen.GRADES)}, "
            "so it has no Pre-Screen Grade label."
        ) from exc


# ── Column 4: the Ready Pick Score ───────────────────────────────────────────

BAND_STRONG = "ready_to_pick_strong"
BAND_READY = "ready_to_pick"
BAND_RESERVATIONS = "consider_with_reservations"
BAND_NOT_RECOMMENDED = "not_recommended"
BAND_UNDER_REVIEW = "under_review"
BAND_PENDING = "pending_ready_pick_profile"

BANDS: tuple[str, ...] = (
    BAND_STRONG,
    BAND_READY,
    BAND_RESERVATIONS,
    BAND_NOT_RECOMMENDED,
    BAND_UNDER_REVIEW,
    BAND_PENDING,
)

BAND_LABELS: dict[str, str] = {
    BAND_STRONG: "Ready to Pick, Strong",
    BAND_READY: "Ready to Pick",
    BAND_RESERVATIONS: "Consider with Reservations",
    BAND_NOT_RECOMMENDED: "Not Recommended",
    BAND_UNDER_REVIEW: "Under Review",
    BAND_PENDING: "Pending Ready Pick Profile",
}

#: What a screen reader announces. "Under Review" is the one the specification
#: singles out: announced with its MEANING, because a red pill that reads as
#: the two words alone tells a blind recruiter nothing about why the stage
#: control beside it is locked.
BAND_SCREEN_READER_LABELS: dict[str, str] = {
    BAND_STRONG: "Ready to Pick, Strong",
    BAND_READY: "Ready to Pick",
    BAND_RESERVATIONS: "Consider with Reservations",
    BAND_NOT_RECOMMENDED: "Not Recommended",
    BAND_UNDER_REVIEW: "Status: Under Review, awaiting integrity disposition",
    BAND_PENDING: "Status: Pending Ready Pick Profile, assessment in progress",
}

#: Scored bands only, best first, with the inclusive lower bound the
#: specification states. `under_review` and `pending` carry no score and are
#: therefore not in this table: they are STATES, decided before the number is
#: consulted.
BAND_CUTPOINTS: tuple[tuple[str, int], ...] = (
    (BAND_STRONG, 85),
    (BAND_READY, 72),
    (BAND_RESERVATIONS, 60),
    (BAND_NOT_RECOMMENDED, 0),
)

#: Strongest first. Used to assert monotonicity against `rating.GRADES` and to
#: order a sorted column deterministically when two rows share a score.
BAND_ORDER: tuple[str, ...] = tuple(band for band, _ in BAND_CUTPOINTS)


def band_for_score(score: float | int | None) -> str:
    """The column 4 band for a Ready Pick Score.

    Boundaries are INCLUSIVE UPWARD, matching claude.md rule 8 and
    `rating.grade_for_percent`: exactly 85 is Ready to Pick, Strong. Checked
    top-down for the same reason.

    A None score is `pending`, never `not_recommended`. The distinction is the
    entire point of the column: "we have not assessed this person" and "we
    assessed this person and they scored badly" are different sentences, and
    collapsing them slanders every candidate still in the queue.
    """
    if score is None:
        return BAND_PENDING
    value = float(score)
    for band, floor in BAND_CUTPOINTS:
        if value >= floor:
            return band
    # Unreachable while the last floor is 0 and scores are non-negative. A
    # negative score is a corrupt aggregate rather than a weak candidate, so it
    # reads as pending rather than as the bottom band.
    return BAND_PENDING


#: Column 4's confidence dot. The specification gives four visual states and
#: three of them collapse onto the aggregator's three confidence words; the
#: fourth (`grayed`) belongs to the two states that carry no score at all.
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


def confidence_indicator(confidence: str | None) -> str:
    """Filled / outline / grayed, from the aggregator's confidence word.

    `high` and `medium` are both FILLED, which is the specification's own
    grouping. It is worth naming why it is not an accident: the aggregator's
    `medium` already means "three of five dimensions were judged on real
    evidence", which is a result a recruiter may act on. `low` is where a
    second look is owed, and that is the one the outline dot marks.
    """
    if confidence is None:
        return CONFIDENCE_GRAYED
    word = str(confidence).lower()
    if word in {"high", "medium"}:
        return CONFIDENCE_FILLED
    if word == "low":
        return CONFIDENCE_OUTLINE
    return CONFIDENCE_GRAYED


#: THE SCORE RANGE THE SPECIFICATION ASKS FOR DOES NOT EXIST, AND IS NOT
#: INVENTED HERE.
#:
#: Column 4 specifies a hover tooltip reading `82 [76 to 88]`. Nothing in the
#: evaluation engine publishes an uncertainty interval: `Aggregate` carries a
#: raw composite, an adjusted composite and a confidence WORD, and no spread.
#: A bracket computed here from the confidence word would be a number with no
#: provenance printed next to one that has some, which is worse than an absent
#: bracket in exactly the way this dashboard is trying to avoid.
#:
#: So the hover carries the score, the confidence and the reason the confidence
#: is what it is, and says plainly that no interval is published.
SCORE_RANGE_UNAVAILABLE = (
    "No uncertainty interval is published by the evaluator, so no score range "
    "is shown."
)


# ── Column 5: the Ready Pick Note ────────────────────────────────────────────
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
# C15 exists to settle. Nothing in this package imports `schemas/reports.py`:
# a report payload carrying a score field now refuses to construct at all, and
# the dashboard's one number reaches a client through its own schema.
READY_PICK_NOTE_KEY = "why_this_candidate"

NOTE_PENDING = "Ready Pick Profile not written yet."
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

SORT_SCORE = "score"
SORT_NAME = "name"
SORT_ADDED = "added"
SORT_SOURCE = "source"
SORT_PRE_SCREEN = "pre_screen"
SORT_STAGE = "stage"

SORT_KEYS: tuple[str, ...] = (
    SORT_SCORE,
    SORT_NAME,
    SORT_ADDED,
    SORT_SOURCE,
    SORT_PRE_SCREEN,
    SORT_STAGE,
)

#: The leading expression per sort key. Every clause is completed with a TOTAL
#: order (`link.created_at, link.id`) by `_order_by`: without it, two rows
#: sharing a score can swap between two page fetches, and a candidate then
#: appears on two pages or on none.
_SORT_EXPRESSIONS: dict[str, str] = {
    SORT_SCORE: "ready_pick_score",
    SORT_NAME: "lower(cand.full_name)",
    SORT_ADDED: "link.created_at",
    SORT_SOURCE: "link.source_type",
    # Ordered by the tier's own rank, not alphabetically: `highly_matching`
    # sorting after `matching` because H follows M would print the strongest
    # candidates in the middle of the list.
    SORT_PRE_SCREEN: "pre_screen_rank",
    SORT_STAGE: "stage_rank",
}

_DIRECTIONS: tuple[str, ...] = ("asc", "desc")


def normalize_page(page: int | None, page_size: int | None) -> tuple[int, int]:
    """Clamp to a real page number and a sane page size."""
    resolved_page = max(1, int(page or 1))
    resolved_size = int(page_size or PAGE_SIZE)
    resolved_size = max(1, min(resolved_size, MAX_PAGE_SIZE))
    return resolved_page, resolved_size


def _order_by(sort: str | None, direction: str | None) -> str:
    key = sort if sort in _SORT_EXPRESSIONS else SORT_SCORE
    order = (direction or "").lower()
    if order not in _DIRECTIONS:
        # Descending by default for the score (the specification's fast-triage
        # workflow opens with "sort descending"); ascending for everything
        # else, where the natural reading is A before B and older before newer.
        order = "desc" if key == SORT_SCORE else "asc"
    expression = _SORT_EXPRESSIONS[key]
    # NULLs LAST in both directions. An unscored candidate is not the worst
    # candidate and must not head an ascending list, and they must not head a
    # descending one either.
    return f"{expression} {order.upper()} NULLS LAST, link.created_at DESC, link.id"


# ── The two artefacts, as two types (spec-doc6 C10) ──────────────────────────
#
# "Ready Pick Profile" is the dashboard's evidence panel over an `Evaluation`.
# "PRISM Report" is the delivered, immutable, employer-facing document, a
# `functional_skills_reports` row. spec-doc6 §8.2 requires the codebase to stop
# using the names interchangeably and to enforce the distinction with types
# rather than with convention, so here are the two types.
#
# What makes them non-interchangeable is not the class names; it is that
# neither can be constructed from the other's identifier and neither carries
# the other's payload. `ReadyPickProfileRef` is keyed on an evaluation and may
# carry a score. `PrismReportRef` is keyed on a report and structurally cannot:
# there is no score field on it, so D8's "no numeric score field in any PRISM
# payload" holds by construction rather than by filtering.


@dataclass(frozen=True)
class ReadyPickProfileRef:
    """The dashboard's evidence panel. Points at an `evaluations` row."""

    evaluation_id: uuid.UUID
    #: D8 permits the number HERE and only here.
    score: int | None = None

    @property
    def artifact(self) -> str:
        return "ready_pick_profile"


@dataclass(frozen=True)
class PrismReportRef:
    """The delivered document. Points at a `functional_skills_reports` row.

    Deliberately has no score field of any kind. A future edit that adds one
    fails `test_dashboard_artifact_types.py`, which asserts the absence by
    field set rather than by name, so a field called `value` would not slip
    through a narrower check.
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
    remark. Those belong to the panels, and the row is the fast-triage surface.
    """

    link_id: uuid.UUID
    job_id: uuid.UUID
    job_title: str
    candidate_id: uuid.UUID
    full_name: str
    system_id: str
    source_type: str
    source_label: str
    pre_screen_grade: str | None
    pre_screen_label: str
    ready_pick_score: int | None
    band: str
    band_label: str
    band_screen_reader_label: str
    confidence: str | None
    confidence_indicator: str
    confidence_label: str
    score_range: str | None
    score_range_note: str
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
    "The Ready Pick Profile has not been written yet. This says nothing about "
    "the PRISM Report, which is a different document."
)
PROFILE_PENDING_UNDER_REVIEW = (
    "The Ready Pick Profile is held while an integrity finding awaits a human "
    "disposition."
)


# ── The query ────────────────────────────────────────────────────────────────
#
# One statement, filtered, sorted and counted in SQL. Written as `text()`
# rather than assembled from the ORM because three of the eight columns need
# JSON extraction and a lateral join to the newest evaluation, and the shape of
# that is easier to review as SQL than as a chain of query-builder calls.
#
# EVERY FRAGMENT INTERPOLATED INTO IT IS FROM A CLOSED SET. `_order_by` reads
# from `_SORT_EXPRESSIONS`, whose keys are matched against `SORT_KEYS` first;
# every value a caller supplies travels as a bound parameter.

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

#: Ranks for the two sorts that must not be alphabetical.
_PRE_SCREEN_RANK_SQL = "CASE link.prescreen_grade " + " ".join(
    f"WHEN '{grade}' THEN {index}"
    for index, grade in enumerate(prescreen.GRADES, start=1)
) + " END"


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


def _scope_clause(
    *,
    scoped_to_assignments: bool,
    job_id: uuid.UUID | str | None,
    source_types: Sequence[str] | None,
    stages: Sequence[str] | None,
    pre_screen_grades: Sequence[str] | None,
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

    if pre_screen_grades:
        clauses.append("link.prescreen_grade = ANY(:pre_screen_grades)")
        params["pre_screen_grades"] = list(pre_screen_grades)

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
    pre_screen_grades: Sequence[str] | None = None,
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
        pre_screen_grades=pre_screen_grades,
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
        {_LATEST_EVALUATION}
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
                    link.prescreen_grade   AS pre_screen_grade,
                    link.status            AS status,
                    link.created_at        AS created_at,
                    link.archived_at       AS archived_at,
                    eval.id                AS evaluation_id,
                    eval.confidence        AS confidence,
                    eval.completed_at      AS evaluated_at,
                    -- THE SORT KEY IS WHAT THE READER CAN SEE.
                    -- A row under integrity review withholds its number
                    -- (`assemble_row` blanks it, and the specification's
                    -- Under Review state shows no score), so sorting on the
                    -- stored composite would drop a numberless row into the
                    -- middle of a descending list with nothing to explain its
                    -- position. From the reader's side that column is not
                    -- sorted at all. Nulled here instead, so it sorts with the
                    -- other rows that show no number, and `NULLS LAST` puts
                    -- them at the end in both directions.
                    CASE WHEN {_UNDER_REVIEW_SQL} THEN NULL
                         ELSE (eval.aggregate_json->>'adjusted_composite')::numeric
                    END                    AS ready_pick_score,
                    eval.aggregate_json->>:note_key
                                           AS ready_pick_note,
                    {_PRE_SCREEN_RANK_SQL} AS pre_screen_rank,
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
                                           AS own_verdict_at
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
    specification's two state tables is reachable from a plain mapping, so the
    state matrix is unit-tested without a database and the query is tested for
    the shape it produces.
    """
    from app.services import reference_code

    under_review = bool(row.get("under_integrity_review"))
    evaluation_id = row.get("evaluation_id")
    raw_score = row.get("ready_pick_score")
    # An evaluation exists but carries no composite: that is a scoring run that
    # did not finish, and it is pending, not zero.
    score = None if raw_score is None else int(round(float(raw_score)))

    if under_review:
        band = BAND_UNDER_REVIEW
        # The number is withheld while the finding is open. Showing a score
        # beside "Under Review" would invite a recruiter to act on it, which is
        # the one thing the lock exists to prevent.
        score = None
    else:
        band = band_for_score(score)

    confidence = row.get("confidence")
    indicator = (
        CONFIDENCE_GRAYED
        if band in {BAND_UNDER_REVIEW, BAND_PENDING}
        else confidence_indicator(confidence)
    )

    grade = row.get("pre_screen_grade")

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
        profile = ReadyPickProfileRef(
            evaluation_id=uuid.UUID(str(evaluation_id)), score=score
        )
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
        pre_screen_grade=grade,
        pre_screen_label=pre_screen_label(grade),
        ready_pick_score=score,
        band=band,
        band_label=BAND_LABELS[band],
        band_screen_reader_label=BAND_SCREEN_READER_LABELS[band],
        confidence=confidence,
        confidence_indicator=indicator,
        confidence_label=CONFIDENCE_LABELS[indicator],
        score_range=None,
        score_range_note=SCORE_RANGE_UNAVAILABLE,
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
    )


# ── The Ready Pick Profile panel ─────────────────────────────────────────────


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
        "company_dna_version": evaluation.get("company_dna_version"),
        "evaluated_at": evaluation.get("completed_at"),
        "scoring_mode": evaluation.get("scoring_mode"),
    }


#: Exported so a caller can assert the two vocabularies never collide.
#: `rating.GRADES` is the assessment scale that reaches a delivered report;
#: `BAND_LABELS.values()` is the dashboard's triage vocabulary. If a word ever
#: appeared in both, a recruiter reading "Matching" on a dashboard and
#: "Matching" in a report would have no way to know they mean different things.
def vocabularies_are_disjoint() -> bool:
    dashboard_words = {label.lower() for label in BAND_LABELS.values()}
    grade_words = {grade.lower() for grade in rating.GRADES}
    return not (dashboard_words & grade_words)
