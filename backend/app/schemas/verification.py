"""Outreach + employer verification schemas (API_CONTRACT.md `/verification`)."""
import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field



class OutreachIn(BaseModel):
    job_id: uuid.UUID
    candidate_ids: list[uuid.UUID] = Field(min_length=1)


class OutreachOut(BaseModel):
    sent: list[uuid.UUID] = []
    # Databank candidates never re-enter the outreach/verification flow
    # (claude.md rule 7) — they are reported here, not silently dropped.
    skipped_databank: list[uuid.UUID] = []
    not_linked: list[uuid.UUID] = []
















class InboundEmailIn(BaseModel):
    """Resend inbound-parsing webhook payload (lenient — provider-shaped)."""
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    to: list[str] | str | None = None
    cc: list[str] | str | None = None
    from_: str | None = Field(default=None, alias="from")
    subject: str | None = None
    text: str | None = None
    html: str | None = None
    #: The sending mail system's own Message-ID. It is what makes an inbound
    #: reply IDEMPOTENT: SNS delivers at least once and a redelivery must not
    #: post the employer's answer into the thread twice.
    message_id: str | None = Field(default=None, alias="messageId")


class InboundEmailOut(BaseModel):
    received: bool = True
    matched: bool = False
