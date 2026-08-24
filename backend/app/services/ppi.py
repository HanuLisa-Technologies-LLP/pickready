"""ReadyPick Profile Intelligence (PPI) -- the per-job evaluation matrix.

PROPRIETARY: PPI is ReadyPick's own competency framework, derived from
first-principles job analysis. It is NOT modelled on, named after, or derived
from any licensed psychometric instrument, and no such instrument may ever be
referenced in this file, the product UI, or the documentation.

THE THREE ASPECTS (Draft v4)
----------------------------
    must_have     the capabilities the role cannot be performed without. This
                  is where technical depth now lives, folded in from what used
                  to be a standalone Technical Assessment Agent.
    nice_to_have  supporting capabilities that strengthen performance.
    behavioural   observable workplace behaviours the role demands.

They were Primary Skills, Secondary Skills and Behavioural Competencies. The
first two were RENAMED, not replaced: the same criteria under names that say
what they mean in hiring language, which is what makes the hard cap below read
as the obvious rule rather than as an arbitrary weighting.

PPI IS THE SOLE OWNER OF DEPTH
------------------------------
Matching (`services/matching`) evaluates background and logistics from resume
text alone -- coarse, inferred, never verified. PPI evaluates demonstrated
depth and behaviour from a conversation. Same named territory in places, a
different question, and no overlap. Nothing outside PPI assesses skill depth.

TWO THINGS THAT MUST NOT BE CONFUSED
------------------------------------
1. **The matrix is per JOB.** Generated once from the JD and the reporting
   authority's SWOT intake, reviewed and saved by the Hiring Manager, then
   FIXED. Every candidate applying to that job is graded against the same items
   -- that is the only reason two candidates' reports are comparable.
2. **The questions are per CANDIDATE.** Once the matrix is saved, questions
   probing it are generated individually from the JD, the saved matrix, and
   that candidate's own resume. What varies is how an item is approached, never
   which items there are.

NO MINIMUM ITEM COUNT, AND A CEILING THAT IS NOT ARBITRARY
----------------------------------------------------------
Draft v4 removed the old floor of five per aspect: the agent recommends however
many items the job genuinely needs. What replaced it is a CEILING, and it comes
from a rule the product already had -- every item in the matrix is probed at
least once, because an item that is graded and charted without being asked
about is exactly the unfair output the review gate exists to prevent. The grade
therefore bounds the matrix: a matrix cannot hold more items than its grade
allows questions. `matrix_is_complete` refuses a save above that, naming the
number to remove, rather than letting a job reach candidates whose reports would
grade items nobody was asked about.

"Culture" is refused as a Behavioural Competency, at generation and at save.
Cultural fit cannot be assessed accurately in a single conversation, and PPI
does not claim otherwise.
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
from app.models.job_setup import SWOT_AREAS, JobSwotIntake
from app.services import agent_loop, llm_router, swot_intake
from app.prompts import registry
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
    "CATEGORY_MUST_HAVE",
    "CATEGORY_NICE_TO_HAVE",
    "FORBIDDEN_COMPETENCY_TERMS",
    "GRADE_QUESTION_RANGES",
    "REQUIRED_LEVEL_SCORES",
    "RUBRIC_SCORED_CATEGORIES",
    "generate_candidate_questions",
    "generate_framework",
    "framework_is_complete",
    "is_forbidden_competency",
    "load_framework",
    "load_swot",
    "matrix_is_complete",
    "matrix_version",
    "published_matrix",
    "publish_tatva_matrix",
    "verify_matrix_for_consumer",
    "max_questions",
    "min_questions",
    "resolve_question_range",
    "resolve_question_target",
    "conversation_may_close",
    "required_level_score",
    "typical_split",
]

# ── Aspects ──────────────────────────────────────────────────────────────────

CATEGORY_MUST_HAVE = "must_have"
CATEGORY_NICE_TO_HAVE = "nice_to_have"
CATEGORY_BEHAVIOURAL = "behavioural"

#: Ordered exactly as the report renders them (spec §9.3).
CATEGORIES: tuple[str, ...] = (
    CATEGORY_MUST_HAVE,
    CATEGORY_NICE_TO_HAVE,
    CATEGORY_BEHAVIOURAL,
)

CATEGORY_LABELS: dict[str, str] = {
    CATEGORY_MUST_HAVE: "Must-have",
    CATEGORY_NICE_TO_HAVE: "Nice-to-have",
    CATEGORY_BEHAVIOURAL: "Behavioural Competencies",
}

#: The two aspects whose answers are scored against the question's OWN stored
#: rubric. Behavioural is absent deliberately: there is no single correct answer
#: to a behavioural question, so it is scored by judgement (spec §8). One
#: scoring agent, two methods, and this frozenset is which is which.
RUBRIC_SCORED_CATEGORIES: frozenset[str] = frozenset(
    {CATEGORY_MUST_HAVE, CATEGORY_NICE_TO_HAVE}
)

#: Structural floor: one item per aspect. NOT a count contract -- Draft v4
#: deliberately removed those. An aspect with no items still gets a grade, a
#: remark and a radar chart in the report, and there would be nothing behind
#: any of the three.
MINIMUM_PER_CATEGORY = 1


# ── The Culture refusal (spec §5) ────────────────────────────────────────────
# Enforced at THREE layers: the generator is told not to produce it, the save
# handler refuses it, and a Postgres CHECK refuses the row. A prompt instruction
# is a request rather than a guarantee, and the Hiring Manager's Edit control
# can type anything.

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


# ── Question volume by grade (spec §5.4) ─────────────────────────────────────
# A RANGE per grade, resolved ONCE per job at setup from how many items that
# job's matrix actually holds -- not a single fixed number per grade, and not a
# number chosen per candidate.
#
# Note the direction: MORE questions for a junior candidate, fewer for a CXO.
# That is the client's table verbatim and it is deliberate -- a CXO's evidence
# is broader per answer, and their time is the scarce resource.

#: grade -> (minimum total, maximum total)
GRADE_QUESTION_RANGES: dict[str, tuple[int, int]] = {
    "non_managerial": (20, 28),
    "managerial": (16, 22),
    "leadership": (11, 16),
    "cxo": (7, 11),
}

#: grade -> {aspect: (low, high)}. ILLUSTRATIVE, and the spec says so in as many
#: words: "typical, illustrative sub-splits ... not a rigid per-job formula".
#: They are held here because they are the client's stated shape of a balanced
#: interview and they steer the remainder allocation when the matrix has fewer
#: items than the grade's floor. Nothing REFUSES a split that falls outside
#: them; only the grade TOTAL is enforced.
TYPICAL_SPLITS: dict[str, dict[str, tuple[int, int]]] = {
    "non_managerial": {
        CATEGORY_MUST_HAVE: (7, 11),
        CATEGORY_NICE_TO_HAVE: (4, 6),
        CATEGORY_BEHAVIOURAL: (8, 12),
    },
    "managerial": {
        CATEGORY_MUST_HAVE: (5, 8),
        CATEGORY_NICE_TO_HAVE: (3, 5),
        CATEGORY_BEHAVIOURAL: (8, 11),
    },
    "leadership": {
        CATEGORY_MUST_HAVE: (2, 4),
        CATEGORY_NICE_TO_HAVE: (1, 3),
        CATEGORY_BEHAVIOURAL: (7, 10),
    },
    "cxo": {
        CATEGORY_MUST_HAVE: (1, 2),
        CATEGORY_NICE_TO_HAVE: (1, 2),
        CATEGORY_BEHAVIOURAL: (5, 7),
    },
}

DEFAULT_GRADE = "non_managerial"


def _grade(grade: str | None) -> str:
    return grade if grade in GRADE_QUESTION_RANGES else DEFAULT_GRADE


def min_questions(grade: str | None) -> int:
    return GRADE_QUESTION_RANGES[_grade(grade)][0]


def max_questions(grade: str | None) -> int:
    return GRADE_QUESTION_RANGES[_grade(grade)][1]


def typical_split(grade: str | None) -> dict[str, tuple[int, int]]:
    return TYPICAL_SPLITS[_grade(grade)]


def resolve_question_range(grade: str | None, item_count: int) -> tuple[int, int]:
    """The RANGE this job's assessment may run to, decided once by Sutra.

    A RANGE, not a number, and the difference is the whole of the 2026-08-23
    change. Previously setup resolved a single total and the conversation asked
    exactly that many questions, whatever the candidate said. The specification
    splits the decision in two: Sutra "sets the total question-count range for
    the role's candidate assessment, based on how many matrix items exist and
    the role's grade", and Vaada decides "the actual count ... dynamically
    during the conversation itself, based on answer depth and completeness".

    Both halves matter, and they protect different things.

      * The RANGE is per JOB and agent-decided with no manual override, which is
        what keeps two candidates on one job comparable. It is still driven by
        the matrix size clamped into the grade's band, exactly as the single
        target was: a matrix with more items than the grade's floor gets one
        question per item so every item the report grades was actually probed,
        and a smaller matrix still asks the grade's minimum rather than becoming
        a four-question interview.
      * The FLOOR is what stops the dynamic half from becoming a way to end an
        assessment early. Vaada may stop when it has sufficient evidence across
        every dimension, and never before this many base questions, so a
        candidate who writes three confident paragraphs is not assessed on less
        than a candidate who writes one.

    Above the grade's ceiling the answer is still not to truncate:
    `matrix_is_complete` refuses the save, because silently dropping items would
    grade a candidate on criteria nobody asked them about.
    """
    low, high = GRADE_QUESTION_RANGES[_grade(grade)]
    resolved = max(low, min(high, int(item_count)))
    return low, resolved


def resolve_question_target(grade: str | None, item_count: int) -> int:
    """The CEILING of the range, i.e. how many questions are written up front.

    Retained under its original name and still stamped onto `job.question_target`
    because a persisted column, an API field and a shipped client all read it.
    What changed is what it MEANS: it used to be the number of questions the
    conversation would ask, and it is now the most it may ask. Vaada stops at or
    before it (`conversation_may_close`).

    Questions are generated to the ceiling rather than to the floor on purpose.
    Generation happens once, before the candidate starts; writing only the floor
    would mean a conversation that legitimately needs more evidence has no
    further prompts to reach for, and the fallback would be a question written
    mid-turn with no rubric behind it.
    """
    return resolve_question_range(grade, item_count)[1]


def conversation_may_close(
    *,
    grade: str | None,
    asked: int,
    total_written: int,
    covered_dimensions: int,
    total_dimensions: int,
) -> bool:
    """Vaada's stopping decision: has enough been gathered, and may it stop yet?

    Three conditions, and each is refusing a different failure:

      * `asked >= floor` -- never stop below the grade's minimum. Without it,
        the dynamic half becomes a way for a fluent candidate to be assessed on
        fewer criteria than a hesitant one, and two reports on the same job stop
        being comparable, which is the one property the matrix exists to give.
      * every dimension covered -- the spec's own stopping rule is "sufficient
        evidence has been gathered across all matrix dimensions". A dimension
        with no evidence is not a dimension that scored badly; it is one nobody
        asked about, and the report must never present the two as the same
        thing.
      * `asked < total_written` -- there is somewhere left to go. Running out of
        written prompts closes the conversation regardless, and that is handled
        by the caller; this function answers the EARLY-stop question only.

    Deterministic and calls no model, for the reason every guard in this
    codebase does: the moment it matters most is the moment the provider is
    down. A model asked "have you gathered enough?" mid-outage returns nothing,
    and the safe direction on no answer must be "keep asking", not "stop".
    """
    floor = min_questions(grade)
    if asked < floor:
        return False
    if total_dimensions <= 0:
        return False
    if covered_dimensions < total_dimensions:
        return False
    return asked < total_written


# ── Required level ───────────────────────────────────────────────────────────
# The radar plots TWO shapes: what the job needs and what the candidate showed
# (spec §9.4). The job's shape comes from a required level the matrix agent
# assigns to each item, stated as one of the same four grade WORDS the client
# already reads -- never a number, at generation or at display.
#
# It is stored as the band's representative internal score purely so it shares
# the column type and the grade projection with the candidate's score; nothing
# reads it as a number outside this module and `rating.grade_for_percent`.

REQUIRED_LEVEL_SCORES: dict[str, int] = {
    GRADE_HIGHLY: 95,
    GRADE_MATCHING: 82,
    GRADE_MODERATELY: 67,
}

#: A job that requires NOTHING of an item would not have it in its matrix, so
#: "Not Matching" is not an offered requirement level. An unrecognised value
#: settles on the middle band rather than raising.
DEFAULT_REQUIRED_LEVEL = REQUIRED_LEVEL_SCORES[GRADE_MATCHING]


def required_level_score(label: Any) -> int:
    return REQUIRED_LEVEL_SCORES.get(str(label).strip(), DEFAULT_REQUIRED_LEVEL)


# ── Deterministic fallback matrix ────────────────────────────────────────────
# CLAUDE.md rule 9: degrade, never crash. With the whole LLM chain down, a job
# still gets a usable matrix built from its own JD, and the Hiring Manager can
# edit it -- which is the review step the workflow already requires.

_FALLBACK_BEHAVIOURAL: tuple[tuple[str, str], ...] = (
    ("Ownership", "Sees committed work through to a finished, verified outcome."),
    ("Communication", "Explains decisions and trade-offs clearly to the people affected."),
    ("Collaboration", "Works effectively across roles and asks for help at the right moment."),
    ("Problem solving", "Breaks an unfamiliar problem down and reasons to a defensible answer."),
    ("Adaptability", "Adjusts approach when priorities, constraints or information change."),
    ("Judgement", "Weighs incomplete evidence and commits to a decision they can defend."),
)

#: How many items the deterministic fallback builds per aspect. Small on
#: purpose: this is the matrix a human is about to review, and a short honest
#: matrix drawn from the JD's own words is more useful to review than a padded
#: one full of mechanically derived names.
_FALLBACK_PER_CATEGORY = 4


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


def _captured_points(swot: JobSwotIntake | dict[str, Any] | None) -> dict[str, list]:
    """The four quadrants, whether they arrived as an artifact or as ORM rows.

    Sutra normally reads Bodha's published artifact, and falls back to the rows
    for a job whose SWOT predates the artifact layer. Both shapes are accepted
    HERE, once, rather than at each of the three call sites: a per-call
    `getattr` would silently return nothing for a Mapping, and "nothing" is
    indistinguishable from "the authority listed no strengths".
    """
    if swot is None:
        return {}
    if isinstance(swot, dict):
        return {area: list(swot.get(area) or []) for area in SWOT_AREAS}
    return swot.captured()


def _swot_terms(swot: JobSwotIntake | dict[str, Any] | None) -> list[str]:
    """Short labels mined from the SWOT intake, for the offline fallback.

    The fallback has no model, so it cannot reason about what the authority
    said -- but their own phrases are still better raw material for a matrix a
    human is about to edit than the JD alone, which is the input the SWOT was
    collected to supplement.
    """
    captured = _captured_points(swot)
    if not captured:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for area in ("strengths", "weaknesses"):
        for item in captured.get(area) or []:
            label = str(item).strip()
            if not label:
                continue
            label = re.split(r"[,;:.]", label, maxsplit=1)[0].strip()[:120]
            if label and label.casefold() not in seen:
                seen.add(label.casefold())
                out.append(label[:1].upper() + label[1:])
    return out


def _fallback_framework(
    job: Job, swot: JobSwotIntake | dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """A matrix built from the job's own words, with no network call.

    No padding loop, and that is the change Draft v4 allowed. The old fallback
    had to reach a floor of five per aspect and manufactured names like
    "Kafka (further core)" to get there -- filler that landed on the one screen
    a human is required to review. With no minimum to hit, the fallback returns
    what the JD and SWOT actually support and stops.
    """
    pool = _swot_terms(swot) + _jd_terms(job, "skills")
    topics = _jd_terms(job, "responsibilities") + _jd_terms(job, "accountabilities")
    seen = {term.casefold() for term in pool}
    pool += [topic for topic in topics if topic.casefold() not in seen]
    if not pool:
        pool = [job.title or "this role"]

    must = pool[:_FALLBACK_PER_CATEGORY]
    nice = pool[_FALLBACK_PER_CATEGORY : _FALLBACK_PER_CATEGORY * 2]
    rows: list[dict[str, Any]] = []
    for name in must:
        rows.append(
            {
                "category": CATEGORY_MUST_HAVE,
                "name": name,
                "description": f"Core capability the job description names as required: {name}.",
                "required_level": GRADE_HIGHLY,
            }
        )
    for name in nice:
        rows.append(
            {
                "category": CATEGORY_NICE_TO_HAVE,
                "name": name,
                "description": f"Supporting capability that strengthens delivery of the role: {name}.",
                "required_level": GRADE_MATCHING,
            }
        )
    if not nice:
        # Every aspect needs at least one item or the report grades an empty
        # section. Derived from the role rather than duplicated from must_have,
        # so the Hiring Manager sees a placeholder to rename and not a repeat.
        rows.append(
            {
                "category": CATEGORY_NICE_TO_HAVE,
                "name": f"Adjacent experience for {job.title or 'this role'}"[:_MAX_NAME],
                "description": (
                    "Placeholder for the hiring team to rename during review: a "
                    "supporting capability beyond those the job description names."
                ),
                "required_level": GRADE_MODERATELY,
            }
        )
    for name, description in _FALLBACK_BEHAVIOURAL[:_FALLBACK_PER_CATEGORY]:
        rows.append(
            {
                "category": CATEGORY_BEHAVIOURAL,
                "name": name,
                "description": description,
                "required_level": GRADE_MATCHING,
            }
        )
    return rows


_MAX_NAME = 255


def _maximum_total(job: Job) -> int:
    """The most items this job's matrix may hold: one question each, at most."""
    return max_questions(job.assessment_grade)


