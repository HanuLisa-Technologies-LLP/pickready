"""Dashboard schemas (API_CONTRACT.md `/dashboard`, FR-10.x)."""
import uuid

from pydantic import BaseModel


class JobMetricsOut(BaseModel):
    job_id: uuid.UUID
    title: str
    databank_matched: int
    fresh_sourced: int
    shortlisted: int
    offered: int
    joined: int


class DashboardSummaryOut(BaseModel):
    jobs: list[JobMetricsOut]
    total_jobs_worked: int


# ── AI Dashboard (2026-08-09) ────────────────────────────────────────────────
# What the AI actually did for this customer. Every figure below is a COUNT OF
# THINGS (jobs, candidates, assessments), which is what the existing dashboard
# already reports and is explicitly outside the no-numbers rule: that rule
# covers a score, percentage, rank or band for an assessment or a match. No
# schema here carries a score, and the grade breakdown is keyed by the four
# WORD labels of services/rating, never by the internal percentage that
# produced them.


# ── The AI Dashboard's response shapes: REMOVED ──────────────────────────────
#
# `GradeCountOut`, `AssessmentFunnelOut`, `FrameworkHealthOut` and
# `AIDashboardOut` lived here and went with the feature (spec 30). Removed
# rather than deprecated: a response model nothing returns is a contract that
# quietly reads as still supported.


# ── The Candidate Dashboard (spec-doc6 §8, Dashboard Specification) ──────────
#
# THE ONE PLACE IN THIS PRODUCT WHERE A SCORE CROSSES THE API BOUNDARY.
#
# spec-doc6 D8 rules that the Ready Pick Score (0-100, plus band, plus
# confidence) is a DASHBOARD TRIAGE ARTIFACT: it renders in the candidate list
# and nowhere else, and it must be technically impossible for it to enter a
# delivered PRISM Report. Two things make that hold here rather than by
# convention:
#
#   * `DashboardRowOut.ready_pick_score` is the only numeric assessment field
#     in any response schema, and `tests/test_dashboard_numbers.py` walks the
#     schema package to keep it that way.
#   * `ReadyPickProfileOut` and the PRISM report schemas are different types
#     over different tables (C10). The profile panel carries NAMED per-dimension
#     ratings and no raw D1-D5 number; `CalibrationInternalsOut` is where the
#     raw numbers live, behind a route only Super Admin and HR Manager reach.
#
# Every schema below is words-plus-one-number by construction. Nothing is
# assembled by filtering a wider dict, because a filter is a list somebody has
# to remember to extend.
import datetime as _dt
from typing import Any, Literal

from pydantic import Field

from app.services import dashboard as _dashboard


class ReadyPickProfileRefOut(BaseModel):
    """Column 6's target: the dashboard's evidence panel over an Evaluation.

    NOT the PRISM Report. `artifact` is a literal so a consumer switching on it
    cannot be handed the other artefact by accident, and no serialiser can
    produce one shape that reads as either.
    """

    artifact: Literal["ready_pick_profile"] = "ready_pick_profile"
    evaluation_id: uuid.UUID


class DashboardRowOut(BaseModel):
    """One row, eight columns.

    Field order follows `dashboard.COLUMNS`, which is the specification's
    scanning order and also its keyboard tab order. Reading order is a product
    decision, so it is written once in the service and mirrored here.
    """

    # 1. Candidate
    link_id: uuid.UUID
    candidate_id: uuid.UUID
    full_name: str
    #: `COMPANY-JOB-CANDIDATE`, from `services/reference_code`. A LABEL: it
    #: identifies a row and authorises nothing, and nothing reads it back as
    #: permission (RBAC 33, spec-doc6 C17).
    system_id: str
    job_id: uuid.UUID
    job_title: str

    # 2. Source. Three values, not the document's two (spec-doc6 C40).
    source_type: str
    source_label: str

    # 3. Pre-Screen Grade. A / B / C / Hold, or null before Yukti has graded
    #    the resume. Rendered muted and outline ONLY, never a solid fill; that
    #    styling rule is enforced by a component test, not by this schema.
    pre_screen_grade: str | None = None
    pre_screen_label: str

    # 4. Ready Pick Score. The number D8 permits, and the band and confidence
    #    beside it. A null score with a `pending` or `under_review` band is the
    #    documented honest state, never a zero.
    ready_pick_score: int | None = None
    band: str
    band_label: str
    band_screen_reader_label: str
    confidence: str | None = None
    confidence_indicator: str
    confidence_label: str
    #: Always null today: no uncertainty interval is published by the
    #: evaluator, and inventing one would print a number with no provenance
    #: beside one that has some. `score_range_note` says so in the hover.
    score_range: str | None = None
    score_range_note: str

    # 5. Ready Pick Note.
    note: str
    note_is_pending: bool

    # 6. Ready Pick Profile.
    profile: ReadyPickProfileRefOut | None = None
    profile_pending_reason: str | None = None

    # 7. Team Review. The row carries COUNTS and the caller's OWN verdict, and
    #    never another reviewer's remark: the specification lists that under
    #    "what's never displayed", and RBAC 29 makes the remark its author's.
    team_review_count: int = 0
    own_verdict: str | None = None
    own_verdict_at: _dt.datetime | None = None

    # 8. Stage. The COARSE candidate pipeline stage (`CandidatePipelineStage`),
    #    derived from the stored FSM status. Never a `JobLifecycleState`: they
    #    are different enums on different entities (spec-doc6 C11).
    stage: str | None = None
    stage_label: str
    stage_on_hold: bool
    stored_status: str

    # Row state.
    under_integrity_review: bool = False
    archived: bool = False


