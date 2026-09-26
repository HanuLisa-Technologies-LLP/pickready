"""Per-candidate question generation against the job's skills contract.

`pickready.generate_candidate_questions` (`workers/tasks_questions.py`) runs
this after an invitation commits. It writes EVERY question the candidate will
be asked, once, before they start.

WHAT IT READS
-------------
The CONTRACT (`assessment_contract.load_contract_for_conversation`): the
skills in three buckets with their hidden priorities and evidence lines, the
role summary Sutra wrote at Save, and the grade. Before any candidate on the
job has started that is the saved live rows; afterwards it is the locked
snapshot. The questions record the digest of the contract they were written
against, and the start compares it with the contract it locks, regenerating
before the first answer when the two differ.

It never reads the job description, the SWOT or anything carrying
compensation. The contract is what every candidate is assessed against;
the job description is recruiter-edited text.

WHAT IT WRITES
--------------
The budget is one question per skill above the grade's floor; the mix is
70/20/10 prose, coding and objective for a coding role, and prose and objective
otherwise (`budget`). The composer places each family on a skill
deterministically (`composition.compose`), and the writers fill the content:

  * prose, ONE model call for every slot (`question_generation`), which is
    also the question any slot is served as if its own writer fails;
  * evidence anchoring, the Must-have and Nice-to-have prose slots anchored to
    a quotable item from this candidate's resume (`generation.anchor_evidence`);
  * objective, one call per slot (`generation.write_structured`);
  * coding, one EXECUTED question per slot, its reference solution proven in
    the sandbox (`coding_generation.write_coding_question`).

EVERY ITEM IS ASKED. Nothing is pre-filled from the resume or from another
employer's record and nothing is trimmed: this SUPERSEDES the resume pre-fill
(C2) and the portable layer (CR 23), and no row this module writes carries a
pre-filled answer.

A DEGRADATION IS A RECORD, NEVER A SHORTFALL
--------------------------------------------
A coding or objective slot its writer could not fill is served as the prose
question already written for its skill, and the reason is recorded. A prose
slot the model could not write is served from a deterministic angle and its
row carries no `generated_at`, so template text is never recorded as
generation. All of it lands in the conversation's composition record
(`assessment_conversations.composition_json`), which the report's provenance
reads.
"""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import AssessmentConversation, CandidateQuestion
from app.models.candidate import JobCandidateLink, Profile
from app.models.job import Job
from app.prompts import registry
from app.services import (
    agent_loop,
    assessment_contract,
    code_execution,
    compensation_guard,
    conversation_guardrails,
    llm_router,
)
from app.services.assessment_contract import (
    BUCKET_BEHAVIOURAL,
    BUCKET_MUST_HAVE,
    AssessmentContract,
    ContractSkill,
)
from app.services.assessment_formats import coding_generation, composition, generation
from app.services.assessment_formats import config as format_config
from app.services.assessment_formats import types as question_types
from app.services.assessment_questions import budget
from app.services.generation_sufficiency import meta_commentary_defects
from app.services.stem_classification import STEM

logger = logging.getLogger(__name__)

__all__ = [
    "COMPOSITION_RECORD_VERSION",
    "GENERIC_ANGLES",
    "PROMPT_NAME",
    "QuestionSet",
    "TASK_TYPE",
    "generate_candidate_questions",
]

TASK_TYPE = "question_generation"
PROMPT_NAME = "assessment_question_generation"
#: The shape of `assessment_conversations.composition_json`. Bumped when a
#: reader would need to tell two shapes apart.
COMPOSITION_RECORD_VERSION = 1

#: The degradation reason when an objective writer produced nothing usable.
OBJECTIVE_GENERATION_FAILED = "objective_generation_failed"

#: Deterministic prose angles, used ONLY for a slot the model did not write.
#: A row asked from one of these carries no `generated_at`.
GENERIC_ANGLES: tuple[str, ...] = (
    "Tell me about a specific situation where {name} was decisive in your work. What did you personally do, and what was the outcome?",
    "Walk me through the most demanding piece of work you have done involving {name}. What made it hard, and how did you handle it?",
    "Describe a time your approach to {name} did not work. What did you change, and what happened next?",
    "Give me a concrete example of {name} in your recent work, including what you decided and how you knew it was right.",
    "How have you developed {name} over your career? Give one example that shows the difference it made.",
)


