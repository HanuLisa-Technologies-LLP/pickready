"""LangGraph orchestration for the PickReady PPI Assessment Report.

TWO scoring processes fan out in parallel from one unified transcript and join
at report synthesis (spec §9). Both consume the ACTUAL candidate answers, keyed
by the `question_key` stamped on every message:

  technical  -> str(TechnicalQuestion.id)      scored against that question's rubric
  ppi        -> str(JobCompetency.id)          scored against the job's framework

There is no third scorer. Validation stopped being an agent on 2026-07-30: it is
six mandatory fields on the application form, and it flows from
`job_candidate_links.validation_json` straight into the report with nothing
scoring, interpreting or judging it (spec §7).

What the client sees, in order (spec §10.3):

  AI Score            the pre-assessment resume snapshot, four matching
                      parameters, 25-30 word remarks
  Overall Assessment  grade + 45-50 word remark + overall radar
  Primary Skills      grade + 45-50 word remark each + radar
  Secondary Skills    same
  Behavioural         same
  Validation          the application fields, verbatim, unrated
  Suggested questions 8-10, anchored on whatever graded Moderately Matching or
                      Not Matching

Every grade is one of four WORDS (services/rating). No number reaches a client
from this module.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from itertools import cycle
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import (
    AssessmentConversation,
    AssessmentMessage,
    CandidateQuestion,
    FunctionalSkillsReport,
    JobCompetency,
    ReportDimension,
    TechnicalQuestion,
)
from app.models.candidate import JobCandidateLink, Profile
from app.models.job import Job
from app.services import answer_quality, llm_router, ppi
from app.services.application_validation import MANDATORY_KEYS, VALIDATION_FIELDS
from app.services.rating import (
    GRADES,
    MODERATE_OR_BELOW,
    PROBE_THRESHOLD,
    band_index_for,
    grade_for_percent,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CATEGORY_TECHNICAL",
    "GRADES",
    "MATCHING_DIMENSIONS",
    "PPI_QUESTION_COUNTS",
    "RADAR_BANDS",
    "REPORT_CATEGORIES",
    "TECHNICAL_QUESTION_COUNTS",
    "assessment_graph",
    "band_index_for",
    "build_radar_charts",
    "generate_question_bank",
    "infer_grade",
    "infer_grade_fallback",
    "rating_label",
    "run_assessment",
    "technical_question_count",
    "word_count",
]

# Technical question count per grade (spec §5). Unchanged by the PPI release.
TECHNICAL_QUESTION_COUNTS: dict[str, int] = {
    "non_managerial": 20,
    "managerial": 17,
    "leadership": 15,
    "cxo": 12,
}

#: Re-exported so callers have one import for both halves of the conversation.
PPI_QUESTION_COUNTS = ppi.PPI_QUESTION_COUNTS

GRADE_NAMES: tuple[str, ...] = tuple(TECHNICAL_QUESTION_COUNTS)

# ── The four AI Score matching parameters (spec §3) ──────────────────────────
# NO WEIGHTS. The spec is explicit: "make sure there are no mathematical
# weightage for giving these AI comments". The parameters were previously
# described to the client as "35% role-fit weighting" and similar, which both
# leaked a number and implied an arithmetic the comments do not perform. Each
# parameter is now judged and reported on its own terms.
CATEGORY_MATCHING = "matching"
CATEGORY_TECHNICAL = "technical"

MATCHING_DIMENSIONS: tuple[tuple[str, str, str], ...] = (
    (
        "Skills match",
        "skills_match",
        "Semantic comparison between the job's required skills and the candidate's "
        "experience, education and certifications.",
    ),
    (
        "Experience relevance",
        "experience_relevance",
        "Whether the experience is in the same function and at a comparable level, "
        "not a numeric count of years.",
    ),
    (
        "Role & responsibility alignment",
        "role_alignment",
        "The candidate's actual designation and duties against the job's role and "
        "responsibilities.",
    ),
    (
        "Education & qualification fit",
        "education_fit",
        "Degree level and specialisation against the job's education requirement.",
    ),
)

#: Report section order, exactly as §10.3 lists it.
REPORT_CATEGORIES: tuple[str, ...] = (
    CATEGORY_MATCHING,
    ppi.CATEGORY_PRIMARY,
    ppi.CATEGORY_SECONDARY,
    ppi.CATEGORY_BEHAVIOURAL,
    CATEGORY_TECHNICAL,
)

#: Word contracts (spec §10.5).
MATCHING_REMARK_WORDS = (25, 30)
PPI_REMARK_WORDS = (45, 50)

# Score assigned when a question was never answered -- factual, not punitive.
UNANSWERED_SCORE = 25

#: Ordered best-to-worst grade labels, for the radar legend and colour ramp.
RADAR_BANDS: tuple[str, ...] = GRADES


def technical_question_count(grade: str | None) -> int:
    return TECHNICAL_QUESTION_COUNTS.get(grade or "", TECHNICAL_QUESTION_COUNTS["non_managerial"])


def rating_label(score: int | float | None) -> str | None:
    """The client-facing grade for an internal 0-100 score.

    Thin alias over `services.rating.grade_for_percent`, kept because a good
    deal of the codebase already imports it from here. The scale itself lives
    in one module so the assessment and the AI Score cannot drift apart.
    """
    return grade_for_percent(score)


def word_count(value: str) -> int:
    return len(re.findall(r"\b[\w&'-]+\b", value))


# ── Radar charts (spec §10.4) ────────────────────────────────────────────────
# FOUR charts per candidate: Overall, Primary Skills, Secondary Skills,
# Behavioural Competencies. Each plots TWO shapes on the same axes -- what the
# job requires and what the candidate demonstrated -- so a reader sees at a
# glance where the candidate exceeds, meets, or falls short.
#
# No number appears on an axis, a data label, or a tooltip. `*_index` is a
# RENDERING COORDINATE (1..4): a radar has no geometry without a radius, and the
# four grades ARE the radial axis. The underlying 0-100 score stays internal.

RADAR_CHART_KEYS: tuple[str, ...] = (
    "overall",
    ppi.CATEGORY_PRIMARY,
    ppi.CATEGORY_SECONDARY,
    ppi.CATEGORY_BEHAVIOURAL,
)

RADAR_CHART_TITLES: dict[str, str] = {
    "overall": "Overall",
    ppi.CATEGORY_PRIMARY: ppi.CATEGORY_LABELS[ppi.CATEGORY_PRIMARY],
    ppi.CATEGORY_SECONDARY: ppi.CATEGORY_LABELS[ppi.CATEGORY_SECONDARY],
    ppi.CATEGORY_BEHAVIOURAL: ppi.CATEGORY_LABELS[ppi.CATEGORY_BEHAVIOURAL],
}

#: The legend below every chart, by word only (spec §10.4).
RADAR_SERIES: tuple[str, ...] = ("Job Requirement", "Candidate Assessment")

#: ASSUMPTION (2026-07-30, open with the client): §10.4 asks for an "Overall"
#: radar without saying what its axes are. It plots the three PPI category
#: aggregates -- one axis per category, both shapes derived from the same rows
#: the sections render.
#:
#: Technical is deliberately NOT an axis here, even though it would make a
#: readable quadrilateral rather than a triangle. §10.4 is explicit that all
#: four charts are part of the PPI Assessment, technical items are not part of
#: the framework, and they carry no job-requirement level -- so the "Job
#: Requirement" shape would have to invent a value for that spoke. A fabricated
#: requirement is worse than a three-sided chart.
OVERALL_AXES: tuple[tuple[str, str], ...] = (
    (ppi.CATEGORY_PRIMARY, ppi.CATEGORY_LABELS[ppi.CATEGORY_PRIMARY]),
    (ppi.CATEGORY_SECONDARY, ppi.CATEGORY_LABELS[ppi.CATEGORY_SECONDARY]),
    (ppi.CATEGORY_BEHAVIOURAL, ppi.CATEGORY_LABELS[ppi.CATEGORY_BEHAVIOURAL]),
)


def _mean(values: list[int]) -> int:
    return round(sum(values) / len(values)) if values else UNANSWERED_SCORE


def _axis(name: str, candidate_score: int, required: int | None) -> dict[str, Any]:
    candidate_band = grade_for_percent(candidate_score) or GRADES[-1]
    requirement_band = grade_for_percent(
        required if required is not None else ppi.DEFAULT_REQUIRED_LEVEL
    ) or GRADES[-1]
    return {
        "axis": name,
        "requirement_band": requirement_band,
        "requirement_index": band_index_for(requirement_band),
        "candidate_band": candidate_band,
        "candidate_index": band_index_for(candidate_band),
    }


def build_radar_charts(dimensions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The four radar charts, built from the SAME dimension rows the sections
    render, so a chart can never disagree with the text beside it.

    Pure and side-effect free; unit-tested in tests/test_assessments.py.
    """
    by_category: dict[str, list[dict[str, Any]]] = {}
    for row in dimensions:
        by_category.setdefault(row["category"], []).append(row)

    charts: list[dict[str, Any]] = []
    overall_axes = []
    for category, label in OVERALL_AXES:
        rows = by_category.get(category) or []
        if not rows:
            continue
        overall_axes.append(
            _axis(
                label,
                _mean([row["score"] for row in rows]),
                _mean([row["required_level"] for row in rows if row.get("required_level")])
                if any(row.get("required_level") for row in rows)
                else None,
            )
        )
    charts.append({"key": "overall", "title": RADAR_CHART_TITLES["overall"], "axes": overall_axes})

    for category in (ppi.CATEGORY_PRIMARY, ppi.CATEGORY_SECONDARY, ppi.CATEGORY_BEHAVIOURAL):
        rows = sorted(by_category.get(category) or [], key=lambda item: item["ordinal"])
        charts.append(
            {
                "key": category,
                "title": RADAR_CHART_TITLES[category],
                "axes": [
                    _axis(row["name"], row["score"], row.get("required_level")) for row in rows
                ],
            }
        )
    return charts


