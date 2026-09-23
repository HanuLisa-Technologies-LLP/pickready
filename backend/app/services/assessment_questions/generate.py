"""Per-candidate question generation: the questions that probe a saved matrix.

Carved out of `services/ppi.py` on 2026-09-24 (PLAN-p3 WP0) as a PURE MOVE,
with no behaviour change. The matrix is per JOB and lives in `services/ppi`;
the questions are per CANDIDATE and are generated here from the JD, the saved
matrix and that candidate's resume. `pickready.generate_candidate_questions`
(`workers/tasks_questions.py`) is the task that runs it.
"""
from __future__ import annotations

import json
import logging
from itertools import cycle

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import CandidateQuestion, JobCompetency
from app.models.candidate import JobCandidateLink, Profile
from app.models.job import Job
from app.models.job_setup import SWOT_AREAS, JobSwotAnalysis
from app.services import (
    consent_catalog,
    llm_router,
    portable_evidence,
)
from app.services.assessment_formats import composition, generation
from app.services.assessment_formats import config as format_config
from app.services.assessment_formats import types as question_types
from app.prompts import registry
from app.services.assessment_questions.budget import (
    DEFAULT_GRADE,
    resolve_question_target,
    typical_split,
)
from app.services.ppi import (
    CATEGORIES,
    load_framework,
)

logger = logging.getLogger(__name__)

__all__ = [
    "generate_candidate_questions",
    "portable_coverage",
]


# ── Per-candidate question generation (spec §5.6) ────────────────────────────


def _allocation_priority(grade: str | None) -> dict[str, int]:
    """Which aspect gets a surplus question first.

    Ordered by the typical split's own weighting for this grade: whichever
    aspect the client's table asks the most of is the one a spare question goes
    to. That keeps the illustrative splits doing what the spec says they are for
    -- shaping a balanced interview -- without any of them being enforced.
    """
    split = typical_split(grade)
    ordered = sorted(CATEGORIES, key=lambda category: -split[category][1])
    return {category: index for index, category in enumerate(ordered)}


def _allocate(
    competencies: list[JobCompetency], total: int, grade: str | None
) -> list[JobCompetency]:
    """Spread `total` questions across the matrix, one per item first.

    Every item must be probed at least once -- an unprobed item still gets a
    grade and a remark in the report, and grading something the candidate was
    never asked about is exactly the unfair output the review gate exists to
    prevent. `matrix_is_complete` refuses a matrix bigger than `total`, so the
    truncation below is unreachable through the product's own save path and
    exists only so a hand-written row cannot make this function lie about its
    length.
    """
    if not competencies:
        return []
    plan = list(competencies[:total])
    if len(plan) >= total:
        return plan
    priority = _allocation_priority(grade)
    extras = cycle(
        sorted(competencies, key=lambda row: (priority[row.category], row.ordinal))
    )
    while len(plan) < total:
        plan.append(next(extras))
    return plan


#: Text in `app/prompts/ppi_candidate_questions_system.txt`, loaded through the
#: registry so a wording change is a versioned diff in a prompt file rather than
#: a string literal in a module of code.
_QUESTION_SYSTEM_PROMPT = registry.render("ppi_candidate_questions_system")

_GENERIC_ANGLES: tuple[str, ...] = (
    "Tell me about a specific situation where {name} was decisive in your work. What did you personally do, and what was the outcome?",
    "Walk me through the most demanding piece of work you have done involving {name}. What made it hard, and how did you handle it?",
    "Describe a time your approach to {name} did not work. What did you change, and what happened next?",
    "Give me a concrete example of {name} in your recent work, including what you decided and how you knew it was right.",
    "How have you developed {name} over your career? Give one example that shows the difference it made.",
)


def _resume_excerpt(profile: Profile | None) -> str:
    if profile is None:
        return ""
    parsed = profile.parsed_fields_json or {}
    parts = [
        json.dumps(
            {
                "skills": parsed.get("skills", []),
                "total_experience_years": parsed.get("total_experience_years"),
                "employment_history": parsed.get("employment_history", [])[:6],
                "education": parsed.get("education", [])[:4],
            }
        ),
        (profile.resume_text or "")[:2500],
    ]
    return "\n".join(part for part in parts if part)