@dataclass(frozen=True)
class QuestionSet:
    """What one generation produced: the rows, and the record of how."""

    rows: tuple[CandidateQuestion, ...]
    #: True when this call wrote the rows; False when they already existed and
    #: were returned untouched (a redelivered task).
    created: bool
    #: The composition record stamped on the conversation, for a set this call
    #: wrote. INTERNAL: counts and reasons for the report's provenance, never
    #: serialised to a client. None for a set that already existed: its record
    #: is the one stamped when it was written.
    composition: dict[str, Any] | None = None


# ── Inputs ───────────────────────────────────────────────────────────────────


def _resume_excerpt(profile: Profile | None) -> str:
    if profile is None:
        return ""
    # No pay reaches the question writer (owner criterion 7): compensation
    # keys are stripped from the parsed fields and pay lines from the resume.
    parsed = compensation_guard.strip_keys(dict(profile.parsed_fields_json or {}))
    parts = [
        json.dumps(
            {
                "skills": parsed.get("skills", []),
                "total_experience_years": parsed.get("total_experience_years"),
                "employment_history": parsed.get("employment_history", [])[:6],
                "education": parsed.get("education", [])[:4],
            }
        ),
        compensation_guard.redact_text(profile.resume_text)[:2500],
    ]
    return "\n".join(part for part in parts if part)


