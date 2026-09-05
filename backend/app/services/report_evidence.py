"""Deterministic evidence extraction for completed PPI conversations.

This runs in the assessment task. It does not ask a model to invent a
summary: it groups the stored Q&A by the exact skill/competency key and records
observable signals and explicit absences. The original messages remain the
source of truth.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import (
    AssessmentConversation,
    CandidateTechnicalQuestion,
    JobCompetency,
    ReportSkillEvidence,
)

_TECHNICAL_TERMS = re.compile(
    r"\b(?:api|cache|database|index|latency|lock|query|queue|retry|schema|"
    r"service|thread|transaction|deployment|metric|trace|test|algorithm|"
    r"architecture|container|cluster|memory|cpu)\w*\b",
    re.IGNORECASE,
)
_STRUCTURE_TERMS = re.compile(
    r"\b(?:because|first|then|after|before|therefore|so that|trade[- ]?off|"
    r"root cause|result|outcome|measured|verified|learned)\b",
    re.IGNORECASE,
)
_EXAMPLE_TERMS = re.compile(
    r"\b(?:I|we)\s+(?:built|changed|chose|created|debugged|delivered|designed|"
    r"fixed|implemented|led|migrated|reduced|resolved|shipped|tested)\b|"
    r"\b\d+(?:\.\d+)?(?:%|ms|s|x| users?| requests?| days?| hours?)?\b",
    re.IGNORECASE,
)


def _snippets(text: str, pattern: re.Pattern[str], *, limit: int = 3) -> list[str]:
    sentences = [
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+|\n+", text)
        if part.strip()
    ]
    return [sentence[:360] for sentence in sentences if pattern.search(sentence)][:limit]


def build_evidence_payload(
    *,
    skill: str,
    category: str,
    question_keys: list[str],
    questions: list[str],
    answers: list[str],
    recorded_gaps: list[str] | None = None,
) -> dict[str, Any]:
    """Build one explainable evidence row without numerical grading."""
    joined = " ".join(answers).strip()
    words = re.findall(r"\b[\w'-]+\b", joined)
    precision = _snippets(joined, _TECHNICAL_TERMS)
    structure = _snippets(joined, _STRUCTURE_TERMS)
    examples = _snippets(joined, _EXAMPLE_TERMS)
    depth: list[str] = []
    if len(words) >= 80:
        depth.append("The answer develops the example across actions, constraints, and outcomes.")
    elif len(words) >= 35:
        depth.append("The answer gives some detail but leaves part of the reasoning or outcome open.")

    relevance = [
        f"The response was filed against {skill} from the role's {category.replace('_', ' ')} framework."
    ] if joined else []
    gaps: list[str] = []
    gaps.extend(recorded_gaps or [])
    if not joined:
        gaps.append("No substantive answer was recorded for this criterion.")
    if joined and not examples:
        gaps.append("No concrete owned example or measurable outcome was identified.")
    if joined and not structure:
        gaps.append("The answer did not clearly connect situation, action, decision, and result.")
    if category == "technical" and joined and not precision:
        gaps.append("No technically precise mechanism, tool, or diagnostic detail was identified.")

    return {
        "category": category,
        "skill": skill,
        "question_keys": question_keys,
        "technical_precision": precision,
        "depth": depth,
        "problem_solving_structure": structure,
        "role_relevance": relevance,
        "concrete_examples": examples,
        "explicit_gaps": gaps,
        # Questions are returned only to the caller building report evidence;
        # they are not duplicated into the evidence table.
        "_questions": questions,
    }


def extract_payloads(
    *,
    transcript: Iterable[dict[str, Any]],
    technical_questions: Iterable[CandidateTechnicalQuestion],
    competencies: Iterable[JobCompetency],
) -> list[dict[str, Any]]:
    technical = {
        str(row.id): ("technical", row.skill, row.prompt)
        for row in technical_questions
    }
    ppi = {
        str(row.id): (row.category, row.name, row.description or row.name)
        for row in competencies
    }
    lookup = {**technical, **ppi}
    grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"keys": [], "questions": [], "answers": [], "recorded_gaps": []}
    )
    for message in transcript:
        key = str(message.get("question_key") or "")
        mapped = lookup.get(key)
        if mapped is None:
            continue
        category, skill, fallback_question = mapped
        bucket = grouped[(category, skill)]
        if key not in bucket["keys"]:
            bucket["keys"].append(key)
        if message.get("speaker") == "agent":
            bucket["questions"].append(str(message.get("content") or fallback_question))
        elif message.get("speaker") == "candidate":
            answer = str(message.get("content") or "").strip()
            if answer:
                bucket["answers"].append(answer)
            if message.get("evidence_gap"):
                label = str(message.get("answer_label") or "non-substantive")
                bucket["recorded_gaps"].append(
                    f"Re-ask limit reached after a {label.replace('_', ' ')} "
                    "answer; the response was retained as an explicit evidence gap."
                )

    # Include every planned criterion, including explicit no-evidence rows.
    for key, (category, skill, question) in lookup.items():
        bucket = grouped[(category, skill)]
        if key not in bucket["keys"]:
            bucket["keys"].append(key)
        if not bucket["questions"]:
            bucket["questions"].append(question)

    return [
        build_evidence_payload(
            skill=skill,
            category=category,
            question_keys=data["keys"],
            questions=data["questions"],
            answers=data["answers"],
            recorded_gaps=data["recorded_gaps"],
        )
        for (category, skill), data in grouped.items()
    ]


async def persist_skill_evidence(
    session: AsyncSession,
    *,
    conversation: AssessmentConversation,
    transcript: list[dict[str, Any]],
    technical_questions: list[CandidateTechnicalQuestion],
    competencies: list[JobCompetency],
) -> list[dict[str, Any]]:
    """Replace one conversation's derived rows atomically and return payloads."""
    payloads = extract_payloads(
        transcript=transcript,
        technical_questions=technical_questions,
        competencies=competencies,
    )
    await session.execute(
        delete(ReportSkillEvidence).where(
            ReportSkillEvidence.conversation_id == conversation.id
        )
    )
    now = datetime.now(timezone.utc)
    for payload in payloads:
        stored = {key: value for key, value in payload.items() if key != "_questions"}
        session.add(
            ReportSkillEvidence(
                tenant_id=conversation.tenant_id,
                conversation_id=conversation.id,
                extracted_at=now,
                **stored,
            )
        )
    await session.flush()
    return payloads
