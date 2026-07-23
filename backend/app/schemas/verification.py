"""Outreach + employer verification schemas (API_CONTRACT.md `/verification`)."""
import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import SubmittedVia, VerificationStatus


class OutreachIn(BaseModel):
    job_id: uuid.UUID
    candidate_ids: list[uuid.UUID] = Field(min_length=1)


class OutreachOut(BaseModel):
    sent: list[uuid.UUID] = []
    # Databank candidates never re-enter the outreach/verification flow
    # (claude.md rule 7) — they are reported here, not silently dropped.
    skipped_databank: list[uuid.UUID] = []
    not_linked: list[uuid.UUID] = []


class VerificationRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    profile_id: uuid.UUID
    employer_seq: int
    employer_email: str
    employer_name: str | None
    status: VerificationStatus
    submitted_via: SubmittedVia | None
    override_reason: str | None
    responded_at: datetime | None


class ProfileVerificationOut(BaseModel):
    profile_id: uuid.UUID
    requests: list[VerificationRequestOut]
    all_resolved: bool  # every request submitted or overridden


class OverrideIn(BaseModel):
    reason: str = Field(min_length=1)


class EmployerFormField(BaseModel):
    name: str
    label: str
    type: str = "text"
    required: bool = True


class EmployerFormOut(BaseModel):
    candidate_name: str | None
    employer_name: str | None
    fields: list[EmployerFormField]


class EmployerFormIn(BaseModel):
    """Structured employer verification response (FR-5.3)."""
    designation: str = Field(max_length=255)
    doj: date
    doe: date
    last_drawn_ctc: str = Field(max_length=100)
    last_drawn_gross: str = Field(max_length=100)
    noc_status: str = Field(max_length=255)
    exit_formalities_complete: bool
    bgv_status: str = Field(max_length=255)
    proofs_details: str | None = None            # educational/address/ID proof detail
    prior_experience_details: str | None = None  # prior experience/compensation detail


class FormSubmitOut(BaseModel):
    status: VerificationStatus


class InboundEmailIn(BaseModel):
    """Resend inbound-parsing webhook payload (lenient — provider-shaped)."""
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    to: list[str] | str | None = None
    from_: str | None = Field(default=None, alias="from")
    subject: str | None = None
    text: str | None = None
    html: str | None = None


class InboundEmailOut(BaseModel):
    received: bool = True
    matched: bool = False