async def _conversation(session: AsyncSession, link: JobCandidateLink) -> AssessmentConversation:
    """The invitation. An application nobody invited is never given questions:
    the row IS the invitation, and questions without one would be an
    assessment reachable by guessing a URL."""
    conversation = (
        await session.execute(
            select(AssessmentConversation).where(
                AssessmentConversation.job_candidate_link_id == link.id
            )
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise LookupError(
            f"application {link.id} has no assessment invitation, so it has no questions to write"
        )
    return conversation


async def _contract(
    session: AsyncSession, job: Job, conversation: AssessmentConversation
) -> AssessmentContract:
    contract = await assessment_contract.load_contract_for_conversation(session, conversation.id)
    if not contract.locked and not await assessment_contract.skills_saved(session, job.id):
        raise assessment_contract.ContractNotReady()
    buckets = {skill.bucket for skill in contract.skills}
    if BUCKET_MUST_HAVE not in buckets or BUCKET_BEHAVIOURAL not in buckets:
        # The skills rules refuse to save such a contract, so this is a defect
        # upstream rather than a state to write questions around.
        raise assessment_contract.ContractNotReady(
            "The saved skills need at least one Must-have and one Behavioural "
            "skill before questions can be written."
        )
    return contract


# ── Prose: one call, every slot ──────────────────────────────────────────────


@dataclass(frozen=True)
class _ProseAttempt:
    valid: dict[int, str]
    reasons: tuple[str, ...]


def _parse_prose(raw: str, *, total: int, max_chars: int) -> _ProseAttempt:
    parsed = json.loads(raw)
    items = parsed.get("questions") if isinstance(parsed, dict) else None
    if not isinstance(items, list):
        return _ProseAttempt({}, ("return a 'questions' list",))
    valid: dict[int, str] = {}
    reasons: list[str] = []
    seen: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            reasons.append("every entry needs an integer 'index' from the slots list")
            continue
        if not 0 <= index < total:
            reasons.append(f"index {index} is not one of the slots")
            continue
        prompt = " ".join(str(item.get("prompt") or "").split())
        problems: list[str] = []
        if len(prompt) < generation.MIN_PROMPT_CHARS:
            problems.append("write a complete question")
        if len(prompt) > max_chars:
            problems.append(f"keep it under {max_chars} characters")
        if conversation_guardrails.inspect_agent_output(prompt) != prompt:
            problems.append("never state a score, a grade, a number about the candidate or how the answer is judged")
        if meta_commentary_defects(prompt):
            problems.append("ask about the candidate's work, never about the information you were given")
        key = prompt.casefold()
        if key in seen:
            problems.append(f"it repeats the question for index {seen[key]}; ask from a different angle")
        if problems:
            reasons.append(f"index {index}: " + "; ".join(problems))
            continue
        seen[key] = index
        valid[index] = prompt
    missing = [index for index in range(total) if index not in valid]
    if missing:
        reasons.append("write one question for every slot index; missing " + ", ".join(map(str, missing)))
    return _ProseAttempt(valid, tuple(reasons))


async def _write_prose(
    session: AsyncSession,
    *,
    job: Job,
    contract: AssessmentContract,
    slots: list[composition.Slot],
    skills: dict[uuid.UUID, ContractSkill],
    resume_excerpt: str,
    resume_text: str,
    project_evidence: str,
    resume_passages: Mapping[uuid.UUID, str] | None = None,
) -> tuple[list[str], list[bool]]:
    """One question per slot. Returns (prompts, generated) in slot order.

    Inside the bounded loop with deterministic criteria. The loop never raises;
    what it could not write, the deterministic angles supply, and the slot is
    recorded as NOT generated.
    """
    system = registry.render(PROMPT_NAME)
    max_chars = generation.max_prompt_chars()
    payload = {
        "job": {"title": job.title, "grade": contract.grade},
        "role_summary": contract.role_summary,
        "slots": [
            {
                "index": slot.index,
                "bucket": slot.category,
                "skill": slot.skill_name,
                "what_good_evidence_looks_like": skills[slot.competency_id].evidence_line,
                # The parts of THIS resume that bear on the skill (the Evidence
                # RAG), redacted like every other resume text in the request.
                "resume_passages_for_this_skill": compensation_guard.redact_text(
                    (resume_passages or {}).get(slot.competency_id, "")
                ),
            }
            for slot in slots
        ],
        # Redacted HERE, at the one place the request is assembled, so a
        # caller that hands in raw text still sends no pay (the CTC canary in
        # tests/test_ctc_never_in_prompt.py calls this function directly).
        "resume_summary": compensation_guard.redact_text(resume_excerpt),
        "candidate_resume": compensation_guard.redact_text(resume_text)[:6000],
        "project_evidence": project_evidence,
    }
    best: dict[int, str] = {}

    async def execute(reflection: str) -> _ProseAttempt:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload)},
        ]
        if reflection:
            messages.append({"role": "user", "content": reflection})
        raw = await llm_router.invoke_llm(
            TASK_TYPE, messages, response_format_json=True, session=session
        )
        attempt = _parse_prose(raw, total=len(slots), max_chars=max_chars)
        if len(attempt.valid) >= len(best):
            best.clear()
            best.update(attempt.valid)
        return attempt

    def evaluate(value: _ProseAttempt) -> agent_loop.Critique:
        return agent_loop.reject(*value.reasons) if value.reasons else agent_loop.ok()

    result = await agent_loop.run_loop(
        name="assessment_question_generation",
        execute=execute,
        evaluate=evaluate,
        fallback=_ProseAttempt({}, ()),
        max_attempts=agent_loop.BACKGROUND_ATTEMPTS,
        deadline_seconds=agent_loop.BACKGROUND_DEADLINE,
        max_generated_tokens=agent_loop.BACKGROUND_TOKEN_BUDGET,
    )
    # A degraded run keeps the valid subset of its best attempt: each of
    # those passed the same deterministic checks one by one, so using them is
    # declining to discard work that happened, not substituting for work that
    # did not. Everything else is a recorded template.
    written = dict(best) if result.degraded else dict(result.value.valid)
    if result.degraded:
        logger.warning(
            "assessment_questions.prose_degraded slots=%d written=%d attempts=%d",
            len(slots), len(written), result.attempts,
        )
    prompts: list[str] = []
    generated: list[bool] = []
    used: set[str] = {prompt.casefold() for prompt in written.values()}
    for slot in slots:
        prompt = written.get(slot.index)
        if prompt is not None:
            prompts.append(prompt)
            generated.append(True)
            continue
        chosen = GENERIC_ANGLES[slot.index % len(GENERIC_ANGLES)].format(name=slot.skill_name)
        for offset in range(len(GENERIC_ANGLES)):
            candidate = GENERIC_ANGLES[(slot.index + offset) % len(GENERIC_ANGLES)].format(
                name=slot.skill_name
            )
            if candidate.casefold() not in used:
                chosen = candidate
                break
        used.add(chosen.casefold())
        prompts.append(chosen)
        generated.append(False)
    return prompts, generated


