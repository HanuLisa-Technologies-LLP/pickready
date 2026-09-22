"""Resume-aware pre-fill and the 40-question ceiling (vivekium feature 2, C2).

THE RULING. The owner accepted C2's trade: the question COUNT is a function
of the individual's resume, capped at `assessment_question_ceiling` (40),
and "a criterion the resume already evidences is pre-filled and skipped
rather than asked", so the matrix still holds a value for every dimension.
What survives of the 2026-08-05 rule is the ORDER: which criteria exist and
their sequence stay deterministic per job; only how many are spoken aloud
varies per candidate.

WHAT COUNTS AS "THE RESUME ALREADY EVIDENCES IT". Two signals, BOTH
required, both deterministic:

* the question's own `resume_anchor` is a SUBSTANTIVE passage
  (`MIN_ANCHOR_CHARS`), not a keyword graze, because the anchor is the
  generator's citation of where the resume speaks to this competency; and
* the competency's name appears in the candidate's PARSED skills, the
  resume's own claim list, matched on word boundaries (the disqualifier
  lesson: "hold" contains "old").

A pre-filled question is NEVER silently dropped: its row keeps its rubric,
the conversation records the exchange with `answer_label="resume_prefill"`
so the transcript says exactly where the words came from, and the scorer
scores the resume's evidence for that criterion. A candidate is never asked
to re-type a fact the platform already holds, which is the brief's sentence.

THE CEILING trims what would still be ASKED past 40, lowest weight first
and latest ordinal first among equals, so the essentials written first
survive. Trimmed rows are never created at all.

A BEHAVIOURAL COMPETENCY IS NEVER PRE-FILLED (2026-09-22)
-----------------------------------------------------------
This module shipped with NO category exclusion at all, so a behavioural
competency whose name happened to appear in the candidate's parsed skills,
beside a substantive anchor, was recorded and skipped exactly like a
technical one. That was already wrong under the 2026-07-30 rule that the
behavioural half is per-job and graded on the account a person gives; the
Portable plus Job Specific split of change request 23 makes it the single
sharpest edge in the product, because a behavioural dimension is the one
thing that layer forbids reusing from any source whatsoever.

`category` is now a REQUIRED keyword on `evidence_for`. Required rather
than defaulted, deliberately: a default is what let this defect exist, and
the next caller of this function must decide rather than inherit.
"""
from __future__ import annotations

import re
from typing import Any, Protocol

#: A substantive resume passage, below which an anchor is a keyword graze.
MIN_ANCHOR_CHARS = 80

#: The transcript label a pre-filled exchange carries. Provenance, visible
#: wherever the transcript is read; never one of the classifier's labels.
ANSWER_LABEL = "resume_prefill"

#: The label a PORTABLE pre-fill carries instead. A different word because it
#: is a different provenance: the resume label says "their resume says so",
#: this one says "their standing record says so, from another application".
#: Reading one as the other would misreport where an answer came from, which
#: is the whole job of a transcript label.
PORTABLE_ANSWER_LABEL = "portable_evidence"

#: `candidate_questions.prefill_source` values, and the mapping from one to
#: the transcript label. DATA rather than a conditional at the recording site,
#: so a third pre-fill source cannot be added without a label being chosen for
#: it.
PREFILL_SOURCE_RESUME = "resume_anchor"
PREFILL_SOURCE_PORTABLE = "portable_evidence"
_LABEL_FOR_SOURCE: dict[str, str] = {
    PREFILL_SOURCE_RESUME: ANSWER_LABEL,
    PREFILL_SOURCE_PORTABLE: PORTABLE_ANSWER_LABEL,
}

#: NEVER pre-filled, from the resume or from anything else. Restated as a
#: literal rather than imported from `ppi`, which imports this module;
#: `tests/test_resume_prefill.py` pins it against `ppi.CATEGORY_BEHAVIOURAL`
#: in both directions, the same bargain `evidence_confidence` makes.
NEVER_PREFILLED_CATEGORIES: frozenset[str] = frozenset({"behavioural"})

#: Bound on the stored pre-filled answer text.
MAX_PREFILL_CHARS = 1200


class _SlotLike(Protocol):
    prompt: str
    resume_anchor: str | None
    question_type: str
    weight: float
    index: int


def _skill_names(parsed_fields: Any) -> list[str]:
    if not isinstance(parsed_fields, dict):
        return []
    skills = parsed_fields.get("skills")
    if not isinstance(skills, list):
        return []
    return [str(s).strip() for s in skills if str(s).strip()]


def _named_in(text: str, name: str) -> bool:
    """Word-boundary, case-insensitive containment either way round."""
    if not name or not text:
        return False
    pattern = rf"(?<!\w){re.escape(name.strip().casefold())}(?!\w)"
    return re.search(pattern, text.casefold()) is not None


def prefill_text(anchor: str) -> str:
    """The stored answer for a pre-filled question: the resume's own words,
    said to be the resume's own words."""
    return (
        "Pre-filled from the candidate's resume, which already evidences "
        f"this: {anchor.strip()}"
    )[:MAX_PREFILL_CHARS]


def answer_label_for(prefill_source: str | None) -> str:
    """The transcript label an exchange pre-filled from `prefill_source` carries.

    Falls back to the resume label ONLY for a row written before
    `prefill_source` existed, where the resume anchor was the one mechanism
    that could have produced the answer, so the label is a true statement
    about it rather than a default standing in for an unknown.
    """
    return _LABEL_FOR_SOURCE.get(str(prefill_source or ""), ANSWER_LABEL)


def evidence_for(
    *,
    competency_name: str,
    category: str,
    resume_anchor: str | None,
    parsed_fields: Any,
    question_type: str,
    text_types: frozenset[str],
) -> str | None:
    """The pre-filled answer, or None when the resume does not settle it.

    A BEHAVIOURAL COMPETENCY IS REFUSED FIRST, before the anchor or the skill
    list is read, because there is no evidence strong enough to change the
    answer: a behavioural dimension is graded on the account a person gives of
    a situation under this job's framing, and a resume bullet is not an
    account. See the module docstring for the defect this closes.

    Only TEXT-answered types are ever pre-filled: an MCQ or a coding task is
    an exercise, and a resume cannot sit an exercise.
    """
    if str(category) in NEVER_PREFILLED_CATEGORIES:
        return None
    if question_type not in text_types:
        return None
    anchor = (resume_anchor or "").strip()
    if len(anchor) < MIN_ANCHOR_CHARS:
        return None
    for skill in _skill_names(parsed_fields):
        if _named_in(competency_name, skill) or _named_in(skill, competency_name):
            return prefill_text(anchor)
    return None


def trim_to_ceiling(
    slots: list[Any], *, prefilled_indexes: set[int], ceiling: int
) -> set[int]:
    """The slot indexes the ceiling removes. Deterministic: lowest weight
    first, then the LATEST ordinal among equals, so the essentials written
    first survive. Pre-filled slots cost nothing to ask and are never
    trimmed; their evidence is already on file."""
    asked = [s for s in slots if s.index not in prefilled_indexes]
    excess = len(asked) - ceiling
    if excess <= 0:
        return set()
    order = sorted(asked, key=lambda s: (s.weight, -s.index))
    return {s.index for s in order[:excess]}