async def generate_candidate_questions(
    session: AsyncSession,
    job: Job,
    link: JobCandidateLink,
    *,
    grade: str | None = None,
) -> list[CandidateQuestion]:
    """Generate this candidate's questions against the job's saved matrix.

    Idempotent: a candidate who already has questions keeps exactly those. Two
    candidates on the same job get DIFFERENT questions probing the SAME matrix
    -- that is what makes their reports comparable while keeping each
    conversation relevant to the person in it (spec §5.6).

    These rows are the WHOLE conversation now. A Must-have or Nice-to-have row
    is re-written with its own rubric at the moment it is asked
    (`services/ppi_interview.write_question`); the prompt stored here is the
    deterministic probe that is asked if that generation is unavailable.
    """
    existing = (
        await session.execute(
            select(CandidateQuestion)
            .where(CandidateQuestion.job_candidate_link_id == link.id)
            .order_by(CandidateQuestion.ordinal)
        )
    ).scalars().all()
    if existing:
        return list(existing)

    # GATE G1. Questions are written against the job's criteria, so a job with
    # no approved, frozen matrix has nothing to write them against. This used to
    # generate one on demand, which meant a candidate could be asked questions
    # derived from criteria nobody had reviewed -- and then graded against them.
    from app.services.hiring import scorecard  # noqa: PLC0415

    await scorecard.require_frozen_matrix(session, job.id)
    framework = await load_framework(session, job.id)
    grade = grade or job.assessment_grade or DEFAULT_GRADE
    # The job's resolved target, not a per-candidate decision. Falls back to
    # resolving it now for a job whose matrix predates `question_target`.
    total = job.question_target or resolve_question_target(
        grade, len(framework), job.role_classification
    )
    allocation = _allocate(framework, total, grade)
    if not allocation:
        return []

    profile = await session.get(Profile, link.profile_id) if link.profile_id else None
    # Project Evidence Intelligence: the derived evidence block (claims and
    # observations labelled, validation areas named) joins the resume in the
    # generation context, so a candidate's questions can probe what their
    # projects actually show. Context only -- it moves no weight, no grade and
    # no report section, and an empty string for a candidate with no projects
    # changes nothing.
    from app.services.projects import context as project_context  # noqa: PLC0415

    project_evidence_block = await project_context.candidate_project_context(
        session, link.candidate_id
    )
    prompts: dict[int, str] = {}
    try:
        raw = await llm_router.chat_completion(
            "behavioral_assessment",
            [
                {"role": "system", "content": _QUESTION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "job": {
                                "title": job.title,
                                "grade": grade,
                                "jd": job.jd_json,
                            },
                            "allocation": [
                                {
                                    "index": index,
                                    "category": row.category,
                                    "name": row.name,
                                    "measures": row.description,
                                }
                                for index, row in enumerate(allocation)
                            ],
                            "candidate_resume": _resume_excerpt(profile),
                            "project_evidence": project_evidence_block,
                        }
                    ),
                },
            ],
            response_format_json=True,
            session=session,
        )
        for item in json.loads(raw).get("questions", []):
            if not isinstance(item, dict):
                continue
            try:
                index = int(item["index"])
            except (KeyError, TypeError, ValueError):
                continue
            prompt = str(item.get("prompt") or "").strip()
            if 0 <= index < len(allocation) and len(prompt) >= 15:
                prompts[index] = prompt
    except Exception:
        logger.warning(
            "ppi.questions.llm_unavailable link_id=%s, using matrix-derived questions",
            link.id,
        )

    base_prompts: list[str] = []
    seen_prompts: set[str] = set()
    for index, competency in enumerate(allocation):
        prompt = prompts.get(index) or _GENERIC_ANGLES[index % len(_GENERIC_ANGLES)].format(
            name=competency.name
        )
        # A model that repeats itself would collapse several items into one
        # probe; fall back to a distinct angle rather than storing a duplicate
        # the candidate would visibly be asked twice.
        if prompt.casefold() in seen_prompts:
            for offset in range(len(_GENERIC_ANGLES)):
                alternative = _GENERIC_ANGLES[(index + offset) % len(_GENERIC_ANGLES)].format(
                    name=competency.name
                )
                if alternative.casefold() not in seen_prompts:
                    prompt = alternative
                    break
        seen_prompts.add(prompt.casefold())
        base_prompts.append(prompt)

    # The format of every slot, and the content of the ones that are not
    # plain text. The text question written above is what every slot falls
    # back to, so nothing below can leave a slot with nothing to ask.
    slots = await _compose_formats(
        session,
        job,
        allocation,
        grade=grade,
        base_prompts=base_prompts,
        profile=profile,
        project_evidence_block=project_evidence_block,
    )
    # ── Resume pre-fill and the asked-question ceiling (feature 2, C2) ──────
    # A criterion the resume already evidences (a substantive anchor AND the
    # skill on the candidate's own parsed claim list) is recorded rather than
    # asked; whatever would still be ASKED past the ceiling is trimmed lowest
    # weight first, and never created at all. The trim is per candidate by
    # ruling: the owner traded fixed-count comparability for speed, and the
    # criteria ORDER stays deterministic per job.
    from app.core.config import get_settings as _get_settings
    from app.services import resume_prefill

    # ── The Portable layer, mapped against THIS job's matrix (CR 23) ────────
    #
    # Owner ruling 2026-09-22. A criterion the candidate's PORTABLE record
    # already establishes is recorded rather than asked, exactly as a resume
    # pre-fill is, and for the same reason: nobody should be made to re-type a
    # fact the platform already holds.
    #
    # THE CRITERION IS NEVER DROPPED FROM THE MATRIX. Its row is still
    # created, still carries its rubric, still carries its required level and
    # is still graded and charted. What changes is where the evidence for it
    # came from, and `prefill_source` says so on the row. A criterion that
    # vanished because the record covered it would be the "insufficient
    # evidence is not negative evidence" rule failing in its other direction:
    # an item nobody grades.
    #
    # PORTABLE IS TRIED FIRST, resume anchor second. A portable fact is the
    # stronger record of the two: it carries an originator, a date and a
    # locator, and one of its kinds was written by somebody other than the
    # candidate. The resume anchor is the weaker fallback, not the default.
    coverage = await portable_coverage(session, job, link)
    covered = coverage.by_name()

    text_types = frozenset(
        {question_types.EVIDENCE_BASED, question_types.SHORT_ANSWER}
    )
    prefills: dict[int, str] = {}
    sources: dict[int, str] = {}
    for slot in slots:
        competency = allocation[slot.index]
        established = covered.get(competency.name)
        if established is not None and slot.question_type in text_types:
            prefills[slot.index] = portable_evidence.prefill_text(established)
            sources[slot.index] = resume_prefill.PREFILL_SOURCE_PORTABLE
            continue
        answer = resume_prefill.evidence_for(
            competency_name=competency.name,
            # THE ARGUMENT THAT CLOSES THE DEFECT. Until 2026-09-22 this call
            # passed no category at all, so a Behavioural competency whose name
            # appeared in the parsed skills was pre-filled and skipped. Every
            # behavioural dimension is freshly assessed, always.
            category=competency.category,
            resume_anchor=slot.resume_anchor,
            parsed_fields=profile.parsed_fields_json if profile else None,
            question_type=slot.question_type,
            text_types=text_types,
        )
        if answer is not None:
            prefills[slot.index] = answer
            sources[slot.index] = resume_prefill.PREFILL_SOURCE_RESUME
    trimmed = resume_prefill.trim_to_ceiling(
        slots,
        prefilled_indexes=set(prefills),
        ceiling=_get_settings().assessment_question_ceiling,
    )
    if prefills or trimmed:
        logger.info(
            "ppi.questions.resume_aware link_id=%s prefilled=%d portable=%d "
            "trimmed=%d",
            link.id,
            len(prefills),
            sum(
                1
                for source in sources.values()
                if source == resume_prefill.PREFILL_SOURCE_PORTABLE
            ),
            len(trimmed),
        )
    rows: list[CandidateQuestion] = [
        CandidateQuestion(
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
            prefilled_answer=prefills.get(slot.index),
            prefill_source=sources.get(slot.index),
        )
        for slot in slots
        if slot.index not in trimmed
    ]
    session.add_all(rows)
    await session.flush()
    by_type = {
        question_type: sum(1 for row in rows if row.question_type == question_type)
        for question_type in question_types.QUESTION_TYPES
    }
    logger.info(
        "ppi.questions.generated link_id=%s grade=%s count=%d formats=%s",
        link.id, grade, len(rows), by_type,
    )
    return rows


