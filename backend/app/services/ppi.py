"""PickReady Profile Intelligence (PPI) -- the per-job evaluation framework.

PROPRIETARY: PPI is PickReady's own competency framework, derived from
first-principles job analysis. It is NOT modelled on, named after, or derived
from any licensed psychometric instrument, and no such instrument may ever be
referenced in this file, the product UI, or the documentation.

What replaced what (2026-07-30)
-------------------------------
PPI supersedes the PickReady Functional Index (PFI). PFI was ONE fixed
dimension set per grade, reused across every job in the product. PPI generates
a FRESH framework for every job, from that job's own JD:

    >= 5 Primary Skills, >= 5 Secondary Skills, >= 5 Behavioural Competencies

The agent may recommend more than five of any category when the job's
complexity warrants it. The trade the client accepted, explicitly: more precise
to the specific role, at the cost of no longer having one fixed list to point
to across the whole product.

The two things that must not be confused
----------------------------------------
1. **The framework is per JOB.** Generated once at job creation, reviewed and
   saved by the Hiring Manager, then FIXED. Every candidate applying to that
   job is graded against the same Primary Skills, Secondary Skills and
   Behavioural Competencies -- that is the only reason two candidates' reports
   are comparable.
2. **The questions are per CANDIDATE.** Once the framework is saved, questions
   probing it are generated individually from the JD, the saved framework, and
   that candidate's own resume. Count is fixed by the candidate's GRADE, never
   by the job (spec §6.1).

"Culture" is refused as a Behavioural Competency, at generation and at save.
Cultural fit cannot be assessed accurately in a single conversation, and PPI
does not claim otherwise (spec §6.2).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from itertools import cycle
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import CandidateQuestion, JobCompetency
from app.models.candidate import JobCandidateLink, Profile
from app.models.job import Job
from app.services import llm_router
from app.services.rating import (
    GRADE_HIGHLY,
    GRADE_MATCHING,
    GRADE_MODERATELY,
    GRADES,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CATEGORIES",
    "CATEGORY_BEHAVIOURAL",
    "CATEGORY_LABELS",
    "CATEGORY_PRIMARY",
    "CATEGORY_SECONDARY",
    "FORBIDDEN_COMPETENCY_TERMS",
    "MINIMUM_PER_CATEGORY",
    "PPI_QUESTION_COUNTS",
    "REQUIRED_LEVEL_SCORES",
    "generate_candidate_questions",
    "generate_framework",
    "framework_is_complete",
    "is_forbidden_competency",
    "load_framework",
    "ppi_question_count",
    "required_level_score",
]

# ── Categories ───────────────────────────────────────────────────────────────

CATEGORY_PRIMARY = "primary_skill"
CATEGORY_SECONDARY = "secondary_skill"
CATEGORY_BEHAVIOURAL = "behavioural"

#: Ordered exactly as the report renders them (spec §10.3).
CATEGORIES: tuple[str, ...] = (
    CATEGORY_PRIMARY,
    CATEGORY_SECONDARY,
    CATEGORY_BEHAVIOURAL,
)

CATEGORY_LABELS: dict[str, str] = {
    CATEGORY_PRIMARY: "Primary Skills",
    CATEGORY_SECONDARY: "Secondary Skills",
    CATEGORY_BEHAVIOURAL: "Behavioural Competencies",
}

#: The floor, not the target. A complex job legitimately gets more (spec §6.2).
MINIMUM_PER_CATEGORY = 5

#: Sanity ceiling. Not in the spec, but a framework of 40 competencies would
#: give a non-managerial candidate fewer than one question per competency and
#: make the radar charts unreadable. Generation is capped, not rejected.
MAXIMUM_PER_CATEGORY = 10


# ── The Culture refusal (spec §6.2) ──────────────────────────────────────────
# Enforced at BOTH ends: the generator is told not to produce it, and the save
# handler refuses it. A prompt instruction is a request, not a guarantee, and
# the Hiring Manager can type anything into the Edit control.

FORBIDDEN_COMPETENCY_TERMS: tuple[str, ...] = (
    "culture",
    "cultural",
)

FORBIDDEN_COMPETENCY_DETAIL = (
    "Culture is not assessable as a Behavioural Competency. Cultural fit cannot "
    "be judged accurately from a single assessment, so PPI does not claim to "
    "measure it. Please use a competency describing an observable behaviour."
)


def is_forbidden_competency(name: str) -> bool:
    """True when `name` is a culture-fit competency, in any casing or phrasing.

    Matches on word boundaries so a legitimate competency that merely CONTAINS
    the letters (there is no such English word in practice, but "agricultural"
    is the shape of the risk) is not caught.
    """
    lowered = str(name or "").casefold()
    return any(
        re.search(rf"\b{term}\b", lowered) for term in FORBIDDEN_COMPETENCY_TERMS
    )


# ── Question counts by CANDIDATE grade (spec §6.1) ───────────────────────────
# Note the direction: MORE questions for a junior candidate, fewer for a CXO.
# That is deliberate and is the client's table verbatim -- a CXO's evidence is
# broader per answer, and their time is the scarce resource.

PPI_QUESTION_COUNTS: dict[str, int] = {
    "non_managerial": 25,
    "managerial": 20,
    "leadership": 15,
    "cxo": 10,
}


def ppi_question_count(grade: str | None) -> int:
    return PPI_QUESTION_COUNTS.get(grade or "", PPI_QUESTION_COUNTS["non_managerial"])


# ── Required level ───────────────────────────────────────────────────────────
# The radar plots TWO shapes: what the job needs and what the candidate showed
# (spec §10.4). The job's shape comes from a required level the framework agent
# assigns to each competency, stated as one of the same four grade WORDS the
# client already reads -- never a number, at generation or at display.
#
# It is stored as the band's representative internal score purely so it shares
# the column type and the grade projection with the candidate's score; nothing
# reads it as a number outside this module and `rating.grade_for_percent`.

REQUIRED_LEVEL_SCORES: dict[str, int] = {
    GRADE_HIGHLY: 95,
    GRADE_MATCHING: 82,
    GRADE_MODERATELY: 67,
}

#: A job that requires NOTHING of a competency would not have it in its
#: framework, so "Not Matching" is not an offered requirement level. An
#: unrecognised value settles on the middle band rather than raising.
DEFAULT_REQUIRED_LEVEL = REQUIRED_LEVEL_SCORES[GRADE_MATCHING]


def required_level_score(label: Any) -> int:
    return REQUIRED_LEVEL_SCORES.get(str(label).strip(), DEFAULT_REQUIRED_LEVEL)


# ── Deterministic fallback framework ─────────────────────────────────────────
# CLAUDE.md rule 9: degrade, never crash. With the whole LLM chain down, a job
# still gets a usable framework built from its own JD, and the recruiter can
# edit it -- which is the review step the workflow already requires.

_FALLBACK_BEHAVIOURAL: tuple[tuple[str, str], ...] = (
    ("Ownership", "Sees committed work through to a finished, verified outcome."),
    ("Communication", "Explains decisions and trade-offs clearly to the people affected."),
    ("Collaboration", "Works effectively across roles and asks for help at the right moment."),
    ("Problem solving", "Breaks an unfamiliar problem down and reasons to a defensible answer."),
    ("Adaptability", "Adjusts approach when priorities, constraints or information change."),
    ("Judgement", "Weighs incomplete evidence and commits to a decision they can defend."),
)


def _jd_terms(job: Job, field: str) -> list[str]:
    value = (job.jd_json or {}).get(field)
    items = value if isinstance(value, list) else ([value] if isinstance(value, str) else [])
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item).strip()
        if not text:
            continue
        # A responsibility line is prose; take its leading clause so the label
        # reads as a skill rather than an instruction.
        label = re.split(r"[,;:.]| that | which | so that ", text, maxsplit=1)[0].strip()
        label = label[:120].strip(" -,")
        if label and label.casefold() not in seen:
            seen.add(label.casefold())
            out.append(label[:1].upper() + label[1:])
    return out


#: Words used to tell two padded competency names apart when the JD gave the
#: fallback fewer distinct terms than the per-category floor. Must hold at least
#: MINIMUM_PER_CATEGORY entries, since one pass of the padding loop may use each
#: at most once.
_PADDING_QUALIFIERS: tuple[str, ...] = (
    "further",
    "adjacent",
    "broader",
    "related",
    "wider",
    "applied",
)


def _fallback_framework(job: Job) -> list[dict[str, Any]]:
    """A framework built from the JD's own words, with no network call."""
    skills = _jd_terms(job, "skills")
    topics = _jd_terms(job, "responsibilities") + _jd_terms(job, "accountabilities")
    pool = skills + [t for t in topics if t.casefold() not in {s.casefold() for s in skills}]
    if not pool:
        pool = [job.title]

    rows: list[dict[str, Any]] = []
    primary = pool[:MINIMUM_PER_CATEGORY]
    secondary = pool[MINIMUM_PER_CATEGORY : MINIMUM_PER_CATEGORY * 2]

    # Cycle the pool rather than emitting fewer than the minimum: the floor is
    # a product contract, and a short framework silently narrows every report
    # written against this job.
    #
    # WHY THIS IS A BOUNDED `for` AND NOT A `while`
    # ---------------------------------------------
    # The padding used to append only when the generated name happened to be
    # new:
    #
    #     while len(secondary) < MINIMUM_PER_CATEGORY:
    #         candidate = f"{next(filler)} (supporting)"
    #         if candidate not in secondary:
    #             secondary.append(candidate)
    #
    # `filler` cycles `pool`, so once every pool term had been used the loop
    # regenerated names it had already rejected and made no further progress.
    # Any job whose pool holds fewer than MINIMUM_PER_CATEGORY DISTINCT terms
    # hung forever, and that is the NORMAL shape of a job created from
    # `jd_markdown` alone, where `jd_json.skills` is [] and the pool is just the
    # title. Observed in production 2026-08-01: `generate_technical_questions`
    # spun until Celery's 600s soft time limit and then autoretried five times,
    # holding one of the worker pool's two slots for the best part of an hour
    # each. Every queued assessment task starved behind it, which is what
    # "assessments are not available" looked like from the outside.
    #
    # The loop is now bounded by the shortfall it is filling and appends exactly
    # one name per pass, so it terminates whatever the pool contains. The
    # qualifier only has to make the name READ differently to the recruiter who
    # reviews it; correctness no longer depends on it being unique, which is why
    # a collision can no longer cost anything worse than one duplicate row that
    # `_normalise` then drops.
    def _pad(target: list[str], suffix: str) -> None:
        taken = {name.casefold() for name in target}
        filler = cycle(pool)
        for attempt in range(MINIMUM_PER_CATEGORY):
            if len(target) >= MINIMUM_PER_CATEGORY:
                return
            base = next(filler)
            name = f"{base} ({suffix})"
            if name.casefold() in taken:
                # No digit here, deliberately. This name is rendered to the
                # Hiring Manager on the setup screen and can survive review into
                # a report, and claude.md's no-numbers rule is about what a
                # client reads, not only about scores.
                name = f"{base} ({_PADDING_QUALIFIERS[attempt]} {suffix})"
            taken.add(name.casefold())
            target.append(name)

    _pad(primary, "core")
    _pad(secondary, "supporting")

    for name in primary:
        rows.append(
            {
                "category": CATEGORY_PRIMARY,
                "name": name,
                "description": f"Core capability the job description names as required: {name}.",
                "required_level": GRADE_HIGHLY,
            }
        )
    for name in secondary:
        rows.append(
            {
                "category": CATEGORY_SECONDARY,
                "name": name,
                "description": f"Supporting capability that strengthens delivery of the role: {name}.",
                "required_level": GRADE_MATCHING,
            }
        )
    for name, description in _FALLBACK_BEHAVIOURAL[:MINIMUM_PER_CATEGORY]:
        rows.append(
            {
                "category": CATEGORY_BEHAVIOURAL,
                "name": name,
                "description": description,
                "required_level": GRADE_MATCHING,
            }
        )
    return rows