def _framework_system_prompt(job: Job) -> str:
    """Rendered per job, because the ceiling is a property of the job's grade.

    Rendered at call time rather than at import: the old module-level constant
    could not carry a value that varies per job, and a ceiling stated in the
    prompt is what stops a generation the save handler would then have to
    refuse.
    """
    return registry.render(
        "ppi_framework_system",
        maximum_total=_maximum_total(job),
        grade_highly=GRADE_HIGHLY,
        grade_matching=GRADE_MATCHING,
        grade_moderately=GRADE_MODERATELY,
    )


def _valid_competency(row: Any) -> bool:
    return (
        isinstance(row, dict)
        and str(row.get("category", "")) in CATEGORIES
        and bool(str(row.get("name", "")).strip())
    )


def _normalise(rows: list[Any], *, maximum_total: int) -> list[dict[str, Any]]:
    """Clean, de-duplicate and cap a generated matrix.

    Culture entries are DROPPED here rather than rejected: refusing a whole
    generation because one entry was disallowed would send the recruiter back to
    an empty screen for a problem the product can fix itself.

    The cap is on the TOTAL rather than per aspect, because that is the bound
    that actually matters -- it is the grade's question ceiling, and every item
    kept must be askable. Items are taken in the order the model returned them,
    which is the order it ranked them.
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        if len(out) >= maximum_total:
            break
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
        seen.add(key)
        out.append(
            {
                "category": category,
                "name": name,
                "description": (str(row.get("description") or "").strip() or None),
                "required_level": row.get("required_level"),
            }
        )
    return out


def _ensure_every_aspect(
    rows: list[dict[str, Any]], job: Job, swot: JobSwotIntake | dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Guarantee one item per aspect, drawing from the JD fallback.

    This is the whole of what remains of the old `_top_up`. It closes a
    structural hole, not a count: an aspect with no items still renders a grade,
    a remark and a radar chart, and there would be nothing behind any of them.
    """
    present = {row["category"] for row in rows}
    missing = [category for category in CATEGORIES if category not in present]
    if not missing:
        return rows
    existing = {(row["category"], row["name"].casefold()) for row in rows}
    for filler in _fallback_framework(job, swot):
        if filler["category"] not in missing:
            continue
        key = (filler["category"], filler["name"].casefold())
        if key in existing:
            continue
        existing.add(key)
        missing.remove(filler["category"])
        rows.append(filler)
        if not missing:
            break
    for category in list(missing):
        rows.append(
            {
                "category": category,
                "name": f"{job.title or 'This role'} ({CATEGORY_LABELS[category].lower()})"[:_MAX_NAME],
                "description": (
                    "Placeholder for the hiring team to rename during review: a "
                    "capability this role needs beyond those the job description "
                    "names."
                ),
                "required_level": GRADE_MATCHING,
            }
        )
    return rows