async def portable_coverage(
    session: AsyncSession, job: Job, link: JobCandidateLink
) -> portable_evidence.Coverage:
    """The Portable plus Job Specific split for one candidate on one job (CR 23).

    HARVEST FIRST, THEN MAP. The harvest is deterministic extraction from rows
    the product already holds (the parsed resume, the finalised employment
    history, an employer's own confirmation) and it calls no model, so running
    it here costs a few indexed statements and guarantees the split is computed
    against what is true NOW rather than against whatever a previous
    application happened to leave behind.

    This runs inside `pickready.generate_candidate_questions`, which is already
    a dispatched task, so no request handler waits on it.

    `retake` is imported INSIDE the function. It owns `RETAKE_WINDOW_DAYS`,
    which is the product's one "how old is too old" boundary and belongs to the
    module that already explains it to the candidate; importing it at module
    scope here would make this file depend on the report models for a single
    integer.

    NEITHER HALF MAY FAIL QUESTION GENERATION. A candidate is waiting on their
    assessment; the worst honest outcome of a portable read that will not work
    is that every criterion is asked, which is the product's behaviour from
    before this feature and is never wrong. The failure is LOGGED with its
    class, never swallowed into a bare pass, and an empty Coverage is a real
    value rather than a substitute for a missing one: it says the record
    establishes nothing, which is exactly what the caller should then act on.
    """
    from app.services import retake

    if not await consent_catalog.cross_employer_reuse_allowed(
        session, link.candidate_id
    ):
        # Not an error and not a degradation. The candidate was asked and
        # either declined or was never asked, and absence of consent is never
        # consent.
        return portable_evidence.Coverage()

    try:
        await portable_evidence.harvest(
            session,
            candidate_id=link.candidate_id,
            job_id=job.id,
            tenant_id=job.tenant_id,
        )
    except Exception as exc:  # noqa: BLE001 - recorded, never silent
        logger.warning(
            "ppi.portable_harvest_failed link_id=%s reason=%s",
            link.id, type(exc).__name__, exc_info=True,
        )

    criteria = await load_framework(session, job.id)
    if not criteria:
        return portable_evidence.Coverage()
    try:
        facts = await portable_evidence.load_for_candidate(
            session, link.candidate_id
        )
    except Exception as exc:  # noqa: BLE001 - recorded, never silent
        logger.warning(
            "ppi.portable_load_failed link_id=%s reason=%s",
            link.id, type(exc).__name__, exc_info=True,
        )
        return portable_evidence.Coverage(
            to_assess=tuple(
                row.name
                for row in criteria
                if row.category not in portable_evidence.NEVER_COVERED_CATEGORIES
            ),
            behavioural=tuple(
                row.name
                for row in criteria
                if row.category in portable_evidence.NEVER_COVERED_CATEGORIES
            ),
        )
    return portable_evidence.coverage(
        list(criteria), facts, max_age_days=retake.RETAKE_WINDOW_DAYS
    )