def infer_grade_fallback(job: Job) -> str:
    """Keyword grade inference. Mirrored exactly by migration 0014's SQL CASE."""
    text = f"{job.title} {job.level or ''}".lower()
    if any(term in text for term in ("chief", "cxo", "ceo", "cto", "cfo", "coo")):
        return "cxo"
    if any(term in text for term in ("director", "head", "vice president", "vp", "leader")):
        return "leadership"
    if any(term in text for term in ("manager", "lead", "supervisor")):
        return "managerial"
    return "non_managerial"


async def infer_grade(job: Job, session: AsyncSession) -> str:
    """LEGACY FALLBACK ONLY. Grade is a required field on the Create Job form and
    is stored on jobs.assessment_grade; this path exists for pre-0014 rows that
    somehow still carry no grade."""
    if job.assessment_grade in TECHNICAL_QUESTION_COUNTS:
        return job.assessment_grade
    try:
        raw = await llm_router.chat_completion(
            "extraction",
            [
                {
                    "role": "system",
                    "content": (
                        "Classify this job into exactly one value: non_managerial, "
                        "managerial, leadership, cxo. Return JSON {\"grade\":\"...\"}."
                    ),
                },
                {"role": "user", "content": json.dumps({"title": job.title, "level": job.level, "jd": job.jd_json})},
            ],
            response_format_json=True,
            session=session,
        )
        grade = json.loads(raw).get("grade")
        return grade if grade in GRADE_NAMES else infer_grade_fallback(job)
    except Exception:
        return infer_grade_fallback(job)