async def load_swot(session: AsyncSession, job_id: Any) -> JobSwotIntake | None:
    return (
        await session.execute(
            select(JobSwotIntake).where(JobSwotIntake.job_id == job_id)
        )
    ).scalars().first()


#: What `_consume_swot_evidence` says it read. Recorded on the log line rather
#: than inferred later, because "the matrix was built from the artifact" and
#: "the matrix was built from the rows because the artifact was refused" produce
#: identical matrices and must not be identical in the record.
SWOT_FROM_ARTIFACT = "artifact"
SWOT_FROM_INTAKE_ROWS = "intake_rows"
SWOT_ABSENT = "absent"


async def _consume_swot_evidence(
    session: AsyncSession, job: Job
) -> tuple[dict[str, Any] | None, str]:
    """Sutra reads Bodha's SWOT evidence, verifying it before it reads it.

    The artifact is the intended path (spec §4): it is typed, scoped and
    verified, so Sutra can tell a real intake from one that degraded, which
    reading the rows directly cannot.

    THE ROW PATH IS A FALLBACK AND MUST STAY ONE. Every job that existed before
    the artifact layer was wired has an intake and no artifact, and there are
    live jobs in that state right now. Refusing to generate a matrix without an
    artifact would strand each of them at `questions_pending_review` with no way
    forward, which is exactly the failure nineteen jobs were already found in on
    2026-08-06 -- a setup step that could not complete and no screen that said
    so. So a missing or refused artifact costs the verification and nothing
    else, and the reason is written on the log line.
    """
    from app.services.agents import artifacts, envelope as run_envelope, gates, identity  # noqa: PLC0415
    try:
        artifact = await swot_intake.published_evidence(session, job)
    except Exception:
        logger.warning("ppi.swot_artifact_unavailable job_id=%s", job.id, exc_info=True)
        artifact = None

    if artifact is not None:
        verdict = artifacts.verify_for_consumer(
            artifact,
            identity.SUTRA,
            tenant_id=str(job.tenant_id),
            job_id=str(job.id),
        )
        if verdict.passed:
            return {area: list(artifact.payload.get(area) or []) for area in SWOT_AREAS}, (
                SWOT_FROM_ARTIFACT
            )
        logger.warning(
            "ppi.swot_artifact_refused job_id=%s issues=%s",
            job.id,
            [finding.issue for finding in verdict.findings],
        )

    swot = await load_swot(session, job.id)
    if swot is None:
        return None, SWOT_ABSENT
    return swot.captured(), SWOT_FROM_INTAKE_ROWS


