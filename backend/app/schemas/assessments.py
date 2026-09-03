import uuid
from datetime import datetime

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.proctoring import ProctoringReportOut
from app.schemas.reports import NumberFreeDelivery
from app.services.assessment_formats import types as question_types
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


# ── The job's PPI matrix (spec §5.2, §5.3) ───────────────────────────────────


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
    # ── Sutra's seven stages, projected for the review screen ────────────────
    #
    # spec-doc6 4.3: "Traceability is a product requirement, not a log line ...
    # The Hiring Manager's review screen shows this in plain language before
    # finalisation."
    #
    # NO NUMBER CROSSES THIS BOUNDARY. `weight`, `threshold` and the four
    # multiplier terms stay on the row; what a reviewer reads is `provenance`,
    # a list of sentences, and `force_rank`, which is an ORDER rather than a
    # score -- the same status the radar chart's band index has had all along,
    # and it is what §20.3's force-ranking is FOR. A weight rendered as "1.4850"
    # would be a number a hiring manager could not usefully argue with.

    #: Stage 2: what we would SEE if a candidate had this.
    observable_evidence: str | None = None
    #: Stage 4, as a word.
    assessment_method: str | None = None
    #: Stage 7, when one applies.
    disqualifier: str | None = None
    #: The hiring manager's own sentence, quoted, when a Layer 3 input produced
    #: this criterion.
    swot_origin: str | None = None
    #: §20.3's position in the force-ranking, 1..n, or null for a behavioural
    #: competency (§20.1's scorecard has no behavioural row to rank).
    force_rank: int | None = None
    #: Where the weight came from, in sentences. `hiring.scorecard.plain_provenance`.
    provenance: list[str] = []


class CompetencyMoveIn(BaseModel):
    """One aspect's order after a drag-and-drop move (spec 5.3).

    The client sends the WHOLE ordered list for each aspect it changed, not a
    (from, to) pair. A pair has to be replayed against whatever the server
    currently holds, and two hiring managers dragging at once would interleave
    into an order neither of them saw; a full list is idempotent and always
    describes a state someone actually looked at.
    """

    category: str = Field(pattern="^(" + "|".join(CATEGORIES) + ")$")
    #: Competency ids, in the order they should appear in this aspect.
    competency_ids: list[uuid.UUID] = Field(max_length=200)


class MatrixReorderIn(BaseModel):
    #: One entry per aspect whose order or membership changed. An aspect that is
    #: absent is left exactly as it is.
    groups: list[CompetencyMoveIn] = Field(min_length=1, max_length=3)


class FrameworkOut(BaseModel):
    job_id: uuid.UUID
    status: str
    approved: bool
    #: Ordered must_have, nice_to_have, behavioural -- report order.
    competencies: list[CompetencyOut]
    #: The most items this matrix may hold. Every item is probed at least once,
    #: so the grade's question ceiling is the matrix's ceiling (spec 5.4).
    maximum_items: int = 0
    #: How many questions this job's candidates will be asked, resolved from the
    #: grade's range and the matrix size. Shown so the Hiring Manager can see
    #: what adding an item actually costs the candidate.
    question_target: int = 0
    #: The RANGE the assessment may run to, as [minimum, maximum]. Sutra fixes
    #: it per job; Vaada decides where inside it a given conversation ends, from
    #: that candidate's own answer depth. Shown as a range rather than a single
    #: number because that is what actually happens now, and a UI promising an
    #: exact count would be wrong for every candidate who answered thoroughly.
    question_range: list[int] = []
    #: There is NO minimum item count in Draft v4: the agent recommends what the
    #: job needs. Reported as one per aspect purely because each aspect is
    #: graded and charted on every report, so none of the three may be empty.
    minimum_per_category: int
    #: Populated when the matrix cannot yet be saved, so the UI can say why
    #: rather than only disabling the Save control.
    blocking_reason: str | None = None


# ── The Reporting Authority SWOT intake (spec 5.1) ───────────────────────


class SwotAnswerIn(BaseModel):
    answer: str = Field(min_length=1, max_length=6000)


