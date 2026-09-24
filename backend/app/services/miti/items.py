"""Miti's ITEM EVALUATION sub-stage: every per-skill grade in the product.

    G1 (the locked contract)  ->  ITEMS (this module)  ->  five evaluators
        ->  triangulation  ->  aggregation of the SKILL grades  ->  caps  ->  G2..G4

MITI IS THE SOLE GRADING AUTHORITY (Vivekium release, WP5-B)
-------------------------------------------------------------
Until this module existed, a skill's report word came from a rubric scorer in
`functional_assessment` while its contribution to the overall came from the
five evaluators' per-competency bands. Two judges, and nothing made them agree.
This module is now the only code that grades a skill, and the aggregation reads
its output and nothing else.

THE FORMAT DECIDES WHERE A SCORE COMES FROM
--------------------------------------------
    mcq, fill-in-the-blank   `assessment_answers.auto_score`, written at
                             submission, deterministic. No row = unanswered.
    coding                   the coding evaluation (read, not executed).
                             No code = unanswered; evaluation failed = not
                             assessed.
    evidence-based prose     the evaluation with reasoning, against the rubric
                             written with the question; failed = not assessed.
    other prose              the STORED rubric (`candidate_questions.
                             rubric_json`), on the `answer_evaluation` task;
                             a row with no rubric uses the general standard,
                             recorded as such; failed = not assessed.
    Behavioural              one judgement across every substantive answer
                             about the skill, against `BEHAVIOURAL_STANDARD`.
    non-substantive answer   `answer_quality.assess`: unanswered, never a
                             model call.

A MODEL FAILURE IS "NOT ASSESSED", NEVER A SCORE
-------------------------------------------------
The hash fallback that stood in the old item scorer (45 to 94) is DELETED. A
provider outage used to produce a plausible grade: measured over 20,000 seeds,
70% of hashed inputs graded Moderately Matching or better. Now a failed
evaluation is `not_assessed` with the failure's CLASS NAME recorded, the skill
carries no score, and a person is told. `unanswered` is kept strictly apart: it
is a fact about the candidate (asked, gave nothing gradeable), and an
unanswered Must-have is a FAILED Must-have (owner ruling O5-1).

THE MODEL IS INJECTED
---------------------
`evaluate_skills` takes `invoke`, defaulting to `llm_router.invoke_llm`
imported inside the function, so a test drives the scorer offline and reads
the exact messages it was sent (which is how the stored-rubric guarantee is
proven rather than asserted).
"""
from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.services import agent_loop, answer_quality, rating
from app.services.assessment_contract import BUCKET_BEHAVIOURAL, ContractSkill
from app.services.assessment_formats import types as question_types
from app.services.miti.grades import (
    ANSWER_GRADED,
    ANSWER_NOT_ASSESSED,
    ANSWER_UNANSWERED,
    METHOD_BEHAVIOURAL_STANDARD,
    METHOD_CODING,
    METHOD_EVIDENCE,
    METHOD_GENERAL_STANDARD,
    METHOD_OBJECTIVE,
    METHOD_RUBRIC,
    METHOD_UNANSWERED,
    ItemEvaluation,
    SkillGrade,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BEHAVIOURAL_STANDARD",
    "EVALUATION_TASK",
    "FAILURE_EVALUATION_DEGRADED",
    "FAILURE_NO_QUESTION",
    "ItemContext",
    "SCORING_PROMPT",
    "UNANSWERED_SCORE",
    "evaluate_skill",
    "evaluate_skills",
    "parse_score",
]

#: The score for an item the candidate was asked and did not answer. Grades
#: Not Matching on `services/rating`'s scale, which is the point: an
#: unanswered Must-have is a failed one (O5-1). INTERNAL, never displayed.
UNANSWERED_SCORE = 25

#: The one task type every model-backed item judgement runs under. Terra,
#: temperature 0.0, in `config/llm_providers.py`. Named here so no caller can
#: route a grading call anywhere else. (`behavioral_assessment`, which the
#: deleted rubric scorer used, keeps only its question-writing caller.)
EVALUATION_TASK = "answer_evaluation"

#: The registry prompt the rubric and behavioural judgements render.
SCORING_PROMPT = "assessment_answer_scoring"

#: `failure` words for the two non-exception reasons an item is not assessed.
FAILURE_EVALUATION_DEGRADED = "evaluation_degraded"
FAILURE_NO_QUESTION = "no_question_issued"

