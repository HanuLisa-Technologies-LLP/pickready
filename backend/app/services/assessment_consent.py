"""The mandatory assessment consent gate (dual-mode spec section 3).

Consent is a hard prerequisite for starting an assessment: the candidate must
not be able to begin until a consent row for THIS session exists (spec 3.1).
The gate asks the TABLE, never a stamp, and it runs in `start_conversation`
beside the proctoring gate.

ONE CONSENT SINCE 2026-09-24 (Appendix B section 1: one assessment mode). The
screen shows one text, `assessment_consent_text`, which states everything the
session collects: the continuous recording and when it is deleted (D4), the
transcription of spoken answers with only the text kept, the answers and
session data, and the paste rule. Rows keep `assessment_mode =
'conversational'`, the one mode, so the column and its CHECK read every row
ever written.

THIS IS NOT THE PROCTORING CONSENT. The proctoring consent screen covers
monitoring; this consent covers what the assessment itself collects and
stores. They are two agreements about two data sets.

The wording is CONFIGURABLE (spec 3.2) and the version identifiers are stamped
onto every row (spec 3.4), so what a candidate agreed to is a stored fact
rather than whatever the settings say today.
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
from app.models.dual_mode import MODE_CONVERSATIONAL, AssessmentConsent

CONSENT_REQUIRED_DETAIL = (
    "Before this assessment can begin you need to review and accept the "
    "consent terms. Please go back to the consent step and accept them."
)

ITEMS_REQUIRED_DETAIL = (
    "Please tick every item on the consent list before continuing. Each one "
    "is recorded separately, so all of them have to be agreed to."
)


def assert_all_items_ticked(consent_keys: list[str] | tuple[str, ...]) -> None:
    """Refuse anything but the complete Stage B set.

    Pure, and separate from `record_consent`, so the refusal can be exercised
    without a conversation: it is a statement about the KEYS and reads no row.
    Set equality rather than a subset check, because an unknown key in the
    list means the client and the catalogue disagree about what was on screen,
    and a consent recorded under that disagreement is worth less than a 422.
    """
    from app.services import consent_catalog

    if set(consent_keys) != set(consent_catalog.STAGE_B_KEYS):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ITEMS_REQUIRED_DETAIL,
        )


@dataclass(frozen=True)
class ConsentTerms:
    """What the consent screen shows, and the versions it binds."""

    assessment_mode: str
    text: str
    consent_version: str
    privacy_policy_version: str
    terms_version: str


def terms_for() -> ConsentTerms:
    """The configured consent terms. ONE text since 2026-09-24: there is one
    assessment mode, so there is nothing to choose between. `assessment_mode`
    is still stamped, as `conversational`, because the consent row's CHECK and
    every row written before today carry it."""
    settings = get_settings()
    return ConsentTerms(
        assessment_mode=MODE_CONVERSATIONAL,
        text=settings.assessment_consent_text,
        consent_version=settings.assessment_consent_version,
        privacy_policy_version=settings.assessment_privacy_policy_version,
        terms_version=settings.assessment_terms_version,
    )


async def find_consent(
    session: AsyncSession, conversation_id: uuid.UUID
) -> AssessmentConsent | None:
    """The consent row this session runs under, in the one mode."""
    return (
        await session.execute(
            select(AssessmentConsent).where(
                AssessmentConsent.conversation_id == conversation_id,
                AssessmentConsent.assessment_mode == MODE_CONVERSATIONAL,
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
    consent = await find_consent(session, conversation.id)
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
    consent_keys: list[str] | tuple[str, ...],
) -> AssessmentConsent:
    """Record the candidate's acceptance of the CURRENT terms.

    Idempotent per conversation: accepting twice returns the original row,
    because the audit question is "did they consent, to which version, when",
    and the first acceptance is the answer.

    Only acceptance is recorded. A decline collects nothing and stores
    nothing; the caller simply does not start the assessment.

    EVERY STAGE B ITEM IS TICKED INDIVIDUALLY, and the SERVER is what refuses
    a short list. A disabled button on a screen is a courtesy; the record has
    to be able to say each item was separately agreed to, and a route that
    stamped all of them off one press could not say that honestly whatever the
    screen looked like. The check runs BEFORE the idempotent shortcut, so a
    replayed request with a short list is refused rather than quietly reading
    as the earlier full acceptance.
    """
    from app.services import consent_catalog

    assert_all_items_ticked(consent_keys)

    existing = await find_consent(session, conversation.id)
    if existing is not None:
        return existing
    # Stage B of the per-item catalogue (vivekium feature 6), in the SAME
    # transaction as the assessment consent: the brief's "one operation" is a
    # literal property of this function, not a convention three call sites
    # follow.
    await consent_catalog.record_items(
        session,
        candidate_id=candidate_id,
        keys=consent_catalog.STAGE_B_KEYS,
        source=consent_catalog.SOURCE_ASSESSMENT,
    )
    # STAGE_B_KEYS rather than `consent_keys`: the two sets were just asserted
    # equal, and writing from the catalogue keeps the stored order and the
    # stored membership the server's rather than a client payload's.
    terms = terms_for()
    consent = AssessmentConsent(
        tenant_id=conversation.tenant_id,
        candidate_id=candidate_id,
        conversation_id=conversation.id,
        job_candidate_link_id=conversation.job_candidate_link_id,
        assessment_mode=terms.assessment_mode,
        consent_status="granted",
        consented_at=datetime.now(timezone.utc),
        consent_version=terms.consent_version,
        privacy_policy_version=terms.privacy_policy_version,
        terms_version=terms.terms_version,
    )
    session.add(consent)
    await session.flush()
    return consent
