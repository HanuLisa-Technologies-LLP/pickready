"""Corporate email sender schemas (Corporate Email System spec, 2026-09-05)."""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SenderCreateIn(BaseModel):
    """Register one corporate mailbox (spec section 3's example: Rahul,
    hr@sarkarcorp.com). The address is validated against the configurable
    free-provider blocklist and lowercased before storage."""

    name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)


class SenderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str
    status: str
    email_verified: bool
    authorized_by: uuid.UUID | None
    authorized_at: datetime | None
    created_at: datetime
    updated_at: datetime


class SenderListOut(BaseModel):
    senders: list[SenderOut]
    #: What the CALLER may do, so the UI renders only reachable controls.
    can_manage: bool
    can_authorize: bool


class OtpVerifyIn(BaseModel):
    #: Exactly six digits; anything else is refused before Redis is asked.
    code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")


class OtpIssueOut(BaseModel):
    """The code itself is NEVER in a response; it travels only to the mailbox."""

    sender_id: uuid.UUID
    status: str
    resend_cooldown_seconds: int
    expires_in_seconds: int


class TemplateOut(BaseModel):
    key: str
    label: str
    subject: str
    body: str
    variables: list[str]


class TemplatePreviewIn(BaseModel):
    variables: dict[str, str] = Field(default_factory=dict)


class TemplatePreviewOut(BaseModel):
    key: str
    subject: str
    body: str
