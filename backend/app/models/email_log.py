"""Outbound email audit trail (migration 0016, spec §6).

One row per message across all six email types, holding the subject and body
that were ACTUALLY sent — after any recruiter edit — plus delivery outcome.
This is the record that answers "what did we tell this candidate, when, and did
a human read it first?", so it is written before the send is attempted and
updated by the worker, never only on success.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

#: The six email types (spec §6.1). Mirrored by the ck_email_log_type CHECK
#: constraint in migration 0016 — keep both in step.
EMAIL_TYPE_APPLICATION_CONFIRMATION = "application_confirmation"
EMAIL_TYPE_ASSESSMENT_REMINDER = "assessment_reminder"
EMAIL_TYPE_SHORTLIST = "shortlist"
EMAIL_TYPE_REJECTED = "rejected"
EMAIL_TYPE_HOLD = "hold"
EMAIL_TYPE_QUESTION_BANK_REMINDER = "question_bank_reminder"
# ── Pipeline transition emails (migration 0019, spec §4.1) ───────────────────
# One per stage that warrants telling the candidate. `assessment_in_progress`
# is deliberately absent: it fires when the candidate opens the assessment, and
# mailing someone about something they just did is noise.
EMAIL_TYPE_ASSESSMENT_INVITATION = "assessment_invitation"
EMAIL_TYPE_ASSESSMENT_COMPLETE = "assessment_complete"
EMAIL_TYPE_INTERVIEW_SCHEDULED = "interview_scheduled"
EMAIL_TYPE_INTERVIEW_COMPLETED = "interview_completed"
EMAIL_TYPE_OFFER_EXTENDED = "offer_extended"
EMAIL_TYPE_JOINED = "joined"

EMAIL_TYPES: tuple[str, ...] = (
    EMAIL_TYPE_APPLICATION_CONFIRMATION,
    EMAIL_TYPE_ASSESSMENT_REMINDER,
    EMAIL_TYPE_SHORTLIST,
    EMAIL_TYPE_REJECTED,
    EMAIL_TYPE_HOLD,
    EMAIL_TYPE_QUESTION_BANK_REMINDER,
    EMAIL_TYPE_ASSESSMENT_INVITATION,
    EMAIL_TYPE_ASSESSMENT_COMPLETE,
    EMAIL_TYPE_INTERVIEW_SCHEDULED,
    EMAIL_TYPE_INTERVIEW_COMPLETED,
    EMAIL_TYPE_OFFER_EXTENDED,
    EMAIL_TYPE_JOINED,
)

#: Which prompt template drafts each type (app/prompts/*.txt).
EMAIL_TYPE_PROMPTS: dict[str, str] = {
    EMAIL_TYPE_APPLICATION_CONFIRMATION: "email_application_confirmation",
    EMAIL_TYPE_ASSESSMENT_REMINDER: "email_assessment_reminder",
    EMAIL_TYPE_SHORTLIST: "email_shortlist",
    EMAIL_TYPE_REJECTED: "email_rejected",
    EMAIL_TYPE_HOLD: "email_hold",
    EMAIL_TYPE_QUESTION_BANK_REMINDER: "email_question_bank_reminder",
    EMAIL_TYPE_ASSESSMENT_INVITATION: "email_assessment_invitation",
    EMAIL_TYPE_ASSESSMENT_COMPLETE: "email_assessment_complete",
    EMAIL_TYPE_INTERVIEW_SCHEDULED: "email_interview_scheduled",
    EMAIL_TYPE_INTERVIEW_COMPLETED: "email_interview_completed",
    EMAIL_TYPE_OFFER_EXTENDED: "email_offer_extended",
    EMAIL_TYPE_JOINED: "email_joined",
}

STATUS_QUEUED = "queued"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"


class EmailLog(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "email_log"
    __table_args__ = (
        Index("ix_email_log_tenant_created", "tenant_id", "created_at"),
        Index("ix_email_log_job", "job_id"),
        Index("ix_email_log_candidate", "candidate_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    email_type: Mapped[str] = mapped_column(String(40), nullable=False)
    recipient_email: Mapped[str] = mapped_column(String(320), nullable=False)
    # All three targets are nullable: the internal question-bank reminder
    # (type 6) goes to a recruiter and has no candidate, while a candidate
    # confirmation may predate any link row.
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("candidates.id", ondelete="SET NULL")
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL")
    )
    job_candidate_link_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_candidate_links.id", ondelete="SET NULL")
    )
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=STATUS_QUEUED, server_default=STATUS_QUEUED
    )
    error: Mapped[str | None] = mapped_column(Text)
    #: True when the recruiter changed the AI draft before sending.
    edited_by_human: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    generated_by_ai: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    sent_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
