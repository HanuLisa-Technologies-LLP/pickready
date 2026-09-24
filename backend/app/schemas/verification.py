"""The inbound-email webhook's schemas (API_CONTRACT.md `/verification`).

The outreach request and response went with the retired 40-aspect outreach
(Phase 6); only the inbound relay's payload and answer remain.
"""
from pydantic import BaseModel, ConfigDict, Field


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
