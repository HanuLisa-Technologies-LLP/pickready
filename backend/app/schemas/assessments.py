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


class JobSetupOut(BaseModel):
    """The one manual step in the pipeline (spec §10), as one payload.

    Draft v4 made that step TWO halves finalised in ONE session: the PPI matrix
    and the job's Matching category list. A job reaches "Ready for Candidates"
    when both are stamped, and everything after that -- the candidate
    conversation, scoring, report synthesis -- runs with no further human
    involvement.

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
    swot_analysis_ready: bool = False
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
    #: EVIDENCE CONFIDENCE (0107): High, Moderate, Low, or "Insufficient
    #: evidence". How well corroborated the evidence behind the grade is, and
    #: never a statement about the candidate: it is derived after scoring, from
    #: distinct evidence ORIGINATORS, and it moves no grade.
    #:
    #: NULL on every row written before 0107, and nothing is substituted. A
    #: report is immutable and the evidence set an older one was written from
    #: cannot be reconstructed; a plausible word here would be the only
    #: uncheckable claim on the line.
    evidence_confidence: str | None = None
    #: The named sources it rests on, as words a reader recognises. Empty on a
    #: pre-0107 row, which renders as no source line rather than as none found.
    evidence_sources: list[str] = []


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


class ValidationPointOut(BaseModel):
    """One Recommended Human Validation Point (0107).

    NOTE THE FIELD LIST. No severity, no score, no priority and no decision.
    Order carries what ranking the section is entitled to state, exactly as the
    Proctoring Report does, and a field an outcome could be written into is a
    field an outcome eventually appears in.
    """

    area: str
    #: `confidence | borderline | contradiction`. Why this area is listed, as a
    #: code the UI can group by; the sentence a reader sees is `reason`.
    driver: str
    #: The evidence confidence word, where the driver is a confidence verdict.
    #: Null rather than blank, so a renderer can tell "not about confidence"
    #: from "confidence was empty".
    confidence: str | None = None
    reason: str
    #: One interview probe. Advisory, grounded in the record, and never phrased
    #: as an advance or reject decision.
    probe: str


class ValidationPointsOut(BaseModel):
    """Recommended Human Validation Points (0107).

    DELIBERATELY NOT THE GAP ANALYSIS. That section is GRADE driven and
    unbounded; this one is CONFIDENCE and CONTRADICTION driven and stops at
    five. A Highly Matching item resting on the candidate's own unchecked
    account is invisible to the first and is the first row of the second.
    """

    note: str = ""
    points: list[ValidationPointOut] = []
    #: Said in words when nothing needs checking, rather than blank space.
    no_points_statement: str | None = None


class ClaimEvidenceEntryOut(BaseModel):
    """One row of the Evidence vs Claim Summary (0107)."""

    #: The competency or employer the claim bears on. May be empty: a claim the
    #: matrix does not grade is still part of the candidate's account.
    area: str = ""
    #: What the candidate asserted, in the ledger's normalised wording.
    claim: str
    #: What was found when it was looked for. ABSENCE OF EVIDENCE IS NEVER
    #: RENDERED AS THE CLAIM BEING FALSE: the sentence for an unevidenced claim
    #: says in so many words that it is a gap in what was examined.
    evidence: str
    #: How well corroborated that evidence is, as a word.
    confidence: str | None = None


class ClaimEvidenceOut(BaseModel):
    """Evidence vs Claim Summary (0107).

    A CLAIM IS NOT A FACT, and this is the section that says so. It never
    states whether a claim is true; it states what the record holds.
    """

    note: str = ""
    entries: list[ClaimEvidenceEntryOut] = []
    no_claims_statement: str | None = None


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
    #: Evidence vs Claim Summary (0107). Empty on a report written before it,
    #: which renders without the section rather than with an empty one.
    claim_evidence: ClaimEvidenceOut = ClaimEvidenceOut()
    #: Recommended Human Validation Points (0107). Same reading of empty.
    validation_points: ValidationPointsOut = ValidationPointsOut()
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
    #: Whether the candidate consented to their assessment being retained for
    #: future jobs (Consent & Privacy spec, 2026-09-05): Download enabled if
    #: Yes, View-only if No or never asked. Advertised here so the UI hides
    #: the Download control instead of offering a button the PDF route will
    #: refuse with 403. Viewing is not gated by this flag.
    report_download_allowed: bool = False


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
    #: Which input mechanism this session uses (dual-mode spec section 2):
    #: 'conversational' or 'video_interview'. Defaulted so every constructor
    #: that predates dual mode stays truthful about its own rows.
    mode: str = "conversational"
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


# ── Dual-mode assessment (2026-09-05 spec sections 2-5, 16) ──────────────────


class AssessmentModeIn(BaseModel):
    """The candidate's mode choice, made before consent and before starting."""

    mode: str

    @field_validator("mode")
    @classmethod
    def _known_mode(cls, value: str) -> str:
        from app.models.dual_mode import ASSESSMENT_MODES

        if value not in ASSESSMENT_MODES:
            raise ValueError(f"unknown assessment mode {value!r}")
        return value


