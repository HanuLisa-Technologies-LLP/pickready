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
"""
from __future__ import annotations

import re
from typing import Any, Protocol

#: A substantive resume passage, below which an anchor is a keyword graze.
MIN_ANCHOR_CHARS = 80

#: The transcript label a pre-filled exchange carries. Provenance, visible
#: wherever the transcript is read; never one of the classifier's labels.
ANSWER_LABEL = "resume_prefill"

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


def evidence_for(
    *,
    competency_name: str,
    resume_anchor: str | None,
    parsed_fields: Any,
    question_type: str,
    text_types: frozenset[str],
) -> str | None:
    """The pre-filled answer, or None when the resume does not settle it.

    Only TEXT-answered types are ever pre-filled: an MCQ or a coding task is
    an exercise, and a resume cannot sit an exercise.
    """
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
