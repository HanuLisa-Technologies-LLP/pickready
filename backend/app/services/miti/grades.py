"""Miti's grading VALUES: what one answer and one skill were judged to be.

Frozen dataclasses and the vocabulary that labels them. No ORM row, no session,
no model call, and no import of anything that can reach a provider, so every
stage that reads a grade (aggregation, the caps, Siddhi, persistence) can
import this module without acquiring a route to a model. `items.py` is the only
writer of these values.

THREE STATUSES, AND THE DIFFERENCE BETWEEN THE LAST TWO IS THE WHOLE POINT
---------------------------------------------------------------------------
    graded        the evidence was judged and a score was written from it
    unanswered    the candidate was asked and gave nothing gradeable (empty,
                  gibberish, no submission). It grades Not Matching, and an
                  unanswered Must-have counts as FAILED (owner ruling O5-1)
    not_assessed  the evaluation itself could not run (a model outage, an
                  execution provider outage). There is NO score, never a
                  synthetic one: an outage is not a finding about a candidate

`unanswered` is a fact about the candidate; `not_assessed` is a fact about the
platform. Conflating them is the defect this module's predecessor had in both
directions: the hash fallback turned an outage into a plausible grade, and an
unreadable input table turned every answer into "unanswered".
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "ANSWER_GRADED",
    "ANSWER_NOT_ASSESSED",
    "ANSWER_UNANSWERED",
    "ANSWER_STATUSES",
    "METHODS",
    "METHOD_BEHAVIOURAL_STANDARD",
    "METHOD_CODING",
    "METHOD_EVIDENCE",
    "METHOD_GENERAL_STANDARD",
    "METHOD_OBJECTIVE",
    "METHOD_RUBRIC",
    "METHOD_UNANSWERED",
    "ItemEvaluation",
    "SkillGrade",
]

ANSWER_GRADED = "graded"
ANSWER_UNANSWERED = "unanswered"
ANSWER_NOT_ASSESSED = "not_assessed"
ANSWER_STATUSES: tuple[str, ...] = (ANSWER_GRADED, ANSWER_UNANSWERED, ANSWER_NOT_ASSESSED)

#: WHERE a score came from. Recorded on every item, because "why did this
#: answer grade as it did" is answered by the method first and the model
#: second, and a method that is not recorded cannot be audited.
METHOD_OBJECTIVE = "objective"                     # mcq / fill-in-the-blank auto_score
METHOD_CODING = "coding"                           # the coding evaluation
METHOD_EVIDENCE = "evidence"                       # evidence-based evaluation with reasoning
METHOD_RUBRIC = "rubric"                           # the stored rubric written with the question
METHOD_GENERAL_STANDARD = "general_standard"       # a rubric-scored row with no stored rubric
METHOD_BEHAVIOURAL_STANDARD = "behavioural_standard"  # one judgement across a behavioural skill
METHOD_UNANSWERED = "unanswered"                   # nothing gradeable was given
METHODS: tuple[str, ...] = (
    METHOD_OBJECTIVE,
    METHOD_CODING,
    METHOD_EVIDENCE,
    METHOD_RUBRIC,
    METHOD_GENERAL_STANDARD,
    METHOD_BEHAVIOURAL_STANDARD,
    METHOD_UNANSWERED,
)


@dataclass(frozen=True)
class ItemEvaluation:
    """One question's judgement.

    `score` is INTERNAL (0 to 100) and is None exactly when `status` is
    `not_assessed`. `failure` carries an exception CLASS NAME or a fixed reason
    word, never a message: a message can quote the answer, and this value
    travels into logs and into the evaluation's working record.
    """

    question_id: uuid.UUID | None
    status: str
    score: int | None
    weight: float
    method: str
    failure: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ANSWER_STATUSES:
            raise ValueError(f"unknown item status {self.status!r}")
        if (self.status == ANSWER_NOT_ASSESSED) != (self.score is None):
            raise ValueError(
                "an item has a score exactly when it was assessed: "
                f"status={self.status!r} score={self.score!r}"
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "question_id": str(self.question_id) if self.question_id else None,
            "status": self.status,
            "score": self.score,
            "weight": self.weight,
            "method": self.method,
            "failure": self.failure,
        }


@dataclass(frozen=True)
class SkillGrade:
    """One skill's grade, the ONLY per-skill judgement in the product.

    Miti is the sole grading authority (Vivekium release, WP5-B): the report's
    per-skill word, the category grades and the overall are all computed from
    these values and from nothing else. The five dimension evaluators read the
    same evidence and check this judgement (a divergence routes the report to a
    person), but they no longer produce a second grade.

    `used_answers` carries the answers the judgement read, so the remark writer
    can ground its prose in them. It is in-process only: never persisted by
    Miti, never logged.
    """

    skill_id: uuid.UUID
    name: str
    bucket: str
    priority: int
    status: str
    #: INTERNAL, 0 to 100. None exactly when `status` is `not_assessed`.
    score: int | None
    #: The rating word; None exactly when `status` is `not_assessed`.
    grade: str | None
    #: Some substantive answers were evaluated and some could not be. Graded on
    #: what was evaluated, and always routed to a person.
    partially_assessed: bool = False
    items: tuple[ItemEvaluation, ...] = ()
    used_answers: tuple[str, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        if self.status not in ANSWER_STATUSES:
            raise ValueError(f"unknown skill status {self.status!r}")
        if (self.status == ANSWER_NOT_ASSESSED) != (self.score is None):
            raise ValueError(
                "a skill has a score exactly when it was assessed: "
                f"status={self.status!r} score={self.score!r}"
            )
        if (self.score is None) != (self.grade is None):
            raise ValueError("a skill carries a grade word exactly when it carries a score")

    @property
    def assessed(self) -> bool:
        return self.status != ANSWER_NOT_ASSESSED

    def as_dict(self) -> dict[str, Any]:
        """INTERNAL projection (carries the score). Never a client payload."""
        return {
            "skill_id": str(self.skill_id),
            "name": self.name,
            "bucket": self.bucket,
            "priority": self.priority,
            "status": self.status,
            "score": self.score,
            "grade": self.grade,
            "partially_assessed": self.partially_assessed,
            "items": [item.as_dict() for item in self.items],
        }
