"""The canonical assessment data model (dual-mode spec sections 13 and 21).

«Video Interview and Conversational Assessment are two different input
mechanisms for the same assessment intelligence pipeline.» Both modes write
the SAME storage records (`assessment_messages` under the question's key, one
`assessment_answers` row per question), so this builder reads those records
without branching on mode: the mode is carried as a FACT on the output, never
consulted to choose a code path. One implementation per concept.

The shape follows spec section 13:

    {
      "assessment_id": ..., "candidate_id": ..., "mode": ..., "job_id": ...,
      "responses": [
        {"question_id", "question_type", "question", "answer", "metadata"}
      ]
    }

The spec's optional `context` block (resume, JD, Company DNA) is deliberately
NOT assembled here: the scorers already read those artifacts through their own
governed paths (`services/rag`, the compiled Company DNA artifact), and a
second copy inside this structure would be a second answer to what the
evidence was. NO SCORE AND NO GRADE APPEARS HERE: this is the collection-side
record, upstream of every judgement.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import (
    AssessmentAnswer,
    AssessmentConversation,
    AssessmentMessage,
    CandidateQuestion,
)
from app.models.candidate import JobCandidateLink


async def build_canonical(
    session: AsyncSession, conversation: AssessmentConversation
) -> dict[str, Any]:
    """The canonical structure for one assessment session, either mode.

    One response per question the candidate actually answered, in the served
    order. The answer text is the candidate's transcript content under the
    question's key: for conversational mode that is what they typed (a
    follow-up's answer joins its parent under the same key, exactly as the
    scorers see it), for video mode it is the transcribed speech. The
    structured `assessment_answers` payload rides along in `metadata`.
    """
    link = await session.get(JobCandidateLink, conversation.job_candidate_link_id)
    questions = (
        await session.execute(
            select(CandidateQuestion)
            .where(
                CandidateQuestion.job_candidate_link_id
                == conversation.job_candidate_link_id
            )
            .order_by(CandidateQuestion.ordinal)
        )
    ).scalars().all()
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
    answers = {
        answer.question_id: answer
        for answer in (
            await session.execute(
                select(AssessmentAnswer).where(
                    AssessmentAnswer.conversation_id == conversation.id
                )
            )
        ).scalars().all()
    }
    spoken_by_key: dict[str, list[str]] = {}
    for message in messages:
        if message.question_key:
            spoken_by_key.setdefault(str(message.question_key), []).append(
                message.content or ""
            )

    responses: list[dict[str, Any]] = []
    for question in questions:
        key = str(question.id)
        parts = [part for part in spoken_by_key.get(key, []) if part]
        record = answers.get(question.id)
        if not parts and record is None:
            # Never reached in this session: not served (video), or the
            # conversation closed on sufficient evidence before it (spec 8).
            # Absent rather than fabricated.
            continue
        metadata: dict[str, Any] = {}
        if record is not None:
            metadata = {
                "answer_json": dict(record.answer_json or {}),
                "time_spent_seconds": record.time_spent_seconds,
                "revision_count": record.revision_count,
            }
        responses.append(
            {
                "question_id": key,
                "question_type": question.question_type,
                "question": question.prompt,
                "answer": "\n\n".join(parts),
                "metadata": metadata,
            }
        )
    return {
        "assessment_id": str(conversation.id),
        "candidate_id": str(link.candidate_id) if link is not None else None,
        "mode": conversation.mode,
        "job_id": str(conversation.job_id),
        "responses": responses,
    }