#: The judgement standard for a Behavioural skill. It is NOT a per-question
#: rubric and must not be mistaken for one: it describes what a credible
#: account of a behaviour looks like in general, because there is no single
#: correct answer to a behavioural question. Every Behavioural skill in the
#: product is judged against this same standard, which is what makes two
#: candidates' behavioural grades comparable.
BEHAVIOURAL_STANDARD: dict[str, str] = {
    "0_39": "No relevant situation described, or the account contradicts the competency.",
    "40_59": "A thin or generic account with little personal action or outcome.",
    "60_74": "A credible situation with clear personal action and a stated outcome.",
    "75_89": "Several strong situations with judgement, trade-offs, and measurable results.",
    "90_100": "Consistently exceptional accounts showing judgement, impact, and transferable insight.",
}

#: A skill whose answers are graded against the rubric written for each
#: question. Behavioural is judged against the standard above instead.
_RUBRIC_SCORED_BUCKETS = frozenset({"must_have", "nice_to_have"})

Invoke = Callable[..., Awaitable[str]]


@dataclass(frozen=True)
class ItemContext:
    """The identifiers an item evaluation files evidence under. No session."""

    tenant_id: uuid.UUID
    job_id: uuid.UUID
    link_id: uuid.UUID
    candidate_id: uuid.UUID


# ── The model-backed judgement ───────────────────────────────────────────────


def _rubric_text(rubric: Mapping[str, Any]) -> str:
    return "; ".join(
        f"{str(band).replace('_', '-')}: {text}" for band, text in rubric.items()
    )


def parse_score(raw: str) -> int | None:
    """The judge's score, or None when the response is not usable.

    A JSON object with a numeric `score` in 0..100. Anything else is None, and
    the loop's critic turns None into an instruction for the next attempt:
    nothing here substitutes a value.
    """
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, Mapping):
        return None
    value = payload.get("score")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    score = int(round(float(value)))
    if score < 0 or score > 100:
        return None
    return score


def _critic(value: int | None) -> agent_loop.Critique:
    if value is None:
        return agent_loop.reject(
            'return one JSON object {"score": <integer 0-100>, "band": "<band key>"} '
            "with the score inside the band you chose"
        )
    return agent_loop.ok()


async def _rubric_score(
    session: Any,
    *,
    framing: str,
    rubric: Mapping[str, Any],
    answer: str,
    invoke: Invoke,
) -> tuple[int | None, str | None]:
    """One judgement against `rubric`. Returns (score, failure).

    Runs inside `agent_loop.run_loop` with a deterministic critic and
    `fallback=None`, so a response that is not a usable score is fed back as
    an instruction and, after the background attempts, becomes `None` with the
    failure recorded. There is no default score on any path.
    """
    from app.prompts import fragments, registry

    system = registry.render(
        SCORING_PROMPT,
        rubric_bands=_rubric_text(rubric),
        candidate_text_is_data=fragments.CANDIDATE_TEXT_IS_DATA,
    )
    base = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps({"question": framing, "answer": answer})},
    ]

    async def execute(reflection: str) -> int | None:
        messages = list(base)
        if reflection:
            messages.append({"role": "user", "content": reflection})
        raw = await invoke(
            EVALUATION_TASK, messages, response_format_json=True, session=session
        )
        return parse_score(raw)

    result = await agent_loop.run_loop(
        name="miti_item_evaluation",
        execute=execute,
        evaluate=_critic,
        fallback=None,
        max_attempts=agent_loop.BACKGROUND_ATTEMPTS,
        deadline_seconds=agent_loop.BACKGROUND_DEADLINE,
        max_generated_tokens=agent_loop.BACKGROUND_TOKEN_BUDGET,
    )
    if result.degraded or result.value is None:
        return None, result.error or FAILURE_EVALUATION_DEGRADED
    return int(result.value), None


async def _format_evaluation(
    session: Any,
    *,
    skill: ContractSkill,
    question: Any,
    answer: str,
    record: Any,
) -> tuple[int | None, str | None]:
    """The evaluation with reasoning for an evidence or coding answer.

    The reasoning is persisted on the answer row, because it is what the
    recruiter's Q&A view shows; a degraded evaluation stores nothing, since a
    record with no reasoning would read as an evaluation that found nothing to
    say. Returns (score, failure).
    """
    from app.services.assessment_formats import evaluation as format_evaluation

    question_type = _question_type(question)
    submitted = dict(getattr(record, "answer_json", None) or {})
    result = await format_evaluation.evaluate(
        session,
        question_type=question_type,
        prompt=question.prompt,
        answer_text=answer,
        item_name=skill.name,
        resume_anchor=getattr(question, "resume_anchor", None),
        question_rubric=(
            question.rubric_json
            if question_type == question_types.EVIDENCE_BASED
            else None
        ),
        payload=dict(getattr(question, "payload_json", None) or {}),
        language=submitted.get("language"),
    )
    if result.degraded or result.value is None:
        return None, result.error or FAILURE_EVALUATION_DEGRADED
    if record is not None:
        record.ai_evaluation_json = result.value
    return int(result.value["score"]), None


