import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.services.ppi import CATEGORIES


# REMOVED 2026-08-06: `TechnicalQuestionIn`, `TechnicalQuestionOut` and
# `QuestionBankOut`, the request and response shapes of the Company Portal's
# preset technical question bank.
#
# A company can no longer create, edit, store or assign technical questions.
# They are written per candidate, during the conversation, from the JD, that
# candidate's resume and the live transcript (`services/technical_interview`),
# so there is nothing on a job for a form to submit and nothing stored for a
# screen to list. The routes went with them; the schemas are removed rather than
# deprecated because a response model nothing returns is a contract that quietly
# reads as still supported.


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


class BulkCompetencyIn(BaseModel):
    """Paste-friendly creation of up to 100 skills/competencies."""

    category: str = Field(pattern="^(" + "|".join(CATEGORIES) + ")$")
    names: list[str] = Field(min_length=1, max_length=100)
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

    That step is now the PPI FRAMEWORK and nothing else. The technical bank
    stopped gating anything on 2026-08-04 and stopped existing on 2026-08-06, so
    `ready_for_candidates` tracks `framework_approved` exactly.

    `questions_approved` is retained and always reports the framework's own
    approval state. It is not a second gate: it is here so a client build that
    still reads the field cannot conclude a ready job is unready and hide the
    invite control. It is deprecated and should be dropped once no client reads
    it.
    """

    job_id: uuid.UUID
    status: str
    grade: str | None
    #: DEPRECATED, mirrors `framework_approved`. See the class docstring.
    questions_approved: bool
    framework_approved: bool
    ready_for_candidates: bool
    generated_at: datetime | None = None
    approved_at: datetime | None = None
    #: True when this job has no usable framework and one has been enqueued.
    #: Populated so the setup screen can say "we are preparing this" instead of
    #: rendering an empty list that looks like a finished, empty framework --
    #: which is exactly what 19 of 35 live jobs were showing.
    framework_pending: bool = False


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


class ConversationAnswerEditIn(BaseModel):
    answer: str = Field(min_length=1, max_length=10000)


class ConversationOut(BaseModel):
    conversation_id: uuid.UUID
    status: str
    prompt: str | None
    progress_label: str
    answered_questions: int
    total_questions: int
    is_reask: bool = False
    answer_message_id: uuid.UUID | None = None


# ── The recruiter's view of what was actually asked and answered ─────────────
# A report states a grade; this is the evidence behind it. A recruiter deciding
# whether to interview someone, and a candidate disputing a grade, both need the
# transcript, and until 2026-08-06 the only way to read one was a psql session.


class TranscriptExchangeOut(BaseModel):
    """One question and the answer it received.

    Paired rather than returned as a flat message list. `assessment_messages`
    stores speakers in sequence, which is the right shape to write and the wrong
    shape to read: a client rendering a Q&A view would have to re-pair them, and
    every client would re-pair them slightly differently. The pairing is done
    once, server-side, by the module that knows how follow-ups are keyed.
    """

    #: 1-based position in the conversation, counting exchanges rather than
    #: messages, so it matches the "Question 7 of 45" the candidate saw.
    ordinal: int
    #: technical | ppi. Provenance for the reader, never a filter that hides
    #: anything: the candidate experienced one conversation and so does this.
    domain: str
    #: What the candidate actually READ, which is not always what was stored --
    #: the interviewer writes the question for this candidate at this point.
    question: str
    #: Verbatim, as submitted, after the inbound guard defanged attack framing.
    #: Never re-worded and never summarised: a summary of an answer is not
    #: evidence of what someone said.
    answer: str
    #: The skill or competency this exchange was filed under, as a WORD. This is
    #: what the answer was scored against, so it is what makes the transcript
    #: readable as evidence rather than as a wall of text.
    criterion: str | None = None
    #: True when this exchange was a follow-up or a re-ask rather than one of
    #: the planned questions. It shares its predecessor's criterion by design.
    follow_up: bool = False
    asked_at: datetime | None = None


class TranscriptOut(BaseModel):
    job_candidate_link_id: uuid.UUID
    candidate_name: str | None = None
    job_title: str | None = None
    status: str
    #: Present only once the conversation is complete.
    completed_at: datetime | None = None
    exchanges: list[TranscriptExchangeOut]
    #: Total exchanges available, for a client paging through a long interview.
    total: int
    limit: int
    offset: int