class SwotIntakeOut(BaseModel):
    """The intake conversation, as one payload.

    `captured` is what the PPI agent will read; `prompt` is what the reporting
    authority is being asked right now. Both are returned every turn so the
    screen can show the growing picture beside the question, which is what makes
    a four-area conversation feel finite to someone doing it unpaid.
    """

    job_id: uuid.UUID
    status: str
    complete: bool
    #: strengths | weaknesses | opportunities | threats, or null when finished.
    current_area: str | None = None
    current_area_label: str | None = None
    prompt: str | None = None
    #: area -> the points captured so far, in the authority's own terms.
    captured: dict[str, list[str]] = {}
    areas_total: int = 4
    areas_done: int = 0
    # ── The rest of §18.2's session, which the four quadrants are only the
    #    first four blocks of ──────────────────────────────────────────────
    #: `swot_intake.PHASES`. Reported so the screen can say which block of the
    #: session the manager is in rather than showing "Threats" through the
    #: force-ranking, the best-performer test and the classification read-back.
    phase: str = "areas"
    phase_label: str | None = None
    #: The §18.4 situation type the manager CONFIRMED, as a word, plus its
    #: label. Never a proposal: a proposal shown as a confirmation is how the
    #: most expensive error at intake gets made silently.
    situation_key: str | None = None
    situation_label: str | None = None
    #: True while §18.5 has handed the intake back. A screen that showed this
    #: the same as "in progress" would let a rejected intake look finished.
    returned_for_rework: bool = False
    #: The §18.5 rules currently refusing, by name. The SENTENCE to say is
    #: `prompt`; these are for the progress panel.
    outstanding_rules: list[str] = []
    #: §18.3 probes and the other instruments already put to the manager.
    instruments_asked: list[str] = []


class JobSetupOut(BaseModel):
    """The one manual step in the pipeline (spec §10), as one payload.

    Draft v4 made that step TWO halves finalised in ONE session: the PPI matrix
    and the job's Matching category list. A job reaches "Ready for Candidates"
    when both are stamped, and everything after that -- the candidate
    conversation, scoring, report synthesis -- runs with no further human
    involvement.

    The SWOT intake is REPORTED but does not gate on its own. It is an input to
    the matrix, so an unfinished intake already shows up as a matrix nobody has
    approved, and gating separately would give one problem two error messages.

    `questions_approved` is retained and always reports the matrix's own approval
    state. It is not a third gate: it is here so a client build that still reads
    the field cannot conclude a ready job is unready and hide the invite control.
    It is deprecated and should be dropped once no client reads it.
    """

    job_id: uuid.UUID
    status: str
    grade: str | None
    #: DEPRECATED, mirrors `framework_approved`. See the class docstring.
    questions_approved: bool
    framework_approved: bool
    #: The second half of the setup session (spec §3.2).
    matching_categories_finalized: bool = False
    #: Whether the reporting authority has finished the SWOT intake.
    swot_complete: bool = False
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


class GapProbeItemOut(BaseModel):
    """One gap, with its grade, its REUSED item remark, and its probes."""

    name: str
    grade: str
    #: The same remark the item carries in its own section (spec 9.6). Reused,
    #: never rewritten: one report states one assessment of an item.
    remark: str | None = None
    #: 25-30 words each, grounded in what the candidate actually said, advisory
    #: only, never phrased as an advance or reject decision (spec 9.5, 9.6).
    probes: list[str] = []


class GapGroupOut(BaseModel):
    category: str
    label: str
    items: list[GapProbeItemOut] = []
    #: Said in words when the group is empty, rather than left as blank space.
    no_gaps_statement: str | None = None
    #: Present on the Must-have group when a Not Matching Must-have item has
    #: capped the Overall Grade, so the reader is told rather than left to
    #: cross-reference the Overall Assessment (spec 9.6).
    cap_statement: str | None = None


class GapAnalysisOut(BaseModel):
    """Gap Analysis & Action Plan (spec 9.6).

    Replaces the suggested-questions section entirely. Nothing about gaps or
    probes appears anywhere else in the report.
    """

    #: One sentence naming the one or two items most worth interview time.
    focus_summary: str = ""
    must_have_cap_applied: bool = False
    #: Must-have, Nice-to-have, Behavioural, in that order.
    groups: list[GapGroupOut] = []