async def generate_framework(
    session: AsyncSession, job: Job, *, replace: bool = False
) -> list[JobCompetency]:
    """Generate the job's PPI matrix and leave it AWAITING REVIEW.

    Generated from the JD **and** the reporting authority's SWOT intake
    (spec §5.2). The intake is not a hard gate here: a job whose SWOT is missing
    still gets a matrix from the JD alone rather than being stranded with none,
    and the setup screen is what tells the team the intake is outstanding. What
    IS gated is the job reaching candidates, which `_refresh_setup_status`
    holds until both halves of the setup session are finalised.

    This never approves anything. The matrix becomes the job's fixed evaluation
    criteria only when the Hiring Manager saves it (spec §5.3).

    Idempotent by default: a job that already has items keeps them, so a Celery
    redelivery cannot discard a matrix a human has already edited.
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

    # Bodha's evidence, verified before it is read. `swot` is the four
    # quadrants as a plain mapping from here down, whichever path produced them,
    # so nothing below can tell an artifact from a row -- and nothing below
    # should: the matrix is built from what the authority said, not from how it
    # travelled.
    swot, swot_origin = await _consume_swot_evidence(session, job)
    maximum_total = _maximum_total(job)
    payload = json.dumps(
        {
            "title": job.title,
            "grade": job.assessment_grade,
            "experience_min_years": job.experience_min_years,
            "experience_max_years": job.experience_max_years,
            "jd": job.jd_json,
            "jd_markdown": (job.jd_markdown or "")[:6000],
            "swot_intake": swot,
        }
    )
    system_prompt = _framework_system_prompt(job)

    async def _execute(reflection: str) -> list[dict[str, Any]]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": payload},
        ]
        if reflection:
            messages.append({"role": "user", "content": reflection})
        raw = await llm_router.chat_completion(
            "jd_generation", messages, response_format_json=True, session=session
        )
        return _normalise(
            json.loads(raw).get("competencies", []), maximum_total=maximum_total
        )

    def _evaluate(candidate: list[dict[str, Any]]) -> agent_loop.Critique:
        """Deterministic criteria. The count is NOT one of them.

        Draft v4 removed the per-aspect minimum, so "you returned three and I
        need five" is no longer a defect -- three may be the right answer for
        the job. What remains checkable is structural: every aspect has to be
        represented, or the report grades an empty section.
        """
        present = {row["category"] for row in candidate}
        missing = [
            CATEGORY_LABELS[category]
            for category in CATEGORIES
            if category not in present
        ]
        if missing:
            return agent_loop.reject(
                "return at least one item in each aspect; these came back empty: "
                + ", ".join(missing)
            )
        return agent_loop.ok()

    result = await agent_loop.run_loop(
        name="ppi_framework",
        execute=_execute,
        evaluate=_evaluate,
        # The JD-derived matrix, which needs no network at all. This is what
        # repaired 19 stranded live jobs on 2026-08-06 with every provider down.
        fallback=_normalise(_fallback_framework(job, swot), maximum_total=maximum_total),
        max_attempts=agent_loop.BACKGROUND_ATTEMPTS,
        deadline_seconds=agent_loop.BACKGROUND_DEADLINE,
        max_generated_tokens=agent_loop.BACKGROUND_TOKEN_BUDGET,
    )
    if result.degraded:
        logger.warning(
            "ppi.framework.degraded job_id=%s attempts=%d reasons=%s",
            job.id, result.attempts, list(result.reasons),
        )
    rows = result.value
    if not rows:
        rows = _normalise(_fallback_framework(job, swot), maximum_total=maximum_total)
    rows = _ensure_every_aspect(rows, job, swot)

    # Deactivate rather than delete on `replace`: an item may already be
    # referenced by a generated candidate question or a written report, and a
    # regenerated matrix must not orphan either.
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
    # Resolved here and not per candidate: every candidate on this job is asked
    # the same NUMBER of questions, which is part of what makes two reports
    # comparable. Recomputed on a regenerate because the matrix size moved.
    job.question_target = resolve_question_target(job.assessment_grade, len(created))
    await session.flush()
    logger.info(
        "ppi.framework.generated job_id=%s must_have=%d nice_to_have=%d "
        "behavioural=%d target=%d swot=%s swot_origin=%s",
        job.id,
        *(sum(1 for c in created if c.category == cat) for cat in CATEGORIES),
        job.question_target,
        bool(swot) and any(swot.values()),
        swot_origin,
    )
    # Sutra's hand-off to Yukti, Vaada, Miti and Siddhi (spec §5). Published
    # LAST, after the rows and both stamps are flushed, so a contract bug in the
    # artifact layer cannot cost a job the matrix it just generated -- the same
    # ordering the SWOT publish uses, and for the same reason.
    publish_tatva_matrix(job, created, version=_next_matrix_version(existing))
    return created


async def load_framework(session: AsyncSession, job_id: Any) -> list[JobCompetency]:
    """The job's active matrix, in report order (must-have, nice-to-have, behavioural)."""
    rows = (
        await session.execute(
            select(JobCompetency)
            .where(JobCompetency.job_id == job_id, JobCompetency.is_active.is_(True))
            .order_by(JobCompetency.ordinal)
        )
    ).scalars().all()
    return sorted(rows, key=lambda row: (CATEGORIES.index(row.category), row.ordinal))