# ── Technical question bank ─────────────────────────────────────────────────

_DEFAULT_RUBRIC = {
    "0_39": "No relevant example or materially incorrect approach.",
    "40_59": "Partial knowledge with limited practical evidence.",
    "60_74": "Sound practical approach with a credible example.",
    "75_89": "Strong depth, trade-offs, and measurable outcomes.",
    "90_100": "Exceptional depth, judgement, outcomes, and transferable insight.",
}

_FALLBACK_ANGLES = (
    "Describe a demanding situation where you applied {skill}. What did you decide, implement, measure, and learn?",
    "Walk me through how you would diagnose a problem involving {skill}. What would you check first, and why?",
    "What trade-offs have you had to make when working with {skill}? Give a concrete example and its outcome.",
    "How do you verify that your work involving {skill} is correct and holds up in production or under review?",
    "Describe the most complex piece of work you have delivered using {skill}. What made it hard?",
)


#: A technical dimension's name is shown to the client verbatim, so it must read
#: as a SKILL ("PostgreSQL", "Incident response"), never as a whole JD sentence.
_MAX_SKILL_LABEL = 60


def _topic_label(sentence: str) -> str:
    """Condense a JD responsibility/accountability line into a short topic label.

    Takes the leading clause, drops a leading verb like "Build"/"Design" so the
    label reads as a subject rather than an instruction, and truncates on a word
    boundary. "Design MongoDB schemas and indexes that support the product's
    access patterns" -> "MongoDB schemas and indexes".
    """
    clause = re.split(r"[,;:.]| that | which | so that ", sentence.strip(), maxsplit=1)[0]
    words = clause.split()
    if words and words[0].lower() in {
        "build", "design", "implement", "maintain", "write", "own", "run",
        "define", "deliver", "monitor", "manage", "support", "create",
        "develop", "integrate", "profile", "diagnose", "model", "take",
    }:
        words = words[1:]
    if words and words[0].lower() in {"and", "the", "a", "an"}:
        words = words[1:]
    label = " ".join(words).strip(" -,")
    while len(label) > _MAX_SKILL_LABEL and " " in label:
        label = label.rsplit(" ", 1)[0]
    label = label.strip(" -,")
    return (label[:1].upper() + label[1:]) if label else ""


def _jd_skills(job: Job) -> list[str]:
    """The JD's declared skills -- the canonical technical dimensions.

    Only `jd.skills` is treated as a skill. Responsibilities and accountabilities
    are prose, and using them verbatim put sentences like "Generative features
    degrade gracefully rather than failing outright" in the report's Technical
    section as if they were a skill. They are still mined, but only as condensed
    topic labels and only when the declared skills cannot supply enough variety.
    """
    jd = job.jd_json or {}
    skills: list[str] = []
    seen: set[str] = set()
    for item in jd.get("skills") or []:
        value = str(item).strip()
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            skills.append(value[:_MAX_SKILL_LABEL])
    if not skills:
        skills = [job.title[:_MAX_SKILL_LABEL]]
    return skills


def _jd_topics(job: Job) -> list[str]:
    """Condensed topic labels mined from JD prose, for banks that need more
    variety than the declared skills alone can provide."""
    jd = job.jd_json or {}
    topics: list[str] = []
    seen = {s.casefold() for s in _jd_skills(job)}
    for field in ("responsibilities", "accountabilities"):
        value = jd.get(field)
        items = value if isinstance(value, list) else ([value] if isinstance(value, str) else [])
        for item in items:
            label = _topic_label(str(item))
            if label and label.casefold() not in seen:
                seen.add(label.casefold())
                topics.append(label)
    return topics


def _question_fallback(job: Job, count: int | None = None) -> list[dict[str, Any]]:
    """Deterministic top-up bank: cycles the JD skills across question angles so
    an exact count is always reachable without repeating a prompt verbatim."""
    skills = _jd_skills(job)
    target = count if count is not None else min(len(skills), 8)
    if len(skills) * len(_FALLBACK_ANGLES) < target:
        skills = skills + _jd_topics(job)
    rows: list[dict[str, Any]] = []
    skill_cycle = cycle(skills)
    angle_index = 0
    while len(rows) < target:
        skill = next(skill_cycle)
        angle = _FALLBACK_ANGLES[angle_index % len(_FALLBACK_ANGLES)]
        rows.append(
            {
                "skill": skill[:255],
                "prompt": angle.format(skill=skill),
                "rubric": dict(_DEFAULT_RUBRIC),
            }
        )
        if len(rows) % len(skills) == 0:
            angle_index += 1
    return rows[:target]


def _valid_question(row: Any) -> bool:
    return (
        isinstance(row, dict)
        and bool(str(row.get("skill", "")).strip())
        and bool(str(row.get("prompt", "")).strip())
        and isinstance(row.get("rubric"), dict)
        and bool(row["rubric"])
    )


