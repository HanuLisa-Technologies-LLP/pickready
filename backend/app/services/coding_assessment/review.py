"""The code-quality review of a final coding answer. One model call, judged.

WHAT IT IS FOR. The hidden tests decide whether a program is correct; they are
seventy parts of a coding question's score (CONTRACT v2). The other thirty are
this review: how the code is written, which edge cases it handles, whether its
approach fits the constraints, whether it is idiomatic. It runs AFTER
execution, so the reviewer is told how the program did and never has to guess.

WHAT THE REVIEWER NEVER SEES: A HIDDEN TEST. Its input is the public problem,
the candidate's code, the reviewer's private approach notes (which describe
the solution, never a test) and the OUTCOME SENTENCE from `phrasing`, which is
built from counts and outcome words only. So nothing this module sends can
quote the answer key, whatever the program printed.

THE CANDIDATE'S CODE IS DATA. It passes `conversation_guardrails.inspect_answer`
before it reaches the prompt, exactly as a prose answer does. A comment
addressed to the reviewer is not refused (the answer is already submitted, and
refusing it would let one comment remove a candidate's work from their
assessment); it is recorded as a LIMITATION and it sets `needs_human_review`,
because a grade the candidate tried to write for themselves is one a person
should look at. The reviewer sees the sanitised text, and citations are
checked against that same text.

JUDGED BY CODE, NEVER BY A MODEL. `agent_loop.run_loop` with deterministic
criteria: every criterion scored on its scale, an overall score on 0 to 100,
reasoning of at least the configured length, at least one citation, every
citation verbatim from the code, and no number in the reasoning a recruiter
reads. A rejection is fed back as the next instruction.

A FAILED REVIEW IS A STATE, NEVER A TEMPLATE. Degraded returns None; the
submission records `review_status='failed'` and the fifteen-minute sweep asks
again. Nothing here writes a default review, and `model_id` and
`prompt_version` are returned only for a review a model actually wrote.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.config import llm_providers
from app.prompts import fragments, registry
from app.services import agent_loop, conversation_guardrails, llm_router
from app.services.assessment_formats import config as format_config
from app.services.coding_assessment.payload import CODING_REVIEW_CRITERIA

logger = logging.getLogger(__name__)

__all__ = [
    "TASK_TYPE",
    "PROMPT_NAME",
    "CRITERIA",
    "LIMITATION_ADDRESSED_TO_REVIEWER",
    "ReviewInput",
    "ReviewOutcome",
    "build_messages",
    "review_code_quality",
]

TASK_TYPE = "coding_quality_review"
PROMPT_NAME = "coding_quality_review"

#: What each criterion means, stated to the reviewer. The keys are the
#: question's stored rubric (`payload.CODING_REVIEW_CRITERIA`), in order.
CRITERIA: dict[str, str] = {
    "code_quality": "Structure, naming and readability, as a colleague maintaining it would judge them.",
    "edge_case_handling": "Which boundary and unusual inputs the code handles, and which it misses.",
    "efficiency_awareness": "Whether the approach fits the stated constraints in time and memory.",
    "idiomatic_use": "Whether the code uses the chosen language the way its practitioners do.",
}
if tuple(CRITERIA) != CODING_REVIEW_CRITERIA:
    raise RuntimeError("review.CRITERIA and payload.CODING_REVIEW_CRITERIA disagree")

SCORE_MIN = 0
SCORE_MAX = 100
UNIT_MIN = 0.0
UNIT_MAX = 1.0

#: Recorded when the code carries text aimed at the reviewer. A fact about the
#: submission, in words a recruiter reads; it names no rule and no pattern.
LIMITATION_ADDRESSED_TO_REVIEWER = (
    "The code contains text addressed to the reviewer. It was reviewed as part "
    "of the code, and a person should read it."
)
_REVIEWER_DIRECTED = frozenset({"prompt_injection", "rubric_probe", "abuse"})


@dataclass(frozen=True)
class ReviewInput:
    """Everything the review reads. Nothing here can hold a hidden test."""

    statement: str
    input_format: str
    output_format: str
    constraints: str
    language: str
    approach_notes: str = field(repr=False)
    outcome_sentence: str
    code: str = field(repr=False)


@dataclass(frozen=True)
class ReviewOutcome:
    """A written review, or None with the loop's reasons.

    `record` is what `coding_submissions.review_json` stores: the overall
    score and the four criterion scores (INTERNAL, never serialised), the
    reasoning, the verbatim citations, the limitations and whether a person
    must look. `model_id` and `prompt_version` are set only with a record.
    """

    record: dict[str, Any] | None
    reasons: tuple[str, ...]
    model_id: str | None
    prompt_version: str | None


def _normalise(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


def _criteria_block() -> str:
    return "\n".join(f"- {name}: {meaning}" for name, meaning in CRITERIA.items())


def build_messages(review_input: ReviewInput, *, code_seen: str) -> list[dict[str, str]]:
    """The system prompt and the one user message. `code_seen` is the
    sanitised code, which is also what citations are checked against."""
    system = registry.render(
        PROMPT_NAME,
        candidate_text_is_data=fragments.CANDIDATE_TEXT_IS_DATA,
        criteria=_criteria_block(),
        min_words=format_config.get_config().evaluation_min_reasoning_words,
    )
    user = json.dumps(
        {
            "problem": review_input.statement,
            "input_format": review_input.input_format,
            "output_format": review_input.output_format,
            "constraints": review_input.constraints or "none stated",
            "language": review_input.language,
            "reviewer_notes": review_input.approach_notes,
            "test_outcome": review_input.outcome_sentence,
            "code": code_seen,
        },
        ensure_ascii=False,
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _parse(raw: str) -> dict[str, Any]:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("the review was not a JSON object")
    criteria = parsed.get("criteria")
    if not isinstance(criteria, dict):
        raise ValueError("the review has no criteria object")

    def unit(name: str) -> float | None:
        value = criteria.get(name)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    score = parsed.get("score")
    return {
        "score": int(round(float(score))) if isinstance(score, (int, float)) and not isinstance(score, bool) else None,
        "criteria": {name: unit(name) for name in CRITERIA},
        "reasoning": " ".join(str(parsed.get("reasoning") or "").split()),
        "citations": [
            str(item) for item in (parsed.get("citations") or []) if str(item).strip()
        ],
    }


def _evaluator(code_seen: str):
    haystack = _normalise(code_seen)
    min_words = format_config.get_config().evaluation_min_reasoning_words

    def evaluate(value: dict[str, Any]) -> agent_loop.Critique:
        from app.services.siddhi import numbers as report_numbers

        reasons: list[str] = []
        score = value["score"]
        if score is None or not SCORE_MIN <= score <= SCORE_MAX:
            reasons.append(f"score must be an integer between {SCORE_MIN} and {SCORE_MAX}")
        for name in CRITERIA:
            unit = value["criteria"].get(name)
            if unit is None or not UNIT_MIN <= unit <= UNIT_MAX:
                reasons.append(f"criteria.{name} must be a number between 0.0 and 1.0")
        words = _word_count(value["reasoning"])
        if words < min_words:
            reasons.append(
                f"write at least {min_words} words of reasoning; the previous attempt had {words}"
            )
        if not value["citations"]:
            reasons.append("cite at least one fragment copied word for word from the code")
        fabricated = [item for item in value["citations"] if _normalise(item) not in haystack]
        if fabricated:
            # Counted, never quoted: a reason is logged, and the model's
            # fabrication may itself paraphrase the candidate's code.
            reasons.append(
                f"{len(fabricated)} citation(s) are not copied word for word from the "
                "code; quote the code exactly"
            )
        for violation in report_numbers.scan_text(value["reasoning"], path="reasoning"):
            reasons.append(
                "the reasoning is prose a recruiter reads and carries no score, "
                f"rating or percentage: {violation.detail}"
            )
        return agent_loop.reject(*reasons) if reasons else agent_loop.ok()

    return evaluate


async def review_code_quality(session: Any, review_input: ReviewInput) -> ReviewOutcome:
    """Review one final coding answer. Never raises for a model failure.

    An empty program is a caller defect (an empty answer is never reviewed:
    it is an evidence gap), so it raises ValueError.
    """
    if not review_input.code.strip():
        raise ValueError("an empty coding answer is an evidence gap and is never reviewed")
    guard = conversation_guardrails.inspect_answer(review_input.code)
    code_seen = guard.sanitized
    limitations: list[str] = []
    needs_human_review = False
    if guard.violation in _REVIEWER_DIRECTED or not guard.allowed:
        limitations.append(LIMITATION_ADDRESSED_TO_REVIEWER)
        needs_human_review = True
        logger.warning("coding_review.addressed_to_reviewer violation=%s", guard.violation)
    base_messages = build_messages(review_input, code_seen=code_seen)
    prompt_version = registry.version(PROMPT_NAME)

    async def execute(reflection: str) -> dict[str, Any]:
        messages = list(base_messages)
        if reflection:
            messages.append({"role": "user", "content": reflection})
        raw = await llm_router.invoke_llm(TASK_TYPE, messages, response_format_json=True, session=session)
        return _parse(raw)

    result = await agent_loop.run_loop(
        name="coding_quality_review",
        execute=execute,
        evaluate=_evaluator(code_seen),
        fallback=None,
        max_attempts=agent_loop.BACKGROUND_ATTEMPTS,
        deadline_seconds=agent_loop.BACKGROUND_DEADLINE,
        max_generated_tokens=agent_loop.BACKGROUND_TOKEN_BUDGET,
    )
    if result.degraded or result.value is None:
        logger.info(
            "coding_review.degraded attempts=%d reasons=%d", result.attempts, len(result.reasons)
        )
        return ReviewOutcome(record=None, reasons=tuple(result.reasons), model_id=None, prompt_version=None)
    value = result.value
    record = {
        "score": value["score"],
        "criteria": dict(value["criteria"]),
        "reasoning": value["reasoning"],
        "citations": list(value["citations"]),
        "limitations": limitations,
        "needs_human_review": needs_human_review,
        "rubric": list(CRITERIA),
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    return ReviewOutcome(
        record=record,
        reasons=(),
        model_id=llm_providers.model_for(TASK_TYPE),
        prompt_version=prompt_version,
    )