_FRAMEWORK_SYSTEM_PROMPT = (
    "You design the evaluation framework for one specific job, from its job "
    "description alone. Produce THREE categories:\n"
    f"  primary_skill  -- at least {MINIMUM_PER_CATEGORY}: the capabilities the role "
    "cannot be performed without.\n"
    f"  secondary_skill -- at least {MINIMUM_PER_CATEGORY}: supporting capabilities "
    "that materially strengthen performance but are not disqualifying.\n"
    f"  behavioural    -- at least {MINIMUM_PER_CATEGORY}: observable workplace "
    "behaviours the role demands.\n"
    "Recommend MORE than the minimum in any category when the job's complexity "
    f"genuinely warrants it, up to {MAXIMUM_PER_CATEGORY} per category.\n\n"
    "HARD RULE: never propose Culture, cultural fit, or any variant as a "
    "behavioural competency. Cultural fit cannot be assessed accurately from a "
    "single assessment and must not appear in the framework.\n\n"
    "Each entry needs a short name (a skill or behaviour, never a sentence from "
    "the job description), a one-line description of what it measures, and a "
    "required_level stating how strongly THIS JOB needs it: one of exactly "
    f"\"{GRADE_HIGHLY}\", \"{GRADE_MATCHING}\", \"{GRADE_MODERATELY}\". "
    "Do not use numbers, percentages, or any other scale.\n\n"
    "Return JSON {\"competencies\":[{\"category\":\"primary_skill\","
    "\"name\":\"...\",\"description\":\"...\",\"required_level\":\"...\"}]}."
)