class DashboardControlsOut(BaseModel):
    """What THIS caller may do, resolved server-side from RBAC.

    Sent with the page rather than inferred in the browser. RBAC 3 is explicit
    that frontend visibility is not a security boundary, so this exists to make
    the UI honest, not to enforce anything: every control it describes is
    independently refused at its own route.
    """

    #: Column 8's move-to control. Recruiter (own assigned jobs), HR Manager,
    #: Super Admin. Disabled with an explanation for Hiring Manager and
    #: Interview Manager (RBAC 24).
    can_move_stage: bool
    stage_disabled_reason: str | None = None
    #: Column 7. Interview Manager, Recruiter, Hiring Manager, HR Manager,
    #: Super Admin (RBAC 13.4, spec-doc6 C6).
    can_team_review: bool
    team_review_disabled_reason: str | None = None
    #: HR Manager by right, Super Admin by audited override (spec-doc6 C7).
    can_disposition_integrity: bool
    #: The audited raw-numbers view (D8).
    can_view_calibration: bool
    #: True when this caller sees only the jobs they are assigned to.
    scoped_to_assignments: bool


class DashboardPageOut(BaseModel):
    columns: list[str] = Field(default_factory=lambda: list(_dashboard.COLUMNS))
    column_labels: dict[str, str] = Field(
        default_factory=lambda: dict(_dashboard.COLUMN_SCREEN_READER_LABELS)
    )
    rows: list[DashboardRowOut]
    total: int
    page: int
    page_size: int
    controls: DashboardControlsOut
    #: The filter domains, served rather than hardcoded in the browser, so the
    #: Source filter cannot ship with two values while the database holds three.
    source_types: list[str] = Field(
        default_factory=lambda: list(_dashboard.SOURCE_TYPES)
    )
    source_labels: dict[str, str] = Field(
        default_factory=lambda: dict(_dashboard.SOURCE_LABELS)
    )
    pre_screen_grades: list[str] = Field(
        default_factory=lambda: list(_dashboard.PRE_SCREEN_GRADES)
    )
    stages: list[str] = Field(default_factory=list)
    sort_keys: list[str] = Field(default_factory=lambda: list(_dashboard.SORT_KEYS))


class ProfileDimensionOut(BaseModel):
    """One of Miti's five dimensions, as a NAMED rating (D8, spec-doc6 C2).

    There is no score field here and there must never be one. `rating` is the
    band the evaluator itself produced (strong / solid / partial / weak /
    absent / contradicted, one per row of the section 9.x rubric), so
    this is not a number rounded into a word: it is the word the number was
    derived from.
    """

    dimension: str
    label: str
    question: str
    rating: str | None = None
    rated: bool
    insufficient_evidence: bool = False
    evidence_refs: list[str] = Field(default_factory=list)


class ReadyPickProfileOut(BaseModel):
    """The slide-over evidence panel. A DIFFERENT ARTEFACT FROM THE PRISM
    REPORT, and typed so the two cannot be confused (spec-doc6 C10, C15).

    The row's pending state refers to THIS, not to the delivered document.
    """

    artifact: Literal["ready_pick_profile"] = "ready_pick_profile"
    evaluation_id: uuid.UUID
    candidate_name: str
    system_id: str
    why_this_candidate: str | None = None
    dimensions: list[ProfileDimensionOut]
    category_ratings: dict[str, str] = Field(default_factory=dict)
    overall_rating: str | None = None
    capped_by_must_have: bool = False
    confidence: str | None = None
    insufficient_dimensions: list[str] = Field(default_factory=list)
    authenticity_findings: list[Any] = Field(default_factory=list)
    open_flags: list[dict] = Field(default_factory=list)
    under_integrity_review: bool = False
    needs_human_review: bool = False
    scorecard_version: int | None = None
    company_dna_version: int | None = None
    evaluated_at: _dt.datetime | None = None
    scoring_mode: str | None = None