# ── Coding: executed, one per slot ───────────────────────────────────────────


async def _write_coding(
    session: AsyncSession,
    *,
    job: Job,
    contract: AssessmentContract,
    slots: list[composition.Slot],
    skills: dict[uuid.UUID, ContractSkill],
    prose: list[str],
    generated: list[bool],
) -> None:
    """Fill every coding slot with a sandbox-proven draft, or degrade it.

    A sandbox that is disabled or unreachable LATCHES: every later coding slot
    is degraded with the same reason and no further model call is made,
    because asking the model again cannot fix a sandbox.
    """
    latched: str | None = None
    avoid = list(await coding_generation.issued_titles(session, job_id=job.id))
    for slot in slots:
        if slot.question_type != question_types.CODING:
            continue
        if latched is not None:
            composition.degrade_to_prose(slot, latched, prose[slot.index], generated=generated[slot.index])
            continue
        outcome = await coding_generation.write_coding_question(
            session,
            job=job,
            contract_skill=skills[slot.competency_id],
            role_summary=contract.role_summary,
            grade=contract.grade,
            avoid_titles=tuple(avoid),
        )
        if outcome.draft is None:
            reason = str(outcome.refusal)
            composition.degrade_to_prose(slot, reason, prose[slot.index], generated=generated[slot.index])
            if reason in (
                coding_generation.REFUSAL_EXECUTION_DISABLED,
                coding_generation.REFUSAL_EXECUTION_UNAVAILABLE,
            ):
                latched = reason
            continue
        slot.coding_draft = outcome.draft
        slot.prompt = outcome.draft.statement
        slot.generated = True
        avoid.append(outcome.draft.title)


# ── Anchoring and objective: the bounded composition loop ────────────────────


async def _fill_and_validate(
    session: AsyncSession,
    *,
    job: Job,
    contract: AssessmentContract,
    slots: list[composition.Slot],
    skills: dict[uuid.UUID, ContractSkill],
    mix: budget.Mix,
    prose: list[str],
    generated: list[bool],
    resume_text: str,
    resume_excerpt: str,
    project_evidence: str,
) -> None:
    conf = format_config.get_config()
    failures: list[str] = []
    for attempt in range(1, conf.composition_attempts + 1):
        anchored, anchor_result = await generation.anchor_evidence(
            session,
            job=job,
            slots=slots,
            skills=skills,
            role_summary=contract.role_summary,
            resume_text=resume_text,
            resume_excerpt=resume_excerpt,
            project_evidence=project_evidence,
            prior_failures=failures,
        )
        for slot in slots:
            if slot.question_type != question_types.EVIDENCE_BASED:
                continue
            written = anchored.get(slot.index)
            if written is None:
                continue
            slot.prompt = written.prompt
            slot.resume_anchor = written.resume_anchor
            slot.payload = dict(written.payload)
            slot.generated = True
        for slot in slots:
            if composition.family_of(slot.question_type) != composition.FAMILY_OBJECTIVE or slot.payload:
                continue
            result = await generation.write_structured(
                session,
                job=job,
                skill=skills[slot.competency_id],
                role_summary=contract.role_summary,
                slot=slot,
                resume_excerpt=resume_excerpt,
            )
            if result.value is None:
                continue
            slot.prompt = result.value.prompt
            slot.payload = dict(result.value.payload)
            slot.rubric = result.value.rubric
            slot.generated = True
        failures = composition.validate(slots, mix=mix, skills=contract.skills)
        logger.info(
            "assessment_questions.composition_validated attempt=%d anchored=%d "
            "anchoring_degraded=%s failures=%d",
            attempt, len(anchored), anchor_result.degraded, len(failures),
        )
        if not failures:
            return

    # Deterministic and always valid: an unanchored evidence slot is asked as
    # the prose question already written for its skill, and an objective slot
    # with no payload is degraded to prose with its reason recorded.
    composition.fall_back(slots, prose, generated=generated)
    remaining = composition.validate(slots, mix=mix, skills=contract.skills)
    if remaining:
        # Unreachable by construction of `fall_back`, and loud if that
        # construction is ever broken: a candidate must never be served an
        # assessment the validator refused.
        raise RuntimeError(
            f"the assessment composition is still invalid after the fallback: {remaining}"
        )