async def generate_question_bank(session: AsyncSession, job: Job) -> list[TechnicalQuestion]:
    """Generate the job's technical bank ONCE and leave it AWAITING REVIEW.

    The bank is generated per JOB, not per candidate, so every applicant to a
    role answers the same technical questions and their scores are comparable.
    Generation NEVER approves: the recruiter finalises the bank (and the PPI
    framework) before the job reaches `ready_for_candidates` (spec §5, §11).
    """
    existing = (
        await session.execute(select(TechnicalQuestion).where(TechnicalQuestion.job_id == job.id))
    ).scalars().all()
    grade = job.assessment_grade if job.assessment_grade in TECHNICAL_QUESTION_COUNTS else await infer_grade(job, session)
    required = technical_question_count(grade)

    if existing:
        job.assessment_grade = grade
        active = [row for row in existing if row.is_active]
        # Legacy banks predate the grade-driven counts and can be short. Top up
        # deterministically rather than regenerating -- every candidate on a job
        # must keep seeing the same questions.
        if len(active) < required:
            existing_prompts = {row.prompt.strip().lower() for row in existing}
            ordinal = max((row.ordinal for row in existing), default=0)
            added: list[TechnicalQuestion] = []
            for filler in _question_fallback(job, (required - len(active)) * 3):
                if len(active) + len(added) >= required:
                    break
                if filler["prompt"].strip().lower() in existing_prompts:
                    continue
                existing_prompts.add(filler["prompt"].strip().lower())
                ordinal += 1
                added.append(
                    TechnicalQuestion(
                        tenant_id=job.tenant_id,
                        job_id=job.id,
                        ordinal=ordinal,
                        skill=str(filler["skill"])[:255],
                        prompt=str(filler["prompt"]),
                        rubric_json=filler["rubric"],
                    )
                )
            while len(active) + len(added) < required:
                ordinal += 1
                added.append(
                    TechnicalQuestion(
                        tenant_id=job.tenant_id,
                        job_id=job.id,
                        ordinal=ordinal,
                        skill=job.title[:255],
                        prompt=f"Question {ordinal}: describe another concrete example of your work relevant to {job.title}.",
                        rubric_json=dict(_DEFAULT_RUBRIC),
                    )
                )
            session.add_all(added)
            existing = list(existing) + added
        await session.flush()
        return existing

    questions: list[dict[str, Any]] = []
    try:
        # Task-type routing: the technical bank prefers Gemini for grounded,
        # specific probes (config/llm_providers.TASK_ROUTES).
        raw = await llm_router.chat_completion(
            "technical_questions",
            [
                {
                    "role": "system",
                    "content": (
                        f"Generate exactly {required} defensible technical assessment questions for this job, "
                        "drawn from the required skills, tools, and responsibilities in the job description. "
                        "Each question carries its own scoring rubric. Return JSON "
                        "{\"questions\":[{\"skill\":\"...\",\"prompt\":\"...\","
                        "\"rubric\":{\"0_39\":\"...\",\"40_59\":\"...\",\"60_74\":\"...\","
                        "\"75_89\":\"...\",\"90_100\":\"...\"}}]}. Rubrics must be observable and job-specific."
                    ),
                },
                {"role": "user", "content": json.dumps({"title": job.title, "level": job.level, "grade": grade, "jd": job.jd_json})},
            ],
            response_format_json=True,
            session=session,
        )
        parsed = json.loads(raw).get("questions", [])
        questions = [row for row in parsed if _valid_question(row)]
    except Exception:
        logger.warning("technical_questions.llm_unavailable job_id=%s, using deterministic bank", job.id)
        questions = []

    questions = questions[:required]
    if len(questions) < required:
        seen = {str(row["prompt"]).strip().lower() for row in questions}
        for filler in _question_fallback(job, required * 2):
            if len(questions) >= required:
                break
            if filler["prompt"].strip().lower() in seen:
                continue
            seen.add(filler["prompt"].strip().lower())
            questions.append(filler)
    while len(questions) < required:  # pathological: single skill, angles exhausted
        index = len(questions) + 1
        questions.append(
            {
                "skill": job.title[:255],
                "prompt": f"Question {index}: describe another concrete example of your work relevant to {job.title}.",
                "rubric": dict(_DEFAULT_RUBRIC),
            }
        )

    rows = [
        TechnicalQuestion(
            tenant_id=job.tenant_id,
            job_id=job.id,
            ordinal=index,
            skill=str(question["skill"])[:255],
            prompt=str(question["prompt"]),
            rubric_json=question["rubric"],
        )
        for index, question in enumerate(questions, 1)
    ]
    session.add_all(rows)
    job.assessment_grade = grade
    job.questions_generated_at = datetime.now(timezone.utc)
    await session.flush()
    return rows


# ── Scoring primitives ──────────────────────────────────────────────────────

def _stable_score(seed: str, low: int = 45, high: int = 94) -> int:
    """DETERMINISTIC LAST-RESORT ONLY (claude.md rule 9: degrade, never crash).

    Used when the LLM chain is unavailable. Any report produced this way is
    marked with scoring_mode='deterministic_fallback'.
    """
    number = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8], 16)
    return low + number % (high - low + 1)


def answers_by_key(transcript: list[dict[str, Any]] | None) -> dict[str, list[str]]:
    """Candidate answers grouped by the question_key stamped on each message."""
    grouped: dict[str, list[str]] = {}
    for message in transcript or []:
        if str(message.get("speaker")) != "candidate":
            continue
        key = message.get("question_key")
        content = str(message.get("content") or "").strip()
        if not key or not content:
            continue
        grouped.setdefault(str(key), []).append(content)
    return grouped


def _rubric_text(rubric: dict | None) -> str:
    if not rubric:
        rubric = _DEFAULT_RUBRIC
    return "; ".join(f"{band.replace('_', '-')}: {text}" for band, text in rubric.items())


