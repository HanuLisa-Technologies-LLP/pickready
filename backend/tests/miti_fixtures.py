"""Value builders for Miti's grading tests. No database, no model.

Since WP5-B the aggregation scores categories from Miti's SKILL grades, so a
test about the overall, the caps or confidence states the skill grades it is
about, instead of relying on the evaluators' per-competency bands (which no
longer grade anything).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.services import assessment_contract, rating
from app.services.miti.grades import (
    ANSWER_GRADED,
    ANSWER_NOT_ASSESSED,
    ANSWER_UNANSWERED,
    METHOD_RUBRIC,
    ItemEvaluation,
    SkillGrade,
)

LOCKED_AT = datetime(2026, 9, 24, 9, 0, tzinfo=timezone.utc)


def skill_id(name: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"miti-test-skill:{name}")


def skill(
    name: str,
    bucket: str = "must_have",
    score: int | None = 92,
    *,
    status: str | None = None,
    priority: int = 1,
    partially_assessed: bool = False,
) -> SkillGrade:
    """One skill grade. `score=None` means NOT ASSESSED."""
    if status is None:
        status = ANSWER_NOT_ASSESSED if score is None else ANSWER_GRADED
    grade = None if score is None else (rating.grade_for_percent(score) or rating.GRADE_NOT)
    item = ItemEvaluation(
        question_id=None,
        status=status,
        score=score,
        weight=1.0,
        method=METHOD_RUBRIC,
        failure="RuntimeError" if status == ANSWER_NOT_ASSESSED else None,
    )
    return SkillGrade(
        skill_id=skill_id(name),
        name=name,
        bucket=bucket,
        priority=priority,
        status=status,
        score=score,
        grade=grade,
        partially_assessed=partially_assessed,
        items=(item,),
    )


def unanswered(name: str, bucket: str = "must_have", priority: int = 1) -> SkillGrade:
    return skill(name, bucket, 25, status=ANSWER_UNANSWERED, priority=priority)


def strong_skills() -> tuple[SkillGrade, ...]:
    """One strong skill in each bucket: an uncapped, graded overall."""
    return (
        skill("Kafka", "must_have", 92),
        skill("Go", "nice_to_have", 92),
        skill("Ownership", "behavioural", 92),
    )


def contract(
    skills: tuple[tuple[str, str], ...] = (("Kafka", "must_have"), ("Ownership", "behavioural")),
    *,
    locked: bool = True,
    grade: str = "managerial",
) -> assessment_contract.AssessmentContract:
    """A contract as `load_contract_for_conversation` would return it."""
    built = tuple(
        assessment_contract.ContractSkill(
            id=skill_id(name),
            name=name,
            bucket=bucket,
            priority=index + 1,
            evidence_line=f"evidence of {name}",
        )
        for index, (name, bucket) in enumerate(skills)
    )
    return assessment_contract.AssessmentContract(
        job_id=uuid.uuid5(uuid.NAMESPACE_URL, "miti-test-job"),
        version=1 if locked else 0,
        locked=locked,
        skills=built,
        role_summary="",
        digest=assessment_contract.compute_digest(built, "", grade),
        grade=grade,
        locked_at=LOCKED_AT if locked else None,
    )