# ── Sutra publishes the matrix (spec §5) ─────────────────────────────────────
#
# WHAT THE VERSION IS, AND WHY IT IS NOT A COLUMN
# -----------------------------------------------
# A regeneration DEACTIVATES the previous rows and inserts a new set in one
# transaction, so `job_competencies` already records every generation this job
# has had -- one batch of rows per generation, each batch sharing the
# transaction's `created_at`. Counting batches is therefore reading the version
# that already exists rather than maintaining a second one beside it. A counter
# column would be a number somebody has to remember to increment, and the
# failure when they forget is the one this whole hand-off exists to prevent: a
# consumer using criteria it believes are current.
#
# It is frozen once the matrix is HM-locked because a locked matrix cannot be
# regenerated -- `generate_framework` is idempotent, and reopening is refused
# once anyone has been assessed. No new batch means no new version.


def matrix_version(rows: list[JobCompetency]) -> int:
    """This job's matrix version, counted from the generations behind it.

    `rows` must be EVERY competency row for the job, active and inactive. Handed
    only the active ones this returns 1 forever, because a regeneration
    deactivates rather than deletes -- which is precisely the stale-version
    reading a consumer must never make silently.
    """
    batches = {row.created_at for row in rows if row.created_at is not None}
    return max(1, len(batches))


