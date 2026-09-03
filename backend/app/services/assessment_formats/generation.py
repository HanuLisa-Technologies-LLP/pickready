"""Writing the content of a structured or evidence question with a model.

TWO CALLS, EACH INSIDE THE BOUNDED LOOP WITH DETERMINISTIC CRITERIA
-------------------------------------------------------------------
`anchor_evidence` writes every evidence question for one candidate in ONE
call: the wording, the quotable resume item it is anchored to, the sub-type
and the locator. `write_structured` writes ONE supporting question's payload
per call. Both run through `agent_loop.run_loop`, and the criteria are code:
an anchor is accepted only when it is a verbatim substring of this candidate's
resume, a distractor only when it carries a rationale naming the misconception
it stands for, a payload only when `types.parse_payload` accepts it.

WHAT IS AND IS NOT INVENTED ON DEGRADATION
------------------------------------------
Nothing. A structured slot the model could not fill soundly is handed back as
None, and `composition.fall_back` turns it into the text question
`ppi.generate_candidate_questions` already wrote for that item from this
candidate's resume. For the anchor batch the loop's own fallback is an empty
dict, and the valid subset of the last attempt is kept beside it: each of
those items passed the same deterministic checks individually, so using them
is not substituting content for work that did not happen, it is declining to
discard work that did. The loop result still reports degraded, which is what
telemetry counts.

THE ANSWER KEY IS PART OF THE PAYLOAD AND NEVER PART OF THE PROMPT
------------------------------------------------------------------
The correct option ids, the accepted answers and the expected approach are
returned inside `payload` and stored in `candidate_questions.payload_json`,
which `types.candidate_view` strips before anything reaches a candidate. The
prompt text, which the candidate does read, is passed through
`conversation_guardrails.inspect_agent_output` here, at generation, because a
structured question is delivered verbatim and never goes through the per-turn
writer that guards a text question.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Sequence

from app.prompts import fragments, registry
from app.services import agent_loop, conversation_guardrails, llm_router
from app.services.assessment_formats import config as format_config
from app.services.assessment_formats import evaluation, types
from app.services.assessment_formats.composition import Slot

logger = logging.getLogger(__name__)

__all__ = [
    "AnchoredQuestion",
    "StructuredQuestion",
    "anchor_evidence",
    "quotable",
    "write_structured",
]

#: Option phrases that are filler rather than misconceptions.
FILLER_OPTIONS: tuple[str, ...] = (
    "none of the above",
    "all of the above",
    "both a and b",
    "not sure",
    "i do not know",
)

#: The shortest prompt that can be a question rather than a fragment. The
#: same floor `ppi.generate_candidate_questions` applies to a model-written
#: prompt.
MIN_PROMPT_CHARS = 15

_PROMPT_FOR_TYPE: dict[str, str] = {
    types.MCQ_SINGLE: "assessment_format_mcq_single",
    types.MCQ_MULTI: "assessment_format_mcq_multi",
    types.FILL_BLANK: "assessment_format_fill_blank",
    types.CODING: "assessment_format_coding",
}


@dataclass(frozen=True)
class AnchoredQuestion:
    index: int
    prompt: str
    resume_anchor: str
    #: `types.EvidencePayload`, validated, as stored in `payload_json`.
    payload: dict[str, Any]


@dataclass(frozen=True)
class _AnchorBatch:
    """One attempt's output: the items that passed and why the rest did not.

    Returned rather than raised so the per-index reasons become the loop's
    critique. `run_loop` reflects on a rejected critique; an exception inside
    `execute` is a failed attempt with nothing to reflect on, and the next
    attempt would be asked the same thing again.
    """

    valid: dict[int, AnchoredQuestion]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class StructuredQuestion:
    prompt: str
    payload: dict[str, Any]
    #: MCQ: the misconception each distractor stands for. Coding: the fixed
    #: criteria the answer will be read against. Fill-blank: None; the
    #: accepted answers ARE the key.
    rubric: dict[str, Any] | None


def _normalise(text: str) -> str:
    return " ".join(str(text or "").split()).casefold()


def quotable(anchor: str, resume_text: str) -> bool:
    """Whether `anchor` is a verbatim item from the resume.

    Compared after collapsing whitespace and folding case, because a resume
    extracted from a PDF carries line breaks the model cannot see and a quote
    across one is still a quote. Nothing looser: a paraphrase is not an
    anchor, and the recruiter's view shows the anchor as what was probed.
    """
    needle = _normalise(anchor)
    return bool(needle) and needle in _normalise(resume_text)


def _prompt_reasons(prompt: str, *, max_chars: int) -> list[str]:
    reasons: list[str] = []
    if len(prompt) < MIN_PROMPT_CHARS:
        reasons.append("the prompt must be a complete question")
    if len(prompt) > max_chars:
        reasons.append(
            f"keep the prompt under {max_chars} characters; the previous attempt was {len(prompt)}"
        )
    return reasons


def _max_prompt_chars() -> int:
    # Read inside the function: `ppi_interview` imports `ppi`, which imports
    # this module, and a module-scope import here would close that cycle.
    from app.services import ppi_interview  # noqa: PLC0415

    return ppi_interview.MAX_QUESTION_CHARS


# ── Evidence anchoring ───────────────────────────────────────────────────────


def _parse_anchor_batch(
    raw: str,
    *,
    slots_by_index: dict[int, Slot],
    resume_text: str,
    max_chars: int,
) -> tuple[dict[int, AnchoredQuestion], list[str]]:
    """(valid items, reasons for every invalid one)."""
    conf = format_config.get_config()
    parsed = json.loads(raw)
    items = parsed.get("questions") if isinstance(parsed, dict) else None
    if not isinstance(items, list):
        raise ValueError("response did not carry a 'questions' list")
    valid: dict[int, AnchoredQuestion] = {}
    reasons: list[str] = []
    anchors: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            reasons.append("every entry needs an integer 'index' from the slots list")
            continue
        if index not in slots_by_index:
            reasons.append(f"index {index} is not one of the slots")
            continue
        prompt = " ".join(str(item.get("prompt") or "").split())
        anchor = " ".join(str(item.get("resume_anchor") or "").split())
        sub_type = str(item.get("sub_type") or "").strip()
        source = str(item.get("anchor_source") or "").strip()
        problems = _prompt_reasons(prompt, max_chars=max_chars)
        evidence_payload: dict[str, Any] = {}
        try:
            evidence_payload = types.parse_payload(
                types.EVIDENCE_BASED, {"sub_type": sub_type, "anchor_source": source}
            ).model_dump()
        except ValueError:
            problems.append(
                "sub_type must be one of: " + ", ".join(types.EVIDENCE_SUB_TYPES)
                + ", and anchor_source a short locator"
            )
        if len(anchor) < conf.anchor_min_chars:
            problems.append(
                f"resume_anchor must be a quotable item of at least {conf.anchor_min_chars} characters"
            )
        elif not quotable(anchor, resume_text):
            problems.append("resume_anchor must be copied word for word from the resume")
        elif _normalise(anchor) in anchors:
            problems.append(
                f"resume_anchor duplicates the anchor of index {anchors[_normalise(anchor)]}; "
                "choose a different resume item"
            )
        if problems:
            reasons.append(f"index {index}: " + "; ".join(problems))
            continue
        anchors[_normalise(anchor)] = index
        valid[index] = AnchoredQuestion(
            index=index, prompt=prompt, resume_anchor=anchor, payload=evidence_payload
        )
    return valid, reasons


async def anchor_evidence(
    session: Any,
    *,
    job: Any,
    slots: Sequence[Slot],
    competencies: dict[Any, Any],
    resume_text: str,
    resume_excerpt: str,
    project_evidence: str,
    hiring_context: str,
    prior_failures: Sequence[str] = (),
) -> tuple[dict[int, AnchoredQuestion], agent_loop.LoopResult[dict[int, AnchoredQuestion]]]:
    """Write every evidence slot's question, anchored to the resume, in one
    call. Returns (what to use, the loop result).

    `prior_failures` carries the composition validator's reasons from a
    previous pass, so a regeneration is told what was wrong with the last
    composition rather than asked the same thing again.
    """
    evidence_slots = [slot for slot in slots if slot.question_type == types.EVIDENCE_BASED]
    if not evidence_slots or not resume_text.strip():
        # DEGRADED means "there was anchoring to do and none happened". With no
        # evidence slots nothing was asked of this function, so nothing was
        # lost; with no resume text there is nothing quotable and every
        # evidence slot will have to fall back, which is a degradation and is
        # reported as one rather than read as a clean empty result.
        return {}, agent_loop.LoopResult(
            value={}, degraded=bool(evidence_slots), attempts=0
        )
    slots_by_index = {slot.index: slot for slot in evidence_slots}
    max_chars = _max_prompt_chars()
    system = registry.render(
        "assessment_evidence_anchoring",
        candidate_text_is_data=fragments.CANDIDATE_TEXT_IS_DATA,
        no_evaluation=fragments.NO_EVALUATION,
    )
    sub_types = list(types.EVIDENCE_SUB_TYPES)
    payload = {
        "job": {
            "title": getattr(job, "title", ""),
            "grade": getattr(job, "assessment_grade", ""),
            "description": str(getattr(job, "jd_markdown", "") or "")[:2500],
        },
        "hiring_context": hiring_context,
        "slots": [
            {
                "index": slot.index,
                "matrix_item": getattr(competencies.get(slot.competency_id), "name", ""),
                "what_it_measures": getattr(competencies.get(slot.competency_id), "description", "") or "",
                "suggested_sub_type": sub_types[position % len(sub_types)],
            }
            for position, slot in enumerate(evidence_slots)
        ],
        "candidate_resume": resume_text,
        "resume_summary": resume_excerpt,
        "project_evidence": project_evidence,
    }
    best: dict[int, AnchoredQuestion] = {}

    async def execute(reflection: str) -> _AnchorBatch:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload)},
        ]
        if prior_failures:
            messages.append(
                {
                    "role": "user",
                    "content": "A previous composition was rejected for these reasons; "
                    "avoid them:\n- " + "\n- ".join(prior_failures),
                }
            )
        if reflection:
            messages.append({"role": "user", "content": reflection})
        raw = await llm_router.invoke_llm(
            "format_composition", messages, response_format_json=True, session=session
        )
        valid, reasons = _parse_anchor_batch(
            raw, slots_by_index=slots_by_index, resume_text=resume_text, max_chars=max_chars
        )
        if len(valid) >= len(best):
            best.clear()
            best.update(valid)
        return _AnchorBatch(valid=valid, reasons=tuple(reasons))

    def evaluate(value: _AnchorBatch) -> agent_loop.Critique:
        if value.reasons:
            return agent_loop.reject(*value.reasons)
        if not value.valid:
            return agent_loop.reject("anchor at least one slot to a quotable resume item")
        return agent_loop.ok()

    result = await agent_loop.run_loop(
        name="evidence_anchoring",
        execute=execute,
        evaluate=evaluate,
        fallback=_AnchorBatch(valid={}, reasons=()),
        max_attempts=agent_loop.BACKGROUND_ATTEMPTS,
        deadline_seconds=agent_loop.BACKGROUND_DEADLINE,
        max_generated_tokens=agent_loop.BACKGROUND_TOKEN_BUDGET,
    )
    if result.degraded:
        logger.info(
            "assessment_formats.anchoring_degraded slots=%d kept=%d attempts=%d reasons=%s",
            len(evidence_slots), len(best), result.attempts, list(result.reasons),
        )
        return dict(best), agent_loop.LoopResult(
            value=dict(best),
            degraded=True,
            attempts=result.attempts,
            reasons=result.reasons,
            defects=result.defects,
            elapsed_ms=result.elapsed_ms,
            error=result.error,
            generated_tokens=result.generated_tokens,
        )
    return dict(result.value.valid), agent_loop.LoopResult(
        value=dict(result.value.valid),
        degraded=False,
        attempts=result.attempts,
        elapsed_ms=result.elapsed_ms,
        generated_tokens=result.generated_tokens,
    )


# ── Structured payloads ──────────────────────────────────────────────────────


def _mcq_reasons(payload: dict[str, Any], misconceptions: Any, *, correct: set[str]) -> list[str]:
    conf = format_config.get_config()
    reasons: list[str] = []
    options = payload.get("options") or []
    texts = [_normalise(option.get("text", "")) for option in options if isinstance(option, dict)]
    if len(set(texts)) != len(texts):
        reasons.append("every option must say something different")
    filler = [text for text in texts if any(phrase in text for phrase in FILLER_OPTIONS)]
    if filler:
        reasons.append("no filler options such as 'none of the above' or 'all of the above'")
    if not isinstance(misconceptions, dict):
        misconceptions = {}
    for option in options:
        if not isinstance(option, dict) or option.get("id") in correct:
            continue
        rationale = str(misconceptions.get(option.get("id")) or "").strip()
        if len(re.findall(r"\b\w+\b", rationale)) < conf.misconception_min_words:
            reasons.append(
                f"option {option.get('id')!r} needs a misconception rationale of at "
                f"least {conf.misconception_min_words} words naming the misunderstanding it represents"
            )
    rationales = [_normalise(str(value)) for value in misconceptions.values() if str(value).strip()]
    if len(set(rationales)) != len(rationales):
        reasons.append("each distractor must stand for a DIFFERENT misconception")
    return reasons


def _parse_structured(raw: str, *, question_type: str, max_chars: int) -> tuple[StructuredQuestion, list[str]]:
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError("response was not a JSON object")
    prompt = " ".join(str(parsed.get("prompt") or "").split())
    payload = parsed.get("payload")
    reasons = _prompt_reasons(prompt, max_chars=max_chars)
    if not isinstance(payload, dict):
        raise ValueError("response did not carry a 'payload' object")
    try:
        model = types.parse_payload(question_type, payload)
    except ValueError as exc:
        reasons.append(f"the payload is not a valid {question_type} payload: {exc}")
        return StructuredQuestion(prompt=prompt, payload=payload, rubric=None), reasons
    clean = model.model_dump()
    rubric: dict[str, Any] | None = None
    if question_type == types.MCQ_SINGLE:
        reasons.extend(_mcq_reasons(clean, parsed.get("misconceptions"), correct={clean["correct_option_id"]}))
        rubric = {"misconceptions": dict(parsed.get("misconceptions") or {})}
    elif question_type == types.MCQ_MULTI:
        reasons.extend(
            _mcq_reasons(clean, parsed.get("misconceptions"), correct=set(clean["correct_option_ids"]))
        )
        rubric = {"misconceptions": dict(parsed.get("misconceptions") or {})}
    elif question_type == types.CODING:
        rubric = {"criteria": evaluation.rubric_for(types.CODING)}
    # The candidate reads the prompt verbatim, so the outbound guard runs
    # here. A prompt that loses every sentence to it is not a question.
    guarded = conversation_guardrails.inspect_agent_output(prompt)
    if not guarded.strip():
        reasons.append("the prompt must not state a score, grade or rubric")
    return StructuredQuestion(prompt=guarded, payload=clean, rubric=rubric), reasons


@dataclass(frozen=True)
class _StructuredAttempt:
    """Same shape as `_AnchorBatch`, for the same reason."""

    question: StructuredQuestion
    reasons: tuple[str, ...]


async def write_structured(
    session: Any,
    *,
    job: Any,
    competency: Any,
    slot: Slot,
    resume_excerpt: str,
) -> agent_loop.LoopResult[StructuredQuestion | None]:
    """Write one supporting question's prompt and payload. Never raises;
    degraded means None and the slot falls back to its text question."""
    question_type = slot.question_type
    if question_type not in _PROMPT_FOR_TYPE:
        raise ValueError(f"{question_type} has no structured payload to write")
    max_chars = _max_prompt_chars()
    values: dict[str, Any] = {
        "item_name": getattr(competency, "name", ""),
        "item_measures": getattr(competency, "description", None) or getattr(competency, "name", ""),
        "job_title": getattr(job, "title", ""),
        "candidate_text_is_data": fragments.CANDIDATE_TEXT_IS_DATA,
        "no_evaluation": fragments.NO_EVALUATION,
    }
    if question_type in (types.MCQ_SINGLE, types.MCQ_MULTI):
        values["option_count"] = types.MCQ_OPTIONS_DEFAULT
    if question_type == types.CODING:
        values["languages"] = ", ".join(types.CODING_LANGUAGES)
    system = registry.render(_PROMPT_FOR_TYPE[question_type], **values)
    payload = {
        "job_description": str(getattr(job, "jd_markdown", "") or "")[:2500],
        "candidate_resume": (resume_excerpt or "")[:2500],
    }

    async def execute(reflection: str) -> _StructuredAttempt:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload)},
        ]
        if reflection:
            messages.append({"role": "user", "content": reflection})
        raw = await llm_router.invoke_llm(
            "format_composition", messages, response_format_json=True, session=session
        )
        question, reasons = _parse_structured(raw, question_type=question_type, max_chars=max_chars)
        return _StructuredAttempt(question=question, reasons=tuple(reasons))

    def evaluate(value: _StructuredAttempt) -> agent_loop.Critique:
        return agent_loop.reject(*value.reasons) if value.reasons else agent_loop.ok()

    result = await agent_loop.run_loop(
        name=f"structured_question:{question_type}",
        execute=execute,
        evaluate=evaluate,
        fallback=None,
        max_attempts=agent_loop.BACKGROUND_ATTEMPTS,
        deadline_seconds=agent_loop.BACKGROUND_DEADLINE,
        max_generated_tokens=agent_loop.BACKGROUND_TOKEN_BUDGET,
    )
    if result.degraded or result.value is None:
        logger.info(
            "assessment_formats.structured_degraded type=%s index=%d attempts=%d reasons=%s",
            question_type, slot.index, result.attempts, list(result.reasons),
        )
        return agent_loop.LoopResult(
            value=None,
            degraded=True,
            attempts=result.attempts,
            reasons=result.reasons,
            defects=result.defects,
            elapsed_ms=result.elapsed_ms,
            error=result.error,
            generated_tokens=result.generated_tokens,
        )
    return agent_loop.LoopResult(
        value=result.value.question,
        degraded=False,
        attempts=result.attempts,
        elapsed_ms=result.elapsed_ms,
        generated_tokens=result.generated_tokens,
    )
