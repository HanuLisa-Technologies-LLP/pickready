"""Recommended Human Validation Points: where a person should look, and why.

THIS IS NOT THE GAP ANALYSIS, AND THE TWO MUST NOT BE MERGED
--------------------------------------------------------------
They answer different questions from different inputs and they will disagree,
which is the point of having both:

    Gap Analysis & Action Plan   is GRADE driven. `gap_analysis.gap_items`
                                 selects every rated item at Moderately
                                 Matching or below, it is unbounded, and it
                                 says nothing about how well evidenced any of
                                 it is. It answers "where is this candidate
                                 weaker than the role needs".

    Human Validation Points      is CONFIDENCE and CONTRADICTION driven. It
                                 selects on the strength of the evidence base
                                 and on what the record disagrees with itself
                                 about, it is bounded at five, and a HIGHLY
                                 MATCHING item resting on the candidate's own
                                 unchecked account belongs in it. It answers
                                 "where is this report least safe to act on".

A strong grade with a thin evidence base is invisible to the first and is the
first row of the second. Merging them would lose exactly that row, which is the
one a recruiter most needs before an offer.

IT RECOMMENDS LOOKING, NEVER DECIDING
---------------------------------------
Nothing here states or implies an outcome. There is no advance, no reject, no
shortlist, no "suitable", and no phrasing that reads as one; every entry names
an area and one thing to ask about it. That is the same rule the Gap Analysis
already publishes at the foot of its own section, and it is the invariant
`hiring/layers.INVARIANTS` protects: a flag routes to a person, it never ends a
candidacy. `tests/test_validation_points.py` sweeps the generated copy for
decision language and fails on a hit.

DETERMINISTIC, AND IT CALLS NO MODEL
--------------------------------------
The whole section is arithmetic over the rated rows plus a fixed catalogue of
probe wordings, in the shape `services/candidate_updates` already uses for the
same reason: this is the surface that exists BECAUSE the evidence was weak, and
a provider outage is exactly when weak evidence most needs saying. A section
that failed at the same time as the thing it reports on would protect nobody.

FEWER THAN THREE IS A REAL STATE
----------------------------------
The brief asks for three to five points. Three is not a floor to pad to: a
report whose evidence base is uniformly strong genuinely has nothing to list,
and inventing a fourth point to reach a count would send an interviewer after a
question the record does not justify asking. The section says what it found,
including when that is nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.services import evidence_confidence
from app.services.rating import GRADES

__all__ = [
    "SECTION_KEY",
    "SECTION_TITLE",
    "MAX_POINTS",
    "DRIVER_CONFIDENCE",
    "DRIVER_BORDERLINE",
    "DRIVER_CONTRADICTION",
    "DRIVERS",
    "NO_POINTS_STATEMENT",
    "ValidationPoint",
    "build",
]

SECTION_KEY = "validation_points"
SECTION_TITLE = "Recommended Human Validation Points"

#: The ceiling the brief states. A list longer than this is not a
#: prioritisation, it is the whole report restated, and an interviewer with
#: nine things to check does none of them.
MAX_POINTS = 5

#: The three drivers, IN PRIORITY ORDER. An area selected by an earlier driver
#: is never listed again by a later one: one area, one reason, so a reader is
#: never told to check the same thing twice for two different stated causes.
DRIVER_CONFIDENCE = "confidence"
DRIVER_BORDERLINE = "borderline"
DRIVER_CONTRADICTION = "contradiction"

DRIVERS: tuple[str, ...] = (
    DRIVER_CONFIDENCE,
    DRIVER_BORDERLINE,
    DRIVER_CONTRADICTION,
)

#: Worst first inside the confidence driver. An area nothing addressed is a
#: sharper call on an interviewer's time than one that was answered but not
#: corroborated, and ordering carries that weight because the section states no
#: severity of its own.
_CONFIDENCE_ORDER: tuple[str, ...] = (
    evidence_confidence.CONFIDENCE_INSUFFICIENT,
    evidence_confidence.CONFIDENCE_LOW,
    evidence_confidence.CONFIDENCE_MODERATE,
)

#: The one sentence an empty section prints, rather than blank space. It states
#: a fact about the RECORD, not a verdict about the candidate: nothing here
#: says the report is safe to act on.
NO_POINTS_STATEMENT = (
    "Every area assessed rests on evidence the record corroborates, so no "
    "additional human validation is recommended beyond the usual interview."
)

#: What a reader is told the section is for, stated once.
SECTION_NOTE = (
    "Areas where the evidence base is thinner than the grade beside it, listed "
    "so a person looks before a decision is made. It identifies what to check, "
    "never whether to advance or reject."
)

#: THE PROBE WORDINGS, AS DATA. A fixed catalogue keyed by driver, never a
#: prompt: see the module docstring. Each one names the area, asks for one
#: specific thing, and states no outcome.
_PROBE: dict[str, str] = {
    evidence_confidence.CONFIDENCE_INSUFFICIENT: (
        "Nothing in the assessment record addressed {name}. Ask for one worked "
        "example of it and listen for what this person decided themselves."
    ),
    evidence_confidence.CONFIDENCE_LOW: (
        "The record for {name} rests on this person's own account. Ask for a "
        "specific instance, and for who else could describe the same work."
    ),
    evidence_confidence.CONFIDENCE_MODERATE: (
        "{name} was answered but not corroborated. Ask them to go a level "
        "deeper into the example they gave and name what made it difficult."
    ),
    DRIVER_BORDERLINE: (
        "{name} is graded just below what this role asks for. Ask for the "
        "hardest instance of it they have handled and what they would change."
    ),
    DRIVER_CONTRADICTION: (
        "Two parts of the record disagree about {name}. Ask them to describe it "
        "again from the start, and note where this account differs."
    ),
}

#: Why the area is listed, stated separately from the probe so a reader can see
#: the cause without reading the question.
_REASON: dict[str, str] = {
    evidence_confidence.CONFIDENCE_INSUFFICIENT: (
        "No evidence was recorded against this area."
    ),
    evidence_confidence.CONFIDENCE_LOW: (
        "The evidence is this person's own account, uncorroborated."
    ),
    evidence_confidence.CONFIDENCE_MODERATE: (
        "The area was probed, and nothing independent corroborates the answer."
    ),
    DRIVER_BORDERLINE: (
        "The grade sits immediately below what the role requires."
    ),
    DRIVER_CONTRADICTION: (
        "The record holds evidence on both sides and nobody has settled it."
    ),
}


@dataclass(frozen=True)
class ValidationPoint:
    """One area to check, with its cause and the one thing to ask.

    NOTE THE FIELD LIST. There is no `severity`, no `score`, no `priority` and
    no `decision`. Order carries what little ranking the section is entitled to
    state, exactly as the Proctoring Report does, and a field an outcome could
    be written into is a field an outcome eventually appears in.
    """

    area: str
    driver: str
    #: The client-facing confidence word, or None where the driver is not a
    #: confidence verdict. None rather than an empty string, so a renderer can
    #: tell "this point is not about confidence" from "confidence was blank".
    confidence: str | None
    reason: str
    probe: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "area": self.area,
            "driver": self.driver,
            "confidence": self.confidence,
            "reason": self.reason,
            "probe": self.probe,
        }


def _grade_gap(grade: str | None, required: str | None) -> int | None:
    """How many bands the assessed grade sits below the required one.

    None when either end is missing. An AI Score parameter and a technical row
    carry no requirement at all, so there is nothing for "borderline" to mean
    on them, and guessing one would manufacture a point.
    """
    try:
        assessed_at = GRADES.index(str(grade))
        required_at = GRADES.index(str(required))
    except ValueError:
        return None
    # GRADES runs best to worst, so a larger index is a worse grade.
    return assessed_at - required_at


def build(
    rows: Sequence[Mapping[str, Any]],
    *,
    contradicted_areas: Sequence[str] = (),
) -> dict[str, Any]:
    """The whole section, from the rated rows and the ledger's contradictions.

    `rows` are the report's own rated lines, each carrying at least `name`,
    `grade`, `required_level` and `evidence_confidence`. Read from the report
    rows rather than from the evaluation because the report is the permanent
    record: a section stored beside grades has to be derivable from the same
    rows those grades were written from, or the two can come apart.

    `contradicted_areas` are the subjects the evidence ledger recorded active
    evidence on BOTH sides of. Passed in rather than queried here so this stays
    a pure function: the whole selection is testable without a database, which
    is what makes the never-states-a-decision sweep cheap enough to run over a
    wide range of inputs.
    """
    selected: dict[str, ValidationPoint] = {}

    def _take(name: str, point: ValidationPoint) -> None:
        if name and name not in selected and len(selected) < MAX_POINTS:
            selected[name] = point

    ordered = [dict(row) for row in rows if str(row.get("name") or "")]

    # ── Driver 1: evidence confidence ───────────────────────────────────────
    for level in _CONFIDENCE_ORDER:
        for row in ordered:
            if str(row.get("evidence_confidence") or "") != level:
                continue
            name = str(row["name"])
            _take(
                name,
                ValidationPoint(
                    area=name,
                    driver=DRIVER_CONFIDENCE,
                    confidence=evidence_confidence.display_word(level),
                    reason=_REASON[level],
                    probe=_PROBE[level].format(name=name),
                ),
            )

    # ── Driver 2: borderline against the role's own requirement ─────────────
    for row in ordered:
        if _grade_gap(row.get("grade"), row.get("required_level")) != 1:
            continue
        name = str(row["name"])
        _take(
            name,
            ValidationPoint(
                area=name,
                driver=DRIVER_BORDERLINE,
                confidence=evidence_confidence.display_word(
                    row.get("evidence_confidence")
                ),
                reason=_REASON[DRIVER_BORDERLINE],
                probe=_PROBE[DRIVER_BORDERLINE].format(name=name),
            ),
        )

    # ── Driver 3: what the record disagrees with itself about ───────────────
    by_name = {str(row["name"]): row for row in ordered}
    for subject in contradicted_areas:
        name = str(subject or "")
        if not name:
            continue
        row = by_name.get(name, {})
        _take(
            name,
            ValidationPoint(
                area=name,
                driver=DRIVER_CONTRADICTION,
                confidence=evidence_confidence.display_word(
                    row.get("evidence_confidence")
                ),
                reason=_REASON[DRIVER_CONTRADICTION],
                probe=_PROBE[DRIVER_CONTRADICTION].format(name=name),
            ),
        )

    points = list(selected.values())
    return {
        "note": SECTION_NOTE,
        "points": [point.as_dict() for point in points],
        # Present ONLY when the list is empty. A statement that also appeared
        # beside a populated list would read as a summary of it, and it is not
        # one: it is the alternative to blank space.
        "no_points_statement": NO_POINTS_STATEMENT if not points else None,
    }
