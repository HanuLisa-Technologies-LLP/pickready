"""Additive data model for the PPI assessment workflow.

Two tables joined the original Functional Skills set on 2026-07-30:

  `job_competencies`   -- the job's PPI framework: Primary Skills, Secondary
                          Skills and Behavioural Competencies, generated once
                          per job and FIXED once the Hiring Manager saves it.
  `candidate_questions` -- the PPI questions generated for ONE candidate
                          against that framework.

The two technical-question tables that used to sit beside them, the per-job
preset bank and the per-candidate technical track, were empty on pilot and
are DROPPED by migration 0128 together with their mappings.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

#: The CHECK on `assessment_conversations.end_reason`: Vaada's six named stop
#: conditions, spelled out here and again in migration 0116.
#:
#: LITERALS RATHER THAN AN IMPORT OF `interviewer.STOP_CONDITIONS`, which is
#: where the vocabulary is authored. A model module reading an attribute off a
#: service module at import time is the exact shape `tests/test_import_graph`
#: exists to refuse: `interviewer` sits on the service graph, and the read
#: becomes `partially initialized module` the first time a cycle reaches it
#: from the other side. The two lists are held in step by
#: `tests/test_vaada_end_reason`, which compares this constraint, the
#: migration and the vocabulary against each other, the same way capability
#: seeds and Runbook data are kept honest.
END_REASON_VALUES: tuple[str, ...] = (
    "floor_reached",
    "every_dimension_covered",
    "no_probe_outstanding",
    "no_conflict_outstanding",
    "evidence_sufficient",
    "prompts_exhausted",
)


#: `job_competencies.authored_by`. Mirrored by a CHECK constraint (0118).
AUTHORED_BY_SUTRA = "sutra"
AUTHORED_BY_HUMAN = "human"
AUTHORED_BY: tuple[str, ...] = (AUTHORED_BY_SUTRA, AUTHORED_BY_HUMAN)


class JobCompetency(Base, UUIDPKMixin, CreatedAtMixin):
    """One entry in a job's PPI evaluation framework (spec §6.2).

    `required_level` is the internal representative score for the band the
    framework agent assigned -- how strongly THIS job needs this competency. It
    draws the "Job Requirement" shape on the radar charts and is projected to a
    grade WORD before it ever crosses the API boundary; no number is displayed.

    Editing is soft: `is_active` false rather than a DELETE, because a report or
    a generated candidate question may already reference the row.
    """

    __tablename__ = "job_competencies"
    __table_args__ = (
        UniqueConstraint("job_id", "category", "name", name="uq_job_competency_name"),
        Index("ix_job_competencies_job", "job_id", "category", "ordinal"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    #: primary_skill | secondary_skill | behavioural (services/ppi.CATEGORIES)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    required_level: Mapped[int] = mapped_column(Integer, nullable=False, default=82)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── Sutra's seven stages (migration 0064, Runbook §19) ───────────────────
    #
    # Nothing enters the Tatva matrix without completing all seven, and these
    # columns are where the other six land. Before 0064 the row kept stage 1's
    # output and threw the rest away, so "why is this item weighted the way it
    # is" could only be answered by re-running a pipeline whose inputs may since
    # have changed -- which is not an answer.
    #
    # ALL NULLABLE, because a row written before 0064 has none of them and a
    # NOT NULL column would have required inventing values for criteria that
    # were never derived this way. `hiring.scorecard.load_frozen_matrix` reads
    # the absence as "this job predates the seven-stage pipeline" and refuses,
    # rather than filling in a weight nobody derived.
    #
    # INTERNAL. `weight`, `threshold_json` and `provenance_json` are ranking
    # data of the same status as `report_dimensions.required_level`: they are
    # projected to words by `ppi._matrix_item` and to sentences by
    # `hiring.scorecard.plain_provenance`, and never serialised as numbers.

    #: Stage 1's second output: which of the five evaluation dimensions this
    #: competency speaks to. The situation-type multiplier acts through it.
    dimension: Mapped[str | None] = mapped_column(String(40))
    #: Stage 2. "What would we SEE if this were true?"
    observable_evidence: Mapped[str | None] = mapped_column(Text)
    #: Stage 3, a list of `department_models.EVIDENCE_SOURCES` keys.
    evidence_sources: Mapped[list | None] = mapped_column(JSONB)
    #: Stage 4, one of `transformation.METHODS`.
    assessment_method: Mapped[str | None] = mapped_column(String(40))
    #: Stage 5's value. The four terms behind it are in `provenance_json`.
    weight: Mapped[float | None] = mapped_column(Float)
    #: Stage 6, as `transformation.Threshold.as_dict()`.
    threshold_json: Mapped[dict | None] = mapped_column(JSONB)
    #: Stage 7, the only optional stage.
    disqualifier: Mapped[str | None] = mapped_column(Text)
    #: The Layer 1 / Layer 2 / Layer 3 terms and every clamp that fired.
    provenance_json: Mapped[dict | None] = mapped_column(JSONB)
    #: The hiring manager's own sentence, quoted so the review screen can show
    #: their words beside the criterion those words produced.
    swot_origin: Mapped[str | None] = mapped_column(Text)
    #: The department-model competency stage 1 named this from, or NULL when the
    #: requirement is genuinely role-specific. NULL is an honest provenance.
    anchor_key: Mapped[str | None] = mapped_column(String(80))
    #: THE SKILL'S PRIORITY WITHIN ITS BUCKET, 1 = highest (migration
    #: 0118_skills_contract). Until that migration it was section 20.3's
    #: force-ranking position across every scored competency of the job; the
    #: migration rewrote it per bucket, in place, for every job whose skills
    #: are not locked. Assigned by Sutra, INTERNAL, never shown to or edited by
    #: the recruitment team, and never serialised
    #: (`services/assessment_contract.ContractSkill.priority` is the reader).
    force_rank: Mapped[int | None] = mapped_column(Integer)
    #: Who wrote this entry: `sutra` (drafted by the model) or `human` (added
    #: or renamed by the hiring team). The Tatva human authority rule
    #: (2026-09-23) made the absence of `swot_origin` the signal for "the
    #: human's entry"; this column states it outright, because a Sutra draft
    #: may legitimately carry no SWOT quotation (a JD-sourced skill) and must
    #: still not read as the team's own. Values in `AUTHORED_BY`, mirrored by
    #: `ck_job_competencies_authored_by`.
    authored_by: Mapped[str] = mapped_column(
        String(10), nullable=False,
        default=AUTHORED_BY_HUMAN, server_default=AUTHORED_BY_HUMAN,
    )


class CandidateQuestion(Base, UUIDPKMixin, CreatedAtMixin):
    """One question generated for one candidate against the job's PPI matrix.

    Keyed to the competency it probes, which is how the PPI scorer knows which
    matrix item an answer is evidence for.

    THE MESSAGE KEY IS THIS ROW'S OWN ID, NOT ITS COMPETENCY ID. This docstring
    said `question_key = str(CandidateQuestion.competency_id)` and had been
    wrong since the unified conversation landed: `api/assessments._conversation_prompts`
    stamps `str(question.id)`, and `functional_assessment._score_item` looks the
    answer up with `answers.get(str(question.id))`. Those two agree, which is
    why nothing was broken -- but a reader who trusts this sentence writes a
    join against `competency_id`, gets zero rows, and cannot see why. Grouping
    BY competency is a second step the scorer does separately
    (`questions_by_item`), and that is where the competency actually enters.
    `tests/test_conversation_key_contract.py` pins the pair.

    THIS IS NOW THE WHOLE CONVERSATION (Draft v4)
    ---------------------------------------------
    Must-have, Nice-to-have and Behavioural questions are all rows of this
    table. There is no second question stream: the candidate answers one blended
    sequence and never sees that different scoring methods sit behind different
    parts of it (spec §7).

    `rubric_json` is what makes that possible. A Must-have or Nice-to-have answer
    is scored against ITS OWN question's rubric, so the rubric is written by the
    same model call that writes the question and persisted before the candidate
    reads either -- the guarantee the retired per-candidate technical track was
    built to give, now given by the row the unified conversation actually asks
    from.

    It is NULL on a Behavioural row, and that is a statement rather than an
    omission: a Behavioural answer is scored by judgement because there is no
    single correct answer to weigh it against (spec §8).

    `generated_at` is NULL when a model never wrote this pair and the candidate
    read the deterministic probe instead. That NULL is the honest record of a
    degradation and is what telemetry counts to keep it from being silent.
    """

    __tablename__ = "candidate_questions"
    __table_args__ = (
        UniqueConstraint("job_candidate_link_id", "ordinal", name="uq_candidate_question_ordinal"),
        Index("ix_candidate_questions_link", "job_candidate_link_id", "ordinal"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    job_candidate_link_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job_candidate_links.id", ondelete="CASCADE"), nullable=False)
    competency_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job_competencies.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    rubric_json: Mapped[dict | None] = mapped_column(JSONB)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── Question formats (migration 0076, assessment-spec-doc section 4) ─────
    #
    # This row IS the specification's `assessment_questions` row: `ordinal` is
    # its `sequence`, `competency_id` its `competency` and `rubric_json` its
    # `rubric`. The format vocabulary lives in
    # `services/assessment_formats/types.py` and is pinned by a database CHECK.
    #
    # `payload_json` is the type-specific structure (options and the correct
    # option ids for an MCQ, the template and accepted answers for a
    # fill-in-the-blank, the language and starter code for a coding question,
    # the sub-type and anchor source for an evidence question). It holds the
    # ANSWER KEY where one exists, so it never crosses the candidate boundary
    # unprojected: `assessment_formats.types.candidate_view` strips it.
    #
    # `resume_anchor` is the specific, quotable resume item an evidence
    # question probes. Stored so the recruiter's Q&A view can show what was
    # being probed, which is the most valuable thing on that screen.
    #
    # Rows written before 0076 read as `short_answer`: they were text
    # questions with no stored anchor and no evidence rubric, and relabelling
    # them as evidence-based would claim a provenance they do not have.
    question_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="short_answer", server_default="short_answer"
    )
    payload_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    # Resume pre-fill (migration 0104, vivekium feature 2 under C2): the
    # answer recorded instead of asking, and its provenance. NULL = asked.
    prefilled_answer: Mapped[str | None] = mapped_column(Text)
    prefill_source: Mapped[str | None] = mapped_column(String(30))
    resume_anchor: Mapped[str | None] = mapped_column(Text)
    #: Suggested time, in seconds. Bounds the assessment's total length per
    #: role (composition rule 6); shown to the candidate as guidance only.
    time_allocation_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=180, server_default="180"
    )
    #: INTERNAL. This question's contribution within its matrix item. It is
    #: what makes evidence dominance structural: a supporting-format question
    #: carries less of the item than an evidence question does.
    weight: Mapped[float] = mapped_column(Float, nullable=False, default=1.0, server_default="1.0")


class AssessmentAnswer(Base, UUIDPKMixin, CreatedAtMixin):
    """The structured answer record, one per (conversation, question).

    The transcript (`assessment_messages`) is unchanged and remains the
    conversational record every scorer already reads by `question_key`. This
    row is what the OBJECTIVE scorer and the recruiter's Q&A view read: the
    type-specific answer as submitted, when the question was opened and
    answered, the deterministic auto-score for an objective type, the AI
    evaluation with its reasoning for a subjective one, and how many times the
    answer changed.

    `auto_score` and the score inside `ai_evaluation_json` are INTERNAL. They
    fold into the matrix item's score through `functional_assessment` and are
    projected to words at the boundary; neither is ever serialised as a number.
    The recruiter sees correctness as a word and the evaluation as prose.

    `time_spent_seconds` is measured by the SERVER from
    `assessment_conversations.prompt_shown_at`, less any time a blocking
    proctoring warning held the screen. A client-reported duration would be a
    number the client chose.
    """

    __tablename__ = "assessment_answers"
    __table_args__ = (
        UniqueConstraint("conversation_id", "question_id", name="uq_assessment_answer_question"),
        Index("ix_assessment_answers_conversation", "conversation_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assessment_conversations.id", ondelete="CASCADE"), nullable=False)
    question_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("candidate_questions.id", ondelete="CASCADE"), nullable=False)
    message_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("assessment_messages.id", ondelete="SET NULL"))
    question_type: Mapped[str] = mapped_column(String(20), nullable=False)
    answer_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    time_spent_seconds: Mapped[int | None] = mapped_column(Integer)
    auto_score: Mapped[float | None] = mapped_column(Float)
    ai_evaluation_json: Mapped[dict | None] = mapped_column(JSONB)
    revision_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")


class AssessmentConversation(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "assessment_conversations"
    __table_args__ = (
        UniqueConstraint("job_candidate_link_id", name="uq_assessment_conversation_link"),
        CheckConstraint(
            "end_reason IS NULL OR end_reason IN ({})".format(
                ", ".join(f"'{word}'" for word in END_REASON_VALUES)
            ),
            name="ck_assessment_conversations_end_reason",
        ),
        Index("ix_assessment_conversations_job", "job_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    job_candidate_link_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job_candidate_links.id", ondelete="CASCADE"), nullable=False)
    grade: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    next_question_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # ── Assessment mode (migration 0081, dual-mode spec section 2) ───────────
    # Which input mechanism this session uses: 'conversational' (the existing
    # typed conversation) or 'video_interview' (spoken answers, recorded,
    # transcribed and structured into the SAME records the scorers read).
    # The server default keeps every legacy row truthful: those sessions were
    # conversational. Frozen once `started_at` is stamped -- switching input
    # mechanisms mid-assessment would leave half the evidence in each channel.
    mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="conversational",
        server_default="conversational",
    )

    # ── Adaptive follow-up state (migration 0038) ────────────────────────────
    # A follow-up is GENERATED when one answer is submitted and ANSWERED on the
    # next request, so it has to survive between the two. It deliberately does
    # not extend the prompt list and does not advance `next_question_index`,
    # because that list's length decides when billing fires and its keys are
    # what the scorers group by.
    #
    # `pending_question_key` is the key of the question that PRODUCED the
    # follow-up, so the follow-up's answer joins that question's existing group
    # in `answers_by_key` and every scorer keeps working unchanged.
    pending_prompt: Mapped[str | None] = mapped_column(Text)
    pending_question_key: Mapped[str | None] = mapped_column(String(80))
    pending_domain: Mapped[str | None] = mapped_column(String(20))
    # A probe follows an accepted answer; a re-ask keeps the base-question
    # counter still. NULL preserves pre-migration pending rows as probes.
    pending_kind: Mapped[str | None] = mapped_column(String(20))
    reasks_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Persisted rather than counted from the transcript: an interview that can
    # ask "one more thing" must be provably finite, and a stored counter holds
    # even if a message fails to persist or a request is retried.
    follow_ups_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # ── Delivered wording of the next BASE question (migration 0039) ─────────
    # `services/interviewer.compose_next_question` says the next scripted
    # question the way an interviewer would say it here, conditioned on the
    # transcript. It is generated when the PREVIOUS answer is submitted and
    # answered on the NEXT request, so like `pending_prompt` it has to survive
    # between the two.
    #
    # It exists so the transcript records what the candidate ACTUALLY READ. The
    # agent message is written on the request that carries the answer, so
    # without this column the composed question would be shown and the stored
    # question logged, and every scorer would read a transcript that never
    # happened.
    #
    # NULL means "no rewrite available, use the stored text", which is the
    # product's previous behaviour and always a correct thing to ask. It never
    # changes WHICH question is asked: `next_question_index` and the question
    # key are untouched by delivery, and `_substance_preserved` refuses a
    # rewrite that dropped a specific term.
    delivered_prompt: Mapped[str | None] = mapped_column(Text)

    # ── When the prompt on screen was delivered (migration 0076) ─────────────
    # Stamped by `start` and by every `respond` that hands the candidate a new
    # prompt. `assessment_answers.time_spent_seconds` is measured from it on
    # the server, so the per-question timing the recruiter reads is not a
    # figure the client reported.
    prompt_shown_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── Invitation + progress tracking (migration 0018) ──────────────────────
    # These three columns existed in the database but not on this model, so
    # every attribute read of them raised AttributeError and turned
    # POST /assessments/conversations/links/{id}/start into a 500. The row IS
    # the invitation, so the gate cannot work without them being mapped.
    invitation_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    invited_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── Why this session ended (migration 0116, change request 28B) ──────────
    # The assessment has stopped early on evidence coverage since 2026-08-23:
    # `ppi.conversation_may_close` decides it and the question ceiling is a
    # ceiling rather than a target. What the product could not answer is WHICH
    # of the two endings a given session had, because the decision lived in a
    # log line and log lines are retained, not queried per candidate. Without
    # it "ended at 14 of 20" and "ended at 14 of 14" read identically in the
    # table, and the second is a truncated interview while the first is the
    # feature working.
    #
    # One of `interviewer.STOP_CONDITIONS`, mirrored by a CHECK constraint.
    # NULL means NOT RECORDED, never "no reason": every row written before
    # this column existed carries NULL, and so does a video-mode session,
    # whose completion is the end of a processing pipeline rather than a
    # stopping decision (`services/video/processing`), and a session
    # terminated by proctoring, which did not stop, it was stopped. A zero
    # value or a default word there would be an assertion about a decision
    # that was never made.
    #
    # INTERNAL. It is provenance for the recruitment team and the operator, in
    # the same class as `interview_telemetry`'s counters: it says something
    # about how much of the matrix a candidate was asked, which is exactly the
    # kind of fact a candidate would read as a verdict. No candidate-facing or
    # employer-facing schema serialises it, and `tests/test_vaada_end_reason`
    # sweeps the response schemas to keep it that way.
    end_reason: Mapped[str | None] = mapped_column(String(40))

    # ── The assessment contract this session runs against (0118) ─────────────
    # Bound by `assessment_contract.lock_contract` at the session's start: the
    # immutable skills snapshot the questions came from and its content
    # digest. Vaada (the conversation) and Miti (the grade) both read the
    # contract through `load_contract_for_conversation`, which reads THIS
    # binding, so a later snapshot version can never move a candidate who has
    # already started. NULL means the session has not started, or started
    # before contracts existed and was never bound; the migration bound every
    # started session it found. SET NULL on the snapshot side only because a
    # snapshot is deleted solely by the cascade of its job or tenant, which
    # takes this row with it anyway.
    skill_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_skill_snapshots.id", ondelete="SET NULL"),
    )
    contract_digest: Mapped[str | None] = mapped_column(String(64))

    # ── Credit reconciliation (migration 0026) ───────────────────────────────
    # The daily reconciliation job charges an abandoned assessment once and only
    # once. `credit_reconciled_at` is the idempotency stamp; without it a
    # nightly sweep would re-charge the same no-show every night forever.
    reminders_sent: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_reminder_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    credit_reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    credit_event: Mapped[str | None] = mapped_column(String(40))


class AssessmentMessage(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "assessment_messages"
    __table_args__ = (Index("ix_assessment_messages_conversation", "conversation_id", "ordinal"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assessment_conversations.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    speaker: Mapped[str] = mapped_column(String(20), nullable=False)
    domain: Mapped[str] = mapped_column(String(20), nullable=False)
    question_key: Mapped[str | None] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Candidate-message-only relevance decision. Agent messages retain the
    # nullable/default values.
    answer_label: Mapped[str | None] = mapped_column(String(30))
    evidence_gap: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


class ReportSkillEvidence(Base, UUIDPKMixin, CreatedAtMixin):
    """Structured evidence extracted from one completed conversation.

    The six JSON arrays preserve concrete snippets and explicit absences rather
    than compressing the transcript into another opaque score. A report
    narrative can therefore cite what this candidate actually said, while the
    full transcript remains the immutable source record.
    """

    __tablename__ = "report_skill_evidence"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "category",
            "skill",
            name="uq_report_skill_evidence",
        ),
        Index(
            "ix_report_skill_evidence_conversation",
            "conversation_id",
            "category",
        ),
        Index("ix_report_skill_evidence_tenant", "tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assessment_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    skill: Mapped[str] = mapped_column(String(255), nullable=False)
    question_keys: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    technical_precision: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    depth: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    problem_solving_structure: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    role_relevance: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    concrete_examples: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    explicit_gaps: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )


class FunctionalSkillsReport(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "functional_skills_reports"
    __table_args__ = (
        UniqueConstraint("job_candidate_link_id", name="uq_functional_report_link"),
        Index("ix_functional_reports_job", "job_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    job_candidate_link_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job_candidate_links.id", ondelete="CASCADE"), nullable=False)
    grade: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="ready")
    overall_summary: Mapped[str] = mapped_column(Text, nullable=False)
    #: INTERNAL. The PPI Assessment's overall score, projected to one of the
    #: four grade words by the API. Nullable for reports written before 0030,
    #: which recompute it from their dimensions on read.
    overall_score: Mapped[int | None] = mapped_column(Integer)
    #: llm_rubric | deterministic_fallback | no_transcript. Was previously
    #: smuggled inside validation_json, which made a scoring-health field look
    #: like candidate-submitted data; it is a property of the RUN.
    scoring_mode: Mapped[str | None] = mapped_column(String(30))
    #: Siddhi's own quality gate failed on this report and it shipped anyway.
    #:
    #: Refusing to write the report would take the product's entire output away
    #: over what may be a single ungrounded phrase, so it ships. What makes that
    #: honest rather than misleading is that it ships MARKED, in the row a
    #: recruiter's report is read from -- a log line is invisible to the one
    #: person who acts on the document. Same posture as
    #: `reliability/degradation`, where a stub is only acceptable because it is
    #: never allowed to read like a result.
    #:
    #: False means NOT FLAGGED, never "verified clean": every report written
    #: before 2026-08-23 predates the gate entirely.
    needs_human_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    #: The gate's findings, as issue/location/severity records. NEVER the prose
    #: they were found in: a finding's detail can quote the report, and this row
    #: is far more widely readable than the report it describes.
    review_findings_json: Mapped[list | None] = mapped_column(JSONB)
    #: PROVENANCE (0094): the model id that wrote the delivered prose, resolved
    #: at write time from the closed MODEL_FOR_TASK mapping for
    #: `report_synthesis`. NULL means either "written before provenance was
    #: recorded" or "no model produced this" (a deterministic-fallback run),
    #: and both readings are deliberate: a fallback report naming a model would
    #: claim work that never happened. Never backfilled.
    model_id: Mapped[str | None] = mapped_column(Text)
    #: PROVENANCE (0094): the registry labels of the versioned prompts this run
    #: used, `name@declared+digest`, semicolon separated. HONEST ABOUT ITS OWN
    #: LIMIT: the remark system prompt lives inline in
    #: `functional_assessment.bounded_remark` rather than in the registry, so
    #: its version is the deployed image, not this column, and the column
    #: records only what the registry actually versions. NULL under the same
    #: two readings as `model_id`.
    prompt_version: Mapped[str | None] = mapped_column(Text)
    validation_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    #: RETIRED. The Gap Analysis & Action Plan replaced this section entirely
    #: (spec §9.6). Nothing writes it any more and it was deliberately not
    #: dropped in the same change that stopped writing it, so a rollback needs
    #: no data restore and a report written before Draft v4 still renders.
    suggested_probes_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    #: The Gap Analysis section: an Interview Focus Summary, the hard-cap flag,
    #: and three groups each holding its gaps with their grades, their reused
    #: item remarks and their grounded interview probes.
    gap_analysis_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: Recommended Human Validation Points (0107). Three to five areas where a
    #: person should look before deciding, driven by EVIDENCE CONFIDENCE and by
    #: contradictions, not by grade. NULLABLE and never backfilled: a report
    #: written before 0107 has no such section and renders without one rather
    #: than with an invented one.
    validation_points_json: Mapped[dict | None] = mapped_column(JSONB)
    #: Evidence vs Claim Summary (0107). The material claims, what evidence was
    #: identified for each, and how well corroborated that evidence is. Same
    #: NULL reading as the column above.
    claim_evidence_json: Mapped[dict | None] = mapped_column(JSONB)
    synthesized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReportDimension(Base, UUIDPKMixin, CreatedAtMixin):
    """One rated line of a report.

    `category` is one of:
      matching        -- an AI Score parameter (spec §3, four of them)
      primary_skill   -- a PPI Primary Skill
      secondary_skill -- a PPI Secondary Skill
      behavioural     -- a PPI Behavioural Competency
      technical       -- a JD skill probed by the job's technical bank

    `score` is INTERNAL and never leaves the server: the API projects it
    through `services.rating.grade_for_percent` into one of the four grades.
    `required_level` is the job's requirement for the same item, copied from the
    framework so a report stays readable after the job is edited; it is null on
    matching and technical rows, which have no "job requirement" shape.
    """

    __tablename__ = "report_dimensions"
    __table_args__ = (
        UniqueConstraint("report_id", "category", "name", name="uq_report_dimension"),
        Index("ix_report_dimensions_report", "report_id", "category", "ordinal"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    report_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("functional_skills_reports.id", ondelete="CASCADE"), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    required_level: Mapped[int | None] = mapped_column(Integer)
    remark: Mapped[str] = mapped_column(Text, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    #: EVIDENCE CONFIDENCE (0107): `high | moderate | low | insufficient`, the
    #: aggregator's own four words. It reports how well corroborated this line's
    #: evidence base is and it MOVES NOTHING: `score` above was decided before
    #: this was computed, and `services/evidence_confidence` imports no scorer.
    #:
    #: NULL means the row was written before 0107. It is never backfilled,
    #: because the evidence set an older report was written from cannot be
    #: reconstructed and writing a plausible word into it would state a finding
    #: about an evidence base nobody assembled.
    evidence_confidence: Mapped[str | None] = mapped_column(String(12))
    #: The source KEYS behind it (`resume`, `answer`, `bgv` ...), never the
    #: client-facing labels. Labels are copy and copy gets corrected; a label
    #: frozen into an immutable row could not be.
    evidence_sources: Mapped[list | None] = mapped_column(JSONB)