# ── Helpers ──────────────────────────────────────────────────────────────────


def _question_type(question: Any) -> str:
    return str(getattr(question, "question_type", None) or question_types.SHORT_ANSWER)


def _question_weight(question: Any) -> float:
    """The stored weight; a row written before weights existed counts fully."""
    return float(getattr(question, "weight", None) or 1.0)


def _weighted_mean(items: Sequence[ItemEvaluation]) -> int:
    """The weighted mean of the SCORED items (graded and unanswered)."""
    scored = [item for item in items if item.score is not None]
    total = sum(max(0.0, item.weight) for item in scored)
    if total <= 0:
        return int(round(sum(item.score for item in scored) / len(scored)))  # type: ignore[misc]
    return int(
        round(
            sum(float(item.score) * max(0.0, item.weight) for item in scored)  # type: ignore[arg-type]
            / total
        )
    )


def _log_not_assessed(
    context: ItemContext, skill: ContractSkill, question_id: Any, failure: str
) -> None:
    logger.warning(
        "miti.item_not_assessed link_id=%s skill_id=%s question_id=%s reason=%s",
        context.link_id, skill.id, question_id, failure,
    )


async def _record_evidence(
    session: Any,
    context: ItemContext,
    skill: ContractSkill,
    question: Any,
    records: Sequence[Any],
) -> None:
    """File the answers through THE ledger writer before the grade is asked for.

    Evidence is what was read; a grade is what was concluded from it, so the
    trail is written whether or not the grade succeeds. The writer is
    idempotent, so an answer already filed during the conversation is a no-op.
    """
    from app.services.assessment_pipeline import evidence as answer_evidence

    await answer_evidence.backfill_answer_evidence(
        session,
        tenant_id=context.tenant_id,
        job_id=context.job_id,
        link_id=context.link_id,
        candidate_id=context.candidate_id,
        skill_id=skill.id,
        skill_name=skill.name,
        skill_bucket=skill.bucket,
        question_id=getattr(question, "id", None),
        records=records,
    )


def _grade_skill(
    skill: ContractSkill,
    items: Sequence[ItemEvaluation],
    used: Sequence[str],
) -> SkillGrade:
    """Fold a skill's items into ONE grade. Never invents a score.

    * no item at all, or every SUBSTANTIVE item failed evaluation: not assessed;
    * nothing substantive was given: unanswered, `UNANSWERED_SCORE`;
    * otherwise graded on the scored items, flagged partially assessed when
      some substantive item could not be evaluated.
    """
    failed = [item for item in items if item.status == ANSWER_NOT_ASSESSED]
    graded = [item for item in items if item.status == ANSWER_GRADED]
    if not items or (failed and not graded):
        return SkillGrade(
            skill_id=skill.id,
            name=skill.name,
            bucket=skill.bucket,
            priority=skill.priority,
            status=ANSWER_NOT_ASSESSED,
            score=None,
            grade=None,
            items=tuple(items),
            used_answers=tuple(used),
        )
    if not graded:
        return SkillGrade(
            skill_id=skill.id,
            name=skill.name,
            bucket=skill.bucket,
            priority=skill.priority,
            status=ANSWER_UNANSWERED,
            score=UNANSWERED_SCORE,
            grade=rating.grade_for_percent(UNANSWERED_SCORE) or rating.GRADE_NOT,
            items=tuple(items),
            used_answers=(),
        )
    score = _weighted_mean(items)
    return SkillGrade(
        skill_id=skill.id,
        name=skill.name,
        bucket=skill.bucket,
        priority=skill.priority,
        status=ANSWER_GRADED,
        score=score,
        grade=rating.grade_for_percent(score) or rating.GRADE_NOT,
        partially_assessed=bool(failed),
        items=tuple(items),
        used_answers=tuple(used),
    )


# ── The two methods ──────────────────────────────────────────────────────────