# ── The record ───────────────────────────────────────────────────────────────


def _record(
    *,
    contract: AssessmentContract,
    floor: int,
    total: int,
    eligibility: budget.CodingEligibility,
    mix: budget.Mix,
    slots: list[composition.Slot],
) -> dict[str, Any]:
    return {
        "version": COMPOSITION_RECORD_VERSION,
        "contract_digest": contract.digest,
        "contract_version": contract.version,
        "grade": contract.grade,
        "skill_count": len(contract.skills),
        "floor": floor,
        "budget": total,
        "coding": {
            "eligible": eligibility.eligible,
            "excluded_reason": eligibility.excluded_reason,
        },
        "planned": mix.as_dict(),
        "served": composition.served_mix(slots),
        "degraded": [
            {
                "ordinal": slot.index + 1,
                "skill_id": str(slot.competency_id),
                "planned": slot.planned_family,
                "reason": slot.degradation,
            }
            for slot in slots
            if slot.degradation
        ],
        "templated": [slot.index + 1 for slot in slots if not slot.generated],
        "unanchored": [
            slot.index + 1
            for slot in slots
            if slot.category != BUCKET_BEHAVIOURAL
            and slot.planned_family == composition.FAMILY_PROSE
            and slot.question_type == question_types.SHORT_ANSWER
        ],
    }


def _stamp(
    conversation: AssessmentConversation, contract: AssessmentContract, record: dict[str, Any]
) -> None:
    """Record on the invitation what these questions were written against.

    The start compares `questions_contract_digest` with the contract it locks
    and regenerates before the first answer when they differ (NULL counts as a
    difference). The columns are migration 0123's (Phase 3 WP3).
    """
    conversation.questions_contract_digest = contract.digest
    conversation.questions_contract_version = contract.version
    conversation.composition_json = record


# ── The entry point ──────────────────────────────────────────────────────────


async def _resume_passages(
    session: AsyncSession,
    *,
    job: Job,
    link: JobCandidateLink,
    skills: Mapping[uuid.UUID, ContractSkill],
) -> dict[uuid.UUID, str]:
    """{skill id: the resume passages that bear on it}, through the tool layer.

    Scoped to the one profile the application was submitted with. An
    application with no profile has no resume to read, which is an empty map
    rather than a retrieval over somebody else's.
    """
    from app.services import evidence_retrieval  # noqa: PLC0415

    if link.profile_id is None:
        return {}
    passages: dict[uuid.UUID, str] = {}
    for skill_id, skill in skills.items():
        read = await evidence_retrieval.resume_passages_for_skill(
            session,
            tenant_id=job.tenant_id,
            link_id=link.id,
            profile_id=link.profile_id,
            skill=skill,
        )
        if read.text:
            passages[skill_id] = read.text
    return passages


