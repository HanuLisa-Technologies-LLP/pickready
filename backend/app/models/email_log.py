"""Outbound email audit trail (migration 0016, spec §6).

One row per message across all six email types, holding the subject and body
that were ACTUALLY sent — after any recruiter edit — plus delivery outcome.
This is the record that answers "what did we tell this candidate, when, and did
a human read it first?", so it is written before the send is attempted and
updated by the worker, never only on success.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, text
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
# ── The databank invitation (migration 0078, workflow section 12) ────────────
# Sent to somebody whose resume the recruiter already holds, asking them to
# sign in and APPLY. It is deliberately not one of the pipeline transition
# emails above: nothing about the candidate's state changes when it is sent,
# because they have no application yet. That is the whole point of Gate 5.
EMAIL_TYPE_DATABANK_INVITATION = "databank_invitation"

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
    EMAIL_TYPE_DATABANK_INVITATION,
)

# ── Logged but never AI-drafted (migration 0114) ─────────────────────────────
# The BGV verification request. It is DELIBERATELY not a member of
# `EMAIL_TYPES`: every type in that tuple has a prompt in `EMAIL_TYPE_PROMPTS`
# and is drafted by `lifecycle_email.draft`, and this one is not drafted at all
# -- a recruiter writes it, or the deterministic template does when automation
# sends it and there is no recruiter to review a draft. It exists as a type so
# the message has an `email_log` row, which is the only thing a delivery event
# can be matched back to.
EMAIL_TYPE_BGV_VERIFICATION = "bgv_verification"
# ── The message notification (migration 0121) ────────────────────────────────
# Sent when a recruiter writes to a candidate in the portal. Fixed copy from
# `services/candidate_message_notifications`, never drafted by a model, so it
# is not a lifecycle type either.
EMAIL_TYPE_MESSAGE_NOTIFICATION = "message_notification"

NON_LIFECYCLE_EMAIL_TYPES: tuple[str, ...] = (
    EMAIL_TYPE_BGV_VERIFICATION,
    EMAIL_TYPE_MESSAGE_NOTIFICATION,
)

#: Everything the `ck_email_log_type` CHECK admits. Mirrored by migrations
#: 0114 and 0121 -- keep them in step.
LOGGED_EMAIL_TYPES: tuple[str, ...] = EMAIL_TYPES + NON_LIFECYCLE_EMAIL_TYPES

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
    EMAIL_TYPE_DATABANK_INVITATION: "email_databank_invitation",
}

STATUS_QUEUED = "queued"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
# ── Delivery-tracking statuses (Corporate Email System spec section 8) ───────
# `processing` is the worker's claim on a row it is actively delivering;
# `delivered`, `bounced` and `complaint` are provider-reported outcomes,
# written by the SES event endpoint keyed on `provider_message_id`. Under the
# SMTP transport a row terminates at `sent`, honestly: Gmail reports no
# per-message delivery events back to this product.
STATUS_PROCESSING = "processing"
STATUS_DELIVERED = "delivered"
STATUS_BOUNCED = "bounced"
STATUS_COMPLAINT = "complaint"

#: Mirrored by ck_email_log_status (migration 0080) -- keep both in step.
EMAIL_STATUSES: tuple[str, ...] = (
    STATUS_QUEUED,
    STATUS_PROCESSING,
    STATUS_SENT,
    STATUS_DELIVERED,
    STATUS_FAILED,
    STATUS_BOUNCED,
    STATUS_COMPLAINT,
)


class EmailLog(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "email_log"
    __table_args__ = (
        Index("ix_email_log_tenant_created", "tenant_id", "created_at"),
        Index("ix_email_log_job", "job_id"),
        Index("ix_email_log_candidate", "candidate_id"),
        Index("ix_email_log_conversation", "conversation_id"),
        # Migration 0121. One row per dedupe key: a redelivered automatic
        # email is refused by the database, not by a lookup that races.
        Index(
            "uq_email_log_dedupe_key",
            "dedupe_key",
            unique=True,
            postgresql_where=text("dedupe_key IS NOT NULL"),
        ),
        Index(
            "ix_email_log_pending",
            "status",
            "created_at",
            postgresql_where=text("status IN ('queued', 'processing')"),
        ),
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
    # ── Delivery tracking (Corporate Email System spec section 8) ────────────
    #: The provider's message id (SES MessageId, or the SMTP Message-ID). What
    #: the SES delivery/bounce/complaint event is matched back on.
    provider_message_id: Mapped[str | None] = mapped_column(String(300))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    bounced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    complained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: SES REJECT and RENDERING_FAILURE: the message never reached a receiver
    #: at all. Deliberately separate from `bounced_at`, which means a receiver
    #: took it and refused it. The two have different causes and different
    #: fixes, and one column would make them indistinguishable afterwards.
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: WHICH TRANSPORT ACTUALLY CARRIED THIS MESSAGE, recorded per row rather
    #: than inferred from today's `settings.email_transport`. A deployment that
    #: switches from smtp to ses would otherwise silently relabel every
    #: historical row, and `sent` means something different under each: Gmail
    #: reports no delivery outcome at all, so under smtp `sent` is terminal.
    transport: Mapped[str | None] = mapped_column(String(20))
    #: The template this body was rendered from. Stored so a bounce traces back
    #: to the copy that produced it without re-deriving it from `email_type`,
    #: which is a coarser thing: several templates share one type.
    template_id: Mapped[str | None] = mapped_column(String(120))
    #: The corporate sender this message was queued under, when the recruiter
    #: chose one. SET NULL so revoking-then-deleting a sender never erases the
    #: delivery record; the send-time chokepoint in workers/tasks.py re-loads
    #: this row and refuses anything that is not `active` (spec section 11).
    sender_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("client_email_senders.id", ondelete="SET NULL")
    )
    #: WHICH BGV VERIFICATION THIS MESSAGE IS (migration 0114). The bounce
    #: handler used to correlate back through the recipient ADDRESS, and one
    #: HR mailbox confirming two candidates at the same employer is the normal
    #: case at any large company: the address resolved to whichever
    #: verification was sent last, silently, and the wrong candidate was told
    #: to fix an address that worked. An address is a property of a recipient
    #: and is never an identity of a message. SET NULL so deleting a
    #: verification never erases the delivery record of what was sent.
    bgv_verification_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("bgv_verifications.id", ondelete="SET NULL")
    )
    #: WHAT MAKES AN AUTOMATIC EMAIL IDEMPOTENT (migration 0121). Written by
    #: `services/email_outbox` only, and it names the STAGE, never just the
    #: type: `assessment_reminder:<link>:24` and `...:72` are two emails, which
    #: is exactly what "any row of this type" could not express, and why the
    #: 72 hour reminder was never sent. NULL for a human-sent email, which may
    #: legitimately be sent twice.
    dedupe_key: Mapped[str | None] = mapped_column(String(200))
    #: The candidate thread this email is part of (migration 0121). Set when a
    #: person sent it, or when it announces a message in that thread; its
    #: Reply-To is then the thread's own address, so an emailed answer lands in
    #: the conversation instead of a mailbox nobody watches. SET NULL so a
    #: deleted conversation never erases the delivery record.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    #: When a send worker CLAIMED this row, moving it from `queued` to
    #: `processing` in one conditional UPDATE (migration 0121). Two
    #: invocations for one row cannot both claim it, so a redelivered or
    #: re-dispatched send never mails a candidate twice. A row still
    #: `processing` long after its claim may or may not have been sent, and it
    #: is reported, never resent.
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