async def _llm_score(session: AsyncSession | None, question: str, rubric: dict | None, answer: str) -> int | None:
    """Score one answer 0-100 strictly against the supplied rubric bands."""
    try:
        raw = await llm_router.chat_completion(
            "behavioral_assessment",
            [
                {
                    "role": "system",
                    "content": (
                        "You are scoring one assessment answer against a fixed rubric. "
                        "Choose the rubric band the answer actually satisfies, then pick an integer "
                        "inside that band's range. Do not reward length or confidence. "
                        f"Rubric bands: {_rubric_text(rubric)}. "
                        "Return JSON {\"score\": <integer 0-100>, \"band\": \"<band key>\"}."
                    ),
                },
                {"role": "user", "content": json.dumps({"question": question, "answer": answer})},
            ],
            response_format_json=True,
            session=None,
        )
        score = int(round(float(json.loads(raw)["score"])))
        return max(0, min(100, score))
    except Exception:
        return None


# ── Remark generation ───────────────────────────────────────────────────────
# Remarks are generated COMPLETE inside their word contract and never truncated
# (CLAUDE.md hard rule). Out-of-range output is regenerated in full.

def _fallback_remark_25(name: str) -> str:
    candidates = [
        (
            f"Available evidence demonstrates dependable capability in {name}, with relevant practical examples. "
            "Interview discussion should confirm depth, decision quality, independent ownership, and consistency across comparable work situations."
        ),
        (
            "Available evidence demonstrates dependable capability in this dimension, with relevant practical examples. "
            "Interview discussion should confirm depth, decision quality, independent ownership, and consistency across comparable work situations."
        ),
    ]
    return next(value for value in candidates if 25 <= word_count(value) <= 30)


def _fallback_remark_45(name: str) -> str:
    """45-50 word fallback for a PPI item or the overall remark.

    Two candidates, the second dropping the item name: a long competency name
    ("Stakeholder & board management") pushes the first variant over the
    ceiling, and the contract is a COMPLETE remark inside the range, never a
    truncated one (CLAUDE.md hard rule).
    """
    candidates = [
        (
            f"The conversation produced usable evidence for {name}, with examples showing how the candidate approaches "
            "this work in practice. The account is credible but not exhaustive, so an interviewer should press for a second "
            "situation, the reasoning behind the decision taken, and the outcome the candidate personally owned."
        ),
        (
            "The conversation produced usable evidence here, with examples showing how the candidate approaches this work "
            "in practice. The account is credible but not exhaustive, so an interviewer should press for a second situation, "
            "the reasoning behind the decision that was taken, and the outcome the candidate personally owned."
        ),
    ]
    return next(value for value in candidates if 45 <= word_count(value) <= 50)


def _unanswered_remark(name: str, minimum: int) -> str:
    """Factual remark for an item the candidate produced no evidence for."""
    if minimum >= 45:
        candidates = [
            (
                f"The conversation produced no usable evidence for {name}. The candidate did not describe a situation that "
                "shows this capability, so nothing here can be graded on demonstrated behaviour. An interviewer should treat "
                "it as an open question and probe it directly before drawing a conclusion."
            ),
            (
                "The conversation produced no usable evidence for this item. The candidate did not describe a situation that "
                "shows the capability, so nothing here can be graded on demonstrated behaviour. An interviewer should treat "
                "it as an open question and probe it directly before drawing a conclusion."
            ),
        ]
        return next(value for value in candidates if 45 <= word_count(value) <= 50)
    candidates = [
        (
            f"The candidate did not provide an answer covering {name} during the conversation, so no evidence exists here. "
            "Interviewers should probe this area directly before drawing any firm conclusion."
        ),
        (
            "The candidate did not provide an answer covering this dimension during the conversation, so no evidence exists here at all. "
            "Interviewers should probe this area directly before drawing any conclusion."
        ),
    ]
    return next(value for value in candidates if 25 <= word_count(value) <= 30)


async def bounded_remark(
    session: AsyncSession | None,
    name: str,
    evidence: str,
    minimum: int = 25,
    maximum: int = 30,
) -> str:
    """A COMPLETE remark inside the word contract, never truncated.

    The `highly dynamic` instruction is not decoration (spec §10.5): a templated
    remark with the competency name swapped in is exactly what the client
    rejected, so the prompt names the evidence and forbids generic phrasing.
    """
    prompt = (
        f"Write one complete, evidence-based assessment remark of exactly {minimum}-{maximum} words "
        f"for '{name}'. Ground every clause in the specific evidence supplied: quote or paraphrase what "
        "this candidate actually said. Do not use templated phrasing that would fit any candidate, and "
        "do not include a score, percentage, grade, recommendation, or heading. "
        f"Evidence: {evidence}"
    )
    for _attempt in range(3):
        try:
            value = (
                await llm_router.chat_completion(
                    "report_synthesis",
                    [{"role": "system", "content": prompt}, {"role": "user", "content": "Return only the remark."}],
                    session=None,
                )
            ).strip()
            if minimum <= word_count(value) <= maximum:
                return value
            prompt += f" Your previous response had {word_count(value)} words; regenerate it within the required range."
        except Exception:
            break
    return _fallback_remark_45(name) if minimum >= 45 else _fallback_remark_25(name)


class AssessmentState(TypedDict, total=False):
    session: AsyncSession
    job: Job
    link: JobCandidateLink
    profile: Profile | None
    transcript: list[dict[str, Any]]
    answers: dict[str, list[str]]
    questions: list[TechnicalQuestion]
    competencies: list[JobCompetency]
    candidate_questions: list[CandidateQuestion]
    grade: str
    matching: list[dict[str, Any]]
    technical: list[dict[str, Any]]
    ppi: list[dict[str, Any]]
    technical_mode: str
    ppi_mode: str
    validation: dict[str, Any]
    report_id: str