_MAX_NAME = 255


def _valid_competency(row: Any) -> bool:
    return (
        isinstance(row, dict)
        and str(row.get("category", "")) in CATEGORIES
        and bool(str(row.get("name", "")).strip())
    )


def _normalise(rows: list[Any]) -> list[dict[str, Any]]:
    """Clean, de-duplicate and cap a generated framework.

    Culture entries are DROPPED here rather than rejected: refusing the whole
    generation because one of eighteen entries was disallowed would send the
    recruiter back to an empty screen for a problem the product can fix itself.
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    per_category: dict[str, int] = {}
    for row in rows:
        if not _valid_competency(row):
            continue
        category = str(row["category"])
        name = str(row["name"]).strip()[:_MAX_NAME]
        if category == CATEGORY_BEHAVIOURAL and is_forbidden_competency(name):
            logger.info("ppi.framework.culture_entry_dropped name=%s", name)
            continue
        key = (category, name.casefold())
        if key in seen:
            continue
        if per_category.get(category, 0) >= MAXIMUM_PER_CATEGORY:
            continue
        seen.add(key)
        per_category[category] = per_category.get(category, 0) + 1
        out.append(
            {
                "category": category,
                "name": name,
                "description": (str(row.get("description") or "").strip() or None),
                "required_level": row.get("required_level"),
            }
        )
    return out


def _top_up(rows: list[dict[str, Any]], job: Job) -> list[dict[str, Any]]:
    """Guarantee the per-category minimum, drawing from the JD fallback."""
    counts = {category: 0 for category in CATEGORIES}
    for row in rows:
        counts[row["category"]] += 1
    if all(count >= MINIMUM_PER_CATEGORY for count in counts.values()):
        return rows
    existing = {(row["category"], row["name"].casefold()) for row in rows}
    for filler in _fallback_framework(job):
        category = filler["category"]
        if counts[category] >= MINIMUM_PER_CATEGORY:
            continue
        key = (category, filler["name"].casefold())
        if key in existing:
            continue
        existing.add(key)
        counts[category] += 1
        rows.append(filler)
    # The fallback offers exactly MINIMUM_PER_CATEGORY names per category, so a
    # single collision with an LLM-generated name leaves the category one short,
    # `framework_is_complete` then refuses the save, and the job is stranded at
    # `questions_pending_review` with no way forward but hand-typing a
    # competency. Close the floor explicitly rather than hoping the two name
    # sets miss each other.
    #
    # Bounded by the shortfall, for the same reason as `_pad` above: a loop that
    # only exits once a generated name happens to be new is a loop that can fail
    # to exit at all.
    for category in CATEGORIES:
        for attempt in range(len(_PADDING_QUALIFIERS)):
            if counts[category] >= MINIMUM_PER_CATEGORY:
                break
            name = f"{job.title} ({_PADDING_QUALIFIERS[attempt]} capability)"[:_MAX_NAME]
            key = (category, name.casefold())
            if key in existing:
                continue
            existing.add(key)
            counts[category] += 1
            rows.append(
                {
                    "category": category,
                    "name": name,
                    "description": (
                        "Placeholder for the hiring team to rename during review: "
                        "a capability this role needs beyond those the job "
                        f"description names for {job.title}."
                    ),
                    "required_level": GRADE_MATCHING,
                }
            )
    return rows


async def generate_framework(
    session: AsyncSession, job: Job, *, replace: bool = False
) -> list[JobCompetency]:
    """Generate the job's PPI framework and leave it AWAITING REVIEW.

    This never approves anything. The framework becomes the job's fixed
    evaluation criteria only when the Hiring Manager saves it
    (`POST /assessments/jobs/{id}/framework/finalize`), which is one half of
    the single manual step in the pipeline (spec §11).

    Idempotent by default: a job that already has competencies keeps them, so a
    Celery redelivery cannot discard a framework a human has already edited.
    """
    existing = (
        await session.execute(
            select(JobCompetency)
            .where(JobCompetency.job_id == job.id)
            .order_by(JobCompetency.ordinal)
        )
    ).scalars().all()
    if existing and not replace:
        return list(existing)

    rows: list[dict[str, Any]] = []
    try:
        raw = await llm_router.chat_completion(
            "jd_generation",
            [
                {"role": "system", "content": _FRAMEWORK_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "title": job.title,
                            "grade": job.assessment_grade,
                            "experience_min_years": job.experience_min_years,
                            "experience_max_years": job.experience_max_years,
                            "jd": job.jd_json,
                            "jd_markdown": (job.jd_markdown or "")[:6000],
                        }
                    ),
                },
            ],
            response_format_json=True,
            session=session,
        )
        rows = _normalise(json.loads(raw).get("competencies", []))
    except Exception:
        logger.warning(
            "ppi.framework.llm_unavailable job_id=%s, using the JD-derived framework",
            job.id,
        )
        rows = []
    if not rows:
        rows = _normalise(_fallback_framework(job))
    rows = _top_up(rows, job)

    # Deactivate rather than delete on `replace`: a competency may already be
    # referenced by a generated candidate question or a written report, and a
    # regenerated framework must not orphan either.
    for row in existing:
        row.is_active = False

    ordinal_by_category = {category: 0 for category in CATEGORIES}
    created: list[JobCompetency] = []
    for row in sorted(rows, key=lambda item: CATEGORIES.index(item["category"])):
        ordinal_by_category[row["category"]] += 1
        created.append(
            JobCompetency(
                tenant_id=job.tenant_id,
                job_id=job.id,
                category=row["category"],
                name=row["name"],
                description=row["description"],
                required_level=required_level_score(row["required_level"]),
                ordinal=ordinal_by_category[row["category"]],
            )
        )
    if not created:
        # THE STAMP IS EVIDENCE, NOT INTENT. Nineteen live jobs carried
        # `framework_generated_at` and had no competency rows, and because every
        # health check in the product asked the stamp rather than the table, the
        # failure was invisible for weeks -- the reminder task even filters on
        # this column being set, so it specifically excluded the jobs that had
        # failed. Leaving it NULL is what lets `reconcile_job_setup` and the
        # setup screen both see that there is work still to do.
        logger.warning(
            "ppi.framework.produced_nothing job_id=%s tenant_id=%s", job.id, job.tenant_id
        )
        await session.flush()
        return []

    session.add_all(created)
    job.framework_generated_at = datetime.now(timezone.utc)
    await session.flush()
    logger.info(
        "ppi.framework.generated job_id=%s primary=%d secondary=%d behavioural=%d",
        job.id,
        *(sum(1 for c in created if c.category == cat) for cat in CATEGORIES),
    )
    return created


async def load_framework(session: AsyncSession, job_id: Any) -> list[JobCompetency]:
    """The job's active framework, in report order (primary, secondary, behavioural)."""
    rows = (
        await session.execute(
            select(JobCompetency)
            .where(JobCompetency.job_id == job_id, JobCompetency.is_active.is_(True))
            .order_by(JobCompetency.ordinal)
        )
    ).scalars().all()
    return sorted(rows, key=lambda row: (CATEGORIES.index(row.category), row.ordinal))


