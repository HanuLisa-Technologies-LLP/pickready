"""Vivekium Profile Intelligence (PPI) -- the per-job evaluation matrix.

PROPRIETARY: PPI is Vivekium's own competency framework, derived from
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

THE SKILLS STEP OWNS THE LIMITS NOW (Vivekium release)
------------------------------------------------------
At most five per bucket, at least one Must-have and one Behavioural, checked at
Save by `services/skills.validate_for_save`. The matrix-level save check that
lived here (`matrix_is_complete`), the A2A matrix artifact (`publish_tatva_matrix`
and its readers) and the grade-sized ceiling went with the Tatva matrix editor.
What survives here is the vocabulary (the three aspects, the culture refusal,
the required-level words) and `load_framework`, the one reader of the active
rows.

"Culture" is refused as a Behavioural Competency, at generation and at save.
Cultural fit cannot be assessed accurately in a single conversation, and PPI
does not claim otherwise.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import JobCompetency
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
    "REQUIRED_LEVEL_SCORES",
    "RUBRIC_SCORED_CATEGORIES",
    "is_forbidden_competency",
    "load_framework",
    "required_level_score",
    "requirement_word",
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


# ── Sutra builds the matrix: `hiring/scorecard.compile_matrix` ──────────────
#
# DELETED 2026-08-29 (spec-doc6 D1, section 4.1 "delete on activation").
#
# `generate_framework` lived here: one model call asking for a whole matrix in
# one pass, with `_fallback_framework` assembling a matrix out of the JD's own
# noun phrases whenever the model was unavailable. Both are gone, along with
# `_normalise`, `_ensure_every_aspect`, `_maximum_total`, `_valid_competency`,
# `_jd_terms`, `_swot_terms`, `_captured_points`, `_consume_swot_evidence` and
# `load_swot`. The prompt file went with them.
#
# TWO THINGS WERE WRONG WITH IT AND ONLY ONE WAS VISIBLE.
#
# The visible one: a weight a model chooses in one pass has no terms to store,
# so the matrix could not carry "which Layer 1 / Layer 2 / Layer 3 input
# produced this and what each contributed" -- which is the product requirement
# this phase is built around.
#
# The quiet one: the fallback produced a matrix that LOOKED like the real
# thing. It was reviewed, approved, and graded against for the life of the job,
# and it rested on nothing but the JD -- which Runbook section 18 calls "almost
# never an accurate specification of the hiring problem". A degradation that
# leaves no trace in what it produces is indistinguishable from success.
#
# What survives in this module is the vocabulary of the three aspects and the
# one reader of a job's active rows. Saving, locking and snapshotting the rows
# are `services/skills` and `services/assessment_contract`; the questions are
# `services/assessment_questions`.


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


def requirement_word(required_level: Any) -> str:
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


# Import-time integrity checks (the budget half moved to
# `assessment_questions/budget.py` with the tables it checks).
assert set(CATEGORY_LABELS) == set(CATEGORIES)
assert RUBRIC_SCORED_CATEGORIES < set(CATEGORIES)
assert set(REQUIRED_LEVEL_SCORES) <= set(GRADES)