def _next_matrix_version(existing: list[JobCompetency]) -> int:
    """The version the batch about to be written will carry.

    Computed from the PREVIOUS rows rather than from the new ones, because the
    new ones get their `created_at` from a server default that this process has
    not read back yet: counting them would silently fail to increment, and an
    unchanged version on changed criteria is the one thing a consumer cannot
    detect for itself.
    """
    return matrix_version(existing) + (1 if existing else 0)


async def _all_competencies(session: AsyncSession, job_id: Any) -> list[JobCompetency]:
    """Every generation's rows, which is what `matrix_version` has to count."""
    return list(
        (
            await session.execute(
                select(JobCompetency)
                .where(JobCompetency.job_id == job_id)
                .order_by(JobCompetency.ordinal)
            )
        ).scalars().all()
    )


def _requirement_word(required_level: Any) -> str:
    """The required level as a WORD.

    An integer here would be a number crossing an agent boundary on its way
    towards a report, and the point at which it stops being convertible is the
    point at which somebody renders it. The internal score exists so the radar
    has a radius; nothing downstream of this artifact needs it.
    """
    for label, score in REQUIRED_LEVEL_SCORES.items():
        if score == required_level:
            return label
    return GRADE_MATCHING


def _matrix_item(row: JobCompetency) -> dict[str, Any]:
    return {
        "competency_id": str(row.id),
        "name": row.name,
        "description": row.description or "",
        # The criterion an answer is measured against at MATRIX level. The
        # per-question rubric bands are written with each question and belong to
        # that question, not here: a rubric copied onto the matrix would be a
        # second copy that drifts from the one the scorer actually reads.
        "rubric": row.description or "",
        "required_level": _requirement_word(row.required_level),
        "evidence_expectation": (
            "At least one answer in the candidate's own words describing what "
            "they did, in what context, and what resulted."
        ),
        "ordinal": row.ordinal,
    }