async def _matching_dimensions(state: AssessmentState) -> list[dict[str, Any]]:
    """The AI Score: the four matching parameters, 25-30 word remarks (§10.5).

    This is the PRE-assessment snapshot. It is generated from the resume by the
    matching pipeline and is deliberately kept separate from the PPI Assessment
    rather than merged with it: a close agreement between the two confirms the
    resume was accurate, and a gap between them is itself useful signal (§10.1).
    """
    breakdown = state["link"].match_breakdown_json or {}
    result = []
    for ordinal, (name, key, description) in enumerate(MATCHING_DIMENSIONS, 1):
        item = breakdown.get(key) or {}
        score = int(float(item.get("score", 5)) * 10)
        evidence = str(item.get("comment") or "resume and application evidence")
        result.append(
            {
                "category": CATEGORY_MATCHING,
                "name": name,
                "description": description,
                "score": max(0, min(100, score)),
                "required_level": None,
                "remark": await bounded_remark(
                    state["session"], name, evidence, *MATCHING_REMARK_WORDS
                ),
                "ordinal": ordinal,
            }
        )
    return result


async def technical_node(state: AssessmentState) -> dict:
    """Score every technical answer against THAT question's own rubric (spec §5),
    then report ONE dimension per distinct JD skill.

    A grade-sized bank asks several questions about the same skill, so scoring
    stays per-question and rubric-bound while the report aggregates those scores
    into a single entry for the skill. `report_dimensions` is UNIQUE on
    (report_id, category, name), so emitting one row per question would raise
    IntegrityError on any bank that probed a skill twice.

    Technical items are not a rendered section of the PPI report (§10.3), but
    they ARE scored and they anchor suggested interview questions (§10.3, last
    bullet), so they are stored and returned.
    """
    answers = state.get("answers") or answers_by_key(state.get("transcript"))
    mode = "no_transcript" if not answers else "llm_rubric"

    by_skill: dict[str, list[TechnicalQuestion]] = {}
    for question in state["questions"]:
        by_skill.setdefault(question.skill, []).append(question)

    rows: list[dict[str, Any]] = []
    for ordinal, (skill, questions) in enumerate(by_skill.items(), 1):
        scores: list[int] = []
        answered: list[str] = []
        for question in questions:
            answer = " ".join(answers.get(str(question.id), []))
            # An answer with nothing in it to grade is treated exactly as an
            # unanswered one, and deliberately never reaches `_llm_score`.
            # Letting it through is what produced a passing grade for
            # `ewidjverip`: on an LLM failure the caller falls back to
            # `_stable_score`, whose 45..94 floor cannot express Not Matching.
            # See services/answer_quality for the full mechanism.
            verdict = answer_quality.assess(answer)
            if not verdict.substantive:
                if answer:
                    logger.info(
                        "functional_assessment.insufficient_answer "
                        "link_id=%s question_id=%s reason=%s",
                        state["link"].id, question.id, verdict.reason,
                    )
                scores.append(UNANSWERED_SCORE)
                continue
            answered.append(answer)
            score = await _llm_score(
                state["session"], question.prompt, question.rubric_json, answer
            )
            if score is None:
                mode = "deterministic_fallback"
                score = _stable_score(f"{state['link'].id}:{question.id}:{answer}")
            scores.append(score)

        aggregate = _mean(scores)
        probes = "; ".join(question.prompt for question in questions)
        if not answered:
            remark = _unanswered_remark(skill, MATCHING_REMARK_WORDS[0])
        else:
            evidence = (
                f"the candidate's own answers across {len(questions)} question(s) on this "
                f"skill, each scored against the rubric written for that question: "
                f"{' '.join(answered)[:600]}"
            )
            remark = await bounded_remark(
                state["session"], skill, evidence, *MATCHING_REMARK_WORDS
            )
        rows.append(
            {
                "category": CATEGORY_TECHNICAL,
                "name": skill,
                "description": probes[:1000],
                "score": aggregate,
                "required_level": None,
                "remark": remark,
                "ordinal": ordinal,
            }
        )
    return {"technical": rows, "technical_mode": mode}


_COMPETENCY_RUBRIC = {
    "0_39": "No relevant situation described, or the account contradicts the competency.",
    "40_59": "A thin or generic account with little personal action or outcome.",
    "60_74": "A credible situation with clear personal action and a stated outcome.",
    "75_89": "Several strong situations with judgement, trade-offs, and measurable results.",
    "90_100": "Consistently exceptional accounts showing judgement, impact, and transferable insight.",
}


async def ppi_node(state: AssessmentState) -> dict:
    """Score the candidate against the job's SAVED framework (spec §9).

    One entry per competency, in report order, each with a 45-50 word remark
    (§10.5). The competency's `required_level` travels onto the report row so
    the radar can plot the job's shape even after the job's framework is later
    edited -- a written report is a permanent record of the criteria it was
    written against.
    """
    answers = state.get("answers") or answers_by_key(state.get("transcript"))
    mode = "no_transcript" if not answers else "llm_rubric"
    rows: list[dict[str, Any]] = []
    ordinal_by_category: dict[str, int] = {}

    for competency in state.get("competencies") or []:
        ordinal_by_category[competency.category] = (
            ordinal_by_category.get(competency.category, 0) + 1
        )
        # Filtered, not just checked, so one real answer alongside a keysmash
        # is still graded on the real one -- and a competency probed only by
        # non-answers falls through to the unanswered branch below rather than
        # to `_stable_score`, which cannot return a failing grade.
        raw_collected = answers.get(str(competency.id), [])
        collected = [item for item in raw_collected if answer_quality.is_substantive(item)]
        if raw_collected and not collected:
            logger.info(
                "functional_assessment.insufficient_answers "
                "link_id=%s competency_id=%s answers=%d",
                state["link"].id, competency.id, len(raw_collected),
            )
        base = {
            "category": competency.category,
            "name": competency.name,
            "description": competency.description,
            "required_level": competency.required_level,
            "ordinal": ordinal_by_category[competency.category],
        }
        if not collected:
            rows.append(
                {
                    **base,
                    "score": UNANSWERED_SCORE,
                    "remark": _unanswered_remark(competency.name, PPI_REMARK_WORDS[0]),
                }
            )
            continue
        combined = "\n".join(f"- {item}" for item in collected)
        question = (
            f"{ppi.CATEGORY_LABELS[competency.category][:-1]} '{competency.name}'"
            f"{': ' + competency.description if competency.description else ''} "
            f"The candidate answered {len(collected)} question(s) probing it."
        )
        score = await _llm_score(state["session"], question, _COMPETENCY_RUBRIC, combined)
        if score is None:
            mode = "deterministic_fallback"
            score = _stable_score(f"{state['link'].id}:{competency.id}:{combined}")
        rows.append(
            {
                **base,
                "score": score,
                "remark": await bounded_remark(
                    state["session"],
                    competency.name,
                    f"the candidate's own answers probing this item: {combined[:800]}",
                    *PPI_REMARK_WORDS,
                ),
            }
        )
    return {"ppi": rows, "ppi_mode": mode}


