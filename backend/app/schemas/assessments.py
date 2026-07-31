import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.services.ppi import CATEGORIES


class TechnicalQuestionIn(BaseModel):
    skill: str = Field(min_length=1, max_length=255)
    prompt: str = Field(min_length=10)
    rubric: dict


class TechnicalQuestionOut(TechnicalQuestionIn):
    id: uuid.UUID
    ordinal: int
    is_active: bool


class QuestionBankOut(BaseModel):
    job_id: uuid.UUID
    status: str
    grade: str | None
    questions: list[TechnicalQuestionOut]
    #: True once a recruiter has finalised the bank. The job still needs the PPI
    #: framework approved before it reaches `ready_for_candidates`.
    approved: bool = False


# ── The job's PPI framework (spec §6.2, §6.3) ────────────────────────────────


class CompetencyIn(BaseModel):
    """What the Hiring Manager's Edit control sends.

    `required_level` is a WORD, one of the four grades. There is no numeric
    input anywhere on this form: the client never types a score and never sees
    one.
    """

    category: str = Field(pattern="^(" + "|".join(CATEGORIES) + ")$")
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=1000)
    required_level: str


class CompetencyOut(BaseModel):
    id: uuid.UUID
    category: str
    name: str
    description: str | None
    required_level: str
    ordinal: int


class FrameworkOut(BaseModel):
    job_id: uuid.UUID
    status: str
    approved: bool
    #: Ordered primary_skill, secondary_skill, behavioural -- report order.
    competencies: list[CompetencyOut]
    #: Per-category minimum that must be met before the framework can be saved.
    minimum_per_category: int
    #: Populated when the framework cannot yet be saved, so the UI can say why
    #: rather than only disabling the Save control.
    blocking_reason: str | None = None


class JobSetupOut(BaseModel):
    """The single manual step in the pipeline (spec §11), as one payload.

    Both halves are generated in parallel and approved independently; the job
    becomes available to candidates only when both are done.
    """

    job_id: uuid.UUID
    status: str
    grade: str | None
    questions_approved: bool
    framework_approved: bool
    ready_for_candidates: bool
    generated_at: datetime | None = None
    approved_at: datetime | None = None


# ── The PPI Assessment Report (spec §10) ─────────────────────────────────────


class DimensionOut(BaseModel):
    name: str
    description: str | None
    #: One of the four grades. Never a number, a percentage, or a letter grade.
    grade: str
    #: What the job requires of this item, as a word. Null on AI Score
    #: parameters and technical items, which have no job-requirement shape.
    required_level: str | None = None
    remark: str


class RadarAxisOut(BaseModel):
    """One spoke of one radar chart (spec §10.4).

    Two shapes are plotted on the same axes: what the job requires and what the
    candidate demonstrated. `*_index` is a RENDERING COORDINATE, not a score:
    1 (Not Matching, innermost) to 4 (Highly Matching, outermost). A radar has
    no geometry without a radius, and the four grades ARE the axis, so this is
    the coarsest value that can draw the required chart. It is never displayed
    as a number anywhere, and it is not the underlying 0-100 score, which stays
    internal.
    """

    axis: str
    requirement_band: str
    requirement_index: int
    candidate_band: str
    candidate_index: int


class RadarChartOut(BaseModel):
    key: str
    title: str
    axes: list[RadarAxisOut]


class FunctionalReportOut(BaseModel):
    id: uuid.UUID
    job_candidate_link_id: uuid.UUID
    grade: str
    # ── AI Score: the pre-assessment resume snapshot (§10.1) ─────────────────
    ai_score: list[DimensionOut]
    # ── PPI Assessment (§10.3) ───────────────────────────────────────────────
    overall_grade: str
    overall_summary: str
    primary_skills: list[DimensionOut]
    secondary_skills: list[DimensionOut]
    behavioural: list[DimensionOut]
    #: Scored and used to anchor suggested questions; not a rendered section.
    technical: list[DimensionOut]
    #: The application's mandatory fields, as submitted. Never rated (§7).
    validation: dict
    #: 8-10, advisory input only (§10.3).
    suggested_interview_questions: list[str]
    #: Four charts: Overall, Primary Skills, Secondary Skills, Behavioural.
    radar_charts: list[RadarChartOut] = []
    #: Ordered best-to-worst grade labels, for the chart legend and colour ramp.
    radar_bands: list[str] = []
    #: The two shapes on every chart, by word (§10.4).
    radar_series: list[str] = []
    synthesized_at: datetime
    #: Reports are immutable. Advertised in the payload so the UI never has to
    #: infer it in order to hide edit/delete affordances.
    immutable: bool = True


class ConversationMessageIn(BaseModel):
    answer: str = Field(min_length=1, max_length=10000)


class ConversationOut(BaseModel):
    conversation_id: uuid.UUID
    status: str
    prompt: str | None
    progress_label: str