async def _hiring_context(session: AsyncSession, job: Job) -> str:
    """The JD and saved Job SWOT document for candidate question generation."""
    analysis = (
        await session.execute(
            select(JobSwotAnalysis).where(JobSwotAnalysis.job_id == job.id)
        )
    ).scalars().first()
    return json.dumps(
        {
            "hiring_requirement": job.jd_json or {},
            "swot": {
                area: getattr(analysis, area) or "" if analysis is not None else ""
                for area in SWOT_AREAS
            },
        }
    )


async def _compose_formats(
    session: AsyncSession,
    job: Job,
    allocation: list[JobCompetency],
    *,
    grade: str,
    base_prompts: list[str],
    profile: Profile | None,
    project_evidence_block: str,
) -> list[composition.Slot]:
    """Decide each slot's format, fill it, validate the mix, and never serve
    an invalid one (spec section 3.2).

    The FORMAT mix is deterministic per job (`composition.compose`); the
    CONTENT is written per candidate by the model, inside the bounded loop.
    A composition that fails validation is regenerated up to
    `composition_attempts` times with the failures fed to the writer, then
    `composition.fall_back` turns every slot the model could not fill into
    the text question already written for its item, which validates by
    construction. Nothing is cached and no template is shared between
    candidates: two candidates on one job get the same formats in the same
    positions and different content in every one of them.
    """
    conf = format_config.get_config()
    role_classification = job.role_classification
    competencies = {row.id: row for row in allocation}
    resume_text = (profile.resume_text or "") if profile is not None else ""
    resume_excerpt = _resume_excerpt(profile)
    hiring_context = await _hiring_context(session, job)
    slots = composition.compose(allocation, grade=grade, role_classification=role_classification)
    for slot in slots:
        slot.prompt = base_prompts[slot.index]

    failures: list[str] = []
    for attempt in range(1, conf.composition_attempts + 1):
        anchored, anchor_result = await generation.anchor_evidence(
            session,
            job=job,
            slots=slots,
            competencies=competencies,
            resume_text=resume_text,
            resume_excerpt=resume_excerpt,
            project_evidence=project_evidence_block,
            hiring_context=hiring_context,
            prior_failures=failures,
        )
        for slot in slots:
            if slot.question_type != question_types.EVIDENCE_BASED:
                continue
            written = anchored.get(slot.index)
            if written is None:
                slot.resume_anchor = None
                slot.payload = {}
                continue
            slot.prompt = written.prompt
            slot.resume_anchor = written.resume_anchor
            slot.payload = dict(written.payload)
        for slot in slots:
            if slot.question_type not in question_types.SUPPORTING_TYPES or slot.payload:
                continue
            result = await generation.write_structured(
                session,
                job=job,
                competency=competencies[slot.competency_id],
                slot=slot,
                resume_excerpt=resume_excerpt,
            )
            if result.value is None:
                # The slot keeps the text question already written for its
                # item. Reverted here, not left empty: a structured row with
                # no payload would be a question with no answer key.
                composition.revert_to_text(slot)
                slot.prompt = base_prompts[slot.index]
                continue
            slot.prompt = result.value.prompt
            slot.payload = dict(result.value.payload)
            slot.rubric = result.value.rubric
        failures = composition.validate(slots, grade, role_classification)
        logger.info(
            "ppi.composition.validated attempt=%d anchored=%d anchoring_degraded=%s failures=%s",
            attempt, len(anchored), anchor_result.degraded, failures,
        )
        if not failures:
            return slots

    # Deterministic, and always valid: every slot the model could not fill
    # soundly becomes the plain text question already written for its item.
    composition.fall_back(slots, grade)
    for slot in slots:
        if slot.question_type == question_types.SHORT_ANSWER:
            slot.prompt = base_prompts[slot.index]
    remaining = composition.validate(slots, grade, role_classification)
    if remaining:
        # Unreachable by construction of `fall_back`, and a loud failure is
        # the right answer if that construction is ever broken: a candidate
        # must never be served an assessment the validator rejected.
        raise RuntimeError(
            "assessment composition is still invalid after the deterministic "
            f"fallback: {remaining}"
        )
    logger.warning(
        "ppi.composition.fell_back link_grade=%s failures=%s", grade, failures
    )
    return slots
