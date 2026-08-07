"""LangGraph orchestration for the PickReady PPI Assessment Report.

TWO scoring processes fan out in parallel from one unified transcript and join
at report synthesis (spec §9). Both consume the ACTUAL candidate answers, keyed
by the `question_key` stamped on every message:

  technical  -> str(CandidateTechnicalQuestion.id)  against that question's own
                                                    rubric
  ppi        -> str(JobCompetency.id)               against the job's framework

CHANGED 2026-08-06: the technical half is no longer a per-job preset bank a
company authored and edited. Each question is written for THIS candidate at the
moment it is asked, together with its rubric, by
`services/technical_interview`. Scoring is unchanged in shape -- an answer is
graded against the rubric belonging to the question that produced it -- and is
now unchanged in fact as well, because a rubric can no longer be left behind by
an edit to a stored prompt.

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
from pathlib import Path
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import (
    AssessmentConversation,
    AssessmentMessage,
    CandidateQuestion,
    CandidateTechnicalQuestion,
    FunctionalSkillsReport,
    JobCompetency,
    ReportDimension,
)
from app.models.candidate import JobCandidateLink, Profile
from app.models.job import Job
from app.services import (
    agent_loop,
    answer_quality,
    conversation_guardrails,
    llm_router,
    ppi,
    technical_interview,
)
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
    "infer_grade",
    "infer_grade_fallback",
    "rating_label",
    "run_assessment",
    "technical_question_count",
    "word_count",
]

# Technical question count per grade (spec §5). Unchanged by the PPI release and
# unchanged by the 2026-08-06 move to per-candidate questions: the questions are
# written differently, not fewer of them. Re-exported from the module that owns
# the technical half so the table has ONE definition -- two copies of a
# per-grade mapping is exactly how the product's old five-label rating scales
# drifted apart.
TECHNICAL_QUESTION_COUNTS: dict[str, int] = technical_interview.TECHNICAL_QUESTION_COUNTS

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
    return technical_interview.technical_question_count(grade)


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


# ── The technical half ────────────────────────────────────────────────────
# The per-JOB preset bank and its generator lived here until 2026-08-06.
# Both are gone: `services/technical_interview` writes each technical
# question for ONE candidate, together with its rubric, at the moment it is
# asked. The JD-skill helpers moved there with it, because the deterministic
# coverage plan is now that module's job.
#
# What remains here is the SCORER, unchanged in shape: every technical
# answer is still graded against the rubric belonging to the question that
# produced it. It is now unchanged in FACT as well -- a rubric can no longer
# be left behind by an edit to a stored prompt, because there is no stored
# prompt for anyone to edit.


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

REPORT_BANNED_PHRASES: tuple[str, ...] = (
    "produced usable evidence for",
    "credible but not exhaustive",
    "approaches this work in practice",
    "describe one recent situation in detail",
)

_PROBE_PROMPT = (
    Path(__file__).resolve().parents[2] / "prompts" / "report_interview_probes.txt"
).read_text(encoding="utf-8")

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
            f"The available answer record links {name} to actions the candidate described and outcomes they reported. "
            "One conversation cannot establish consistency across situations, so an interviewer should request another "
            "example, examine the decision trade-offs, and confirm which result the candidate personally owned from "
            "start to finish."
        ),
        (
            "The available answer record links this area to actions the candidate described and outcomes they reported. "
            "One conversation cannot establish consistency across situations, so an interviewer should request another "
            "example, examine the decision trade-offs, and confirm which result the candidate personally owned from "
            "start to finish."
        ),
    ]
    return next(value for value in candidates if 45 <= word_count(value) <= 50)


def _evidence_anchor(evidence: str) -> str:
    """A short, candidate-specific phrase safe to embed in a fallback."""
    ignored = {
        "answer",
        "answers",
        "candidate",
        "candidates",
        "evidence",
        "item",
        "question",
        "questions",
        "skill",
        "their",
        "this",
    }
    words = [
        token
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9+'-]*", evidence)
        if token.casefold() not in ignored
    ]
    return " ".join(words[:4]) or "the available account"


def rating_differentiated_fallback(
    evidence: str,
    rating: str,
    minimum: int,
    maximum: int,
) -> str:
    """A safe evidence-anchored fallback with a distinct contract per rating."""
    anchor = _evidence_anchor(evidence)
    if minimum < 40:
        templates = {
            "Highly Matching": (
                "The candidate gave a specific example involving {anchor}, connecting personal action to an outcome. "
                "Interview verification should test whether the demonstrated strength holds under comparable role constraints."
            ),
            "Matching": (
                "The answer connects {anchor} to relevant work and supports dependable capability. "
                "A focused interview probe should confirm independent ownership, decision trade-offs, and consistency."
            ),
            "Moderately Matching": (
                "The answer mentions {anchor} but leaves the candidate's personal decision, technical depth, or outcome unclear. "
                "Interviewers should probe the missing detail before relying on this evidence."
            ),
            "Not Matching": (
                "The conversation did not establish capability beyond {anchor}; no clear owned action or outcome was demonstrated. "
                "A direct probe should distinguish missing knowledge from an unexpressed example."
            ),
        }
    else:
        templates = {
            "Highly Matching": (
                "The candidate tied {anchor} to a specific situation, explained the action personally taken, and identified the resulting outcome. "
                "That detail demonstrates highly matching capability in this area. Interview verification should now test whether the same judgement "
                "and depth remain consistent when constraints, scale, or stakeholders change."
            ),
            "Matching": (
                "The candidate connected {anchor} to relevant work, with enough detail to confirm matching capability and a credible personal contribution. "
                "The account leaves one useful verification area: interviewers should probe the hardest trade-off, how the result was checked, and whether "
                "the candidate could repeat the approach independently."
            ),
            "Moderately Matching": (
                "The answer referred to {anchor}, but only partly connected the situation to a personal decision, precise action, or verified outcome. "
                "This is incomplete evidence. Interviewers should probe the gap directly, asking what the candidate personally changed, how they measured "
                "the result, and what they learned."
            ),
            "Not Matching": (
                "The conversation did not establish capability beyond {anchor}; it contained no sufficiently clear owned action, technical reasoning, or outcome "
                "for this area. Interviewers should treat the criterion as unresolved and use a direct role-specific probe to distinguish missing knowledge "
                "from capability the candidate simply did not express."
            ),
        }
    value = templates.get(rating, templates["Matching"]).format(anchor=anchor)
    if minimum <= word_count(value) <= maximum:
        return value
    # Long names and unusual evidence cannot affect these templates; this is a
    # last-resort guard if their wording is edited without updating the tests.
    return _fallback_remark_45("this area") if minimum >= 40 else _fallback_remark_25("this area")


def _unanswered_remark(name: str, minimum: int) -> str:
    """Factual remark for an item the candidate produced no evidence for."""
    if minimum >= 45:
        candidates = [
            (
                f"No substantive answer addressed {name} during the completed assessment conversation. The candidate did not describe a situation that "
                "shows this capability, so nothing here can be graded on demonstrated behaviour. An interviewer should treat "
                "it as an open question and probe it directly before drawing a conclusion."
            ),
            (
                "No substantive answer addressed this item during the completed assessment conversation. The candidate did not describe a situation that "
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
    *,
    rating: str | None = None,
) -> str:
    """A COMPLETE remark inside the word contract, never truncated.

    The `highly dynamic` instruction is not decoration (spec §10.5): a templated
    remark with the competency name swapped in is exactly what the client
    rejected, so the prompt names the evidence and forbids generic phrasing.

    RUN THROUGH `agent_loop` SINCE 2026-08-06, AND IT FIXED THREE REAL DEFECTS
    -------------------------------------------------------------------------
    The hand-rolled loop this replaced did retry, so the change looks cosmetic.
    It is not:

      1. It APPENDED each correction to the same prompt string, so a second
         miss left the model reading two contradictory instructions ("...had 38
         words, regenerate... ...had 52 words, regenerate..."). `run_loop`
         passes the current reflection as a fresh turn and never accumulates.

      2. It did `except: break`. One transient provider error abandoned every
         remaining attempt and shipped the canned fallback -- on the single most
         client-visible string in the product. A raised attempt is now just a
         failed attempt, and the next one still runs.

      3. NOTHING CHECKED THE NO-NUMBERS RULE. The prompt asked for no score,
         percentage or grade, and a prompt instruction is a request rather than
         a guarantee (the same reasoning that puts a Postgres CHECK behind the
         "Culture" ban). A remark is prose written by a model that has just been
         shown a candidate's answers and is being asked to assess them, which is
         precisely where "demonstrates strong 8/10 capability" comes from. It is
         now a rejection reason, so the model is told and writes it again.
    """
    fallback = (
        rating_differentiated_fallback(evidence, rating, minimum, maximum)
        if rating
        else (_fallback_remark_45(name) if minimum >= 45 else _fallback_remark_25(name))
    )

    system = (
        f"Write one complete, evidence-based assessment remark of exactly {minimum}-{maximum} words "
        f"for '{name}'. Ground every clause in the specific evidence supplied: quote or paraphrase what "
        "this candidate actually said. Do not use templated phrasing that would fit any candidate, and "
        "do not include a score, percentage, grade, recommendation, or heading. "
        + (
            {
                "Highly Matching": "Cite the specific example, personal action, and outcome. ",
                "Matching": "Confirm the demonstrated evidence and name exactly one useful probe area. ",
                "Moderately Matching": "Diagnose the precise partial gap and name what needs probing. ",
                "Not Matching": "State what was absent and propose a probe that distinguishes missing knowledge from unexpressed capability. ",
            }.get(rating or "", "")
        )
        + f"Evidence: {evidence}"
    )

    async def execute(reflection: str) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": "Return only the remark."},
        ]
        if reflection:
            messages.append({"role": "user", "content": reflection})
        return (
            await llm_router.chat_completion(
                "report_synthesis", messages, session=None
            )
        ).strip()

    def evaluate(value: str) -> agent_loop.Critique:
        defects: list[agent_loop.Defect] = []
        if not value:
            defects.append(
                agent_loop.Defect(
                    "empty",
                    f"remark.{name}",
                    "return the remark itself, not an empty response",
                )
            )
        words = word_count(value)
        if not (minimum <= words <= maximum):
            defects.append(
                agent_loop.Defect(
                    "length",
                    f"remark.{name}",
                    (
                        f"write between {minimum} and {maximum} words; the previous "
                        f"attempt was {words}"
                    ),
                )
            )
        if conversation_guardrails.contains_forbidden_number(value):
            defects.append(
                agent_loop.Defect(
                    "numeric_score",
                    f"remark.{name}",
                    (
                        "state no score, percentage, rating out of a total, or "
                        "percentile; describe the evidence in words only"
                    ),
                )
            )
        defects.extend(
            agent_loop.banned_phrase_gate(
                value,
                REPORT_BANNED_PHRASES,
                location=f"remark.{name}",
            ).defects
        )
        evidence_terms = {
            token
            for token in re.findall(r"[a-z0-9]+", evidence.casefold())
            if len(token) >= 5
        }
        output_terms = set(re.findall(r"[a-z0-9]+", value.casefold()))
        if evidence_terms and not evidence_terms.intersection(output_terms):
            defects.append(
                agent_loop.Defect(
                    "evidence_anchor",
                    f"remark.{name}",
                    "quote or paraphrase at least one concrete term from the supplied evidence",
                )
            )
        return agent_loop.reject_defects(*defects) if defects else agent_loop.ok()

    result = await agent_loop.run_loop(
        name="report_remark",
        execute=execute,
        evaluate=evaluate,
        fallback=fallback,
        # Background: this runs inside the scoring Celery task, nobody is
        # watching, and the alternative to one more attempt is a canned string
        # in a report a client reads.
        max_attempts=agent_loop.BACKGROUND_ATTEMPTS,
        deadline_seconds=agent_loop.BACKGROUND_DEADLINE,
        max_generated_tokens=agent_loop.BACKGROUND_TOKEN_BUDGET,
    )
    return result.value


class AssessmentState(TypedDict, total=False):
    session: AsyncSession
    job: Job
    link: JobCandidateLink
    profile: Profile | None
    transcript: list[dict[str, Any]]
    answers: dict[str, list[str]]
    questions: list[CandidateTechnicalQuestion]
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
                    state["session"],
                    name,
                    evidence,
                    *MATCHING_REMARK_WORDS,
                    rating=grade_for_percent(max(0, min(100, score))),
                ),
                "ordinal": ordinal,
            }
        )
    return result


async def technical_node(state: AssessmentState) -> dict:
    """Score every technical answer against THAT question's own rubric (spec §5),
    then report ONE dimension per distinct JD skill.

    A grade-sized plan asks several questions about the same skill, so scoring
    stays per-question and rubric-bound while the report aggregates those scores
    into a single entry for the skill. `report_dimensions` is UNIQUE on
    (report_id, category, name), so emitting one row per question would raise
    IntegrityError on any plan that probed a skill twice -- and the plan probes
    every declared skill several times by design (technical_interview.skill_plan).

    The questions are this CANDIDATE's, written during their own conversation,
    and each carries the rubric generated with it. Grouping by `skill` is what
    keeps the report comparable across candidates even though no two of them
    were asked the same words: the skills are the deterministic part.

    Technical items are not a rendered section of the PPI report (§10.3), but
    they ARE scored and they anchor suggested interview questions (§10.3, last
    bullet), so they are stored and returned.
    """
    answers = state.get("answers") or answers_by_key(state.get("transcript"))
    mode = "no_transcript" if not answers else "llm_rubric"

    by_skill: dict[str, list[CandidateTechnicalQuestion]] = {}
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
                state["session"],
                skill,
                evidence,
                *MATCHING_REMARK_WORDS,
                rating=grade_for_percent(aggregate),
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
                    rating=grade_for_percent(score),
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
        "Ask {name}: walk through a recent case, the choice you made, and the result you owned.",
        "Explore {name}: what would you do differently now, and what changed your view?",
        "Probe {name}: walk through a case where your approach did not work, and how you recovered.",
        "On {name}, ask for the evidence they would point to that the outcome actually held.",
        "For {name}, ask which constraint most changed the solution and why.",
        "On {name}, ask how they verified the result rather than assuming it worked.",
        "For {name}, ask what they personally implemented and what belonged to the wider team.",
        "Probe {name} by changing one key constraint and asking how their approach would adapt.",
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


async def generate_suggested_questions(
    session: AsyncSession,
    *,
    job_title: str,
    dimensions: list[dict[str, Any]],
) -> list[str]:
    """Generate gap-anchored probes through the shared bounded loop.

    Report synthesis is called by the Celery assessment task, so this
    multi-attempt generation never adds latency to an HTTP request.
    """
    fallback = _suggested_questions(dimensions)
    anchorable = [
        row for row in dimensions if row["category"] != CATEGORY_MATCHING
    ]
    ordered = sorted(anchorable, key=lambda item: item["score"])
    gaps = [
        {
            "name": row["name"],
            "rating": grade_for_percent(row["score"]),
            "evidence_summary": row.get("remark")
            or "No answer evidence was recorded.",
        }
        for row in ordered
    ]
    payload = json.dumps(
        {"job_title": job_title, "evidence_gaps": gaps},
        ensure_ascii=False,
    )

    async def execute(reflection: str) -> list[str]:
        messages = [
            {"role": "system", "content": _PROBE_PROMPT},
            {"role": "user", "content": payload},
        ]
        if reflection:
            messages.append({"role": "user", "content": reflection})
        raw = await llm_router.chat_completion(
            "report_synthesis",
            messages,
            response_format_json=True,
            session=None,
        )
        parsed = json.loads(raw)
        return [
            str(item).strip()
            for item in parsed.get("probes", [])
            if str(item).strip()
        ]

    def evaluate(probes: list[str]) -> agent_loop.Critique:
        defects: list[agent_loop.Defect] = []
        if not 8 <= len(probes) <= 10:
            defects.append(
                agent_loop.Defect(
                    "count",
                    "interview_probes",
                    (
                        "return eight to ten probes; the previous attempt "
                        f"returned {len(probes)}"
                    ),
                )
            )
        names = [str(row["name"]) for row in ordered]
        not_matching = [
            str(row["name"])
            for row in ordered
            if grade_for_percent(row["score"]) == "Not Matching"
        ]
        if not_matching:
            first_block = probes[: min(len(not_matching), len(probes))]
            missing_priority = [
                name
                for name in not_matching
                if not any(name.casefold() in probe.casefold() for probe in first_block)
            ]
            if missing_priority:
                defects.append(
                    agent_loop.Defect(
                        "priority",
                        "interview_probes",
                        (
                            "place probes for every Not Matching criterion first; "
                            "the first block missed " + ", ".join(missing_priority)
                        ),
                    )
                )
        for index, probe in enumerate(probes):
            if names and not any(
                name.casefold() in probe.casefold() for name in names
            ):
                defects.append(
                    agent_loop.Defect(
                        "role_anchor",
                        f"interview_probes[{index}]",
                        "name the exact supplied skill or competency this probe investigates",
                    )
                )
            if conversation_guardrails.contains_forbidden_number(probe):
                defects.append(
                    agent_loop.Defect(
                        "numeric_score",
                        f"interview_probes[{index}]",
                        "remove scores, percentages, ratings out of a total, and percentiles",
                    )
                )
        defects.extend(
            agent_loop.banned_phrase_gate(
                "\n".join(probes),
                REPORT_BANNED_PHRASES,
                location="interview_probes",
            ).defects
        )
        defects.extend(
            agent_loop.similarity_gate(
                probes,
                maximum=0.84,
                location="interview_probes",
            ).defects
        )
        return agent_loop.reject_defects(*defects) if defects else agent_loop.ok()

    result = await agent_loop.run_loop(
        name="report_interview_probes",
        execute=execute,
        evaluate=evaluate,
        fallback=fallback,
        max_attempts=agent_loop.BACKGROUND_ATTEMPTS,
        deadline_seconds=agent_loop.BACKGROUND_DEADLINE,
        max_generated_tokens=agent_loop.BACKGROUND_TOKEN_BUDGET,
    )
    return result.value


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
        rating=grade_for_percent(overall_score),
    )

    probes = await generate_suggested_questions(
        session,
        job_title=state["job"].title,
        dimensions=dimensions,
    )

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

    # This candidate's OWN technical questions, each carrying the rubric that
    # was written with it. `ensure_slots` is idempotent and creates nothing when
    # rows already exist, so this is a read on every normal run; it repairs only
    # the case where a report is being written for a link that never opened a
    # conversation, which the "no transcript" report path deliberately allows.
    questions = await technical_interview.ensure_slots(session, job, link)

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
