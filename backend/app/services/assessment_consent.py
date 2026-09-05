"""The mandatory assessment consent gate (dual-mode spec section 3).

Consent is a hard prerequisite for starting an assessment in EITHER mode: the
candidate must not be able to begin until a consent row for THIS session and
THIS mode exists (spec 3.1). The gate asks the TABLE, never a stamp, and it
runs in `start_conversation` beside the proctoring gate.

THIS IS NOT THE PROCTORING CONSENT. The proctoring consent screen covers
monitoring, which stores no media; this consent covers what the assessment
itself collects and stores (answers and metadata in conversational mode; a
recorded, transcribed, AI-analyzed video in video mode). The two are shown as
two steps because they are two different agreements about two different data
sets, and collapsing them would make one screen claim the other's terms.

The wording is CONFIGURABLE (`assessment_consent_text_*` settings, spec 3.2)
and the version identifiers are stamped onto every row (spec 3.4), so what a
candidate agreed to is a stored fact rather than whatever the settings say
today.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.assessment import AssessmentConversation
from app.models.dual_mode import (
    ASSESSMENT_MODES,
    MODE_CONVERSATIONAL,
    MODE_VIDEO_INTERVIEW,
    AssessmentConsent,
)

CONSENT_REQUIRED_DETAIL = (
    "Before this assessment can begin you need to review and accept the "
    "consent terms for the mode you chose. Please go back to the consent "
    "step and accept it."
)


@dataclass(frozen=True)
class ConsentTerms:
    """What one mode's consent screen shows, and the versions it binds."""

    assessment_mode: str
    text: str
    consent_version: str
    privacy_policy_version: str
    terms_version: str


def terms_for(mode: str) -> ConsentTerms:
    """The configured consent terms for `mode`. Raises on an unknown mode."""
    settings = get_settings()
    if mode == MODE_VIDEO_INTERVIEW:
        text = settings.assessment_consent_text_video
    elif mode == MODE_CONVERSATIONAL:
        text = settings.assessment_consent_text_conversational
    else:
        raise ValueError(f"unknown assessment mode {mode!r}")
    return ConsentTerms(
        assessment_mode=mode,
        text=text,
        consent_version=settings.assessment_consent_version,
        privacy_policy_version=settings.assessment_privacy_policy_version,
        terms_version=settings.assessment_terms_version,
    )


def all_terms() -> list[ConsentTerms]:
    return [terms_for(mode) for mode in ASSESSMENT_MODES]


async def find_consent(
    session: AsyncSession, conversation_id: uuid.UUID, mode: str
) -> AssessmentConsent | None:
    return (
        await session.execute(
            select(AssessmentConsent).where(
                AssessmentConsent.conversation_id == conversation_id,
                AssessmentConsent.assessment_mode == mode,
            )
        )
    ).scalars().first()


async def require_consent(
    session: AsyncSession, conversation: AssessmentConversation
) -> AssessmentConsent:
    """The consent row this conversation runs under, or a 409.

    409 rather than 403, for the same reason the proctoring gate answers 409:
    the candidate is who they say they are and is allowed to be here; what is
    missing is a step of the flow, and the detail names it.
    """
    consent = await find_consent(session, conversation.id, conversation.mode)
    if consent is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=CONSENT_REQUIRED_DETAIL
        )
    return consent


async def record_consent(
    session: AsyncSession,
    conversation: AssessmentConversation,
    *,
    candidate_id: uuid.UUID,
) -> AssessmentConsent:
    """Record the candidate's acceptance of the CURRENT mode's terms.

    Idempotent per (conversation, mode): accepting twice returns the original
    row, because the audit question is "did they consent, to which version,
    when", and the first acceptance is the answer.

    Only acceptance is recorded. A decline collects nothing and stores
    nothing; the caller simply does not start the assessment.
    """
    existing = await find_consent(session, conversation.id, conversation.mode)
    if existing is not None:
        return existing
    terms = terms_for(conversation.mode)
    consent = AssessmentConsent(
        tenant_id=conversation.tenant_id,
        candidate_id=candidate_id,
        conversation_id=conversation.id,
        job_candidate_link_id=conversation.job_candidate_link_id,
        assessment_mode=conversation.mode,
        consent_status="granted",
        consented_at=datetime.now(timezone.utc),
        consent_version=terms.consent_version,
        privacy_policy_version=terms.privacy_policy_version,
        terms_version=terms.terms_version,
    )
    session.add(consent)
    await session.flush()
    return consent