async def _rubric_scored(
    session: Any,
    *,
    context: ItemContext,
    skill: ContractSkill,
    questions: Sequence[Any],
    answers: Mapping[str, Sequence[str]],
    locators: Mapping[str, Sequence[Any]],
    structured: Mapping[str, Any],
    invoke: Invoke,
) -> SkillGrade:
    """Must-have and Nice-to-have: each answer against ITS OWN question's rubric."""
    from app.services.assessment_formats import rendering as format_rendering

    items: list[ItemEvaluation] = []
    used: list[str] = []
    for question in questions:
        key = str(question.id)
        answer = " ".join(answers.get(key, []))
        question_type = _question_type(question)
        weight = _question_weight(question)
        record = structured.get(key)

        if question_type in question_types.OBJECTIVE_TYPES:
            if record is None or record.auto_score is None:
                items.append(ItemEvaluation(question.id, ANSWER_UNANSWERED, UNANSWERED_SCORE, weight, METHOD_UNANSWERED))
                continue
            used.append(
                answer
                or format_rendering.transcript_line(
                    question_type,
                    dict(getattr(question, "payload_json", None) or {}),
                    dict(record.answer_json or {}),
                )
            )
            items.append(
                ItemEvaluation(
                    question.id, ANSWER_GRADED, int(round(float(record.auto_score) * 100)), weight, METHOD_OBJECTIVE
                )
            )
            continue

        if question_type == question_types.CODING:
            code = str((record.answer_json or {}).get("code") or "") if record is not None else ""
            if not code.strip():
                items.append(ItemEvaluation(question.id, ANSWER_UNANSWERED, UNANSWERED_SCORE, weight, METHOD_UNANSWERED))
                continue
            used.append(answer or code)
            await _record_evidence(session, context, skill, question, locators.get(key, ()))
            score, failure = await _format_evaluation(
                session, skill=skill, question=question, answer=answer or code, record=record
            )
            if score is None:
                _log_not_assessed(context, skill, question.id, failure or FAILURE_EVALUATION_DEGRADED)
                items.append(ItemEvaluation(question.id, ANSWER_NOT_ASSESSED, None, weight, METHOD_CODING, failure))
            else:
                items.append(ItemEvaluation(question.id, ANSWER_GRADED, score, weight, METHOD_CODING))
            continue

        # Prose. A non-answer never reaches a scoring prompt: empty, gibberish
        # and single-token answers are settled deterministically, which is the
        # only reason a model outage cannot turn keyboard mash into a grade.
        verdict = answer_quality.assess(answer)
        if not verdict.substantive:
            if answer:
                logger.info(
                    "miti.insufficient_answer link_id=%s question_id=%s reason=%s",
                    context.link_id, question.id, verdict.reason,
                )
            items.append(ItemEvaluation(question.id, ANSWER_UNANSWERED, UNANSWERED_SCORE, weight, METHOD_UNANSWERED))
            continue
        used.append(answer)
        await _record_evidence(session, context, skill, question, locators.get(key, ()))
        if question_type == question_types.EVIDENCE_BASED:
            score, failure = await _format_evaluation(
                session, skill=skill, question=question, answer=answer, record=record
            )
            method = METHOD_EVIDENCE
        else:
            rubric = question.rubric_json
            method = METHOD_RUBRIC
            if not rubric:
                # A rubric-scored row with no stored rubric: the general
                # standard the question writer itself falls back to, and the
                # method says so rather than presenting it as the row's rubric.
                from app.services.ppi_interview import DEFAULT_RUBRIC

                rubric = DEFAULT_RUBRIC
                method = METHOD_GENERAL_STANDARD
            score, failure = await _rubric_score(
                session, framing=question.prompt, rubric=rubric, answer=answer, invoke=invoke
            )
        if score is None:
            _log_not_assessed(context, skill, question.id, failure or FAILURE_EVALUATION_DEGRADED)
            items.append(ItemEvaluation(question.id, ANSWER_NOT_ASSESSED, None, weight, method, failure))
        else:
            items.append(ItemEvaluation(question.id, ANSWER_GRADED, score, weight, method))
    return _grade_skill(skill, items, used)