class FunctionalReportOut(NumberFreeDelivery):
    # THE SERIALISER-LEVEL NUMBER BAN (spec-doc6 D8). Inherited rather than
    # asserted in the route: this model is the last shape a delivered PRISM
    # Report holds before it becomes JSON, and a check placed anywhere earlier
    # would be a check a second route could skip. See `schemas/reports.py`.
    id: uuid.UUID
    job_candidate_link_id: uuid.UUID
    #: COMPANY-JOB-CANDIDATE, the same code the candidate table renders under
    #: the name. Present so a printed report and a row on screen can be matched
    #: by eye. Derived, one-way, and never an authorisation input.
    reference_code: str = ""
    grade: str
    # ── AI Score: the pre-assessment resume snapshot (9.1) ──────────────
    ai_score: list[DimensionOut]
    # ── PPI Assessment (9.3) ────────────────────────────────────
    overall_grade: str
    overall_summary: str
    must_have: list[DimensionOut]
    nice_to_have: list[DimensionOut]
    behavioural: list[DimensionOut]
    #: LEGACY, and empty on every report written from Draft v4 onward: technical
    #: depth is assessed inside Must-have. Populated only for a report written
    #: against the standalone technical bank that no longer exists.
    technical: list[DimensionOut] = []
    #: The application's mandatory fields, as submitted. Never rated (6).
    validation: dict
    #: The Proctoring Report (proctoring spec 7), appended as the final section
    #: of the delivered document. INFORMATIONAL ONLY: it moves no grade, no
    #: score and no ranking, and it is words only so this model's number ban
    #: holds over it unchanged. None when no proctoring report exists yet.
    proctoring: ProctoringReportOut | None = None
    #: Gap Analysis & Action Plan (9.6).
    gap_analysis: GapAnalysisOut = GapAnalysisOut()
    #: RETIRED, replaced by `gap_analysis`. Non-empty only on a report written
    #: before Draft v4, so an old report opened today still renders what it was
    #: actually written with rather than an empty section.
    suggested_interview_questions: list[str] = []
    #: Four charts: Overall, Must-have, Nice-to-have, Behavioural.
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
    """One turn. Prose for a text question; a structure for the others.

    `answer` stays the transcript line for the two text formats. For a
    structured format the client sends `answer_payload` in the shape
    `assessment_formats.types.ANSWER_MODELS` names and the SERVER renders the
    transcript line from it; an `answer` string sent alongside is ignored so a
    client can never disagree with its own structured submission.

    `paused_ms` is how long a blocking proctoring warning held the screen
    during this question, subtracted from the server-measured time spent and
    bounded by it. `behaviour` is the answer field's keystroke and pointer
    timings, evaluated server-side against the candidate's own baseline.
    """

    answer: str = Field(default="", max_length=10000)
    answer_payload: dict[str, Any] | None = None
    paused_ms: int = Field(default=0, ge=0, le=24 * 3600 * 1000)
    behaviour: AnswerBehaviourIn | None = None


class ConversationAnswerEditIn(BaseModel):
    answer: str = Field(min_length=1, max_length=10000)


class QuestionOut(BaseModel):
    """The question on screen, as the candidate may see it (formats spec 5).

    `payload` is the CANDIDATE VIEW (`assessment_formats.types.candidate_view`):
    an MCQ's options in this candidate's order and no correct id, a
    fill-blank's template and blank sizes and no accepted answers, a coding
    question's language and starter code and no expected approach. The answer
    key never crosses this boundary.
    """

    id: uuid.UUID
    question_type: str
    payload: dict[str, Any] = {}
    #: Suggested time, shown as guidance. Navigation, not a score.
    time_allocation_seconds: int

    @field_validator("question_type")
    @classmethod
    def _known_type(cls, value: str) -> str:
        if value not in question_types.QUESTION_TYPES:
            raise ValueError(f"unknown question type {value!r}")
        return value


class ConversationOut(BaseModel):
    conversation_id: uuid.UUID
    #: active | completed | terminated
    status: str
    prompt: str | None
    progress_label: str
    answered_questions: int
    total_questions: int
    is_reask: bool = False
    answer_message_id: uuid.UUID | None = None
    #: The format of the prompt on screen. None once the conversation is over
    #: and on a follow-up or re-ask, which is always answered in prose.
    question: QuestionOut | None = None
    #: The proctoring termination notice, in plain language, when `status` is
    #: terminated. Never a reason code.
    termination_message: str | None = None


class AnswerBehaviourIn(BaseModel):
    """Keystroke and mouse TIMINGS for the answer being submitted (proctoring
    spec 4.5). Offsets in milliseconds from when the field was first focused.
    Never characters: what was typed is the answer, stored separately.
    """

    model_config = ConfigDict(extra="forbid")

    keydown_offsets_ms: list[int] = Field(default_factory=list, max_length=20_000)
    backspace_offsets_ms: list[int] = Field(default_factory=list, max_length=20_000)
    #: Blocked paste, drop and clipboard attempts on this field.
    blocked_action_count: int = Field(default=0, ge=0, le=10_000)
    #: Total milliseconds the field held focus.
    focus_ms: int = Field(default=0, ge=0, le=24 * 3600 * 1000)
    #: Pointer path, aggregated on the client at the sample rate the server
    #: configured. Never raw coordinates.
    mouse_samples: int = Field(default=0, ge=0, le=1_000_000)
    mouse_path_px: int = Field(default=0, ge=0, le=100_000_000)
    mouse_idle_ms: int = Field(default=0, ge=0, le=24 * 3600 * 1000)
    mouse_clicks: int = Field(default=0, ge=0, le=100_000)
    #: Offsets of clicks on MCQ options, for rapid-fire versus considered
    #: selection.
    option_click_offsets_ms: list[int] = Field(default_factory=list, max_length=1_000)
    #: Scroll events while the question was being read.
    scroll_events: int = Field(default=0, ge=0, le=100_000)