class ConsentTermsOut(BaseModel):
    """One mode's consent screen, exactly as configured (spec 3.2, 3.3).

    The versions travel with the text so the client shows what the server will
    stamp; the row written on acceptance re-reads the settings server-side and
    never trusts these echoes back.
    """

    assessment_mode: str
    text: str
    consent_version: str
    privacy_policy_version: str
    terms_version: str
    #: The Stage B per-item catalogue (vivekium feature 6), server-authored:
    #: {key, stage, text, version} each. The screen renders these VERBATIM
    #: beside the mode text and ticks them one at a time; acceptance stamps
    #: each item individually server-side.
    items: list[dict] = []


class AssessmentConsentIn(BaseModel):
    """The Stage B items the candidate actually ticked.

    The keys are sent rather than a single boolean because the record has to
    be able to say each item was agreed to separately (vivekium feature 6).
    The server refuses anything but the complete Stage B set, so this is the
    candidate's list of ticks and never a way to consent to a subset.
    """

    consent_keys: list[str] = []


class ModeStateOut(BaseModel):
    """Where this assessment session stands in the mode/consent flow."""

    mode: str
    #: True once the assessment has begun (or a recording exists): the mode
    #: can no longer change, because the records already written belong to it.
    mode_frozen: bool
    #: Whether a consent row exists for THIS session in THIS mode.
    consented: bool
    #: The consent terms for the CURRENT mode.
    consent: ConsentTermsOut


class VideoQuestionOut(BaseModel):
    """One question of the video interview, in served order."""

    ordinal: int
    #: The text the candidate reads aloud and answers in speech.
    prompt: str
    #: The format detail (candidate view only; the answer key never crosses).
    question: QuestionOut


class VideoStartOut(BaseModel):
    """The video interview, opened: the recording session and the questions."""

    conversation_id: uuid.UUID
    recording_id: uuid.UUID
    status: str
    questions: list[VideoQuestionOut]
    #: Ceilings the recorder must respect, served so the client and server
    #: never disagree about a number (the proctoring config rule, applied here).
    max_upload_bytes: int
    max_duration_seconds: int


class VideoMarkIn(BaseModel):
    """The next-question control: the question now on screen."""

    question_id: uuid.UUID


class VideoRecordingStatusOut(BaseModel):
    """The candidate's honest view of their recording (spec 16).

    `status` is the lifecycle state, `message` is the plain-language account
    of it. No score, no grade, no internal identifier beyond the recording's
    own id, and the message never pretends a failed step ran.
    """

    recording_id: uuid.UUID
    status: str
    message: str
    #: True only for `upload_failed`, where the fix is the candidate's own
    #: re-upload; every other failure is retried server-side by staff.
    can_retry_upload: bool = False


# ── The AI-assisted Job SWOT Analysis (2026-09-13 spec, sections 23 to 33) ───

class SwotAnalysisSectionsIn(BaseModel):
    """A human's four sections. Every one is optional and may be empty: a team
    that wants to clear a section they disagree with must be able to, and a
    required field would force them to keep the model's paragraph."""

    strengths: str = Field(default="", max_length=4000)
    weaknesses: str = Field(default="", max_length=4000)
    opportunities: str = Field(default="", max_length=4000)
    threats: str = Field(default="", max_length=4000)
    #: The version the editor loaded. Sent back so a save that would overwrite
    #: somebody else's newer save is refused instead of silently winning.
    expected_version: int = Field(ge=0)


class SwotAnalysisGenerateIn(BaseModel):
    """Section 32: regeneration over human-edited content is an explicit act.

    Defaults to False so the destructive path is never the one a caller
    reaches by omission."""

    confirm_overwrite: bool = False


class SwotAnalysisOut(BaseModel):
    """The SWOT document plus everything the UI needs to render the right state.

    `can_edit` is included deliberately. The frontend could resolve it from
    the capability list it already holds, and for `edit_swot` alone that would
    be right; it could not resolve the per-JOB half of the same question,
    because assignment scope and lifecycle state are properties of this job
    and not of the person. So the server answers the whole question once, on
    the resource, and the UI renders from the answer. It is not a security
    boundary: the write routes re-authorize independently.
    """

    job_id: uuid.UUID
    #: not_generated | generated | failed | edited
    status: str
    strengths: str | None = None
    weaknesses: str | None = None
    opportunities: str | None = None
    threats: str | None = None
    #: "ai" once a generation has succeeded, null for a hand-written document.
    generated_by: str | None = None
    last_generated_at: datetime | None = None
    #: Why the last generation failed. Rendered only in the `failed` state.
    generation_error: str | None = None
    human_edited: bool = False
    last_modified_at: datetime | None = None
    last_modified_by_name: str | None = None
    version: int = 0
    #: True when a confirmed regeneration replaced human content that can
    #: still be put back.
    can_restore_previous: bool = False
    #: The effective answer for THIS user on THIS job.
    can_edit: bool = False
