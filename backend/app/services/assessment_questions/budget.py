"""How many questions a candidate is asked: the per-grade ranges and the close rule.

Carved out of `services/ppi.py` on 2026-09-24 (PLAN-p3 WP0) as a PURE MOVE,
with no behaviour change. The matrix (`services/ppi`) and the question budget
are different questions: the matrix is per JOB, the budget bounds how many
questions probe it. This module reads the matrix's category constants, so
`ppi.matrix_is_complete` and `ppi._matrix_payload` import the ceiling and the
range from here INSIDE the function: a module-level import in both directions
would close a cycle.
"""
from __future__ import annotations

from app.services.ppi import (
    CATEGORY_BEHAVIOURAL,
    CATEGORY_MUST_HAVE,
    CATEGORY_NICE_TO_HAVE,
)


__all__ = [
    "DEFAULT_GRADE",
    "GRADE_QUESTION_RANGES",
    "STEM_GRADE_QUESTION_RANGES",
    "TYPICAL_SPLITS",
    "conversation_may_close",
    "max_questions",
    "min_questions",
    "resolve_question_range",
    "resolve_question_target",
    "typical_split",
]


# ── Question volume by role type and grade (Master Directive Part 3 §6) ─────
# A RANGE per (role classification, grade), resolved ONCE per job at setup
# from how many items that job's matrix actually holds -- not a single fixed
# number per grade, and not a number chosen per candidate.
#
# THE DIRECTIVE'S TABLE REPLACED THE OLD SINGLE-RANGE ONE (spec §5.4), in
# direction as well as in numbers: STEM roles probe DEEPER at every grade
# (Vaada's 25-35 exchange budget vs 15-20, Part 3 §1), and seniority now adds
# questions rather than removing them. The directive's STEM rows are keyed by
# seniority words this platform does not store (Junior/Mid/Senior/Principal);
# they are mapped onto the four stored grades by role: a non-managerial STEM
# IC gets the Mid-level band, a managerial one the Senior/Lead band, and
# leadership/CXO the Principal/Architect band.

#: grade -> (minimum total, maximum total) -- NON-STEM roles (the default).
GRADE_QUESTION_RANGES: dict[str, tuple[int, int]] = {
    "non_managerial": (12, 18),
    "managerial": (15, 22),
    "leadership": (18, 25),
    "cxo": (18, 25),
}

#: grade -> (minimum total, maximum total) -- STEM roles (Part 3 §6).
STEM_GRADE_QUESTION_RANGES: dict[str, tuple[int, int]] = {
    "non_managerial": (18, 28),
    "managerial": (22, 35),
    "leadership": (25, 38),
    "cxo": (25, 38),
}


def _grade_ranges(role_classification: str | None) -> dict[str, tuple[int, int]]:
    """The job's STEM flag is the input to this logic (Part 3 §6). None or an
    unknown value resolves to the non-STEM table, same direction as every
    other fallback in the feature."""
    if role_classification == "STEM":
        return STEM_GRADE_QUESTION_RANGES
    return GRADE_QUESTION_RANGES

#: grade -> {aspect: (low, high)}. ILLUSTRATIVE, and the spec says so in as many
#: words: "typical, illustrative sub-splits ... not a rigid per-job formula".
#: They are held here because they are the client's stated shape of a balanced
#: interview and they steer the remainder allocation when the matrix has fewer
#: items than the grade's floor. Nothing REFUSES a split that falls outside
#: them; only the grade TOTAL is enforced.
TYPICAL_SPLITS: dict[str, dict[str, tuple[int, int]]] = {
    "non_managerial": {
        CATEGORY_MUST_HAVE: (4, 7),
        CATEGORY_NICE_TO_HAVE: (2, 4),
        CATEGORY_BEHAVIOURAL: (6, 8),
    },
    "managerial": {
        CATEGORY_MUST_HAVE: (5, 8),
        CATEGORY_NICE_TO_HAVE: (3, 5),
        CATEGORY_BEHAVIOURAL: (7, 9),
    },
    "leadership": {
        CATEGORY_MUST_HAVE: (5, 8),
        CATEGORY_NICE_TO_HAVE: (3, 5),
        CATEGORY_BEHAVIOURAL: (9, 12),
    },
    "cxo": {
        CATEGORY_MUST_HAVE: (4, 7),
        CATEGORY_NICE_TO_HAVE: (2, 4),
        CATEGORY_BEHAVIOURAL: (11, 14),
    },
}

DEFAULT_GRADE = "non_managerial"


def _grade(grade: str | None) -> str:
    return grade if grade in GRADE_QUESTION_RANGES else DEFAULT_GRADE


def min_questions(grade: str | None, role_classification: str | None = None) -> int:
    return _grade_ranges(role_classification)[_grade(grade)][0]


def max_questions(grade: str | None, role_classification: str | None = None) -> int:
    return _grade_ranges(role_classification)[_grade(grade)][1]


def typical_split(grade: str | None) -> dict[str, tuple[int, int]]:
    return TYPICAL_SPLITS[_grade(grade)]


def resolve_question_range(
    grade: str | None,
    item_count: int,
    role_classification: str | None = None,
) -> tuple[int, int]:
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
    low, high = _grade_ranges(role_classification)[_grade(grade)]
    resolved = max(low, min(high, int(item_count)))
    return low, resolved


def resolve_question_target(
    grade: str | None,
    item_count: int,
    role_classification: str | None = None,
) -> int:
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
    return resolve_question_range(grade, item_count, role_classification)[1]


def conversation_may_close(
    *,
    grade: str | None,
    asked: int,
    total_written: int,
    covered_dimensions: int,
    total_dimensions: int,
    role_classification: str | None = None,
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
    floor = min_questions(grade, role_classification)
    if asked < floor:
        return False
    if total_dimensions <= 0:
        return False
    if covered_dimensions < total_dimensions:
        return False
    return asked < total_written


# Import-time integrity checks -- these ranges are a product contract
# (Master Directive Part 3 §6).
assert set(GRADE_QUESTION_RANGES) == {"non_managerial", "managerial", "leadership", "cxo"}
assert set(STEM_GRADE_QUESTION_RANGES) == set(GRADE_QUESTION_RANGES)
assert set(TYPICAL_SPLITS) == set(GRADE_QUESTION_RANGES)
assert all(low <= high for low, high in GRADE_QUESTION_RANGES.values())
assert all(low <= high for low, high in STEM_GRADE_QUESTION_RANGES.values())
# STEM probes deeper than non-STEM at every grade (Part 3 §1, §6).
assert all(
    STEM_GRADE_QUESTION_RANGES[g][0] >= GRADE_QUESTION_RANGES[g][0]
    and STEM_GRADE_QUESTION_RANGES[g][1] >= GRADE_QUESTION_RANGES[g][1]
    for g in GRADE_QUESTION_RANGES
)
# Every grade's typical split must be able to sit inside its own total range, or
# the illustrative shape would contradict the rule that is actually enforced.
assert all(
    sum(low for low, _ in split.values()) <= GRADE_QUESTION_RANGES[grade][1]
    and sum(high for _, high in split.values()) >= GRADE_QUESTION_RANGES[grade][0]
    for grade, split in TYPICAL_SPLITS.items()
)
