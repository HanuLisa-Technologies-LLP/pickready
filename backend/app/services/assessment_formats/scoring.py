"""Deterministic scoring for the objective formats (spec section 6.1).

SCORED ON SUBMISSION, SERVER-SIDE, AND THE CLIENT'S OPINION IS NEVER READ.
`api/assessments.respond` validates the answer against the question it
answers (`types.parse_answer`) and calls `score`; nothing in the request body
carries a score and nothing here would read one.

PARTIAL CREDIT IS THE ONLY MULTI-CORRECT RULE (section 2.3):
    full credit     every correct option selected and no incorrect one
    partial         (correct selected minus incorrect selected) over the
                    number of correct options, floored at zero
    zero            no correct selection, or every option selected
The "select everything" strategy is why the incorrect-selection penalty
exists: with four options of which two are correct, selecting all four scores
(2 - 2) / 2 = 0, and the explicit all-selected rule makes that zero hold even
where the arithmetic alone would not (three correct of four: (3 - 1) / 3).

FILL-IN-THE-BLANK MATCHES EXACT FIRST AND ASKS BEFORE MARKING WRONG.
Whitespace is trimmed and collapsed, case is folded unless the blank says
otherwise. A value that matches no accepted answer is put to the model as a
yes-or-no equivalence question BEFORE it is marked incorrect, because a
candidate who writes "PostgreSQL" against a key that says "Postgres" has
answered. The model runs inside the bounded loop with a deterministic
evaluation; a degraded loop marks the blank incorrect and logs that it did,
which is the product's rule before the fallback existed and is never a
silent success.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.prompts import fragments, registry
from app.services import agent_loop, llm_router
from app.services.assessment_formats import types

logger = logging.getLogger(__name__)

__all__ = [
    "BLANK_RESULTS",
    "CORRECTNESS_WORDS",
    "FillBlankResult",
    "ObjectiveScore",
    "correctness_word",
    "match_blank",
    "normalise_blank",
    "score",
    "score_fill_blank",
    "score_mcq_multi",
    "score_mcq_single",
    "semantically_equivalent",
]

#: Per-blank outcomes, as the recruiter's view reads them.
RESULT_EXACT = "exact"
RESULT_EQUIVALENT = "equivalent"
RESULT_INCORRECT = "incorrect"
RESULT_NOT_ANSWERED = "not_answered"
BLANK_RESULTS: tuple[str, ...] = (RESULT_EXACT, RESULT_EQUIVALENT, RESULT_INCORRECT, RESULT_NOT_ANSWERED)

#: Correctness of a whole objective answer, as a WORD. The only projection of
#: `auto_score` that ever crosses a boundary.
CORRECT = "correct"
PARTIALLY_CORRECT = "partially_correct"
INCORRECT = "incorrect"
NOT_ANSWERED = "not_answered"
CORRECTNESS_WORDS: tuple[str, ...] = (CORRECT, PARTIALLY_CORRECT, INCORRECT, NOT_ANSWERED)

FULL_CREDIT = 1.0
NO_CREDIT = 0.0


@dataclass(frozen=True)
class FillBlankResult:
    score: float
    blank_results: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ObjectiveScore:
    """INTERNAL. `auto_score` is 0.0 to 1.0 and never leaves the server as a
    number; `correctness_word` is what does."""

    auto_score: float
    blank_results: list[str] = field(default_factory=list)


def correctness_word(auto_score: float | None) -> str:
    if auto_score is None:
        return NOT_ANSWERED
    if auto_score >= FULL_CREDIT:
        return CORRECT
    if auto_score <= NO_CREDIT:
        return INCORRECT
    return PARTIALLY_CORRECT


# ── Multiple choice ──────────────────────────────────────────────────────────


def score_mcq_single(payload: dict[str, Any], answer: dict[str, Any]) -> float:
    question = types.McqSinglePayload.model_validate(payload)
    selected = types.McqSingleAnswer.model_validate(answer)
    return FULL_CREDIT if selected.selected_option_id == question.correct_option_id else NO_CREDIT


def score_mcq_multi(payload: dict[str, Any], answer: dict[str, Any]) -> float:
    question = types.McqMultiPayload.model_validate(payload)
    selected = set(types.McqMultiAnswer.model_validate(answer).selected_option_ids)
    correct = set(question.correct_option_ids)
    offered = {option.id for option in question.options}
    if not selected & correct:
        return NO_CREDIT
    if selected >= offered:
        # The select-everything strategy scores zero whatever the arithmetic
        # below would say.
        return NO_CREDIT
    if selected == correct:
        return FULL_CREDIT
    credit = (len(selected & correct) - len(selected - correct)) / len(correct)
    return max(NO_CREDIT, min(FULL_CREDIT, credit))


# ── Fill in the blank ────────────────────────────────────────────────────────


def normalise_blank(value: str, *, case_sensitive: bool) -> str:
    """Trimmed, inner whitespace collapsed, case folded unless it matters."""
    collapsed = " ".join(str(value or "").split())
    return collapsed if case_sensitive else collapsed.casefold()


def match_blank(value: str, blank: types.FillBlank) -> str:
    """`exact`, `not_answered`, or `incorrect` before the model is asked."""
    typed = normalise_blank(value, case_sensitive=blank.case_sensitive)
    if not typed:
        return RESULT_NOT_ANSWERED
    accepted = {normalise_blank(item, case_sensitive=blank.case_sensitive) for item in blank.accepted}
    return RESULT_EXACT if typed in accepted else RESULT_INCORRECT


def _parse_equivalence(raw: str) -> bool:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict) or not isinstance(parsed.get("equivalent"), bool):
        raise ValueError("response did not carry a boolean 'equivalent'")
    return bool(parsed["equivalent"])


async def semantically_equivalent(
    session: Any,
    *,
    template: str,
    blank: types.FillBlank,
    value: str,
) -> bool:
    """Does `value` mean the same thing as one of the blank's accepted answers?

    A yes-or-no classification on the candidate's own request path, so it
    runs on the extraction tier with the interactive attempt and deadline
    bounds. The deterministic evaluation is that the answer is a boolean at
    all; the fallback is False, logged, because a value the model could not
    judge is not one the product may mark right on nobody's authority.
    """
    system = registry.render(
        "assessment_fill_blank_equivalence",
        candidate_text_is_data=fragments.CANDIDATE_TEXT_IS_DATA,
    )
    payload = {
        "sentence_with_blanks": template,
        "blank_index": blank.index,
        "accepted_answers": list(blank.accepted),
        "candidate_entry": value,
    }

    async def execute(reflection: str) -> bool:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload)},
        ]
        if reflection:
            messages.append({"role": "user", "content": reflection})
        raw = await llm_router.invoke_llm(
            "fill_blank_equivalence", messages, response_format_json=True, session=session
        )
        return _parse_equivalence(raw)

    result = await agent_loop.run_loop(
        name="fill_blank_equivalence",
        execute=execute,
        evaluate=lambda verdict: agent_loop.ok() if isinstance(verdict, bool) else agent_loop.reject(
            "return JSON with a boolean 'equivalent'"
        ),
        fallback=False,
        max_attempts=agent_loop.INTERACTIVE_ATTEMPTS,
        deadline_seconds=agent_loop.INTERACTIVE_DEADLINE,
    )
    if result.degraded:
        logger.info(
            "assessment_formats.equivalence_unavailable blank=%d attempts=%d reasons=%s",
            blank.index, result.attempts, list(result.reasons),
        )
    return bool(result.value)


EquivalenceCheck = Callable[..., Awaitable[bool]]


async def score_fill_blank(
    session: Any,
    payload: dict[str, Any],
    answer: dict[str, Any],
    *,
    equivalence: EquivalenceCheck | None = None,
) -> FillBlankResult:
    """Per blank, then aggregated as the mean over blanks (section 2.4).

    `equivalence` is resolved at CALL time rather than bound as a default,
    so the fuzzy check is one substitutable seam rather than two: a caller
    that passes one gets it, and everything else gets this module's, whatever
    that is when the call happens.
    """
    check = equivalence or semantically_equivalent
    question = types.FillBlankPayload.model_validate(payload)
    values = types.FillBlankAnswer.model_validate(answer).values
    blanks = sorted(question.blanks, key=lambda item: item.index)
    results: list[str] = []
    for blank in blanks:
        value = values[blank.index] if blank.index < len(values) else ""
        outcome = match_blank(value, blank)
        if outcome == RESULT_INCORRECT and await check(
            session, template=question.template, blank=blank, value=value
        ):
            outcome = RESULT_EQUIVALENT
        results.append(outcome)
    credited = sum(1 for outcome in results if outcome in (RESULT_EXACT, RESULT_EQUIVALENT))
    return FillBlankResult(score=credited / len(results), blank_results=results)


async def score(
    session: Any,
    question_type: str,
    payload: dict[str, Any] | None,
    answer: dict[str, Any],
) -> ObjectiveScore:
    """The one entry point `respond` calls for an objective answer."""
    payload = payload or {}
    if question_type == types.MCQ_SINGLE:
        return ObjectiveScore(auto_score=score_mcq_single(payload, answer))
    if question_type == types.MCQ_MULTI:
        return ObjectiveScore(auto_score=score_mcq_multi(payload, answer))
    if question_type == types.FILL_BLANK:
        result = await score_fill_blank(session, payload, answer)
        return ObjectiveScore(auto_score=result.score, blank_results=result.blank_results)
    raise ValueError(f"{question_type} is not an objective format")
