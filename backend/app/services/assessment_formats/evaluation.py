"""AI evaluation with reasoning for the evidence-based format (spec 6.2).

A BARE NUMBER IS NOT DEFENSIBLE IN A HIRING CONTEXT. Every evaluation this
module produces carries its reasoning, the parts of the answer it cites, and a
per-criterion breakdown, and the deterministic evaluation inside the loop
refuses an output that lacks any of them. The score folds into the skill
through Miti's item stage (`miti.items`) and is projected to a word at the
boundary; the reasoning is what the recruiter's Q&A view shows.

CODING IS NOT EVALUATED HERE ANY MORE (PLAN-p5 WP5-D, the p4-4f hunk). A
coding answer is graded from what the sandbox RAN, 70 parts hidden tests and
30 parts the code-quality review (`coding_assessment.review` and
`coding_assessment.evidence`). The read-only coding evaluator and its
"not executed" note are deleted, and `evaluate` REFUSES a coding answer, so a
caller that still routes one here fails loudly instead of grading by reading.

CITATIONS ARE VERBATIM OR THEY ARE REJECTED. A citation is a phrase copied
from the answer; the check is a normalised substring test, so a model that
"cites" a paraphrase is told and writes it again. A fabricated citation reads
as provenance, which is worse than none.

THE FALLBACK IS THE PRODUCT'S PREVIOUS SCORER. `evaluate` returns None when
degraded and the caller scores the answer the way every rubric-scored answer
was scored before this module existed; nothing here invents a reasoning.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

from app.prompts import fragments, registry
from app.services import agent_loop, llm_router
from app.services.assessment_formats import config as format_config
from app.services.assessment_formats import types

logger = logging.getLogger(__name__)

__all__ = [
    "EVIDENCE_CRITERIA",
    "evaluate",
    "rubric_for",
]

#: Section 2.1's evaluation rubric, in order.
EVIDENCE_CRITERIA: dict[str, str] = {
    "specificity": "Concrete detail rather than generality: names, numbers, sequences, constraints.",
    "ownership_clarity": "Whether the candidate says what they did themselves, distinct from what the team did.",
    "technical_depth": "Depth appropriate to the seniority the resume claims.",
    "coherence_with_resume": "Whether the account fits the resume item the question probed.",
    "honesty_markers": "Willingness to name difficulty, failure, trade-offs or limits.",
}

#: The bounds of the two numeric fields the model returns.
SCORE_MIN = 0
SCORE_MAX = 100
UNIT_MIN = 0.0
UNIT_MAX = 1.0

_PROMPT_FOR_TYPE: dict[str, str] = {
    types.EVIDENCE_BASED: "assessment_answer_evaluation_evidence",
}


def rubric_for(question_type: str) -> dict[str, str]:
    """The fixed criteria this format is evaluated against."""
    if question_type == types.EVIDENCE_BASED:
        return dict(EVIDENCE_CRITERIA)
    raise ValueError(f"{question_type} is not AI-evaluated by this module")


def _criteria_block(criteria: dict[str, str]) -> str:
    return "\n".join(f"- {name}: {description}" for name, description in criteria.items())


def _question_rubric_block(rubric: dict[str, Any] | None) -> str:
    if not rubric:
        return ""
    bands = "; ".join(f"{band}: {text}" for band, text in rubric.items() if text)
    return (
        "THE STANDARD WRITTEN FOR THIS QUESTION, which the overall score must "
        "respect: " + bands
    )


def _normalise(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


def _parse(raw: str, criteria: dict[str, str]) -> dict[str, Any]:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("response was not a JSON object")
    score = int(round(float(parsed.get("score"))))
    rubric_scores = parsed.get("rubric_scores")
    if not isinstance(rubric_scores, dict):
        raise ValueError("rubric_scores missing")
    return {
        "score": score,
        "rubric_scores": {
            str(name): float(rubric_scores.get(name)) if rubric_scores.get(name) is not None else None
            for name in criteria
        },
        "reasoning": " ".join(str(parsed.get("reasoning") or "").split()),
        "citations": [
            " ".join(str(item).split())
            for item in (parsed.get("citations") or [])
            if str(item).strip()
        ],
    }


def _evaluator(*, question_type: str, criteria: dict[str, str], answer_text: str):
    """The deterministic criteria for one evaluation, as a closure."""
    conf = format_config.get_config()
    haystack = _normalise(answer_text)

    def evaluate_output(value: dict[str, Any]) -> agent_loop.Critique:
        from app.services.siddhi import numbers as report_numbers

        reasons: list[str] = []
        score = value["score"]
        if not SCORE_MIN <= score <= SCORE_MAX:
            reasons.append(f"score must be an integer between {SCORE_MIN} and {SCORE_MAX}")
        for name in criteria:
            unit = value["rubric_scores"].get(name)
            if unit is None or not UNIT_MIN <= unit <= UNIT_MAX:
                reasons.append(f"rubric_scores.{name} must be a number between 0.0 and 1.0")
        reasoning = value["reasoning"]
        if _word_count(reasoning) < conf.evaluation_min_reasoning_words:
            reasons.append(
                f"write at least {conf.evaluation_min_reasoning_words} words of reasoning; "
                f"the previous attempt had {_word_count(reasoning)}"
            )
        if not value["citations"]:
            reasons.append("cite at least one phrase copied word for word from the answer")
        fabricated = [item for item in value["citations"] if _normalise(item) not in haystack]
        if fabricated:
            reasons.append(
                "every citation must be copied word for word from the answer; these "
                "are not in it: " + "; ".join(repr(item[:60]) for item in fabricated[:3])
            )
        for violation in report_numbers.scan_text(reasoning, path="reasoning"):
            reasons.append(
                "the reasoning is prose a recruiter reads and carries no score, "
                f"rating or percentage: {violation.detail}"
            )
        return agent_loop.reject(*reasons) if reasons else agent_loop.ok()

    return evaluate_output


async def evaluate(
    session: Any,
    *,
    question_type: str,
    prompt: str,
    answer_text: str,
    item_name: str,
    resume_anchor: str | None = None,
    question_rubric: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    language: str | None = None,
) -> agent_loop.LoopResult[dict[str, Any] | None]:
    """Evaluate one evidence-based answer. Degraded means None.

    The returned dict is what `assessment_answers.ai_evaluation_json` stores:
    the score, the per-criterion scores, the reasoning, the verbatim
    citations and the rubric used.

    RAISES `ValueError` for a coding answer: coding is graded from the
    sandbox run and `coding_assessment.review`, never by reading the code.
    """
    if question_type == types.CODING:
        raise ValueError(
            "a coding answer is graded from its sandbox run and "
            "coding_assessment.review, never evaluated by reading the code"
        )
    criteria = rubric_for(question_type)
    conf = format_config.get_config()
    system = registry.render(
        _PROMPT_FOR_TYPE[question_type],
        item_name=item_name,
        resume_anchor=resume_anchor or "not recorded",
        question=prompt,
        candidate_text_is_data=fragments.CANDIDATE_TEXT_IS_DATA,
        criteria=_criteria_block(criteria),
        question_rubric=_question_rubric_block(question_rubric),
        min_words=conf.evaluation_min_reasoning_words,
    )

    async def execute(reflection: str) -> dict[str, Any]:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps({"answer": answer_text})},
        ]
        if reflection:
            messages.append({"role": "user", "content": reflection})
        raw = await llm_router.invoke_llm(
            "answer_evaluation", messages, response_format_json=True, session=session
        )
        return _parse(raw, criteria)

    result = await agent_loop.run_loop(
        name="answer_evaluation",
        execute=execute,
        evaluate=_evaluator(question_type=question_type, criteria=criteria, answer_text=answer_text),
        fallback=None,
        max_attempts=agent_loop.BACKGROUND_ATTEMPTS,
        deadline_seconds=agent_loop.BACKGROUND_DEADLINE,
        max_generated_tokens=agent_loop.BACKGROUND_TOKEN_BUDGET,
    )
    if result.degraded or result.value is None:
        logger.info(
            "assessment_formats.evaluation_degraded type=%s attempts=%d reasons=%s",
            question_type, result.attempts, list(result.reasons),
        )
        return result
    record = dict(result.value)
    record["rubric"] = criteria
    record["evaluated_at"] = datetime.now(timezone.utc).isoformat()
    result.value = record
    return result