def framework_is_complete(rows: list[JobCompetency]) -> tuple[bool, str | None]:
    """Whether a framework may be saved as the job's fixed criteria.

    Returns (ok, reason). The minimum is a product contract, not a suggestion:
    a job saved with three Primary Skills would produce a radar chart with
    three spokes and reports that are not comparable with the rest of the
    product.
    """
    for category in CATEGORIES:
        count = sum(1 for row in rows if row.category == category and row.is_active)
        if count < MINIMUM_PER_CATEGORY:
            return False, (
                f"{CATEGORY_LABELS[category]} needs at least {MINIMUM_PER_CATEGORY} "
                f"entries before the framework can be saved. There are {count}."
            )
    offending = [
        row.name
        for row in rows
        if row.is_active
        and row.category == CATEGORY_BEHAVIOURAL
        and is_forbidden_competency(row.name)
    ]
    if offending:
        return False, FORBIDDEN_COMPETENCY_DETAIL
    return True, None


# ── Per-candidate question generation (spec §6.4) ────────────────────────────


def _allocate(competencies: list[JobCompetency], total: int) -> list[JobCompetency]:
    """Spread `total` questions across the framework, one per competency first.

    Every competency must be probed at least once -- an unprobed competency
    still gets a grade and a remark in the report, and grading something the
    candidate was never asked about is exactly the kind of unfair output the
    review gate exists to prevent. Remainder goes to Primary Skills first, then
    Behavioural, then Secondary: that is the order in which a hiring decision
    actually leans on the evidence.
    """
    if not competencies:
        return []
    plan = list(competencies[:total])
    if len(plan) >= total:
        return plan
    priority = sorted(
        competencies,
        key=lambda row: (
            {CATEGORY_PRIMARY: 0, CATEGORY_BEHAVIOURAL: 1, CATEGORY_SECONDARY: 2}[row.category],
            row.ordinal,
        ),
    )
    extras = cycle(priority)
    while len(plan) < total:
        plan.append(next(extras))
    return plan


