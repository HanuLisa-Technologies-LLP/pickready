"""Additive data model for the PPI assessment workflow.

Two tables joined the original Functional Skills set on 2026-07-30:

  `job_competencies`   -- the job's PPI framework: Primary Skills, Secondary
                          Skills and Behavioural Competencies, generated once
                          per job and FIXED once the Hiring Manager saves it.
  `candidate_questions` -- the PPI questions generated for ONE candidate
                          against that framework. Per candidate, unlike
                          `technical_questions`, which stay per job so every
                          applicant answers the same technical set.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin


class TechnicalQuestion(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "technical_questions"
    __table_args__ = (
        UniqueConstraint("job_id", "ordinal", name="uq_technical_question_job_ordinal"),
        Index("ix_technical_questions_job", "job_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    skill: Mapped[str] = mapped_column(String(255), nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    rubric_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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


class CandidateQuestion(Base, UUIDPKMixin, CreatedAtMixin):
    """One PPI question generated for one candidate (spec §6.4).

    Keyed to the competency it probes, which is how the PPI scorer knows which
    framework entry an answer is evidence for. The conversation stamps
    `question_key = str(CandidateQuestion.competency_id)` on the message.
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


class AssessmentConversation(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "assessment_conversations"
    __table_args__ = (
        UniqueConstraint("job_candidate_link_id", name="uq_assessment_conversation_link"),
        Index("ix_assessment_conversations_job", "job_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    job_candidate_link_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("job_candidate_links.id", ondelete="CASCADE"), nullable=False)
    grade: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="active")
    next_question_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

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
    validation_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    suggested_probes_json: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
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