def _matrix_payload(
    job: Job, rows: list[JobCompetency], *, version: int, locked: bool
) -> dict[str, Any]:
    from app.services.agents import artifacts, envelope as run_envelope, gates, identity  # noqa: PLC0415
    by_category = {
        category: [_matrix_item(row) for row in rows if row.category == category]
        for category in CATEGORIES
    }
    minimum, maximum = resolve_question_range(job.assessment_grade, len(rows))
    return {
        **by_category,
        "coverage": {
            category: len(items) for category, items in by_category.items()
        },
        # The RANGE, not a number of questions to ask. Sutra decides the band and
        # Vaada decides where inside it a conversation stops; publishing a single
        # figure would hand a consumer the old pre-2026-08-23 contract under the
        # new name.
        "question_count_range": {"minimum": minimum, "maximum": maximum},
        "grade": job.assessment_grade,
        "version": version,
        "locked": locked,
        "jd_version": swot_intake.jd_version(job),
        "provenance": {
            "producer": identity.SUTRA,
            "job_id": str(job.id),
            "generated_at": (
                job.framework_generated_at.isoformat()
                if job.framework_generated_at
                else None
            ),
            "approved_at": (
                job.framework_approved_at.isoformat()
                if job.framework_approved_at
                else None
            ),
        },
        # What `sutra_gate` compares. The JD's critical requirements are the
        # Must-have names themselves: this product has no separate list of what
        # the JD demanded, and inventing one from JD text here would be a second
        # extraction nobody reviews. Stated plainly rather than left out, so the
        # gate is measuring something real rather than an empty set.
        "critical_requirements": [item["name"] for item in by_category[CATEGORY_MUST_HAVE]],
        "covered_requirements": [
            item["name"] for items in by_category.values() for item in items
        ],
    }


def publish_tatva_matrix(
    job: Job,
    rows: list[JobCompetency],
    *,
    version: int,
    correlation_id: str | None = None,
) -> artifacts.Artifact | None:
    """Run Sutra's gate, then publish the `tatva_matrix` artifact.

    Returns None rather than raising, for the same reason Bodha's publish does:
    this runs inside job setup, which is the step that gates a job reaching
    candidates at all. A publish failure here must cost the hand-off and never
    the matrix -- the rows and the stamps are already flushed by the time this
    is called, and a raised exception would roll back a generation that
    succeeded.

    LOCKED IS DERIVED FROM THE JOB, NOT PASSED IN. `framework_approved_at` is
    stamped by the review handler, and a caller that could assert "locked" for
    itself could publish a mutable matrix as immutable, which is the one claim a
    consumer has no way to check.
    """
    from app.services.agents import artifacts, envelope as run_envelope, gates, identity  # noqa: PLC0415
    if not rows:
        return None
    try:
        locked = job.framework_approved_at is not None
        payload = _matrix_payload(job, rows, version=version, locked=locked)
        verdict = gates.run_gate(identity.SUTRA, payload)
        envelope = run_envelope.Envelope.for_run(
            tenant_id=str(job.tenant_id),
            agent_id=identity.SUTRA,
            task_type="jd_generation",
            interactive=False,
            job_id=str(job.id),
            workflow_id=correlation_id,
            context_version=str(version),
        )
        payload["correlation_id"] = envelope.workflow_id
        artifact = artifacts.publish(
            producer=identity.SUTRA,
            artifact_type="tatva_matrix",
            payload=payload,
            tenant_id=str(job.tenant_id),
            job_id=str(job.id),
            version=version,
            # A locked matrix is what makes two reports on one job comparable,
            # so it is published under the frozen status rather than merely
            # flagged: `verify_for_consumer` reads status, and a flag it does not
            # read is a flag nobody enforces.
            status=(
                artifacts.STATUS_LOCKED if locked else artifacts.STATUS_PUBLISHED
            ),
            source_refs=tuple(f"job_competencies:{row.id}" for row in rows),
            validated=verdict.passed,
        )
        logger.info(
            "ppi.matrix_artifact_published job_id=%s artifact_id=%s version=%d "
            "locked=%s validated=%s",
            job.id,
            artifact.artifact_id,
            version,
            locked,
            verdict.passed,
        )
        return artifact
    except Exception:
        logger.warning(
            "ppi.matrix_artifact_publish_failed job_id=%s", job.id, exc_info=True
        )
        return None


async def published_matrix(
    session: AsyncSession, job: Job, *, correlation_id: str | None = None
) -> artifacts.Artifact | None:
    """The job's current matrix as a verifiable artifact, or None if it has none.

    The entry point Yukti, Vaada, Miti and Siddhi use. It reads the version from
    EVERY generation's rows and publishes only the active ones, which is the
    pairing that makes a stale read detectable: the payload is what a consumer
    would grade against, and the version is how many times that payload has been
    replaced.
    """
    from app.services.agents import artifacts, envelope as run_envelope, gates, identity  # noqa: PLC0415
    active = await load_framework(session, job.id)
    if not active:
        return None
    version = matrix_version(await _all_competencies(session, job.id))
    return publish_tatva_matrix(
        job, active, version=version, correlation_id=correlation_id
    )


