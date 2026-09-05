"""Lifecycle email schemas (spec §6)."""
import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.email_log import EMAIL_TYPES

EmailType = Literal[
    "application_confirmation",
    "assessment_reminder",
    "shortlist",
    "rejected",
    "hold",
    "question_bank_reminder",
    # Pipeline transition emails (spec §4.1).
    "assessment_invitation",
    "assessment_complete",
    "interview_scheduled",
    "interview_completed",
    "offer_extended",
    "joined",
    # The databank invitation (workflow section 12). Listed here so a recruiter
    # can compose one by hand through /emails/draft like any other type; note
    # it is NOT a transition email -- nothing about the candidate's state
    # changes when it goes out, because they have not applied.
    "databank_invitation",
]

# Belt and braces: the Literal above and the model constant must not drift.
assert set(EMAIL_TYPES) == set(EmailType.__args__), (
    "schemas.emails.EmailType is out of step with models.email_log.EMAIL_TYPES"
)


class EmailDraftIn(BaseModel):
    """Ask the AI for a draft, or several.

    `link_ids` are job_candidate_links: one draft is produced PER candidate so
    each email can reference that person's own evidence (spec §6.2, the
    "AI will draft (personalized per candidate)" branch). Sending one template
    to everyone is the other branch — the client simply skips this call and
    posts its own subject/body to /send.
    """
    email_type: EmailType
    link_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    #: Extra prompt context (e.g. hold_days, next_steps). Merged over defaults.
    context: dict = {}


class EmailDraftOut(BaseModel):
    link_id: uuid.UUID
    candidate_id: uuid.UUID | None = None
    recipient_email: str | None = None
    candidate_name: str | None = None
    email_type: str
    subject: str
    body: str
    #: False when the provider chain was unavailable and a deterministic
    #: template was used — surfaced so the recruiter knows to read it closely.
    generated_by_ai: bool


class EmailDraftsOut(BaseModel):
    email_type: str
    drafts: list[EmailDraftOut]
    #: Candidates skipped because they have no email address on file, with the
    #: reason. Never silently dropped.
    skipped: list[dict] = []


class EmailSendItem(BaseModel):
    """One message to send — the copy as the recruiter left it."""
    link_id: uuid.UUID
    subject: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1)
    #: True when the recruiter changed the AI draft. Recorded on the audit row.
    edited_by_human: bool = False
    generated_by_ai: bool = True


class EmailSendIn(BaseModel):
    email_type: EmailType
    messages: list[EmailSendItem] = Field(min_length=1, max_length=50)


class EmailLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email_type: str
    recipient_email: str
    candidate_id: uuid.UUID | None
    job_id: uuid.UUID | None
    subject: str
    body: str
    status: str
    error: str | None
    edited_by_human: bool
    generated_by_ai: bool
    created_at: datetime
    sent_at: datetime | None


class EmailSendOut(BaseModel):
    queued: int
    logs: list[EmailLogOut]
    skipped: list[dict] = []
