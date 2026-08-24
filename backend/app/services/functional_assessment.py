"""LangGraph orchestration for the ReadyPick PPI Assessment Report.

ONE scoring agent, TWO methods (spec §8). The PPI Scoring Agent consumes the
actual candidate answers, keyed by the `question_key` stamped on every message,
and the method varies by the ITEM the question probed:

  must_have / nice_to_have  graded against THAT question's own stored rubric,
                            one question at a time
  behavioural               graded by judgement across everything said about the
                            competency, because there is no single correct
                            answer to weigh a behavioural account against

Draft v4 removed a node from this graph. There used to be a `technical_scoring`
node beside `ppi_scoring`, because there were two question banks; technical
depth is now assessed by the matrix's Must-have items, so there is one bank, one
scorer, and no seam for a candidate to notice.

Validation is not a scorer and has not been one since 2026-07-30: it is
mandatory fields on the application form, flowing from
`job_candidate_links.validation_json` straight into the report with nothing
scoring, interpreting or judging it (spec §6, §19).

What the client sees, in order (spec §9.3):

  AI Score            the pre-assessment resume snapshot, the job's matching
                      categories, 25-30 word remarks
  Overall Assessment  grade + 45-50 word remark + overall radar
  Must-have           grade + 45-50 word remark each + radar
  Nice-to-have        same
  Behavioural         same
  Validation          the application fields, verbatim, unrated
  Gap Analysis        grouped by aspect, every gap paired with probes grounded
                      in what the candidate actually said

Two rules that live in this module and nowhere else:

  * the Must-have HARD CAP (spec §5.5) -- one Not Matching Must-have item holds
    the Overall Grade to Moderately Matching whatever else the candidate scored;
  * report synthesis does not finalise until scoring completes (spec §19),
    which the graph's join edge enforces rather than a convention.

Every grade is one of four WORDS (services/rating). No number reaches a client
from this module.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime, timezone
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
)
from app.models.candidate import Candidate, JobCandidateLink, Profile
from app.models.job import Job
from app.services import (
    agent_loop,
    answer_quality,
    conversation_guardrails,
    gap_analysis,
    llm_router,
    matching_categories,
    ppi,
    ppi_interview,
    rating,
)
from app.services.application_validation import MANDATORY_KEYS, VALIDATION_FIELDS
from app.prompts import registry
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
    "GRADE_QUESTION_RANGES",
    "MATCHING_DIMENSIONS",
    "PROBE_REMARK_WORDS",
    "RADAR_BANDS",
    "REPORT_CATEGORIES",
    "assessment_graph",
    "band_index_for",
    "build_gap_analysis",
    "build_radar_charts",
    "infer_grade",
    "infer_grade_fallback",
    "must_have_cap_applies",
    "rating_label",
    "run_assessment",
    "word_count",
]

#: Re-exported so a caller has one import for the whole assessment contract.
#: Ranges, not counts: Draft v4 resolves a total per JOB from the grade's range
#: and the size of that job's matrix (spec §5.4).
GRADE_QUESTION_RANGES = ppi.GRADE_QUESTION_RANGES

GRADE_NAMES: tuple[str, ...] = tuple(GRADE_QUESTION_RANGES)

# ── The AI Score's matching categories (spec §3.2) ───────────────────────────
# NO WEIGHTS. The spec is explicit: "make sure there are no mathematical
# weightage for giving these AI comments". The parameters were previously
# described to the client as "35% role-fit weighting" and similar, which both
# leaked a number and implied an arithmetic the comments do not perform. Each
# category is judged and reported on its own terms.
#
# THE LIST IS NOW PER JOB. `services/matching.DEFAULT_CATEGORIES` is the AI's
# proposed default five and `job_matching_categories` holds what the recruiter
# finalised, so the tuple below is no longer the product's fixed set -- it is
# the fallback shape for a job whose categories predate the change, and the
# report reads the job's own list when it has one.
CATEGORY_MATCHING = "matching"

#: LEGACY. Reports written before Draft v4 carry rows under this category,
#: scored against the standalone technical bank that no longer exists. Nothing
#: writes it any more; it is read so a historic report still renders.
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

#: Report section order, exactly as §9.3 lists it. `technical` trails the list
#: because a historic report may still carry rows under it; nothing new does.
REPORT_CATEGORIES: tuple[str, ...] = (
    CATEGORY_MATCHING,
    ppi.CATEGORY_MUST_HAVE,
    ppi.CATEGORY_NICE_TO_HAVE,
    ppi.CATEGORY_BEHAVIOURAL,
    CATEGORY_TECHNICAL,
)

#: Word contracts (spec §9.5).
MATCHING_REMARK_WORDS = (25, 30)
PPI_REMARK_WORDS = (45, 50)
#: A Gap Analysis probe is a prompt for the interviewer, not a written
#: assessment, and is capped shorter than an item remark for exactly that
#: reason (spec §9.5).
PROBE_REMARK_WORDS = (25, 30)

# Score assigned when a question was never answered -- factual, not punitive.
UNANSWERED_SCORE = 25

#: Ordered best-to-worst grade labels, for the radar legend and colour ramp.
RADAR_BANDS: tuple[str, ...] = GRADES


#: Re-exported from the module that owns the Gap Analysis section, so a caller
#: has one import for the whole report contract and the two cannot drift.
build_gap_analysis = gap_analysis.build_gap_analysis
must_have_cap_applies = gap_analysis.must_have_cap_applies


def rating_label(score: int | float | None) -> str | None:
    """The client-facing grade for an internal 0-100 score.

    Thin alias over `services.rating.grade_for_percent`, kept because a good
    deal of the codebase already imports it from here. The scale itself lives
    in one module so the assessment and the AI Score cannot drift apart.
    """
    return grade_for_percent(score)


def word_count(value: str) -> int:
    return len(re.findall(r"\b[\w&'-]+\b", value))


# ── Radar charts (spec §9.4) ─────────────────────────────────────────────────
# FOUR charts per candidate: Overall, Must-have, Nice-to-have, Behavioural
# Competencies. Each plots TWO shapes on the same axes -- what the job requires
# and what the candidate demonstrated -- so a reader sees at a glance where the
# candidate exceeds, meets, or falls short.
#
# No number appears on an axis, a data label, or a tooltip. `*_index` is a
# RENDERING COORDINATE (1..4): a radar has no geometry without a radius, and the
# four grades ARE the radial axis. The underlying 0-100 score stays internal.

RADAR_CHART_KEYS: tuple[str, ...] = ("overall", *ppi.CATEGORIES)

RADAR_CHART_TITLES: dict[str, str] = {
    "overall": "Overall",
    **{category: ppi.CATEGORY_LABELS[category] for category in ppi.CATEGORIES},
}

#: The legend below every chart, by word only (spec §9.4).
RADAR_SERIES: tuple[str, ...] = ("Job Requirement", "Candidate Assessment")

#: ASSUMPTION (2026-07-30, still open with the client): §9.4 asks for an
#: "Overall" radar without saying what its axes are. It plots the three PPI
#: aspect aggregates -- one axis per aspect, both shapes derived from the same
#: rows the sections render, so a chart can never disagree with the text beside
#: it.
#:
#: Under Draft v4 this is a cleaner three-spoke chart than it was: technical is
#: no longer a separate category that would have needed a fabricated job
#: requirement to become a fourth spoke. It is inside Must-have, with a real
#: required level like every other item.
OVERALL_AXES: tuple[tuple[str, str], ...] = tuple(
    (category, ppi.CATEGORY_LABELS[category]) for category in ppi.CATEGORIES
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

    for category in ppi.CATEGORIES:
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
    if job.assessment_grade in GRADE_NAMES:
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


# ── The technical half no longer exists as a half ─────────────────────────
# There was a standalone technical track here: its own question bank, its own
# scorer, its own report category with no place in the rendered report. Draft v4
# folded it into the PPI matrix's Must-have items, where it is scored on the
# same footing as every other item and surfaces in a section the client actually
# reads.
#
# What survived is the RULE, not the track: a Must-have or Nice-to-have answer
# is graded against the rubric belonging to the question that produced it. See
# `services/ppi_interview.write_question`, which writes question and rubric in
# one call, and `_score_item` below, which is the only place either is read.


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
        rubric = ppi_interview.DEFAULT_RUBRIC
    return "; ".join(f"{band.replace('_', '-')}: {text}" for band, text in rubric.items())


async def _llm_score(session: AsyncSession | None, question: str, rubric: dict | None, answer: str) -> int | None:
    """Score one answer 0-100 strictly against the supplied rubric bands."""
    try:
        raw = await llm_router.chat_completion(
            "behavioral_assessment",
            [
                {
                    "role": "system",
                    "content": registry.render(
                        "assessment_answer_scoring",
                        rubric_bands=_rubric_text(rubric),
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

#: Was read from `backend/prompts/`, a SECOND prompt directory holding this one
#: file while `app/prompts/` held fourteen. Both reached the image only because
#: the Dockerfile does `COPY . .`; the next one added would not have. One
#: directory now, one loader.

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


#: Capitalised words that are ordinary English rather than a named technology,
#: employer or product. Without this the check would flag every sentence that
#: begins with "Interview" or "Evidence", which is most of them.
_ORDINARY_CAPITALISED: frozenset[str] = frozenset({
    "a", "an", "the", "this", "that", "these", "those", "they", "their",
    "he", "she", "his", "her", "it", "its", "we", "our", "you", "your",
    "and", "but", "for", "with", "without", "while", "when", "where", "which",
    "who", "whose", "what", "how", "why", "if", "then", "than", "there",
    "here", "both", "each", "either", "neither", "all", "any", "some", "no",
    "not", "only", "also", "however", "although", "though", "because",
    "candidate", "candidates", "interview", "interviews", "interviewer",
    "evidence", "experience", "answers", "answer", "discussion", "discussions",
    "role", "roles", "work", "team", "teams", "delivery", "design", "designs",
    "further", "strong", "clear", "limited", "little", "more", "most",
    "recent", "recently", "across", "during", "given", "described",
    "demonstrated", "confirmed", "probing", "probe", "probes", "should",
    "would", "could", "may", "can", "will", "must", "one", "two", "three",
    "several", "many", "few", "at", "in", "on", "of", "to", "from", "by",
    "as", "is", "was", "were", "are", "be", "been", "has", "had", "have",
    "do", "does", "did", "so", "such", "under", "over", "into", "about",
})


def invented_terms(value: str, *, evidence: str, name: str) -> list[str]:
    """Proper nouns in a remark that appear NOWHERE in its source.

    The loop already checks the other direction -- a remark must quote at least
    one concrete term from the evidence. That catches a remark that says
    nothing; it does not catch one that says too much. "Demonstrates strong
    Kubernetes experience" for a candidate who never mentioned Kubernetes is
    the failure mode a client would actually notice, and it passed every
    existing check: right length, no number, anchored on some other term.

    Deliberately CONSERVATIVE, in the direction that matters. A guard that
    rejects a good remark costs a round of latency every time and, worse,
    teaches the next reader to loosen it. So a token is only reported when all
    of these hold:

      * it is capitalised and NOT at the start of a sentence (a sentence-
        initial capital carries no information about proper-noun-ness);
      * it is not an ordinary English word;
      * it does not appear in the evidence or in the dimension's own name --
        the name comes from the job's framework, so naming the skill being
        assessed is always legitimate;
      * it is not a plain morphological variant of something that does (so
        "Kafka" in the evidence permits "Kafka's").

    Returns the offending tokens, so the rejection fed back to the model can
    name them. `agent_loop`'s whole contract is that a rejection is an
    instruction.
    """
    haystack = f"{evidence} {name}".casefold()
    known = set(re.findall(r"[a-z0-9+#.]+", haystack))

    invented: list[str] = []
    # Split into sentences so the first word of each can be exempted.
    for sentence in re.split(r"(?<=[.!?])\s+", value or ""):
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9+#.\-]*", sentence)
        for position, token in enumerate(tokens):
            if position == 0:
                continue  # sentence-initial capitalisation means nothing
            if not token[:1].isupper():
                continue
            folded = token.casefold().strip(".")
            if folded in _ORDINARY_CAPITALISED or len(folded) < 3:
                continue
            stem = folded.rstrip("s").rstrip("'")
            if folded in known or stem in known:
                continue
            if any(stem and stem in candidate for candidate in known):
                continue
            invented.append(token)
    # Stable, de-duplicated, so the same defect reads the same way twice.
    return sorted(set(invented))


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
        # The other direction. The anchor check above catches a remark that
        # references nothing; this catches one that references something that
        # was never there.
        fabricated = invented_terms(value, evidence=evidence, name=name)
        if fabricated:
            defects.append(
                agent_loop.Defect(
                    "invented_term",
                    f"remark.{name}",
                    (
                        "do not name anything the candidate did not mention: "
                        + ", ".join(fabricated[:3])
                        + ". Write only about what is in the evidence supplied."
                    ),
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
    competencies: list[JobCompetency]
    candidate_questions: list[CandidateQuestion]
    grade: str
    matching: list[dict[str, Any]]
    ppi: list[dict[str, Any]]
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
    # THE JOB'S OWN CATEGORIES, not the product's (spec §3.2). Read from the
    # job rather than from `MATCHING_DIMENSIONS` so a report renders the same
    # categories the candidate was actually ranked against; the module constant
    # is the fallback for a job whose list predates the change.
    categories = await matching_categories.resolved_categories(
        state["session"], state["job"].id
    )
    result = []
    for ordinal, (key, name, description) in enumerate(categories, 1):
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


#: The judgement standard for a Behavioural item. It is NOT a per-question
#: rubric and must not be mistaken for one: it describes what a credible
#: account of a behaviour looks like in general, because there is no single
#: correct answer to a behavioural question to weigh a specific rubric against
#: (spec §8). Every Behavioural item in the product is judged against this same
#: standard, which is what makes two candidates' behavioural grades comparable.
_BEHAVIOURAL_STANDARD = {
    "0_39": "No relevant situation described, or the account contradicts the competency.",
    "40_59": "A thin or generic account with little personal action or outcome.",
    "60_74": "A credible situation with clear personal action and a stated outcome.",
    "75_89": "Several strong situations with judgement, trade-offs, and measurable results.",
    "90_100": "Consistently exceptional accounts showing judgement, impact, and transferable insight.",
}


async def _score_item(
    state: AssessmentState,
    competency: JobCompetency,
    questions: list[CandidateQuestion],
    answers: dict[str, list[str]],
) -> tuple[int, list[str], bool]:
    """Score one matrix item. Returns (score, the answers used, degraded).

    THE DUAL METHOD, AND IT IS THE WHOLE POINT OF THIS FUNCTION
    -----------------------------------------------------------
    Must-have and Nice-to-have answers are graded against the rubric written for
    the specific question that produced them, one question at a time, and the
    item's score is the mean of those. That is what makes a grade defensible
    when a client asks why a candidate was rated as they were: the answer is a
    rubric written for the exact question they were asked.

    Behavioural answers are graded together, once, against
    `_BEHAVIOURAL_STANDARD`. Splitting them per question would be worse, not
    better: a competency is demonstrated across a conversation, and three
    separate judgements of three fragments average out exactly the pattern the
    aspect exists to see.

    A question with no rubric on a rubric-scored item falls back to the general
    standard rather than being skipped. That happens when generation degraded
    and the candidate read the pre-generated question, and dropping the answer
    would silently narrow the evidence rather than visibly degrade the score.
    """
    rubric_scored = ppi_interview.is_rubric_scored(competency.category)
    degraded = False

    if rubric_scored:
        scores: list[int] = []
        used: list[str] = []
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
            used.append(answer)
            score = await _llm_score(
                state["session"],
                question.prompt,
                question.rubric_json or _BEHAVIOURAL_STANDARD,
                answer,
            )
            if score is None:
                degraded = True
                score = _stable_score(f"{state['link'].id}:{question.id}:{answer}")
            scores.append(score)
        if not used:
            return UNANSWERED_SCORE, [], degraded
        return _mean(scores), used, degraded

    # Behavioural: one judgement across everything said about the competency.
    collected: list[str] = []
    for question in questions:
        for answer in answers.get(str(question.id), []):
            if answer_quality.is_substantive(answer):
                collected.append(answer)
    if not collected:
        return UNANSWERED_SCORE, [], degraded
    combined = "\n".join(f"- {item}" for item in collected)
    framing = (
        f"Behavioural competency '{competency.name}'"
        f"{': ' + competency.description if competency.description else ''} "
        f"The candidate answered {len(collected)} question(s) probing it."
    )
    score = await _llm_score(state["session"], framing, _BEHAVIOURAL_STANDARD, combined)
    if score is None:
        degraded = True
        score = _stable_score(f"{state['link'].id}:{competency.id}:{combined}")
    return score, collected, degraded


async def ppi_scoring_node(state: AssessmentState) -> dict:
    """THE PPI Scoring Agent -- one agent, two methods (spec §8).

    This replaced two nodes that ran side by side, a technical scorer and a PPI
    scorer. They were two agents because there were two question banks; there is
    one matrix now, so there is one scorer, and the method varies by ITEM TYPE
    rather than by agent.

    One report row per matrix item, in report order, each with a 45-50 word
    remark (§9.5). The item's `required_level` travels ONTO the report row so
    the radar can plot the job's shape even after the job's matrix is later
    edited -- a written report is a permanent record of the criteria it was
    written against.
    """
    answers = state.get("answers") or answers_by_key(state.get("transcript"))
    mode = "no_transcript" if not answers else "llm_rubric"

    questions_by_item: dict[str, list[CandidateQuestion]] = {}
    for question in state.get("candidate_questions") or []:
        questions_by_item.setdefault(str(question.competency_id), []).append(question)

    rows: list[dict[str, Any]] = []
    ordinal_by_category: dict[str, int] = {}
    for competency in state.get("competencies") or []:
        ordinal_by_category[competency.category] = (
            ordinal_by_category.get(competency.category, 0) + 1
        )
        questions = questions_by_item.get(str(competency.id), [])
        score, used, degraded = await _score_item(state, competency, questions, answers)
        if degraded:
            mode = "deterministic_fallback"
        base = {
            "category": competency.category,
            "name": competency.name,
            "description": competency.description,
            "required_level": competency.required_level,
            "ordinal": ordinal_by_category[competency.category],
        }
        if not used:
            rows.append(
                {
                    **base,
                    "score": UNANSWERED_SCORE,
                    "remark": _unanswered_remark(competency.name, PPI_REMARK_WORDS[0]),
                }
            )
            continue
        combined = "\n".join(f"- {item}" for item in used)
        evidence_prefix = (
            "the candidate's own answers, each graded against the rubric written "
            "for the question that produced it: "
            if ppi_interview.is_rubric_scored(competency.category)
            else "the candidate's own answers probing this competency: "
        )
        rows.append(
            {
                **base,
                "score": score,
                "remark": await bounded_remark(
                    state["session"],
                    competency.name,
                    f"{evidence_prefix}{combined[:800]}",
                    *PPI_REMARK_WORDS,
                    rating=grade_for_percent(score),
                ),
            }
        )
    return {"ppi": rows, "ppi_mode": mode}


async def validation_node(state: AssessmentState) -> dict:
    """Carry the application's mandatory fields, and the candidate's full
    profile questionnaire, into the report, UNCHANGED.

    Nothing here is scored, interpreted or judged (spec §7). The candidate's
    answer to "Why does this role interest you?" reaches the recruiter exactly
    as written -- the recruiter, not any agent, decides whether the stated
    interest is genuine.

    The profile's 38 items were missing from this report section entirely
    (2026-08-16 report) -- only the six application-level fields ever reached
    it. Appended after the application fields under the same "fields" list the
    frontend already renders, so no rendering change was needed, only the
    fuller data reaching it.
    """
    from app.services.candidate_profile_form import profile_form_answers

    submitted = state["link"].validation_json or {}
    captured = {key: submitted.get(key) for key in MANDATORY_KEYS}
    candidate = await state["session"].get(Candidate, state["link"].candidate_id)
    profile_fields = [
        {
            "key": item["key"],
            "label": item["question"],
            "value": item["answer"],
            "group": item["group"],
        }
        for item in profile_form_answers(
            candidate.profile_form_json if candidate else None
        )
    ]
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
                    "group": "Application",
                }
                for field in VALIDATION_FIELDS
            ]
            + profile_fields,
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


# The Gap Analysis & Action Plan replaced the suggested-questions section
# entirely (spec 9.6). `_suggested_questions` and `generate_suggested_questions`
# lived here and are DELETED, not deprecated: they generated a flat list of
# eight to ten probes from the item REMARKS, which is a summary, so a probe
# could only ever restate the assessment back at the interviewer. The section
# that replaced them is in `services/gap_analysis`, is grouped by aspect, and
# grounds every probe in what the candidate actually said.


def _evidence_by_item(state: AssessmentState) -> dict[str, list[dict[str, str]]]:
    """What each matrix item was actually asked, and what was actually answered.

    The Gap Analysis agent's grounding input (spec §9.6): a probe has to
    reference the candidate's own claim, and it cannot do that from a remark,
    which is already a summary. Keyed by item NAME because that is what survives
    onto the immutable report row.
    """
    answers = state.get("answers") or answers_by_key(state.get("transcript"))
    by_id = {
        str(competency.id): competency.name
        for competency in state.get("competencies") or []
    }
    evidence: dict[str, list[dict[str, str]]] = {}
    for question in state.get("candidate_questions") or []:
        name = by_id.get(str(question.competency_id))
        if not name:
            continue
        answer = " ".join(answers.get(str(question.id), [])).strip()
        if not answer:
            continue
        evidence.setdefault(name, []).append(
            {"question": question.prompt, "answer": answer[:1500]}
        )
    return evidence


def _gate_report(
    state: "AssessmentState",
    dimensions: list[dict[str, Any]],
    overall: str,
    gaps: dict[str, Any],
    validation: dict[str, Any],
) -> "verification_base.Verdict":
    """Project the assembled report into the shape Siddhi's gate reads.

    An ADAPTER, deliberately, rather than reshaping the report to suit the gate.
    The gate is a contract about what a PRISM report must be true of; the report
    shape is what the renderers and the database already agreed on. Bending
    either to fit the other would couple two things that change for different
    reasons.

    NEVER RAISES. A gate is a guard on the output, and a guard that can fail the
    run it was guarding turns a cosmetic defect into a lost report. On its own
    error it returns a passing verdict carrying a low finding, so the failure is
    recorded without being charged to the candidate -- the same direction every
    degradation path in this codebase takes, because the alternative is a report
    withheld for a reason nobody can see.
    """
    from app.services.agents import gates
    from app.services import verification as verification_base

    try:
        graded = {row["name"]: row.get("grade") for row in dimensions if row.get("name")}
        # CLIENT-VISIBLE FIELDS ONLY in the sections. The first version of this
        # adapter passed the raw dimension rows, and the gate immediately
        # rejected the report with `number_reaches_client` pointing at
        # `evidence_refs[0]` -- an evidence locator like
        # `assessment_messages:1`. The gate was right to scan and wrong only
        # about what it was scanning: a locator is an internal audit handle that
        # exists so a grade can be traced, and it is never rendered. Refs travel
        # on `claims` below, which is where the gate expects to find them.
        def _rendered(row: dict[str, Any]) -> dict[str, Any]:
            return {
                "name": row.get("name"),
                "description": row.get("description"),
                "grade": row.get("grade"),
                "remark": row.get("remark"),
            }

        payload = {
            "ai_score": [
                _rendered(r) for r in dimensions if r.get("category") == CATEGORY_MATCHING
            ],
            "ppi_assessment": [
                _rendered(r) for r in dimensions if r.get("category") != CATEGORY_MATCHING
            ],
            "validation": validation,
            # Compared field by field against what the candidate actually
            # submitted. Nothing scores Validation, so a report that reworded a
            # notice period has fabricated a fact in a document a client decides
            # from.
            "validation_source": dict(state.get("validation") or {}),
            "gap_analysis": gaps,
            "overall_summary": overall,
            # Miti's grades and Siddhi's must be the same grades. They are read
            # from one place here, so this check only has teeth once the two
            # stages are genuinely separate; it is wired now so that the day
            # they are, the check already exists rather than being remembered.
            "grades": graded,
            "miti_grades": graded,
            "claims": [
                {
                    "id": row.get("name"),
                    "text": row.get("remark") or "",
                    "evidence_refs": row.get("evidence_refs") or [],
                }
                for row in dimensions
            ],
        }
        return gates.run_gate("siddhi", payload)
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.warning(
            "functional_assessment.report_gate_failed link_id=%s",
            state["link"].id, exc_info=True,
        )
        return verification_base.verdict(
            "gate:siddhi",
            [
                verification_base.low(
                    "gate_unavailable",
                    "report",
                    "the report quality gate could not run",
                    "Investigate the gate error; the report itself was produced normally.",
                )
            ],
        )


async def synthesis_node(state: AssessmentState) -> dict:
    """Join scoring and validation, write the report (spec §9).

    Waits for the PPI Scoring Agent; the graph's join edge is what enforces
    that, and it is the rule spec §19 states in as many words: report synthesis
    does not finalise until PPI scoring has completed.
    """
    session = state["session"]
    matching = await _matching_dimensions(state)
    ppi_rows = state.get("ppi") or []
    dimensions = _dedupe_dimensions(matching + ppi_rows)

    # The Overall grade is the PPI Assessment's, not the AI Score's: §9.3 puts
    # it at the head of the PPI section, below the AI Score, and the two are
    # deliberately never merged.
    assessed = [row for row in dimensions if row["category"] != CATEGORY_MATCHING]
    overall_score = _mean([row["score"] for row in assessed])

    # ── The Must-have hard cap (spec §5.5) ───────────────────────────────────
    # Applied to the STORED score, not only to the displayed grade, and that is
    # deliberate: the internal score also orders candidate lists, and a
    # candidate with a Not Matching Must-have genuinely should sort below one
    # without. `cap_to_moderately` only ever lowers, so a candidate already
    # grading Not Matching is not promoted into the band the cap exists to keep
    # people out of.
    cap_applied = gap_analysis.must_have_cap_applies(dimensions)
    if cap_applied:
        capped = rating.cap_to_moderately(overall_score)
        logger.info(
            "functional_assessment.must_have_cap link_id=%s from=%s to=%s",
            state["link"].id, overall_score, capped,
        )
        overall_score = capped

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

    gaps = await gap_analysis.build_gap_analysis(
        session, dimensions, _evidence_by_item(state)
    )

    validation = dict(state["validation"])
    scoring_mode = state.get("ppi_mode", "llm_rubric")
    if scoring_mode != "llm_rubric":
        logger.warning(
            "functional_assessment.scoring_mode link_id=%s mode=%s",
            state["link"].id, scoring_mode,
        )

    # ── Siddhi's quality gate, BEFORE the report is written ─────────────────
    #
    # Before, not after, and the ordering is the whole value. A gate that runs
    # after persistence has already let the report reach the candidate table,
    # and a recruiter who opens it in the next thirty seconds sees an unmarked
    # document. Running it first means the flag and the report are written in
    # one transaction and cannot disagree.
    #
    # A FAILING GATE DOES NOT BLOCK THE REPORT. It cannot: refusing to write one
    # would take the product's entire output away over what may be a single
    # ungrounded phrase, and the recruiter would be left with nothing rather
    # than with something imperfect they can judge. It ships marked instead,
    # which is the same trade `reliability/degradation` makes for a stub.
    #
    # There is no retry here on purpose. `agent_loop.run_loop` already bounds
    # regeneration twice over and feeds a rejection back verbatim; a second
    # retry mechanism at this layer would multiply attempts nobody is counting.
    gate_verdict = _gate_report(state, dimensions, overall, gaps, validation)

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
        "gap_analysis_json": gaps,
        # `suggested_probes_json` is deliberately NOT written any more and
        # deliberately NOT dropped. Gap Analysis replaces that section entirely
        # (spec §9.6), and leaving the column in place means a rollback of this
        # release needs no data restore. Reports written before today keep
        # theirs and still render it.
        "synthesized_at": datetime.now(timezone.utc),
        "needs_human_review": not gate_verdict.passed,
        # Issue, location and severity only. A finding's `detail` can quote the
        # report prose, and this column is read from far more places than the
        # report itself.
        "review_findings_json": [
            {
                "severity": finding.severity,
                "issue": finding.issue,
                "location": finding.location,
                "recommendation": finding.recommendation,
            }
            for finding in gate_verdict.findings
        ] or None,
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
    """ONE scorer, joining validation capture at synthesis (spec §8, §19).

    Draft v4 removed a node from this graph and that removal is the point. There
    used to be a `technical_scoring` node running beside `ppi_scoring`, because
    there were two question banks. There is one matrix now, so there is one
    scoring agent, and the method varies by item type inside it rather than by
    agent across the graph.

    `validation_capture` is a node but NOT a scorer: it copies the application's
    mandatory fields into the report shape and touches no model. It runs on the
    same fan-out because synthesis needs its output, not because it judges
    anything (spec §19: validation stays outside the scoring flow).

    The join edge is what makes "report synthesis waits for PPI scoring" a
    property of the graph rather than a convention someone has to remember.
    """
    graph = StateGraph(AssessmentState)
    graph.add_node("ppi_scoring", ppi_scoring_node)
    graph.add_node("validation_capture", validation_node)
    graph.add_node("report_synthesis", synthesis_node)
    graph.add_edge(START, "ppi_scoring")
    graph.add_edge(START, "validation_capture")
    graph.add_edge(["ppi_scoring", "validation_capture"], "report_synthesis")
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

    competencies = await ppi.load_framework(session, job.id)
    if not competencies:
        logger.info("functional_assessment.generating_matrix_on_demand job_id=%s", job.id)
        await ppi.generate_framework(session, job)
        competencies = await ppi.load_framework(session, job.id)

    # This candidate's OWN questions, each rubric-scored one carrying the rubric
    # written with it. Generated on demand only for a link that never opened a
    # conversation, which the "no transcript" report path deliberately allows;
    # on every normal run the rows already exist and this is a read.
    questions = await ppi_interview.load_for_link(session, link.id)
    if not questions:
        questions = await ppi.generate_candidate_questions(session, job, link)

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
            "candidate_questions": questions,
            "competencies": competencies,
            "grade": grade,
        }
    )
    return result["report_id"]