# ── The recruiter's view of what was actually asked and answered ─────────────
# A report states a grade; this is the evidence behind it. A recruiter deciding
# whether to interview someone, and a candidate disputing a grade, both need the
# transcript, and until 2026-08-06 the only way to read one was a psql session.


class TranscriptAnswerDetailOut(BaseModel):
    """The recruiter's view of one structured answer (formats spec 7).

    Correctness is a WORD (`correct`, `partially_correct`, `incorrect`,
    `not_answered`, or None for a format that has none), never a score. The
    AI evaluation is its reasoning, never its number. `not_executed_note` is
    present on every coding answer so a reader cannot mistake a read-only
    judgement for a verified run.
    """

    #: The candidate view of the payload, so the recruiter sees the options
    #: in the order the candidate saw them.
    payload: dict[str, Any] = {}
    #: The answer as submitted, in its type's shape.
    answer: dict[str, Any] = {}
    #: For an MCQ: the correct option ids. For a fill-blank: accepted answers
    #: per blank. Shown BESIDE the candidate's choice, marked clearly.
    answer_key: dict[str, Any] = {}
    correctness: str | None = None
    #: Per blank, for a fill-blank: `exact`, `equivalent`, `incorrect`,
    #: `not_answered`.
    blank_results: list[str] = []
    evaluation_reasoning: str | None = None
    evaluation_citations: list[str] = []
    not_executed_note: str | None = None
    time_spent: str | None = None


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
    #: The aspect this exchange probed. Provenance for the reader, never a
    #: filter that hides anything: the candidate experienced one conversation
    #: and so does this.
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
    # ── The question's format (assessment-spec-doc section 7) ────────────────
    #: One of `assessment_formats.types.QUESTION_TYPES`, or None for an
    #: exchange whose question predates the formats or whose key belongs to a
    #: retired question table. The recruiter's view dispatches on it.
    question_type: str | None = None
    #: The specific, quotable resume item an evidence-based question was
    #: anchored to. "the most valuable thing in the view" (section 7): it is
    #: what tells a recruiter what was being probed.
    resume_anchor: str | None = None
    #: The per-format view of the answer: the option chosen beside the correct
    #: one, the entry beside the accepted answers, the evaluation's reasoning.
    #: Present on a BASE exchange only; a follow-up is more evidence for the
    #: same question and would otherwise render the detail twice.
    detail: TranscriptAnswerDetailOut | None = None


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


# ── The assessment invitation link (2026-08-11) ──────────────────────────────

class InvitationResolveOut(BaseModel):
    """What the invitation landing page needs to decide where to send someone.

    ONE response shape covers the whole flow -- signed out, signed in as the
    wrong person, expired, already submitted, ready -- because the page's job
    is to branch, and a branch is far harder to get wrong when the states are
    an enum in one payload than when they are spread across status codes.

    `state` is the branch. Everything else is context for the copy.
    """

    #: One of:
    #:   needs_auth        the link is good, nobody is signed in
    #:   wrong_account     signed in, but not as the invited candidate
    #:   ready             go to the assessment
    #:   in_progress       partly answered, same destination, different copy
    #:   completed         already submitted; the report is the destination
    #:   not_invited       the recruiter has not invited this application
    #:   expired           the signed link is past its lifetime
    #:   window_closed     the 30 + 5 day posting window has ended
    #:   invalid           not one of our links
    state: str
    #: Where to send the browser once the state allows it. Always a path on
    #: this site, never an absolute URL: an open redirect in an emailed link is
    #: exactly the thing a phisher would want from this endpoint.
    redirect_to: str | None = None
    #: Masked, e.g. `as***@example.com`. Only populated for `wrong_account`, so
    #: the candidate can tell which of their addresses was invited.
    invited_email_masked: str | None = None
    #: The email currently signed in, unmasked -- the caller already knows it.
    signed_in_email: str | None = None
    job_title: str | None = None
    company_name: str | None = None
    #: Human-readable, already resolved server-side. The page renders this
    #: rather than mapping the state to copy itself, so the email, the API and
    #: the page cannot describe the same situation three different ways.
    message: str
    #: True when a prior report for this candidate is under the six-month
    #: window, so the page can explain why they are answering questions again.
    #: Never a reason to skip the assessment: under PPI the framework is
    #: generated from each job's own JD, so nothing is portable between jobs.
    recent_prior_report: bool = False