class TeamReviewEntryOut(BaseModel):
    """One reviewer's verdict, with its author and timestamp (RBAC 13.4/29).

    `editable` is False for everybody except the author. Nobody edits another
    user's remark, and the route refuses it independently: this field exists so
    the panel does not offer a control that would be refused.
    """

    id: uuid.UUID
    reviewer_user_id: uuid.UUID
    reviewer_email: str | None = None
    reviewer_role: str | None = None
    verdict: str
    verdict_label: str
    remarks: str
    created_at: _dt.datetime
    updated_at: _dt.datetime
    editable: bool = False


class TeamReviewPanelOut(BaseModel):
    link_id: uuid.UUID
    candidate_name: str
    system_id: str
    verdicts: list[str]
    verdict_labels: dict[str, str]
    entries: list[TeamReviewEntryOut]
    can_write: bool


class TeamReviewIn(BaseModel):
    verdict: str
    remarks: str = Field(min_length=1, max_length=4000)


class StageMoveIn(BaseModel):
    #: The stored FSM status to move to, taken from the server's own
    #: `allowed_transitions` list. The dashboard renders the COARSE stage; the
    #: move names the fine-grained status, because the FSM is what carries the
    #: promise attached to each stage.
    status: str
    remarks: str | None = None


class StageOptionsOut(BaseModel):
    stage: str | None = None
    stage_label: str
    stored_status: str
    allowed_transitions: list[dict]
    can_move: bool
    disabled_reason: str | None = None


class IntegrityDispositionIn(BaseModel):
    disposition: str
    note: str | None = None


class OverrideRateOut(BaseModel):
    """The Dashboard Specification's calibration metric, as counts and a rate.

    NO TARGET, NO THRESHOLD, NO VERDICT. spec-doc6 8.2 and `PRODUCT.md`:
    measure, never nudge. A payload carrying "under target" would be one
    component away from a scoreboard beside a recruiter's name, and a target
    that quietly discourages disagreement destroys the signal it measures.
    """

    comparable: int
    diverged: int
    rate: float


class DivergenceOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID | None = None
    job_title: str | None = None
    link_id: uuid.UUID | None = None
    candidate_id: uuid.UUID | None = None
    candidate_name: str | None = None
    reviewer_user_id: uuid.UUID | None = None
    reviewer_email: str | None = None
    reviewer_role: str | None = None
    verdict: str | None = None
    predicted_grade: str | None = None
    predicted_confidence: str | None = None
    outcome_assessment: str | None = None
    created_at: _dt.datetime


class DivergenceListOut(BaseModel):
    divergences: list[DivergenceOut]
    override_rate: OverrideRateOut


class CalibrationDimensionOut(BaseModel):
    dimension: str
    label: str
    band: str | None = None
    #: RAW. This is the field D8 keeps off every other surface.
    raw_score: int | None = None
    insufficient_evidence: bool = False
    evidence_refs: list[str] = Field(default_factory=list)


class CalibrationInternalsOut(BaseModel):
    """Raw D1-D5 numbers and aggregation internals. Super Admin / HR Manager.

    Every read of this shape writes an audit row before the response is built
    (D8: "always logged when viewed"). The route is the enforcement; this type
    exists so the numbers have exactly one schema and cannot be reached through
    a wider one.
    """

    artifact: Literal["calibration_internals"] = "calibration_internals"
    evaluation_id: uuid.UUID
    scorecard_version: int | None = None
    company_dna_version: int | None = None
    situation_type: str | None = None
    scoring_mode: str | None = None
    dimensions: list[CalibrationDimensionOut]
    competency_scores: dict[str, Any] = Field(default_factory=dict)
    category_scores: dict[str, float] = Field(default_factory=dict)
    raw_composite: float | None = None
    adjusted_composite: float | None = None
    authenticity_factor: float | None = None
    authenticity_reason: str | None = None
    must_have_cap_applied: bool = False
    confidence: str | None = None
    insufficient_dimensions: list[str] = Field(default_factory=list)
    review_reasons: list[str] = Field(default_factory=list)
    gate_results: list[dict] = Field(default_factory=list)
    triangulation: dict[str, Any] = Field(default_factory=dict)