async def validation_node(state: AssessmentState) -> dict:
    """Carry the application's mandatory fields into the report, UNCHANGED.

    Nothing here is scored, interpreted or judged (spec §7). The candidate's
    answer to "Why does this role interest you?" reaches the recruiter exactly
    as written -- the recruiter, not any agent, decides whether the stated
    interest is genuine.
    """
    submitted = state["link"].validation_json or {}
    captured = {key: submitted.get(key) for key in MANDATORY_KEYS}
    return {
        "validation": {
            # "captured" = this application carried the mandatory fields at all.
            # Applications submitted before 2026-07-30 predate them and render
            # as an explicit "not collected" rather than a blank panel.
            "captured": bool(submitted),
            **captured,
            "fields": [
                {
                    "key": field["key"],
                    "label": field["label"],
                    "value": submitted.get(field["key"]),
                }
                for field in VALIDATION_FIELDS
            ],
        }
    }


def _dedupe_dimensions(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Collapse rows sharing a (category, name) key, keeping the first and
    re-numbering ordinals within each category.

    `report_dimensions` is UNIQUE on (report_id, category, name). A duplicate
    used to surface as an IntegrityError that failed the whole Celery task
    *after* matching had already committed, so a run looked failed when it had
    largely succeeded. The scoring nodes are the real fix; this is the belt-and-
    braces guarantee that no future framework shape can ever 500 synthesis.
    """
    seen: set[tuple[str, str]] = set()
    kept: list[dict[str, Any]] = []
    for row in rows:
        key = (row["category"], row["name"])
        if key in seen:
            logger.warning(
                "functional_assessment.duplicate_dimension category=%s name=%s, collapsed",
                row["category"], row["name"],
            )
            continue
        seen.add(key)
        kept.append(row)
    per_category: dict[str, int] = {}
    for row in kept:
        per_category[row["category"]] = per_category.get(row["category"], 0) + 1
        row["ordinal"] = per_category[row["category"]]
    return kept


def _suggested_questions(dimensions: list[dict[str, Any]]) -> list[str]:
    """8-10 interviewer questions, anchored on whatever graded Moderately
    Matching or Not Matching (spec §10.3).

    Advisory input, never a recommendation to reject or accept -- the wording
    below says so, and no question here draws a conclusion for the reader.
    """
    anchorable = [
        row
        for row in dimensions
        if row["category"] != CATEGORY_MATCHING
    ]
    weak = sorted(
        (
            row
            for row in anchorable
            if grade_for_percent(row["score"]) in MODERATE_OR_BELOW
        ),
        key=lambda item: item["score"],
    )
    pool = weak or sorted(anchorable, key=lambda item: item["score"])
    if not pool:
        pool = sorted(dimensions, key=lambda item: item["score"])

    angles = (
        "Ask {name}: describe one recent situation in detail, and what you personally decided.",
        "Explore {name}: what would you do differently now, and what changed your view?",
        "Probe {name}: walk through a case where your approach did not work, and how you recovered.",
        "On {name}, ask for the evidence they would point to that the outcome actually held.",
    )
    # One pass over the weakest items first, then widen by asking a different
    # angle of the same items -- eight is the floor even when only two items
    # graded Moderately Matching or below.
    questions: list[str] = []
    for round_index in range(len(angles)):
        for row in pool:
            if len(questions) >= 10:
                break
            candidate = angles[round_index].format(name=row["name"])
            if candidate not in questions:
                questions.append(candidate)
        if len(questions) >= 8:
            break
    while len(questions) < 8:
        questions.append(
            "Ask for one further worked example relevant to this role and the candidate's personal contribution."
        )
    return questions[:10]


async def synthesis_node(state: AssessmentState) -> dict:
    """Join both scorers, write the report (spec §10).

    Waits for BOTH scoring agents; the graph's join edge is what enforces that.
    """
    session = state["session"]
    matching = await _matching_dimensions(state)
    ppi_rows = state.get("ppi") or []
    dimensions = _dedupe_dimensions(matching + ppi_rows + state["technical"])

    # The Overall grade is the PPI Assessment's, not the AI Score's: §10.3 puts
    # it at the head of the PPI section, below the AI Score, and the two are
    # deliberately never merged.
    assessed = [row for row in dimensions if row["category"] != CATEGORY_MATCHING]
    overall_score = _mean([row["score"] for row in assessed])
    weak_names = ", ".join(
        row["name"]
        for row in assessed
        if grade_for_percent(row["score"]) in MODERATE_OR_BELOW
    ) or "role-specific depth"
    strong_names = ", ".join(
        row["name"] for row in assessed if row["score"] >= PROBE_THRESHOLD
    ) or "the areas evidenced in the conversation"
    overall = await bounded_remark(
        session,
        "this candidate's overall suitability",
        (
            "the candidate's demonstrated skills and behavioural competencies against this job. "
            f"Stronger evidence: {strong_names}. Weaker or unevidenced: {weak_names}."
        ),
        *PPI_REMARK_WORDS,
    )

    probes = _suggested_questions(dimensions)

    validation = dict(state["validation"])
    modes = {state.get("technical_mode", "llm_rubric"), state.get("ppi_mode", "llm_rubric")}
    scoring_mode = (
        "deterministic_fallback" if "deterministic_fallback" in modes
        else ("no_transcript" if modes == {"no_transcript"} else "llm_rubric")
    )
    if scoring_mode != "llm_rubric":
        logger.warning(
            "functional_assessment.scoring_mode link_id=%s mode=%s",
            state["link"].id, scoring_mode,
        )

    current = (
        await session.execute(
            select(FunctionalSkillsReport).where(FunctionalSkillsReport.job_candidate_link_id == state["link"].id)
        )
    ).scalars().first()
    fields = {
        "grade": state["grade"],
        "overall_summary": overall,
        "overall_score": overall_score,
        "scoring_mode": scoring_mode,
        "validation_json": validation,
        "suggested_probes_json": probes,
        "synthesized_at": datetime.now(timezone.utc),
    }
    if current is None:
        current = FunctionalSkillsReport(
            tenant_id=state["job"].tenant_id,
            job_id=state["job"].id,
            job_candidate_link_id=state["link"].id,
            **fields,
        )
        session.add(current)
        await session.flush()
    else:
        for key, value in fields.items():
            setattr(current, key, value)
        await session.execute(delete(ReportDimension).where(ReportDimension.report_id == current.id))
    session.add_all(
        [ReportDimension(tenant_id=state["job"].tenant_id, report_id=current.id, **row) for row in dimensions]
    )
    await session.flush()
    return {"report_id": str(current.id)}


def build_assessment_graph():
    """Two scorers in parallel, joining at synthesis (spec §9).

    `validation_capture` is a third node but NOT a third scorer: it copies the
    application's mandatory fields into the report shape and touches no model.
    It runs on the same fan-out because synthesis needs its output, not because
    it is doing any judging.
    """
    graph = StateGraph(AssessmentState)
    graph.add_node("technical_scoring", technical_node)
    graph.add_node("ppi_scoring", ppi_node)
    graph.add_node("validation_capture", validation_node)
    graph.add_node("report_synthesis", synthesis_node)
    graph.add_edge(START, "technical_scoring")
    graph.add_edge(START, "ppi_scoring")
    graph.add_edge(START, "validation_capture")
    graph.add_edge(
        ["technical_scoring", "ppi_scoring", "validation_capture"],
        "report_synthesis",
    )
    graph.add_edge("report_synthesis", END)
    return graph.compile()


assessment_graph = build_assessment_graph()


async def run_assessment(
    session: AsyncSession,
    job: Job,
    link: JobCandidateLink,
    transcript: list[dict[str, Any]] | None = None,
) -> str:
    """Run both scorers and synthesise the PPI Assessment Report.

    A job that somehow reached this point without a technical bank or a PPI
    framework gets one generated on demand rather than the run failing: the work
    the candidate has already done must not be discarded over a setup gap.
    """
    if transcript is None:
        conversation = (
            await session.execute(
                select(AssessmentConversation).where(
                    AssessmentConversation.job_candidate_link_id == link.id
                )
            )
        ).scalars().first()
        if conversation is not None:
            messages = (
                await session.execute(
                    select(AssessmentMessage)
                    .where(AssessmentMessage.conversation_id == conversation.id)
                    .order_by(AssessmentMessage.ordinal)
                )
            ).scalars().all()
            transcript = [
                {
                    "speaker": message.speaker,
                    "domain": message.domain,
                    "question_key": message.question_key,
                    "content": message.content,
                }
                for message in messages
            ]
        else:
            transcript = []

    questions = (
        await session.execute(
            select(TechnicalQuestion)
            .where(TechnicalQuestion.job_id == job.id, TechnicalQuestion.is_active.is_(True))
            .order_by(TechnicalQuestion.ordinal)
        )
    ).scalars().all()
    if not questions:
        logger.info("functional_assessment.generating_bank_on_demand job_id=%s", job.id)
        await generate_question_bank(session, job)
        questions = (
            await session.execute(
                select(TechnicalQuestion)
                .where(TechnicalQuestion.job_id == job.id, TechnicalQuestion.is_active.is_(True))
                .order_by(TechnicalQuestion.ordinal)
            )
        ).scalars().all()

    competencies = await ppi.load_framework(session, job.id)
    if not competencies:
        logger.info("functional_assessment.generating_framework_on_demand job_id=%s", job.id)
        await ppi.generate_framework(session, job)
        competencies = await ppi.load_framework(session, job.id)

    grade = job.assessment_grade if job.assessment_grade in GRADE_NAMES else infer_grade_fallback(job)
    profile = await session.get(Profile, link.profile_id) if link.profile_id else None
    result = await assessment_graph.ainvoke(
        {
            "session": session,
            "job": job,
            "link": link,
            "profile": profile,
            "transcript": transcript,
            "answers": answers_by_key(transcript),
            "questions": questions,
            "competencies": competencies,
            "grade": grade,
        }
    )
    return result["report_id"]