def verify_matrix_for_consumer(
    artifact: artifacts.Artifact,
    consumer_id: str,
    *,
    tenant_id: str,
    job_id: str,
    expected_version: int | None = None,
) -> verification.Verdict:
    """`verify_for_consumer` plus the check only Sutra can state.

    THE FAILURE THIS PREVENTS. A candidate is assessed against matrix version 2
    while the report is synthesised against version 1, and every grade in that
    report is stated against criteria the candidate was never asked about. Both
    versions verify perfectly on their own -- right tenant, right job, right
    producer, published -- so nothing in the generic envelope check can see it.
    It is HIGH because the report is immutable: by the time anyone notices, the
    only remedy is re-running an assessment that cannot be re-run.

    A frozen matrix that arrives unfrozen is the same defect one step earlier.
    `expected_version` is compared arithmetically, never inferred, and a
    consumer that does not know which version it wants passes None and gets the
    envelope check alone rather than a check that quietly always passes.
    """
    # Imported HERE, not at module scope. Importing the verification
    # package runs its __init__, which eagerly pulls in every critic --
    # and `ppi_report` imports `functional_assessment`, which imports
    # `gap_analysis`, which imports this module. A module-level import
    # therefore closes a cycle that fails as `partially initialized
    # module` and only under some import orders, so the full suite stays
    # green while one test file goes red.
    from app.services.verification import base as verification  # noqa: PLC0415
    from app.services.agents import artifacts, envelope as run_envelope, gates, identity  # noqa: PLC0415
    findings = list(
        artifacts.verify_for_consumer(
            artifact, consumer_id, tenant_id=tenant_id, job_id=job_id
        ).findings
    )
    if expected_version is not None and int(artifact.version) != int(expected_version):
        findings.append(
            verification.high(
                "matrix_version_mismatch",
                "tatva_matrix.version",
                f"the consumer expects version {int(expected_version)} and the "
                f"artifact carries version {int(artifact.version)}",
                "Reload the job's current matrix; never grade against a version "
                "the candidate was not assessed on.",
            )
        )
    payload_version = artifact.payload.get("version")
    if payload_version is not None and int(payload_version) != int(artifact.version):
        findings.append(
            verification.high(
                "matrix_version_disagreement",
                "tatva_matrix.payload.version",
                f"the envelope says version {int(artifact.version)} and the payload "
                f"says {int(payload_version)}",
                "Republish the matrix; an artifact that disagrees with itself "
                "cannot establish which criteria were used.",
            )
        )
    if artifact.payload.get("locked") and artifact.status != artifacts.STATUS_LOCKED:
        findings.append(
            verification.high(
                "locked_matrix_published_mutable",
                "tatva_matrix.status",
                "the payload states the matrix is HM-locked and the envelope does not",
                "Republish the locked matrix under the frozen status.",
            )
        )
    return verification.verdict(f"a2a:{artifact.artifact_type}", findings)


def matrix_is_complete(
    rows: list[JobCompetency], grade: str | None
) -> tuple[bool, str | None]:
    """Whether a matrix may be saved as the job's fixed criteria.

    Two rules, and neither is a count contract in the sense the old floor of
    five was:

      * every aspect carries at least one item, because each aspect is graded,
        remarked and charted in every report written against this job;
      * the matrix holds no more items than the grade allows questions, because
        every item is probed at least once and an item nobody was asked about
        must never be graded.

    The ceiling names the number to remove rather than truncating silently. The
    Hiring Manager is looking at the matrix when this refusal arrives and is the
    right person to choose which items go.
    """
    active = [row for row in rows if row.is_active]
    for category in CATEGORIES:
        if not any(row.category == category for row in active):
            return False, (
                f"{CATEGORY_LABELS[category]} has no items. Every aspect is graded "
                "and charted on each candidate's report, so each one needs at "
                "least one item before the matrix can be saved."
            )
    ceiling = max_questions(grade)
    if len(active) > ceiling:
        surplus = len(active) - ceiling
        return False, (
            f"This matrix holds {len(active)} items and a "
            f"{(grade or DEFAULT_GRADE).replace('_', '-')} assessment asks at most "
            f"{ceiling} questions. Every item is probed at least once, so please "
            f"remove {surplus} item{'s' if surplus != 1 else ''} before saving."
        )
    offending = [
        row.name
        for row in active
        if row.category == CATEGORY_BEHAVIOURAL and is_forbidden_competency(row.name)
    ]
    if offending:
        return False, FORBIDDEN_COMPETENCY_DETAIL
    return True, None


def framework_is_complete(
    rows: list[JobCompetency], grade: str | None = None
) -> tuple[bool, str | None]:
    """Deprecated spelling of `matrix_is_complete`, kept for one release.

    The routes and the workers were renamed together; this survives because the
    name appears in tests and in the setup screen's error path, and a rename is
    not worth a broken import on a rolling deploy.
    """
    return matrix_is_complete(rows, grade)


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

    framework = await load_framework(session, job.id)
    if not framework:
        framework = await generate_framework(session, job)
    grade = grade or job.assessment_grade or DEFAULT_GRADE
    # The job's resolved target, not a per-candidate decision. Falls back to
    # resolving it now for a job whose matrix predates `question_target`.
    total = job.question_target or resolve_question_target(grade, len(framework))
    allocation = _allocate(framework, total, grade)
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
            "ppi.questions.llm_unavailable link_id=%s, using matrix-derived questions",
            link.id,
        )

    rows: list[CandidateQuestion] = []
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


# Import-time integrity checks -- these ranges are a product contract (spec §5.4).
assert set(GRADE_QUESTION_RANGES) == {"non_managerial", "managerial", "leadership", "cxo"}
assert set(TYPICAL_SPLITS) == set(GRADE_QUESTION_RANGES)
assert all(low <= high for low, high in GRADE_QUESTION_RANGES.values())
assert set(CATEGORY_LABELS) == set(CATEGORIES)
assert RUBRIC_SCORED_CATEGORIES < set(CATEGORIES)
assert set(REQUIRED_LEVEL_SCORES) <= set(GRADES)
# Every grade's typical split must be able to sit inside its own total range, or
# the illustrative shape would contradict the rule that is actually enforced.
assert all(
    sum(low for low, _ in split.values()) <= GRADE_QUESTION_RANGES[grade][1]
    and sum(high for _, high in split.values()) >= GRADE_QUESTION_RANGES[grade][0]
    for grade, split in TYPICAL_SPLITS.items()
)
