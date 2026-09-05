"""Background verification via departmental HR email (add-features spec,
2026-09-05, "Candidate Verification" section; migration 0085).

CANDIDATE-OWNED DATA, NOT TENANT DATA. The candidate provides their previous
two employers' departmental mailboxes (hr@, careers@, resumes@) on their own
profile; ReadyPick dispatches one inquiry email per employer and the parsed
response lands HERE, on the candidate's side of the product. An employer
(tenant) sees it only through `bgv_share_consents`: one row per tenant the
candidate has explicitly shared that inquiry with, never directly. That
consent boundary is the locked design decision from the spec discussion.

Following up with an unresponsive employer is the candidate's own
responsibility by design: ReadyPick sends the inquiry once per explicit
candidate action and never chases, so there is no reminder sweep here.

`domain_match_result` is PROVENANCE, never a gate: a deterministic comparison
of the departmental email's domain against the employer name
(`services/bgv.domain_match`). It is a word, it never blocks a dispatch, and
nothing scores it.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

# ── Inquiry lifecycle ────────────────────────────────────────────────────────
#
# One linear machine with explicit, honest failure states. `dispatch_failed`
# and `parse_failed` are real terminal-until-retried states shown to the
# candidate in words; neither is ever silently relabelled as success.

STATUS_COLLECTED = "collected"                # employer + mailbox recorded
STATUS_DISPATCHED = "dispatched"              # inquiry email accepted by SMTP
STATUS_DISPATCH_FAILED = "dispatch_failed"    # delivery failed, visibly
STATUS_RESPONSE_RECEIVED = "response_received"  # a reply arrived, parse queued
STATUS_PARSED = "parsed"                      # seven fields extracted
STATUS_PARSE_FAILED = "parse_failed"          # reply held, extraction failed

ALL_STATUSES: frozenset[str] = frozenset(
    {
        STATUS_COLLECTED,
        STATUS_DISPATCHED,
        STATUS_DISPATCH_FAILED,
        STATUS_RESPONSE_RECEIVED,
        STATUS_PARSED,
        STATUS_PARSE_FAILED,
    }
)

#: States from which the candidate may (re)trigger the inquiry email.
DISPATCHABLE_STATUSES: frozenset[str] = frozenset(
    {STATUS_COLLECTED, STATUS_DISPATCH_FAILED}
)

#: The domain-match vocabulary. Words only, checked by the database too.
DOMAIN_MATCH_RESULTS: frozenset[str] = frozenset(
    {"matched", "mismatched", "indeterminate"}
)

#: The seven fields the spec asks the employer to confirm, and the exact key
#: set `parsed_fields_json` carries. `services/bgv.validate_parsed_fields`
#: normalises every parse to this shape.
BGV_FIELDS: tuple[str, ...] = (
    "duration",
    "exit_formalities",
    "compensation",
    "noc",
    "designation",
    "reporting_manager",
    "relieving_method",
)

#: The spec collects the previous TWO employers.
MAX_INQUIRIES_PER_CANDIDATE = 2


class BGVInquiry(Base, UUIDPKMixin, CreatedAtMixin):
    """One background-verification inquiry to one previous employer."""

    __tablename__ = "bgv_inquiries"
    __table_args__ = (
        Index("ix_bgv_inquiries_candidate", "candidate_id"),
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    employer_name: Mapped[str] = mapped_column(String(200), nullable=False)
    departmental_email: Mapped[str] = mapped_column(String(320), nullable=False)
    #: matched | mismatched | indeterminate. Provenance, never a gate.
    domain_match_result: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default=STATUS_COLLECTED
    )
    #: Matches the reply back to this row through the shared inbound-email
    #: webhook (api/verification.py), the same intake the employer
    #: verification form's email fallback already uses.
    reply_token: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    inquiry_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    response_received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    #: The employer's reply, verbatim. Kept even when parsing fails so a
    #: failed extraction never destroys the evidence it failed on.
    response_raw: Mapped[str | None] = mapped_column(Text)
    #: Exactly the BGV_FIELDS keys, every value nullable. NULL until parsed.
    parsed_fields_json: Mapped[dict | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BGVShareConsent(Base, UUIDPKMixin):
    """The candidate's consent to show ONE inquiry's result to ONE tenant.

    No row, no visibility: the recruiter endpoint filters on this table, and
    an employer the candidate has not consented to sees "Not shared by the
    candidate", never the fields. Revoking deletes the row.
    """

    __tablename__ = "bgv_share_consents"
    __table_args__ = (
        UniqueConstraint(
            "bgv_inquiry_id", "tenant_id", name="uq_bgv_share_consent"
        ),
        Index("ix_bgv_share_consents_tenant", "tenant_id"),
    )

    bgv_inquiry_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("bgv_inquiries.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    consented_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
