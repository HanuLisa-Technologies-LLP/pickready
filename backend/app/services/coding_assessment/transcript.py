"""What a recruiter reads about an executed coding answer, beside the transcript.

The recruiter's transcript (`api/assessments.get_transcript`, behind
`view_review_screen` and the job-closure 410 gate) shows each structured
answer's detail. For an executed (v2) coding answer that detail is four
things, and every one of them is prose:

  outcome           `evidence.CodingEvidence.phrase`, the spelled-out sentence
                    a PRISM report prints ("Passed seven of the ten hidden
                    tests; ..."). Counts in words, never digits.
  compile_error     the compiler's own message, when the code did not
                    compile. It is derived from the candidate's code alone,
                    because compilation precedes any input, so it cannot
                    quote a hidden test.
  review_reasoning  the code-quality review's reasoning, once it is written.
  review_citations  its citations, each verbatim from the candidate's code.

WHAT IS DELIBERATELY ABSENT. No hidden test, no expected output, no
reference solution and no approach notes: this module never imports the
answer-key module and reads no column that holds one. No count and no
score: the 70/30 score and the pass counts are the grader's (Miti's), and a
recruiter reads the sentence.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.coding import CodingSubmission
from app.services.coding_assessment import evidence as coding_evidence

__all__ = ["RecruiterView", "recruiter_views"]


@dataclass(frozen=True)
class RecruiterView:
    """The recruiter-readable facts about one executed coding answer."""

    outcome: str
    compile_error: str | None
    review_reasoning: str | None
    review_citations: tuple[str, ...]


async def recruiter_views(
    session: AsyncSession, conversation_id: uuid.UUID
) -> dict[uuid.UUID, RecruiterView]:
    """Every executed coding answer on a conversation, keyed by question id.

    Reads through the caller's session, so RLS bounds it to the caller's
    tenant. A question with no submission (a legacy v1 coding answer, or any
    other format) is simply absent.
    """
    evidence = await coding_evidence.for_conversation(session, conversation_id)
    if not evidence:
        return {}
    compile_output = dict(
        (
            await session.execute(
                select(CodingSubmission.question_id, CodingSubmission.compile_output).where(
                    CodingSubmission.conversation_id == conversation_id
                )
            )
        ).all()
    )
    views: dict[uuid.UUID, RecruiterView] = {}
    for question_id, item in evidence.items():
        review = item.review
        views[question_id] = RecruiterView(
            outcome=item.phrase,
            compile_error=(compile_output.get(question_id) or None) if item.compile_error else None,
            review_reasoning=review.reasoning if review is not None else None,
            review_citations=review.citations if review is not None else (),
        )
    return views
