"""Bulk candidate-outreach schemas (`/outreach/preview`, `/outreach/send`).

Selection is by `job_candidate_links.id` — the row that ties a candidate to a
job — so the server can validate tenant + job membership in one lookup instead
of trusting a bare candidate id (claude.md rule 1: the RLS session is the
boundary, these checks are defence in depth).
"""
import uuid
from typing import Literal

from pydantic import BaseModel, Field

# Placeholders documented in the UI and substituted in manual mode.
MANUAL_PLACEHOLDERS = (
    "candidate_name",
    "company",
    "job_title",
    "candidate_strengths",
)

OutreachMode = Literal["ai", "manual"]


class OutreachComposeIn(BaseModel):
    """Shared request body. `subject`/`body` are required for manual mode."""

    job_id: uuid.UUID
    # min_length=1 → an empty selection is a 422, never a silent no-op.
    link_ids: list[uuid.UUID] = Field(min_length=1)
    mode: OutreachMode = "ai"
    subject: str | None = None
    body: str | None = None


class RecipientOverride(BaseModel):
    """Per-candidate edit made in the modal before sending."""

    link_id: uuid.UUID
    subject: str
    body: str


class OutreachSendIn(OutreachComposeIn):
    overrides: list[RecipientOverride] = []


class ResolvedEmail(BaseModel):
    link_id: uuid.UUID
    candidate_id: uuid.UUID
    name: str
    email: str
    subject: str
    body: str
    # AI mode only: True when the LLM chain was unavailable and the
    # deterministic template was used instead (surfaced, never silent).
    ai_fallback: bool = False


class SkippedRecipient(BaseModel):
    link_id: uuid.UUID
    candidate_id: uuid.UUID | None = None
    name: str
    reason: str


class OutreachPreviewOut(BaseModel):
    job_title: str
    company: str
    mode: OutreachMode
    recipients: list[ResolvedEmail]
    skipped: list[SkippedRecipient] = []
    placeholders: list[str] = list(MANUAL_PLACEHOLDERS)
    # Delivery-config transparency: a missing SMTP credential must surface in
    # the UI rather than produce a silently undelivered email (claude.md r5).
    smtp_configured: bool = True
    delivery_warning: str | None = None


class OutreachSendOut(BaseModel):
    queued: int
    recipients: list[str] = []          # email addresses actually queued
    task_ids: list[str] = []
    skipped: list[SkippedRecipient] = []
    smtp_configured: bool = True
    delivery_warning: str | None = None


class OutreachDeliveryStatusIn(BaseModel):
    task_ids: list[str] = Field(min_length=1, max_length=100)


class OutreachDeliveryStatusOut(BaseModel):
    total: int
    pending: int
    sent: int
    failed: int
    done: bool