_QUESTION_SYSTEM_PROMPT = (
    "You are writing assessment questions for ONE candidate applying to ONE "
    "job. You are given the job description, the job's fixed evaluation "
    "framework, and this candidate's resume.\n\n"
    "Write exactly one question for each framework entry listed in "
    "`allocation`, in the same order. Each question must probe THAT entry and "
    "must be anchored in something specific from this candidate's own resume "
    "-- a named employer, project, tool, or responsibility -- so it could not "
    "have been asked of a different candidate unchanged.\n\n"
    "Ask for concrete situations and what the candidate personally decided or "
    "did. Do not ask yes/no questions, do not reveal how answers are scored, "
    "and never mention scores, grades, or the framework itself.\n\n"
    "Return JSON {\"questions\":[{\"index\":0,\"prompt\":\"...\"}]} with one "
    "entry per allocation index."
)

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
    """Generate this candidate's PPI questions against the job's saved framework.

    Idempotent: a candidate who already has questions keeps exactly those. Two
    candidates on the same job get DIFFERENT questions probing the SAME
    framework -- that is what makes their reports comparable while keeping each
    conversation relevant to the person in it (spec §6.4).
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

    framework = await load_framework(session, job.id)
    if not framework:
        framework = await generate_framework(session, job)
    grade = grade or job.assessment_grade or "non_managerial"
    allocation = _allocate(framework, ppi_question_count(grade))
    if not allocation:
        return []

    profile = await session.get(Profile, link.profile_id) if link.profile_id else None
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
            "ppi.questions.llm_unavailable link_id=%s, using framework-derived questions",
            link.id,
        )

    rows: list[CandidateQuestion] = []
    seen_prompts: set[str] = set()
    for index, competency in enumerate(allocation):
        prompt = prompts.get(index) or _GENERIC_ANGLES[index % len(_GENERIC_ANGLES)].format(
            name=competency.name
        )
        # A model that repeats itself would collapse several competencies into
        # one probe; fall back to a distinct angle rather than storing a
        # duplicate the candidate would visibly be asked twice.
        if prompt.casefold() in seen_prompts:
            for offset in range(len(_GENERIC_ANGLES)):
                alternative = _GENERIC_ANGLES[(index + offset) % len(_GENERIC_ANGLES)].format(
                    name=competency.name
                )
                if alternative.casefold() not in seen_prompts:
                    prompt = alternative
                    break
        seen_prompts.add(prompt.casefold())
        rows.append(
            CandidateQuestion(
                tenant_id=job.tenant_id,
                job_id=job.id,
                job_candidate_link_id=link.id,
                competency_id=competency.id,
                ordinal=index + 1,
                prompt=prompt,
            )
        )
    session.add_all(rows)
    await session.flush()
    logger.info(
        "ppi.questions.generated link_id=%s grade=%s count=%d", link.id, grade, len(rows)
    )
    return rows


# Import-time integrity checks -- these counts are a product contract (spec §6.1).
assert set(PPI_QUESTION_COUNTS) == {"non_managerial", "managerial", "leadership", "cxo"}
assert set(CATEGORY_LABELS) == set(CATEGORIES)
assert set(REQUIRED_LEVEL_SCORES) <= set(GRADES)
