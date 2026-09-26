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
from typing import Any, Sequence

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
    "RETRIEVAL_DEGRADED",
    "RETRIEVAL_NOT_REQUESTED",
    "RETRIEVAL_STATUSES",
    "RETRIEVAL_USED",
    "ItemEvaluation",
    "SkillGrade",
    "must_have_failed",
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


#: Whether the judgement of a skill read retrieved transcript passages from the
#: candidate's OTHER answers (the Evidence RAG, through the typed tool layer).
#: Recorded on every skill so "did the judge see related context" is a fact on
#: the record rather than an inference. `not_requested` is explicit: the
#: caller supplied no reader, or the bucket is one retrieval is not asked for
#: (Nice-to-have). `degraded` means the reader was asked and came back without
#: a whole answer; the grade still stands on the answers themselves.
RETRIEVAL_NOT_REQUESTED = "not_requested"
RETRIEVAL_USED = "used"
RETRIEVAL_DEGRADED = "degraded"
RETRIEVAL_STATUSES: tuple[str, ...] = (
    RETRIEVAL_NOT_REQUESTED,
    RETRIEVAL_USED,
    RETRIEVAL_DEGRADED,
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
    #: The retrieved passages (`evidence_retrieval.PassageRef`: chunk id,
    #: source, verbatim content, NO score) the judgement was shown, so Siddhi
    #: can cite them with `context_chunks:<id>` locators. In-process only.
    passages: tuple[Any, ...] = field(default=(), repr=False)
    #: One of `RETRIEVAL_STATUSES`, and the reason when degraded (an exception
    #: CLASS name or a fixed reason word, never a message).
    retrieval: str = RETRIEVAL_NOT_REQUESTED
    retrieval_reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ANSWER_STATUSES:
            raise ValueError(f"unknown skill status {self.status!r}")
        if self.retrieval not in RETRIEVAL_STATUSES:
            raise ValueError(f"unknown retrieval status {self.retrieval!r}")
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
            "retrieval": self.retrieval,
            "retrieval_reason": self.retrieval_reason,
            # Locators only. The passage TEXT is a candidate's words and is
            # resolved at read time, never copied into a working record.
            "passage_locators": [
                str(getattr(piece, "locator", "")) for piece in self.passages
            ],
        }


def must_have_failed(skill_grades: "Sequence[SkillGrade]") -> bool:
    """THE definition of a failed Must-have, stated once (PLAN-p5 3.2.5).

    A Must-have skill counts as FAILED iff it was graded or left unanswered AND
    its word is Not Matching. That includes a Must-have the candidate was asked
    about and did not answer (owner ruling O5-1). A Must-have Miti could NOT
    assess is not failed: an outage is not a finding about a candidate, and it
    withholds the overall instead (`aggregation.OVERALL_NOT_ASSESSED`).

    Every reader of the rule calls this: the aggregation's flag, the result's
    property and, through the stored report column, the ranking blend. Two
    spellings of one predicate is how two readers come to disagree.
    """
    from app.services import rating
    from app.services.assessment_contract import BUCKET_MUST_HAVE

    return any(
        grade.bucket == BUCKET_MUST_HAVE
        and grade.status != ANSWER_NOT_ASSESSED
        and grade.grade == rating.GRADE_NOT
        for grade in skill_grades
    )
