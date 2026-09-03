"""The AI-generated-text observation (section 3.5), behind a flag.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
The specification is blunt: these detectors "are unreliable against
current-generation language models". The observation is INFORMATIONAL ONLY.
It runs after the session, never in the request path; it produces a Path C
event and nothing else; it contributes to no warning, no termination, no
score and no ranking; and the report words it with the hedge the
specification wrote. It ships disabled (`proctoring_ai_text_detection_enabled`
is false) so that an environment without a licensed detector reports nothing
rather than something wrong.

WHAT IS SENT. The candidate's own text answers, one per question, and only
those at least `ai_text_min_chars` long, because a detector's output on a
short sentence is noise. Nothing about the candidate travels with the text.
What is recorded is the question the observation belongs to and a rounded
probability as an internal aggregate; the detector's model identifier is
never stored and never reaches a recruiter.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import AssessmentConversation, AssessmentMessage
from app.models.proctoring import ProctoringEvent, ProctoringSession
from app.services.proctoring import catalog
from app.services.proctoring.config import ProctoringConfig, get_config

logger = logging.getLogger(__name__)

__all__ = ["post_text", "scan_conversation", "EVENT_TYPE"]

EVENT_TYPE = "AI_TEXT_SIGNAL"

Poster = Callable[[str, ProctoringConfig], Awaitable[float]]


async def post_text(text: str, config: ProctoringConfig) -> float:
    """POST one answer to `/ai-text` and return the reported probability."""
    url = config.analysis_service_url.rstrip("/") + "/ai-text"
    async with httpx.AsyncClient(timeout=config.analysis_timeout_seconds) as client:
        response = await client.post(url, json={"text": text})
    response.raise_for_status()
    body: Any = response.json()
    value = body.get("probability_ai") if isinstance(body, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("analysis service answered without a probability_ai")
    return float(value)


async def _already_scanned(session: AsyncSession, ps: ProctoringSession) -> bool:
    row = (
        await session.execute(
            select(ProctoringEvent.id).where(
                ProctoringEvent.proctoring_session_id == ps.id,
                ProctoringEvent.event_type == EVENT_TYPE,
            ).limit(1)
        )
    ).first()
    return row is not None


def _question_uuid(key: str | None) -> uuid.UUID | None:
    if not key:
        return None
    try:
        return uuid.UUID(key)
    except ValueError:
        return None


async def scan_conversation(
    session: AsyncSession,
    ps: ProctoringSession,
    conversation: AssessmentConversation,
    *,
    now: datetime,
    post: Poster = post_text,
) -> int:
    """Post each long enough text answer; record an observation for those at
    or above the threshold. Returns how many were recorded. Idempotent per
    session: a session already carrying an observation is not rescanned."""
    config = get_config()
    if not config.ai_text_detection_enabled or not config.analysis_service_url:
        return 0
    if await _already_scanned(session, ps):
        return 0
    messages = (
        await session.execute(
            select(AssessmentMessage)
            .where(
                AssessmentMessage.conversation_id == conversation.id,
                AssessmentMessage.speaker == "candidate",
            )
            .order_by(AssessmentMessage.ordinal)
        )
    ).scalars().all()
    # One text per question: a follow-up's answer joins its parent's.
    by_key: dict[str, list[str]] = {}
    for message in messages:
        by_key.setdefault(message.question_key or "", []).append(message.content or "")
    recorded = 0
    for key, parts in by_key.items():
        text = "\n".join(part for part in parts if part).strip()
        if len(text) < config.ai_text_min_chars:
            continue
        try:
            probability = await post(text, config)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "proctoring.ai_text.failed session_id=%s err=%s", ps.id, type(exc).__name__
            )
            continue
        if probability < config.ai_text_threshold:
            continue
        session.add(
            ProctoringEvent(
                tenant_id=ps.tenant_id,
                proctoring_session_id=ps.id,
                event_type=EVENT_TYPE,
                occurred_at=now,
                duration_ms=None,
                path=catalog.PATH_C,
                question_id=_question_uuid(key),
                metadata_json={"probability": round(probability, 3), "chars": len(text)},
            )
        )
        recorded += 1
    await session.flush()
    return recorded
