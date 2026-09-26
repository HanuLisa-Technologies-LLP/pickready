"""What a final coding answer is worth to the grader, in one typed record.

Miti is the one grading authority. This module hands it, per coding answer, a
`CodingEvidence`: what state the answer is in, how many hidden tests it passed
(INTERNAL), how the others failed, the code-quality review, the 70/30 score
from `scoring.coding_question_score`, and the sentence a report prints. Miti
decides the grade; nothing here names a grade word.

FOUR STATES, AND THE ONE THAT IS NOT A SCORE OF ZERO
----------------------------------------------------
  complete      every hidden test ran and the review was written. `score` is set.
  pending       still executing or being reviewed, and younger than
                `coding_execution_max_wait_hours`. Scoring WAITS for it.
  unavailable   still open after that window (a sandbox or provider outage
                that outlasted a day). `score` is None, which the grader reads
                as "Not assessed", and `needs_human_review` is set. Never a
                synthetic score: a candidate is not graded for our outage.
  not_answered  the final answer was empty. An evidence gap, never a zero.

The age is measured from the submission's own `created_at`, the moment the
answer was accepted, so the window is the same whichever worker looks.

WHAT CROSSES INTO A REPORT. Only `phrase` and the review's reasoning and
citations are prose; the counts and scores are internal and a report states
results through `phrase`, which is spelled-out words
(`phrasing.outcome_sentence`).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.assessment import AssessmentAnswer, CandidateQuestion
from app.models.coding import (
    EXECUTION_COMPLETE,
    EXECUTION_NO_CODE,
    REVIEW_COMPLETE,
    CodingSubmission,
)
from app.services.coding_assessment import phrasing, scoring
from app.services.coding_assessment.execution import TEST_COMPILE_ERROR, outcome_counts

__all__ = [
    "STATE_COMPLETE",
    "STATE_PENDING",
    "STATE_UNAVAILABLE",
    "STATE_NOT_ANSWERED",
    "STATES",
    "CodingReview",
    "CodingEvidence",
    "build",
    "evidence_for_answer",
    "for_conversation",
]

STATE_COMPLETE = "complete"
STATE_PENDING = "pending"
STATE_UNAVAILABLE = "unavailable"
STATE_NOT_ANSWERED = "not_answered"
STATES: tuple[str, ...] = (STATE_COMPLETE, STATE_PENDING, STATE_UNAVAILABLE, STATE_NOT_ANSWERED)


@dataclass(frozen=True)
class CodingReview:
    """The code-quality review as the grader reads it.

    `score` and `criteria` are INTERNAL. `reasoning` and `citations` are
    prose a recruiter may read; every citation is verbatim from the code.
    """

    score: int
    criteria: Mapping[str, float]
    reasoning: str
    citations: tuple[str, ...]
    limitations: tuple[str, ...]
    needs_human_review: bool


@dataclass(frozen=True)
class CodingEvidence:
    """One final coding answer, ready for the grader. See the module docstring."""

    answer_id: uuid.UUID
    question_id: uuid.UUID
    #: `candidate_questions.competency_id`: the skill this question probes.
    skill_id: uuid.UUID
    state: str
    language: str | None
    auto_submitted: bool
    #: INTERNAL counts, never serialised as numbers.
    tests_total: int | None
    tests_passed: int | None
    outcome_counts: Mapping[str, int] = field(default_factory=dict)
    compile_error: bool = False
    review: CodingReview | None = None
    #: The 70/30 score, INTERNAL. None unless the state is `complete`.
    score: int | None = None
    #: Spelled-out words, the only statement of the result a report carries.
    phrase: str = ""
    needs_human_review: bool = False


def _review(review_json: Any) -> CodingReview | None:
    if not isinstance(review_json, dict):
        return None
    return CodingReview(
        score=int(review_json["score"]),
        criteria={str(k): float(v) for k, v in dict(review_json.get("criteria") or {}).items()},
        reasoning=str(review_json.get("reasoning") or ""),
        citations=tuple(str(item) for item in review_json.get("citations") or ()),
        limitations=tuple(str(item) for item in review_json.get("limitations") or ()),
        needs_human_review=bool(review_json.get("needs_human_review")),
    )


def _as_aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)


def build(
    submission: CodingSubmission,
    *,
    skill_id: uuid.UUID,
    language: str | None,
    now: datetime,
    max_wait: timedelta,
    test_weight: float,
) -> CodingEvidence:
    """The evidence for one submission row. Pure: the caller supplies `now`."""
    common: dict[str, Any] = {
        "answer_id": submission.answer_id,
        "question_id": submission.question_id,
        "skill_id": skill_id,
        "language": language,
        "auto_submitted": bool(submission.auto_submitted),
    }
    if submission.execution_status == EXECUTION_NO_CODE:
        return CodingEvidence(
            **common,
            state=STATE_NOT_ANSWERED,
            tests_total=None,
            tests_passed=None,
            phrase=phrasing.outcome_sentence(state=STATE_NOT_ANSWERED),
        )
    executed = submission.execution_status == EXECUTION_COMPLETE
    counts = outcome_counts(submission.test_results_json or ()) if executed else {}
    tests_total = submission.tests_total if executed else None
    tests_passed = submission.tests_passed if executed else None
    compile_error = executed and bool(counts.get(TEST_COMPILE_ERROR))
    review = _review(submission.review_json) if submission.review_status == REVIEW_COMPLETE else None
    if executed and review is not None:
        return CodingEvidence(
            **common,
            state=STATE_COMPLETE,
            tests_total=tests_total,
            tests_passed=tests_passed,
            outcome_counts=counts,
            compile_error=compile_error,
            review=review,
            score=scoring.coding_question_score(
                tests_passed=tests_passed,
                tests_total=tests_total,
                review_score=review.score,
                test_weight=test_weight,
            ),
            phrase=phrasing.outcome_sentence(
                state=STATE_COMPLETE,
                tests_total=tests_total,
                tests_passed=tests_passed,
                outcome_counts=counts,
            ),
            needs_human_review=review.needs_human_review,
        )
    expired = now - _as_aware(submission.created_at) >= max_wait
    state = STATE_UNAVAILABLE if expired else STATE_PENDING
    return CodingEvidence(
        **common,
        state=state,
        tests_total=tests_total,
        tests_passed=tests_passed,
        outcome_counts=counts,
        compile_error=compile_error,
        review=review,
        score=None,
        phrase=phrasing.outcome_sentence(state=state),
        needs_human_review=expired,
    )


def _settings_window() -> tuple[timedelta, float]:
    settings = get_settings()
    return timedelta(hours=settings.coding_execution_max_wait_hours), settings.coding_score_test_weight


async def _rows(session: AsyncSession, *conditions: Any) -> list[tuple[CodingSubmission, uuid.UUID, Any]]:
    result = await session.execute(
        select(CodingSubmission, CandidateQuestion.competency_id, AssessmentAnswer.answer_json)
        .join(CandidateQuestion, CandidateQuestion.id == CodingSubmission.question_id)
        .join(AssessmentAnswer, AssessmentAnswer.id == CodingSubmission.answer_id)
        .where(*conditions)
        .order_by(CodingSubmission.created_at)
    )
    return [(row[0], row[1], row[2]) for row in result.all()]


def _language(answer_json: Any) -> str | None:
    if isinstance(answer_json, dict) and isinstance(answer_json.get("language"), str):
        return answer_json["language"]
    return None


async def evidence_for_answer(
    session: AsyncSession, answer_id: uuid.UUID, *, now: datetime | None = None
) -> CodingEvidence | None:
    """The evidence for one answer, or None when it has no coding submission
    (a legacy v1 coding answer, or any other format). Reads through the
    caller's session, so RLS bounds it to the tenant."""
    rows = await _rows(session, CodingSubmission.answer_id == answer_id)
    if not rows:
        return None
    submission, skill_id, answer_json = rows[0]
    max_wait, weight = _settings_window()
    return build(
        submission,
        skill_id=skill_id,
        language=_language(answer_json),
        now=now or datetime.now(timezone.utc),
        max_wait=max_wait,
        test_weight=weight,
    )


async def for_conversation(
    session: AsyncSession, conversation_id: uuid.UUID, *, now: datetime | None = None
) -> dict[uuid.UUID, CodingEvidence]:
    """Every coding answer's evidence on one conversation, keyed by question."""
    max_wait, weight = _settings_window()
    moment = now or datetime.now(timezone.utc)
    return {
        submission.question_id: build(
            submission,
            skill_id=skill_id,
            language=_language(answer_json),
            now=moment,
            max_wait=max_wait,
            test_weight=weight,
        )
        for submission, skill_id, answer_json in await _rows(
            session, CodingSubmission.conversation_id == conversation_id
        )
    }