async def generate_candidate_questions(
    session: AsyncSession, job: Job, link: JobCandidateLink
) -> QuestionSet:
    """Write this candidate's questions against the job's contract. Idempotent.

    A candidate who already has questions keeps exactly those, so a
    redelivered task cannot hand somebody a different assessment. Raises
    `LookupError` for an application with no invitation and
    `assessment_contract.ContractNotReady` for a job whose skills are not
    saved; both fail the task loudly rather than writing an assessment nobody
    can take or nobody agreed to.
    """
    conversation = await _conversation(session, link)
    existing = (
        await session.execute(
            select(CandidateQuestion)
            .where(CandidateQuestion.job_candidate_link_id == link.id)
            .order_by(CandidateQuestion.ordinal)
        )
    ).scalars().all()
    if existing:
        return QuestionSet(rows=tuple(existing), created=False)

    contract = await _contract(session, job, conversation)
    skills = {skill.id: skill for skill in contract.skills}
    floor = budget.question_floor(contract.grade)
    total = budget.question_budget(contract.grade, len(contract.skills))
    eligibility = budget.coding_eligibility(
        role_classification=job.role_classification,
        job_title=job.title,
        execution_enabled=code_execution.is_enabled(),
    )
    mix = budget.mix(total, coding=eligibility.eligible)
    allocation = composition.allocate(
        contract.skills, total, stem=job.role_classification == STEM
    )
    slots = composition.compose(allocation, mix=mix, grade=contract.grade)

    profile = await session.get(Profile, link.profile_id) if link.profile_id else None
    resume_text = (profile.resume_text or "") if profile is not None else ""
    resume_excerpt = _resume_excerpt(profile)
    # THE EVIDENCE RAG, THROUGH THE TYPED TOOL LAYER (PLAN-p5 C4, WP5-D).
    # Project evidence and the resume passages per skill are read through
    # `evidence_retrieval`, the one entry point, so the interviewer's capability
    # and the tenant check run before any row is read. Context only: it moves
    # no weight and no grade. A degraded read is logged by the entry point and
    # costs the question its anchor, never the question.
    from app.services import evidence_retrieval  # noqa: PLC0415

    projects = await evidence_retrieval.project_evidence_for_candidate(
        session,
        tenant_id=job.tenant_id,
        link_id=link.id,
        candidate_id=link.candidate_id,
    )
    project_evidence = projects.text
    resume_passages = await _resume_passages(session, job=job, link=link, skills=skills)

    prose, generated = await _write_prose(
        session,
        job=job,
        contract=contract,
        slots=slots,
        skills=skills,
        resume_excerpt=resume_excerpt,
        resume_text=resume_text,
        project_evidence=project_evidence,
        resume_passages=resume_passages,
    )
    for slot in slots:
        slot.prompt = prose[slot.index]
        slot.generated = generated[slot.index]
    await _write_coding(
        session,
        job=job,
        contract=contract,
        slots=slots,
        skills=skills,
        prose=prose,
        generated=generated,
    )
    await _fill_and_validate(
        session,
        job=job,
        contract=contract,
        slots=slots,
        skills=skills,
        mix=mix,
        prose=prose,
        generated=generated,
        resume_text=resume_text,
        resume_excerpt=resume_excerpt,
        project_evidence=project_evidence,
    )

    now = datetime.now(timezone.utc)
    rows: list[CandidateQuestion] = []
    for slot in slots:
        row = CandidateQuestion(
            tenant_id=job.tenant_id,
            job_id=job.id,
            job_candidate_link_id=link.id,
            competency_id=slot.competency_id,
            ordinal=slot.index + 1,
            prompt=slot.prompt,
            rubric_json=slot.rubric,
            question_type=slot.question_type,
            payload_json=dict(slot.payload),
            resume_anchor=slot.resume_anchor,
            time_allocation_seconds=slot.time_allocation_seconds,
            weight=slot.weight,
            generated_at=now if slot.generated else None,
        )
        if slot.question_type == question_types.CODING:
            await coding_generation.persist_coding_question(session, row, slot.coding_draft)
        else:
            session.add(row)
        rows.append(row)
    record = _record(
        contract=contract,
        floor=floor,
        total=total,
        eligibility=eligibility,
        mix=mix,
        slots=slots,
    )
    _stamp(conversation, contract, record)
    await session.flush()
    logger.info(
        "assessment_questions.generated link_id=%s conversation_id=%s "
        "contract_version=%d contract_digest=%s count=%d planned=%s served=%s "
        "degraded=%d templated=%d",
        link.id, conversation.id, contract.version, contract.digest, len(rows),
        record["planned"], record["served"], len(record["degraded"]),
        len(record["templated"]),
    )
    return QuestionSet(rows=tuple(rows), created=True, composition=record)
