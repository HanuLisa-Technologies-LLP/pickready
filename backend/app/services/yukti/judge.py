"""The ONE model call Yukti makes: a batch of resumes read against one job.

Task type `yukti_matching` (Terra, the judging tier; temperature 0.0, the
judge policy). One call per batch of `BATCH_SIZE` candidates, with ONE
corrective retry that names the exact defects, the house pattern
`swot_analysis.draft` and the old matching batch already follow.

WHAT THE MODEL IS GIVEN (`build_messages`)
------------------------------------------
The system prompt `yukti_matching_system` (static rules, no job data), and a
user message that is one JSON document: the role, the Must-have and
Nice-to-have skills with their hidden evidence lines, the saved SWOT needs,
and the prepared resumes. Everything is referenced by OPAQUE refs (`s1`, `n1`,
`c1`): no database id, no candidate name, no employer. What is NEVER in it:
compensation of any kind, any other candidate's history or any past hire, the
parsed-field dictionary, the application answers. `tests/test_ctc_never_in_prompt`
captures this exact payload at the router boundary and asserts it.

WHAT THE MODEL MAY RETURN (`parse_response`)
--------------------------------------------
Exactly the shape the prompt states, validated strictly: an unknown key is a
defect, a missing skill or need is a defect, a verdict outside the three words
is a defect. A defect sends the candidate to the corrective retry; a candidate
still defective after it is `model_output_invalid`. The grounding of quotes
and the hygiene of tags are NOT checked here: they are `grounding.py`'s job,
and a well-formed reading with a bad quote is a grounding outcome, recorded,
not a retry.

WHAT A FAILURE BECOMES
----------------------
A `JudgeFailure` with a reason, which scoring turns into `not_assessed`. There
is NO deterministic substitute reading and no default verdict: a model
failure is "Not assessed" (owner acceptance criterion 6), and a candidate the
model could not read is never silently given a grade.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.prompts import fragments, registry
from app.services import llm_router
from app.services.yukti import config
from app.services.yukti.grounding import RawItem, RawJudgement, RawNeed
from app.services.yukti.inputs import CandidateInput, JobContext

logger = logging.getLogger(__name__)

__all__ = [
    "BatchRefs",
    "JudgeFailure",
    "build_messages",
    "judge_batch",
    "parse_response",
    "system_prompt",
]

_ENTRY_KEYS = frozenset({"candidate", "skills", "experience_level", "role_fit", "company_needs"})
_SKILL_KEYS = frozenset({"skill", "verdict", "quote"})
_ITEM_KEYS = frozenset({"verdict", "quote", "tag"})
_NEED_KEYS = frozenset({"need", "verdict", "quote", "tag"})


@dataclass(frozen=True)
class JudgeFailure:
    """Why a candidate has no reading: a `config.FAILURE_*` reason."""

    reason: str


@dataclass(frozen=True)
class BatchRefs:
    """The opaque references of one batch and what each one names."""

    skills: Mapping[str, uuid.UUID]          # "s1" -> job_competencies.id
    candidates: Mapping[str, uuid.UUID]      # "c1" -> job_candidate_links.id
    needs: frozenset[str]                    # "n1".. as listed


def system_prompt() -> str:
    """The rendered system prompt. Static: it carries no job or candidate data."""
    return registry.render(
        config.PROMPT_NAME,
        candidate_text_is_data=fragments.CANDIDATE_TEXT_IS_DATA,
        max_tag_words=str(config.MAX_TAG_WORDS),
        min_quote_words=str(config.MIN_QUOTE_WORDS),
        max_quote_chars=str(config.MAX_QUOTE_CHARS),
    )


def build_messages(
    ctx: JobContext, candidates: Sequence[CandidateInput]
) -> tuple[list[dict[str, str]], BatchRefs]:
    """The messages for one batch, and the refs that decode the answer. Pure."""
    skill_refs: dict[str, uuid.UUID] = {}
    skills_payload: list[dict[str, str]] = []
    for position, skill in enumerate(ctx.skills, 1):
        ref = f"s{position}"
        skill_refs[ref] = skill.id
        entry = {"ref": ref, "skill": skill.name, "bucket": skill.bucket}
        if skill.evidence_line:
            entry["good_evidence"] = skill.evidence_line
        skills_payload.append(entry)

    candidate_refs: dict[str, uuid.UUID] = {}
    candidates_payload: list[dict[str, str]] = []
    for position, candidate in enumerate(candidates, 1):
        ref = f"c{position}"
        candidate_refs[ref] = candidate.link_id
        candidates_payload.append({"ref": ref, "resume": candidate.resume_text})

    role: dict[str, str] = {"title": ctx.title, "grade": ctx.grade_label}
    if ctx.department:
        role["department"] = ctx.department
    if ctx.experience_band:
        role["experience_band"] = ctx.experience_band
    if ctx.role_summary:
        role["summary"] = ctx.role_summary
    if ctx.jd_text:
        role["job_description"] = ctx.jd_text

    payload = {
        "role": role,
        "skills": skills_payload,
        "needs": [
            {"ref": need.ref, "kind": need.source, "need": need.text}
            for need in ctx.needs
        ],
        "candidates": candidates_payload,
    }
    messages = [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    refs = BatchRefs(
        skills=skill_refs,
        candidates=candidate_refs,
        needs=frozenset(need.ref for need in ctx.needs),
    )
    return messages, refs


# ── Parsing ──────────────────────────────────────────────────────────────────


def _verdict(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    word = value.strip().casefold()
    return word if word in config.VERDICTS else None


def _item(value: Any, where: str, defects: list[str]) -> RawItem | None:
    if not isinstance(value, dict):
        defects.append(f"{where} must be an object with verdict, quote and tag")
        return None
    extra = set(value) - _ITEM_KEYS
    if extra:
        defects.append(f"{where} has keys that are not allowed: {sorted(extra)}")
    verdict = _verdict(value.get("verdict"))
    if verdict is None:
        defects.append(f"{where}.verdict must be strong, some or none")
    quote, tag = value.get("quote", ""), value.get("tag", "")
    if not isinstance(quote, str) or not isinstance(tag, str):
        defects.append(f"{where}.quote and {where}.tag must be strings")
        return None
    if extra or verdict is None:
        return None
    return RawItem(verdict, quote, tag)


def _entry(
    entry: Mapping[str, Any], refs: BatchRefs, defects: list[str]
) -> RawJudgement | None:
    extra = set(entry) - _ENTRY_KEYS
    if extra:
        defects.append(f"keys that are not allowed: {sorted(extra)}")

    skills: dict[uuid.UUID, RawItem] = {}
    raw_skills = entry.get("skills")
    if not isinstance(raw_skills, list):
        defects.append("skills must be a list with one entry per listed skill")
    else:
        for item in raw_skills:
            if not isinstance(item, dict) or set(item) - _SKILL_KEYS:
                defects.append("each skills entry must carry exactly skill, verdict and quote")
                continue
            ref = item.get("skill")
            if ref not in refs.skills:
                defects.append(f"skills names {ref!r}, which is not a listed skill")
                continue
            skill_id = refs.skills[ref]
            if skill_id in skills:
                defects.append(f"skill {ref} appears more than once")
                continue
            verdict = _verdict(item.get("verdict"))
            quote = item.get("quote", "")
            if verdict is None or not isinstance(quote, str):
                defects.append(f"skill {ref} needs a verdict of strong, some or none and a string quote")
                continue
            skills[skill_id] = RawItem(verdict, quote)
        missing = sorted(ref for ref, sid in refs.skills.items() if sid not in skills)
        if missing:
            defects.append(f"skills is missing {missing}")

    experience = _item(entry.get("experience_level"), "experience_level", defects)
    role_fit = _item(entry.get("role_fit"), "role_fit", defects)

    needs: list[RawNeed] = []
    raw_needs = entry.get("company_needs")
    if not isinstance(raw_needs, list):
        defects.append("company_needs must be a list with one entry per listed need")
    else:
        seen: set[str] = set()
        for item in raw_needs:
            if not isinstance(item, dict) or set(item) - _NEED_KEYS:
                defects.append("each company_needs entry must carry exactly need, verdict, quote and tag")
                continue
            ref = item.get("need")
            verdict = _verdict(item.get("verdict"))
            quote, tag = item.get("quote", ""), item.get("tag", "")
            if not isinstance(ref, str) or verdict is None:
                defects.append("each company_needs entry needs a need reference and a verdict")
                continue
            if not isinstance(quote, str) or not isinstance(tag, str):
                defects.append(f"need {ref} needs a string quote and tag")
                continue
            if ref in refs.needs:
                if ref in seen:
                    defects.append(f"need {ref} appears more than once")
                    continue
                seen.add(ref)
            # An UNLISTED need ref is not a structural defect: it is carried
            # through and dropped, and recorded, by grounding (rule 4).
            needs.append(RawNeed(ref, verdict, quote, tag))
        missing_needs = sorted(refs.needs - seen)
        if missing_needs:
            defects.append(f"company_needs is missing {missing_needs}")

    if defects or experience is None or role_fit is None:
        return None
    return RawJudgement(
        skills=skills,
        experience=experience,
        role_fit=role_fit,
        needs=tuple(needs),
    )


def parse_response(
    raw: str, refs: BatchRefs, wanted: Sequence[str]
) -> tuple[dict[str, RawJudgement], dict[str, list[str]]]:
    """(valid readings by candidate ref, defects by candidate ref) for `wanted`.

    A response that is not the documented object makes EVERY wanted candidate
    defective with the same reason. A candidate the response does not mention
    is defective ("missing"); a candidate mentioned twice is defective rather
    than resolved by picking one, because either choice would be a guess.
    """
    wanted_set = set(wanted)
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}, {ref: ["the response was not valid JSON"] for ref in wanted}
    if not isinstance(data, dict) or set(data) != {"results"} or not isinstance(
        data.get("results"), list
    ):
        return {}, {
            ref: ['the response must be a JSON object with exactly one key, "results", holding a list']
            for ref in wanted
        }

    by_ref: dict[str, list[Mapping[str, Any]]] = {}
    for entry in data["results"]:
        if isinstance(entry, dict) and entry.get("candidate") in wanted_set:
            by_ref.setdefault(entry["candidate"], []).append(entry)

    valid: dict[str, RawJudgement] = {}
    defects: dict[str, list[str]] = {}
    for ref in wanted:
        entries = by_ref.get(ref, [])
        if not entries:
            defects[ref] = ["this candidate is missing from results"]
            continue
        if len(entries) > 1:
            defects[ref] = ["this candidate appears more than once in results"]
            continue
        found: list[str] = []
        reading = _entry(entries[0], refs, found)
        if reading is None:
            defects[ref] = found or ["the entry is malformed"]
        else:
            valid[ref] = reading
    return valid, defects


def _corrective(defects: Mapping[str, Sequence[str]]) -> str:
    lines = [
        "Your previous response broke the required shape for some candidates. "
        "The problems, per candidate:"
    ]
    for ref in sorted(defects):
        lines.append(f"{ref}: " + "; ".join(defects[ref]))
    lines.append(
        'Re-emit ONLY a JSON object {"results": [...]} with one complete entry '
        f"for each of these candidates: {', '.join(sorted(defects))}. Every "
        "listed skill and every listed need must appear exactly once in each "
        "entry. Same rules as before: quotes copied exactly from the resume, "
        "no numbers, no extra keys."
    )
    return "\n".join(lines)


# ── The call ─────────────────────────────────────────────────────────────────


async def judge_batch(
    db: AsyncSession | None, ctx: JobContext, candidates: Sequence[CandidateInput]
) -> dict[uuid.UUID, RawJudgement | JudgeFailure]:
    """One reading per candidate (keyed by link id), or why there is none.

    Never raises for a provider or output problem: an `LLMUnavailableError`
    (including a refusal or a truncation, its subclasses) marks the affected
    candidates `model_unavailable`, and a reading still malformed after the one
    corrective retry is `model_output_invalid`. Anything else is a programming
    error and propagates.
    """
    if not candidates:
        return {}
    messages, refs = build_messages(ctx, candidates)
    wanted = list(refs.candidates)
    by_link = {ref: refs.candidates[ref] for ref in wanted}

    try:
        raw = await llm_router.chat_completion(
            config.TASK_TYPE, messages, response_format_json=True, session=db
        )
    except llm_router.LLMUnavailableError as exc:
        logger.warning(
            "yukti.judge_unavailable job_id=%s candidates=%d err=%s",
            ctx.job_id, len(wanted), type(exc).__name__,
        )
        return {by_link[ref]: JudgeFailure(config.FAILURE_MODEL_UNAVAILABLE) for ref in wanted}

    valid, defects = parse_response(raw, refs, wanted)
    results: dict[uuid.UUID, RawJudgement | JudgeFailure] = {
        by_link[ref]: reading for ref, reading in valid.items()
    }
    if not defects:
        return results

    retry_messages = messages + [
        {"role": "assistant", "content": raw},
        {"role": "user", "content": _corrective(defects)},
    ]
    retry_wanted = sorted(defects)
    try:
        raw_retry = await llm_router.chat_completion(
            config.TASK_TYPE, retry_messages, response_format_json=True, session=db
        )
    except llm_router.LLMUnavailableError as exc:
        logger.warning(
            "yukti.judge_unavailable_on_retry job_id=%s candidates=%d err=%s",
            ctx.job_id, len(retry_wanted), type(exc).__name__,
        )
        for ref in retry_wanted:
            results[by_link[ref]] = JudgeFailure(config.FAILURE_MODEL_UNAVAILABLE)
        return results

    retried, still = parse_response(raw_retry, refs, retry_wanted)
    for ref, reading in retried.items():
        results[by_link[ref]] = reading
    for ref in still:
        logger.warning(
            "yukti.judge_output_invalid job_id=%s link_id=%s defects=%d",
            ctx.job_id, by_link[ref], len(still[ref]),
        )
        results[by_link[ref]] = JudgeFailure(config.FAILURE_MODEL_OUTPUT_INVALID)
    return results
