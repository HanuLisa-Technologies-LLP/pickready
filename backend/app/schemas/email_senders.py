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
    #: Retained so rows verified under the withdrawn mailbox OTP still report
    #: truthfully what once happened to them. Nothing sets it any more.
    email_verified: bool
    authorized_at: datetime | None
    created_at: datetime
    #: WHETHER MAIL WOULD ACTUALLY LEAVE, asked of the provider at read time
    #: rather than stored: an identity's state changes in AWS without this
    #: product being told, so a cached copy would be a fact with no refresh.
    can_send: bool
    #: One plain sentence for the Super Admin. NO AWS VOCABULARY EVER: not
    #: SES, not an identity, not DKIM, not a verification status. The client is
    #: told whether the address can send and who completes the setup if not.
    sending_detail: str
    #: The sender automatic emails go out under (migration 0121). At most one
    #: per tenant, and only ever an active one.
    is_default: bool = False


class ActiveSenderOut(BaseModel):
    """One sender the email composer may choose, for a user who sends email
    and manages no senders. Name and address only."""

    id: uuid.UUID
    name: str
    email: str
    is_default: bool


class SenderListOut(BaseModel):
    senders: list[SenderOut]
    #: What the CALLER may do, so the UI renders only reachable controls.
    can_manage: bool
    can_authorize: bool


# The OTP request/response pair was DELETED rather than deprecated when the
# mailbox code was withdrawn. A schema nobody sends is a schema the next person
# wires a route back onto; SES identity verification is the ownership check now.


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