async def _behavioural(
    session: Any,
    *,
    context: ItemContext,
    skill: ContractSkill,
    questions: Sequence[Any],
    answers: Mapping[str, Sequence[str]],
    locators: Mapping[str, Sequence[Any]],
    invoke: Invoke,
) -> SkillGrade:
    """Behavioural: ONE judgement across everything said about the skill.

    Splitting it per question would be worse, not better: a competency is
    demonstrated across a conversation, and three judgements of three fragments
    average out exactly the pattern the skill exists to see.
    """
    collected: list[str] = []
    for question in questions:
        key = str(question.id)
        answered = [
            answer for answer in answers.get(key, []) if answer_quality.is_substantive(answer)
        ]
        if not answered:
            continue
        collected.extend(answered)
        await _record_evidence(session, context, skill, question, locators.get(key, ()))
    if not collected:
        return _grade_skill(
            skill,
            [ItemEvaluation(None, ANSWER_UNANSWERED, UNANSWERED_SCORE, 1.0, METHOD_UNANSWERED)],
            (),
        )
    framing = (
        f"Behavioural skill '{skill.name}'. "
        f"The candidate answered {len(collected)} question(s) probing it."
    )
    score, failure = await _rubric_score(
        session,
        framing=framing,
        rubric=BEHAVIOURAL_STANDARD,
        answer="\n".join(f"- {item}" for item in collected),
        invoke=invoke,
    )
    if score is None:
        _log_not_assessed(context, skill, None, failure or FAILURE_EVALUATION_DEGRADED)
        item = ItemEvaluation(None, ANSWER_NOT_ASSESSED, None, 1.0, METHOD_BEHAVIOURAL_STANDARD, failure)
    else:
        item = ItemEvaluation(None, ANSWER_GRADED, score, 1.0, METHOD_BEHAVIOURAL_STANDARD)
    return _grade_skill(skill, [item], collected)


# ── The entry points ─────────────────────────────────────────────────────────


async def evaluate_skill(
    session: Any,
    *,
    context: ItemContext,
    skill: ContractSkill,
    questions: Sequence[Any],
    answers: Mapping[str, Sequence[str]],
    locators: Mapping[str, Sequence[Any]],
    structured: Mapping[str, Any],
    invoke: Invoke | None = None,
) -> SkillGrade:
    """Grade ONE contract skill from the questions the candidate was asked on it.

    `questions` are this candidate's `candidate_questions` rows for the skill,
    each carrying the prompt and the rubric written with it. A skill with no
    question issued is NOT ASSESSED rather than unanswered: the candidate was
    never asked, so nothing about them has been learned, and grading it Not
    Matching would fail an essential skill over a platform defect.
    """
    if invoke is None:
        from app.services import llm_router

        invoke = llm_router.invoke_llm
    if not questions:
        _log_not_assessed(context, skill, None, FAILURE_NO_QUESTION)
        return _grade_skill(
            skill,
            [ItemEvaluation(None, ANSWER_NOT_ASSESSED, None, 1.0, METHOD_UNANSWERED, FAILURE_NO_QUESTION)],
            (),
        )
    if skill.bucket == BUCKET_BEHAVIOURAL:
        return await _behavioural(
            session,
            context=context,
            skill=skill,
            questions=questions,
            answers=answers,
            locators=locators,
            invoke=invoke,
        )
    if skill.bucket not in _RUBRIC_SCORED_BUCKETS:
        raise ValueError(f"unknown skill bucket {skill.bucket!r}")
    return await _rubric_scored(
        session,
        context=context,
        skill=skill,
        questions=questions,
        answers=answers,
        locators=locators,
        structured=structured,
        invoke=invoke,
    )


async def evaluate_skills(
    session: Any,
    *,
    context: ItemContext,
    skills: Sequence[ContractSkill],
    questions: Sequence[Any],
    answers: Mapping[str, Sequence[str]],
    locators: Mapping[str, Sequence[Any]],
    structured: Mapping[str, Any],
    invoke: Invoke | None = None,
) -> tuple[SkillGrade, ...]:
    """Every skill in the contract, in contract order. Sequential on purpose.

    One `AsyncSession` is not safe to share across concurrent tasks, and every
    skill writes ledger rows through it. The model calls are the cost; running
    them one skill at a time bounds the concurrent load a single scoring run
    puts on the provider as well.
    """
    by_skill: dict[uuid.UUID, list[Any]] = {}
    for question in questions:
        skill_id = getattr(question, "competency_id", None)
        if skill_id is None:
            continue
        by_skill.setdefault(skill_id, []).append(question)
    grades: list[SkillGrade] = []
    for skill in skills:
        grades.append(
            await evaluate_skill(
                session,
                context=context,
                skill=skill,
                questions=by_skill.get(skill.id, []),
                answers=answers,
                locators=locators,
                structured=structured,
                invoke=invoke,
            )
        )
    return tuple(grades)
