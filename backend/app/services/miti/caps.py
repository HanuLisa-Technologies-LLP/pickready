"""The Runbook's band-capping controls. Three of them, and the product had one.

    RPN-PHIL-001 section 12.1  Competency threshold
                               a named competency fails its minimum score
    RPN-PHIL-001 section 12.2  Dimension floor
                               D1 < 45, D4 < 45, D3 < 40  (and D4 < 25 -> HOLD)
    RPN-PHIL-001 section 14.1  Unassessed Must-have
                               a Must-have has no evidence above E1

WHAT THIS MODULE CORRECTS
---------------------------
CLAUDE.md carried a standing rule called "the Must-have hard cap", citing
"spec section 5.5". The Runbook contains neither the phrase nor that section
number. What the rule actually is, and how it should be cited, is:

    section 12.1   the trigger, and that failure "caps the band"
    section 12.2   the ceiling, "Cannot exceed 'Consider with reservations'"
    sections 10.1, 10.5, 10.8   the position: gates and thresholds act after
                   the weighted composite, the multiplier produces RPS, and
                   the band table is keyed on RPS

So the rule was a correct synthesis of mechanisms the Runbook does state, and
only the name and the citation were invented. It was also a correct SUBSET: the
Runbook states three capping controls and the product implemented one. See
RUNBOOK_OPEN_QUESTIONS.md Q7 and its 2026-08-29 addendum, whose standing
recommendation is option C, "apply all three independently and take the lowest
resulting ceiling". That is what `apply` does.

WHY SECTION 14.1 IS NOT REDUNDANT, WHICH IS THE WHOLE REASON THIS FILE EXISTS
-------------------------------------------------------------------------------
Section 10.2 defines a competency score as

    Comp(k) = sum over claims c [ rubric_level(c) x S_final(c) ]
              / sum over claims c [ S_final(c) ]

with the evidence strength in both the numerator and the denominator. For a
competency supported by a SINGLE claim the strength terms cancel and
`Comp(k) = rubric_level(c)` exactly, at every tier from E0 to E5. The Runbook
states the consequence itself, in that section: "a dazzling claim with weak
evidence and a modest claim with strong evidence can land in the same place,
and that is the intended behaviour."

A fabricated Must-have resting on one weakest-tier resume bullet therefore
scores HIGH, grades Matching, and cannot trip any score-based control. That is
the AI-generated-resume case this product exists to defeat. Section 14.1's
trigger is an evidence-TIER condition precisely because a score-based one is
structurally blind to it.

THE CEILING IS 71, NOT 74, AND IT IS READ FROM THE RUNBOOK
------------------------------------------------------------
"Cannot exceed 'Consider with reservations'" resolves through
`runbook_data/bands.yaml`'s section 10.8 table to the top of that band, which
is 71. The product's `rating.MODERATELY_CEILING` is 74, because
"Moderately Matching" (60 to 74 on a four-grade scale) and "Consider with
reservations" (60 to 71 on the Runbook's six-band scale) are the same POSITION
on two different scales, not the same number. Both mean "genuine candidate,
material gap stated". The Runbook is the authority on the number, so the number
comes from here, and nothing in this file is typed as a literal.

ORDERING: LAST, ON THE SCORE, AS A `min`
------------------------------------------
Both properties are load-bearing and the second is the subtle one.

LAST. CLAUDE.md's stated reason ("a cap a later multiplication can undo is not
a cap") reaches the right conclusion by the wrong route: section 10.5 bounds
the authenticity multiplier at 1.00, so a later multiplication can only push a
score FURTHER BELOW a ceiling. The operative reason is the boundary case. With
`cap_last = min(x * m, C)` the ceiling holds for every multiplier without
exception; with `cap_first = min(x, C) * m` it holds only while `m <= 1`, and
the Runbook's own earlier rounding of the 60-to-75 slope reached 1.0005 at
D4 = 75 (Q3), which would have breached the ceiling by exactly that much.
Cap-last is the only order that holds absolutely.

A `min`, NEVER AN ASSIGNMENT. A candidate whose composite already grades below
the ceiling must STAY there. Setting the score would promote the weakest
candidates into the band the cap exists to keep the strong ones out of.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.services.hiring.situations import DIMENSION_BY_RUNBOOK_ID

__all__ = [
    "BAND_CONSIDER_WITH_RESERVATIONS",
    "BAND_READY_TO_PICK",
    "CONTROL_COMPETENCY_THRESHOLD",
    "CONTROL_DIMENSION_FLOOR",
    "CONTROL_UNASSESSED_MUST_HAVE",
    "CONTROLS",
    "BandCap",
    "CapDataError",
    "apply",
    "band_ceiling",
    "bands_data",
    "competency_threshold_caps",
    "dimension_floor_caps",
    "hold_reason",
    "lowest_ceiling",
    "unassessed_must_haves",
    "unassessed_must_have_caps",
]

# -- The three controls, named after the sections that state them ------------

CONTROL_COMPETENCY_THRESHOLD = "competency_threshold"
CONTROL_DIMENSION_FLOOR = "dimension_floor"
CONTROL_UNASSESSED_MUST_HAVE = "unassessed_must_have"

CONTROLS: tuple[str, ...] = (
    CONTROL_COMPETENCY_THRESHOLD,
    CONTROL_DIMENSION_FLOOR,
    CONTROL_UNASSESSED_MUST_HAVE,
)

#: Band names as section 10.8 prints them. Used to look a ceiling up, never
#: rendered: a client reads `services/rating`'s four words.
BAND_CONSIDER_WITH_RESERVATIONS = "Consider with reservations"
BAND_READY_TO_PICK = "Ready to Pick"

_CITATION_12_1 = "RPN-PHIL-001 section 12.1"
_CITATION_12_2 = "RPN-PHIL-001 section 12.2"
_CITATION_14_1 = "RPN-PHIL-001 section 14.1"


class CapDataError(RuntimeError):
    """`bands.yaml` cannot supply a ceiling this module needs.

    Raised rather than defaulted, and this is the one place where that matters
    most: a substituted ceiling is a number nobody chose deciding whether a
    candidate may be delivered as Ready to Pick.
    """


def bands_data() -> Any:
    """`bands.yaml`, loaded inside the function.

    `runbook_data` is read at call time rather than at import time so this
    module stays importable without touching the filesystem, which is what
    lets `test_miti_pipeline.py` walk its AST.
    """
    from app.services.hiring import runbook_data

    return runbook_data.bands()


def _score_bands() -> list[Mapping[str, Any]]:
    table = bands_data().get("score_bands")
    rows = table.get("bands") if isinstance(table, Mapping) else None
    if not isinstance(rows, list) or not rows:
        raise CapDataError(
            "runbook_data/bands.yaml carries no section 10.8 score_bands table; "
            "every ceiling in this module is read from it."
        )
    return [row for row in rows if isinstance(row, Mapping)]


def band_ceiling(band: str) -> int:
    """The highest score still inside `band`, from section 10.8's table.

    Raises when the band is absent or carries no upper bound. The HOLD row has
    no numeric range on purpose and is not a ceiling; `hold_reason` handles it.
    """
    for row in _score_bands():
        if str(row.get("band")) != band:
            continue
        high = row.get("high")
        if high is None:
            break
        return int(high)
    raise CapDataError(
        f"runbook_data/bands.yaml section 10.8 has no band {band!r} with a "
        f"numeric upper bound."
    )


def _ceiling_below(band: str) -> int:
    """The highest score that is NOT in `band`, i.e. the top of the one under it.

    Section 14.1 says a candidate with an unassessed Must-have "cannot be Ready
    to Pick" and section 12.2 says a D3 breach "cannot be delivered as Ready to
    Pick for this role". Neither states a number; both state an exclusion, and
    the number is whatever the band immediately below tops out at. Derived so
    that moving a cut-point in the Runbook moves this with it.
    """
    rows = [row for row in _score_bands() if row.get("low") is not None]
    target = None
    for row in rows:
        if str(row.get("band")) == band:
            target = row
            break
    if target is None:
        raise CapDataError(
            f"runbook_data/bands.yaml section 10.8 has no band {band!r}."
        )
    floor = int(target["low"])
    below = [
        int(row["high"])
        for row in rows
        if row.get("high") is not None and int(row["high"]) < floor
    ]
    if not below:
        raise CapDataError(
            f"runbook_data/bands.yaml section 10.8 has no band beneath {band!r}, "
            f"so 'cannot be delivered as {band}' has no ceiling to resolve to."
        )
    return max(below)


@dataclass(frozen=True)
class BandCap:
    """One ceiling that fired, and everything needed to explain it.

    Frozen and fully self-describing because a cap that left no trace is
    indistinguishable from a candidate who simply scored there. `subject` names
    the competency or the dimension, `citation` names the Runbook section, and
    `reason` is the internal sentence a recruiter's explanation is built from.

    `ceiling` is a NUMBER and stays internal, like every other score in this
    package. `Aggregate.client_projection` renders words.
    """

    control: str
    citation: str
    subject: str
    reason: str
    ceiling: int


def competency_threshold_caps(
    *,
    grades: Mapping[str, str],
    scores: Mapping[str, float] | None = None,
    thresholds: Mapping[str, float] | None = None,
) -> list[BandCap]:
    """Section 12.1: "minimum score on a named competency; failure caps the band".

    ONE RULE, TWO SOURCES FOR THE MINIMUM, and which one applies is decided by
    the frozen matrix rather than by a branch anybody chose. When the matrix
    declares a numeric threshold for a competency, that is the minimum and the
    breach test is `score < threshold`. When it declares none, the minimum is
    the product's own published floor for a criterion the hiring manager called
    essential: an item graded Not Matching has failed it. Both are the same
    sentence from section 12.1, differing only in where the number came from.

    `grades` is keyed by MUST-HAVE ITEM and never by dimension. That
    distinction was a real defect once: keying the composite on a
    dimension-to-category table produced an EMPTY Must-have grade for a job
    whose essentials all sat on one dimension, and the cap had nothing to bind
    against. Must-have is a property of the criterion the hiring manager
    declared essential, not of the internal dimension it happens to sit on.
    """
    from app.services import rating

    scores = dict(scores or {})
    thresholds = dict(thresholds or {})
    ceiling = band_ceiling(BAND_CONSIDER_WITH_RESERVATIONS)
    caps: list[BandCap] = []
    for name in sorted(grades):
        threshold = thresholds.get(name)
        score = scores.get(name)
        if threshold is not None and score is not None:
            if float(score) >= float(threshold):
                continue
            reason = (
                f"the Must-have {name!r} did not reach the minimum score the "
                f"approved scorecard sets for it"
            )
        else:
            if grades[name] != rating.GRADE_NOT:
                continue
            reason = (
                f"the Must-have {name!r} graded {rating.GRADE_NOT}, which is "
                f"below the minimum for a criterion declared essential"
            )
        caps.append(
            BandCap(
                control=CONTROL_COMPETENCY_THRESHOLD,
                citation=_CITATION_12_1,
                subject=name,
                reason=reason,
                ceiling=ceiling,
            )
        )
    return caps


#: How section 12.2's `effect_if_breached` prose resolves to a ceiling. A
#: LOOKUP AND NOT A SUBSTRING SEARCH over the Runbook's sentence: an effect
#: this table does not recognise raises, so a Runbook edit that adds a fourth
#: effect fails the build instead of being silently ignored. That is the same
#: lesson Decision Contract C5 taught, where one wrong section number in a
#: citation authorised filtering on age and caste.
_FLOOR_EFFECTS: dict[str, str] = {
    'Cannot exceed "Consider with reservations"': BAND_CONSIDER_WITH_RESERVATIONS,
    "Cannot be delivered as Ready to Pick for this role; may be flagged for a "
    "different role": BAND_READY_TO_PICK,
}

#: The one effect that is not a ceiling. Section 12.2's D4 floor of 25 takes a
#: candidate out of ranking entirely pending a human disposition, which is a
#: different consequence from capping a band and must not be modelled as one.
_HOLD_EFFECT = "HOLD - mandatory human review before any delivery"


def _floor_rows() -> list[Mapping[str, Any]]:
    """Section 12.2's floors, with EVERY effect validated up front.

    Validated on load rather than on breach, and the difference matters. If an
    unrecognised effect only raised when some candidate happened to score below
    that floor, a Runbook edit adding a fourth consequence would sit unnoticed
    until the first candidate it applied to, and would then look like a runtime
    error rather than a configuration one. This way it fails on the first
    evaluation after the edit.
    """
    table = bands_data().get("dimension_floors")
    rows = table.get("floors") if isinstance(table, Mapping) else None
    if not isinstance(rows, list) or not rows:
        raise CapDataError(
            "runbook_data/bands.yaml carries no section 12.2 dimension_floors "
            "table."
        )
    kept = [row for row in rows if isinstance(row, Mapping)]
    for row in kept:
        effect = str(row.get("effect_if_breached") or "")
        if effect != _HOLD_EFFECT and effect not in _FLOOR_EFFECTS:
            raise CapDataError(
                f"runbook_data/bands.yaml section 12.2 states an effect this "
                f"module does not know how to apply: {effect!r}. A floor whose "
                f"consequence cannot be resolved must stop the build, not be "
                f"skipped."
            )
    return kept


def _floor_value(row: Mapping[str, Any]) -> float:
    """A floor row's number. Absent is a data defect, never a permissive zero.

    A floor of zero would be a floor nothing can breach, so a missing value
    silently disables the control it belongs to. That is the shape of failure
    this whole module is written against.
    """
    value = row.get("floor")
    if not isinstance(value, (int, float)):
        raise CapDataError(
            f"runbook_data/bands.yaml section 12.2 states no numeric floor for "
            f"{row.get('dimension')!r}."
        )
    return float(value)


def _ceiling_for_effect(effect: str) -> int:
    band = _FLOOR_EFFECTS.get(effect)
    if band is None:
        raise CapDataError(
            f"runbook_data/bands.yaml section 12.2 states an effect this module "
            f"does not know how to apply: {effect!r}. A floor whose consequence "
            f"cannot be resolved must stop the build, not be skipped."
        )
    if band == BAND_READY_TO_PICK:
        return _ceiling_below(band)
    return band_ceiling(band)


def dimension_floor_caps(
    dimension_scores: Mapping[str, float],
) -> list[BandCap]:
    """Section 12.2's Layer 1 default floors, D1 45, D4 45, D3 40.

    `dimension_scores` is keyed by this codebase's dimension names; the floors
    are keyed by the Runbook's D1 to D5, and `situations.DIMENSION_BY_RUNBOOK_ID`
    is the one translation between them.

    A DIMENSION NOT PRESENT IS NOT A BREACH. A dimension excluded from the
    composite for want of evidence has not scored below its floor; it has not
    scored at all, and insufficient evidence is not negative evidence. It is
    paid for in confidence, which is where the Runbook puts it.

    The D4 < 25 row is deliberately not returned here. It is a HOLD, not a
    ceiling, and `hold_reason` answers it.
    """
    caps: list[BandCap] = []
    for row in _floor_rows():
        effect = str(row.get("effect_if_breached") or "")
        if effect == _HOLD_EFFECT:
            continue
        runbook_id = str(row.get("dimension") or "")
        name = DIMENSION_BY_RUNBOOK_ID.get(runbook_id)
        if name is None:
            raise CapDataError(
                f"runbook_data/bands.yaml section 12.2 names dimension "
                f"{runbook_id!r}, which is not one of D1 to D5."
            )
        score = dimension_scores.get(name)
        if score is None:
            continue
        floor = _floor_value(row)
        if float(score) >= floor:
            continue
        caps.append(
            BandCap(
                control=CONTROL_DIMENSION_FLOOR,
                citation=_CITATION_12_2,
                subject=name,
                reason=(
                    f"the {runbook_id} dimension scored below the Layer 1 floor "
                    f"the Runbook sets for it"
                ),
                ceiling=_ceiling_for_effect(effect),
            )
        )
    return caps


def hold_reason(dimension_scores: Mapping[str, float]) -> str | None:
    """Section 12.2 and section 14.1: D4 below 25 is a HOLD, not a low grade.

    Returns the reason when the candidate must be held, and None otherwise. A
    HOLD is "not ranked pending human disposition" (section 10.8), which is a
    routing consequence rather than a band. Modelling it as a fifth grade would
    put an integrity outcome on the scale a client reads, and modelling it as a
    ceiling would deliver the candidate anyway with a lower number.

    THIS FLOOR WAS UNREACHABLE UNTIL 2026-09-02, which is worth knowing when
    reading anything written against it before then. The scores handed in come
    from `dimensions.BANDS`, which carried four words against the section 9.x
    rubric's six rows and bottomed out at 40 -- above this floor of 25, and
    above section 12.2's D3 floor of 40 as well. The two missing rows were
    added, the bottom one scores 12, and all four floors in this table can now
    be breached. See RUNBOOK_OPEN_QUESTIONS.md Q24.
    """
    from app.services.hiring.department_models import DIM_AUTHENTICITY

    score = dimension_scores.get(DIM_AUTHENTICITY)
    if score is None:
        return None
    for row in _floor_rows():
        if str(row.get("effect_if_breached") or "") != _HOLD_EFFECT:
            continue
        if float(score) < _floor_value(row):
            return (
                "the account's internal consistency fell below the floor at "
                "which the Runbook requires a human disposition before any "
                "delivery"
            )
    return None


def unassessed_must_haves(
    evidence_tiers: Mapping[str, Sequence[str]],
    must_haves: Sequence[str],
) -> list[str]:
    """Which Must-haves section 14.1 reports as Unassessed, in a stable order.

    The condition is "no evidence above E1", and a Must-have with NO evidence
    mapped to it satisfies that trivially. Both cases are the same finding: the
    scorecard names this as essential and nothing in the record examines it.
    """
    from app.services.evidence import tiers as evidence_tier_model

    unassessed: list[str] = []
    for name in sorted(set(must_haves)):
        found = evidence_tiers.get(name) or ()
        if not any(evidence_tier_model.above_e1(str(tier)) for tier in found):
            unassessed.append(name)
    return unassessed


def unassessed_must_have_caps(
    evidence_tiers: Mapping[str, Sequence[str]],
    must_haves: Sequence[str],
) -> list[BandCap]:
    """Section 14.1: an unassessed Must-have means the candidate cannot be
    Ready to Pick.

    THIS IS THE CONTROL A SCORE-BASED CAP CANNOT REPLACE. See the module
    docstring: section 10.2's arithmetic makes a single-claim competency score
    exactly its rubric level whatever the evidence tier, so a high grade on one
    E0 resume bullet is not merely possible, it is the expected output. The
    trigger here reads the tier and never the score, which is why it fires on
    exactly the candidate the score cannot see.
    """
    ceiling = _ceiling_below(BAND_READY_TO_PICK)
    return [
        BandCap(
            control=CONTROL_UNASSESSED_MUST_HAVE,
            citation=_CITATION_14_1,
            subject=name,
            reason=(
                f"the Must-have {name!r} rests on nothing stronger than an "
                f"unverified self-report, so it is reported as Unassessed"
            ),
            ceiling=ceiling,
        )
        for name in unassessed_must_haves(evidence_tiers, must_haves)
    ]


def lowest_ceiling(caps: Sequence[BandCap]) -> int | None:
    """The binding ceiling: the lowest of whichever controls fired.

    SOURCE: RPN-PHIL-001 section 12.1 (v1.3), "The delivered band is the
    MINIMUM of every ceiling that fires." The Runbook previously stated the
    three capping controls in isolation and never said how they compose, so
    this was an implementer's reading; taking the minimum is now the stated
    rule, on the ground that it is the only composition under which no stated
    control is quietly ignored.
    """
    return min((cap.ceiling for cap in caps), default=None)


def apply(score: float, caps: Sequence[BandCap]) -> float:
    """`min(score, lowest ceiling)`. Never an assignment, never a promotion.

    Two properties, and they are the whole of what a cap must guarantee:

      * `apply(x, caps) <= ceiling` for every ceiling that fired; and
      * `apply(x, caps) <= x`, so a candidate already below the ceiling is
        never lifted to it.

    `tests/test_band_caps.py` asserts both as properties over generated inputs
    rather than over examples, because both are claims about every score and
    every combination of controls.
    """
    ceiling = lowest_ceiling(caps)
    if ceiling is None:
        return float(score)
    return float(min(float(score), float(ceiling)))
