"""The one line the candidate's `respond` turn calls after a structured answer.

WHY A HOOK AND NOT A BRANCH IN THE TURN
---------------------------------------
`respond` (Phase 3's file) writes every structured answer the same way: the
two transcript lines, then one `assessment_answers` row. A final coding
answer needs one thing more, `submissions.accept_final`, which stores the
submission and dispatches its hidden-test run AFTER the commit. The turn
does not hold the answer row (its writer returns nothing), and it should not
have to know which formats execute.

So the turn calls `accept_structured_answer` unconditionally after the
answer row is written, and this module decides:

  * not a coding question, or a legacy (v1) coding question that is read
    rather than run: nothing happens and None comes back;
  * an executed (v2) coding question: the answer row is read in the SAME
    session (so the caller's flush is visible and RLS still applies) and
    handed to `accept_final`, which is idempotent on the answer.

It runs in the caller's transaction and never commits: a turn that rolls
back leaves no submission and dispatches nothing, which is the property
`dispatch_after_commit` exists for.

A missing answer row RAISES. The caller has just written it, so its absence
is a routing defect, and a silent None would leave a final coding answer
stored and never executed with nothing recording why.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import AssessmentAnswer, AssessmentConversation, CandidateQuestion
from app.models.coding import CodingSubmission
from app.services.assessment_formats import types
from app.services.coding_assessment import payload as coding_payload
from app.services.coding_assessment import submissions

__all__ = ["accept_structured_answer"]


async def accept_structured_answer(
    session: AsyncSession,
    *,
    conversation: AssessmentConversation,
    question: CandidateQuestion,
    auto_submitted: bool = False,
) -> CodingSubmission | None:
    """Hand a final v2 coding answer to execution; a no-op for anything else.

    `auto_submitted` is True when the turn was closed by the timer rather
    than by the candidate (Phase 3's expiry path), and is recorded on the
    submission so a reader can tell the two apart.
    """
    if question.question_type != types.CODING or not coding_payload.is_v2(question.payload_json):
        return None
    answer = (
        await session.execute(
            select(AssessmentAnswer).where(
                AssessmentAnswer.conversation_id == conversation.id,
                AssessmentAnswer.question_id == question.id,
            )
        )
    ).scalar_one_or_none()
    if answer is None:
        raise ValueError(
            "a final coding answer was accepted before its assessment_answers row was written"
        )
    return await submissions.accept_final(
        session,
        conversation=conversation,
        question=question,
        answer=answer,
        auto_submitted=auto_submitted,
    )
