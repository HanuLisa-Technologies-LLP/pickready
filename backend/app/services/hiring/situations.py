"""The six role situation types, and what each one does to the weights.

spec-doc5 §A.3 names situation misclassification as "the single most expensive
error available at intake". That claim is worth unpacking, because it is what
justifies the confirmation step below.

Every other intake error is local. A weak SWOT weakness produces one thin
competency. A vague success criterion produces one loose rubric. Situation type
is different because it re-weights the WHOLE matrix: a Turnaround role and a
Greenfield role hiring for the same job title, in the same department, at the
same seniority, should produce visibly different matrices -- one leaning on
proven impact, the other on trajectory and adaptability. Get the type wrong and
every item is weighted for the wrong job, consistently, in a way that looks
entirely coherent from the outside. Nothing downstream can detect it, because
there is nothing inconsistent to detect.

WHICH IS WHY BODHA STATES THE CLASSIFICATION BACK
---------------------------------------------------
`confirmation_prompt` renders the classification, its consequences and the
signals it was drawn from, and the Hiring Manager confirms it explicitly before
the session closes. That is the only check available: a model cannot audit its
own classification (that is the same unfalsifiable-judge problem this codebase
refuses everywhere else), and no downstream signal reveals it. A person who
knows the role can look at "Turnaround" and say "no, we are actually scaling"
in one second.

THE MODIFIERS ARE MULTIPLIERS ON A DIMENSION, NOT ON A COMPETENCY
------------------------------------------------------------------
A situation says something about what KIND of evidence matters for this role,
not about which specific competencies exist -- that is the department model's
job at Layer 1 and the SWOT's job at Layer 3. So the modifiers below key on the
five internal dimensions, and a competency inherits its situation modifier
through its primary dimension. This keeps the two layers from fighting: the SWOT
can say "this role needs deep systems design" and the situation can say "and
proven impact matters more than potential here", and both survive.

PROVENANCE, AND WHAT THE RECONCILIATION AGAINST RPN-PHIL-001 §18.4 CHANGED
--------------------------------------------------------------------------
§18.4 is a table whose "Weight consequence" column is stated in arrows over the
five dimensions, and four of its six rows disagreed with what this module held
before the Runbook was read:

    Gap-fill      D3 up-up, D1 up     was: D1 up-up, D2 up, D5 DOWN
    Turnaround    D2 up-up, D3 up     was: right in direction, plus an
                                           unstated D1 lift and a D5 cut
    Scale-up      D2 up,    D5 up     was: D2 up, D1 up, D3 up, D5 NEUTRAL
    Greenfield    D5 up-up, D3 up     was: right in direction, plus an
                                           unstated D1 lift and a D2 cut
    Steady-state  D1 up-up, D5 down   was: D1 and D3 lifted equally, plus D4
    Succession    D5 up-up, D2 up     was: D5 up, D3 up, D4 up, D2 NEUTRAL

Two of those are inversions rather than approximations. Gap-fill led on Verified
Competence and said nothing at all about Role and Context Fit, where the Runbook
leads on Role and Context Fit; Succession left Track Record neutral, where the
Runbook lifts it. §11.3 corroborates four of the six rows independently, in its
own vocabulary ("hire must close a specific named capability gap: D3 up, D1 up",
"turnaround / crisis mandate: D2 up, D3 up", "greenfield / zero-to-one: D5 up,
D3 up", "defined, stable execution seat: D1 up, D5 down"), so the directions are
stated twice in the Runbook and agree both times.

THE DEFECT THESE SHARE IS WORTH NAMING, because it is the one to look for
anywhere else a table was reconstructed from a summary: every invented modifier
was PLAUSIBLE. "A gap-fill needs someone who can do the work now, so weight
demonstrated competence" is a good argument. It is simply not the Runbook's,
which reads a gap-fill as a FIT problem -- its evidence emphasis is "direct
prior experience of that exact problem" -- rather than as a capability problem.
Nothing downstream could have detected the difference, which is precisely what
§18.4 says about misclassification and is equally true of mis-weighting.

The arrows are ORDINAL and the Runbook attaches no multiplier to them, so the
magnitudes are read from `runbook_data/situation_types.yaml` against their
§18.4 citation rather than restated here (spec-doc6 §10.1 rule 5). This module
holds no weight of its own.

EVIDENCE EMPHASIS IS CARRIED TOO. §18.4's fourth column says what KIND of
evidence each situation should reach for, and it was absent entirely before.
It is not a weight and it does not enter the arithmetic; it is what Sutra's
stage 3 and Vaada's questioning should be steering toward, and a situation type
that changed the weights without changing what gets asked would re-rank
candidates on evidence nobody went looking for.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from app.services.hiring import layers
from app.services.hiring.department_models import (
    DIM_AUTHENTICITY,
    DIM_ROLE_FIT,
    DIM_TRACK_RECORD,
    DIM_TRAJECTORY,
    DIM_VERIFIED_COMPETENCE,
)

#: The five dimensions, in the Runbook's D1..D5 order. A situation modifier map
#: always names all five, so a caller never has to distinguish "this dimension
#: is unaffected" from "this dimension was forgotten".
_ALL_DIMENSIONS: tuple[str, ...] = (
    DIM_VERIFIED_COMPETENCE,
    DIM_TRACK_RECORD,
    DIM_ROLE_FIT,
    DIM_AUTHENTICITY,
    DIM_TRAJECTORY,
)


def _arrow_magnitudes() -> dict[str, float]:
    """{arrow level: multiplier}, from the Runbook data package.

    RAISES RATHER THAN SUPPLYING A NUMBER, and this is the site where that rule
    costs something, so it is worth stating why it is still right.

    §18.4 states each situation's weight consequence as arrows and attaches no
    magnitude to any of them. §11.3 supplies an additive bound for four of the
    six ("+0.08 combined", "+0.07 combined", "+-0.06") and nothing at all for
    Scale-up or Succession. So there is no multiplier for these arrows anywhere
    in RPN-PHIL-001, and a literal here would be a number with a section
    citation it does not have -- which is worse than no number, because the
    citation would make it look settled. Recorded as a RUNBOOK-AMBIGUITY and
    escalated; see RUNBOOK_OPEN_QUESTIONS_PHASE0B.md.
    """
    raw = layers.runbook_value("situation_types", "arrow_magnitudes")
    if not isinstance(raw, dict) or not raw:
        raise layers.RunbookDataUnavailable(
            "runbook_data/situation_types.yaml has no 'arrow_magnitudes' "
            "mapping. RPN-PHIL-001 §18.4 states its weight consequences as "
            "arrows only, so the multiplier for "
            f"{ARROW_LEVELS} has to be declared once, in that file, with the "
            "section it is standing in for. It is deliberately not restated "
            "here."
        )
    return {str(k): float(v) for k, v in raw.items()}

__all__ = [
    "GAP_FILL",
    "TURNAROUND",
    "SCALE_UP",
    "GREENFIELD",
    "STEADY_STATE",
    "SUCCESSION",
    "SITUATION_TYPES",
    "Situation",
    "SITUATIONS",
    "get",
    "classify_signals",
    "confirmation_prompt",
    "dimension_modifiers",
    "evidence_emphasis",
    "ARROW_LEVELS",
    "STRONG_UP",
    "UP",
    "DOWN",
    "is_valid",
]

GAP_FILL = "gap_fill"
TURNAROUND = "turnaround"
SCALE_UP = "scale_up"
GREENFIELD = "greenfield"
STEADY_STATE = "steady_state"
SUCCESSION = "succession"

SITUATION_TYPES: tuple[str, ...] = (
    GAP_FILL,
    TURNAROUND,
    SCALE_UP,
    GREENFIELD,
    STEADY_STATE,
    SUCCESSION,
)


@dataclass(frozen=True)
class Situation:
    key: str
    label: str
    #: RPN-PHIL-001 §18.4's own Description column, plus one sentence a hiring
    #: manager would recognise their own role in. This is read back to them
    #: verbatim, so it is written for THEM and not for us.
    description: str
    #: {dimension: arrow}, straight off §18.4's "Weight consequence" column.
    #: `STRONG_UP` is the table's double arrow, `UP` its single, `DOWN` its
    #: downward one, and a dimension the row does not name is ABSENT rather
    #: than present at neutral -- a situation type with an opinion about all
    #: five would be expressing a preference rather than a fact about the role.
    effects: dict[str, str]
    #: §18.4's "Evidence emphasis" column. What kind of evidence this situation
    #: should reach for. Never enters the arithmetic.
    evidence_emphasis: str
    rationale: str
    #: Phrases in a SWOT that point at this type. Used by `classify_signals` to
    #: propose, never to decide.
    signals: tuple[str, ...]
    #: What a hiring manager most often confuses this with. Named in the
    #: confirmation prompt, because "is it this or that" is a much easier
    #: question to answer than "is this right".
    confused_with: tuple[str, ...] = ()


#: §18.4's arrows, as the three ordinal levels the table actually uses.
#:
#: The names are `runbook_data/situation_types.yaml`'s own
#: (`dimension_effects` transcribes the glyphs into exactly these three), so
#: there is one vocabulary for the arrows rather than a code spelling and a data
#: spelling that have to be kept in step by hand. The MAGNITUDE of each is a
#: Runbook data value; these are only its name.
STRONG_UP = "strong_increase"
UP = "increase"
DOWN = "decrease"
ARROW_LEVELS: tuple[str, ...] = (STRONG_UP, UP, DOWN)

#: §18.4 and §11.1-§11.3 address the dimensions as D1..D5; this codebase names
#: them. The map is stated once, here, because two spellings of the same five
#: things is how the product ended up with two parallel rating scales.
DIMENSION_BY_RUNBOOK_ID: dict[str, str] = {
    "D1": DIM_VERIFIED_COMPETENCE,
    "D2": DIM_TRACK_RECORD,
    "D3": DIM_ROLE_FIT,
    "D4": DIM_AUTHENTICITY,
    "D5": DIM_TRAJECTORY,
}
RUNBOOK_ID_BY_DIMENSION: dict[str, str] = {
    name: rid for rid, name in DIMENSION_BY_RUNBOOK_ID.items()
}


SITUATIONS: dict[str, Situation] = {
    GAP_FILL: Situation(
        key=GAP_FILL,
        label="Gap-fill",
        description=(
            "A specific capability is missing. Someone left, or the work is "
            "currently not being done, and you need that exact problem solved "
            "again."
        ),
        # §18.4: D3 up-up, D1 up.
        effects={DIM_ROLE_FIT: STRONG_UP, DIM_VERIFIED_COMPETENCE: UP},
        evidence_emphasis="Direct prior experience of that exact problem",
        rationale=(
            "The Runbook reads a gap-fill as a FIT problem rather than a "
            "capability problem, and the evidence emphasis is what makes the "
            "difference legible: it asks for direct prior experience of that "
            "exact problem, not for general strength at the craft. Somebody who "
            "is excellent at the discipline and has never met this particular "
            "problem is not the answer to a gap-fill."
        ),
        signals=(
            "backfill", "replacement", "vacancy", "left the company", "resigned",
            "gap in the team", "currently unfilled", "someone to take over",
            "missing capability", "nobody here can",
        ),
        confused_with=(SUCCESSION, STEADY_STATE),
    ),
    TURNAROUND: Situation(
        key=TURNAROUND,
        label="Turnaround",
        description="Something is broken and this person is expected to fix it.",
        # §18.4: D2 up-up, D3 up.
        effects={DIM_TRACK_RECORD: STRONG_UP, DIM_ROLE_FIT: UP},
        evidence_emphasis="Evidence of fixing, not just running",
        rationale=(
            "The evidence emphasis carries the whole row: fixing and running "
            "look identical on a resume and are different jobs. A turnaround is "
            "the situation where the hire must have DONE this before, because "
            "the organisation has already demonstrated it cannot solve the "
            "problem from first principles. Role and Context Fit rises with it "
            "because a turnaround fails on the politics at least as often as on "
            "the competence."
        ),
        signals=(
            "turn around", "turnaround", "fix", "broken", "underperforming",
            "losing", "attrition", "morale", "declining", "recover", "rebuild",
            "not delivering", "behind schedule", "quality problems",
            # Added after the worked example ran on real prose and matched
            # nothing. A hiring manager describing a turnaround rarely uses the
            # word "turnaround" -- they describe the symptom, and the symptom
            # they describe most often is a team that has lost faith in
            # something.
            "stopped trusting", "lost confidence", "no longer trust",
            "stopped believing", "reputation", "credibility",
            "never owned", "nobody owns", "always somebody else",
        ),
        confused_with=(GAP_FILL, SCALE_UP),
    ),
    SCALE_UP: Situation(
        key=SCALE_UP,
        label="Scale-up",
        description=(
            "It works, and it now has to work at several times the current size."
        ),
        # §18.4: D2 up, D5 up.
        effects={DIM_TRACK_RECORD: UP, DIM_TRAJECTORY: UP},
        evidence_emphasis="Evidence of operating at the next scale",
        rationale=(
            "The only row where the Runbook lifts two dimensions equally, and "
            "the pairing is the point: scaling needs evidence of having "
            "operated at the next size up (Track Record) AND headroom to keep "
            "going past it (Trajectory). Weighting proven delivery alone would "
            "select the person who ran a good small thing and will reproduce it "
            "larger, which is the failure mode this situation has."
        ),
        signals=(
            "scale", "scaling", "growth", "grow the team", "double", "triple",
            "expansion", "increase capacity", "more volume", "new markets",
        ),
        confused_with=(GREENFIELD, TURNAROUND),
    ),
    GREENFIELD: Situation(
        key=GREENFIELD,
        label="Greenfield",
        description="This does not exist yet and this person is expected to build it.",
        # §18.4: D5 up-up, D3 up.
        effects={DIM_TRAJECTORY: STRONG_UP, DIM_ROLE_FIT: UP},
        evidence_emphasis="Evidence of building without inherited structure",
        rationale=(
            "Nobody has a track record in a thing that does not exist, so "
            "insisting on one selects for people who did something adjacent and "
            "will rebuild it here. The evidence emphasis is sharper than the "
            "weights alone: building WITHOUT INHERITED STRUCTURE is a different "
            "claim from having built something, and it is the one worth probing."
        ),
        signals=(
            "greenfield", "from scratch", "new function", "first hire",
            "does not exist", "build it out", "net new", "zero to one",
            "stand up", "establish",
        ),
        confused_with=(SCALE_UP,),
    ),
    STEADY_STATE: Situation(
        key=STEADY_STATE,
        label="Steady-state",
        description=(
            "This runs well and needs to keep running well. You are maintaining "
            "and executing, not changing direction."
        ),
        # §18.4: D1 up-up, D5 down.
        effects={DIM_VERIFIED_COMPETENCE: STRONG_UP, DIM_TRAJECTORY: DOWN},
        evidence_emphasis="Reliability, depth, consistency",
        rationale=(
            "The one row where the Runbook weights a dimension DOWN as its "
            "second effect rather than lifting a second one, and it is "
            "deliberate: the risk in a working team is disruption, and "
            "potential is the dimension that most reliably arrives as change. "
            "Corroborated by §11.3, which pairs a defined, stable execution "
            "seat with D1 up and D5 down."
        ),
        signals=(
            "steady", "business as usual", "additional headcount", "add capacity",
            "same as the existing team", "keep up with demand", "another",
            "maintain", "keep it running",
        ),
        confused_with=(GAP_FILL, SCALE_UP),
    ),
    SUCCESSION: Situation(
        key=SUCCESSION,
        label="Succession",
        description=(
            "You are hiring the person who will take over a larger role than the "
            "one they are starting in."
        ),
        # §18.4: D5 up-up, D2 up.
        effects={DIM_TRAJECTORY: STRONG_UP, DIM_TRACK_RECORD: UP},
        evidence_emphasis="Trajectory, readiness indicators",
        rationale=(
            "The hire is evaluated against a job they are not yet doing, so "
            "trajectory leads. Track Record rises with it rather than staying "
            "neutral, and that pairing is the Runbook's answer to the obvious "
            "failure mode: potential with nothing behind it is a hope, and a "
            "succession candidate has to have finished things at the size they "
            "are at now before readiness for the next size means anything."
        ),
        signals=(
            "succession", "step up", "grow into", "eventually take over",
            "next leader", "successor", "ready in a year", "second in command",
        ),
        confused_with=(GAP_FILL, GREENFIELD),
    ),
}


def is_valid(key: str | None) -> bool:
    return key in SITUATIONS


def get(key: str | None) -> Situation | None:
    return SITUATIONS.get(key or "")


def dimension_modifiers(key: str | None) -> dict[str, float]:
    """The multiplier per dimension, or a flat 1.0 map for an unknown type.

    THE ARROWS ARE §18.4's; THE MAGNITUDES ARE RUNBOOK DATA. §18.4 states each
    situation's weight consequence ordinally (a double arrow, a single arrow, a
    downward arrow) and attaches no number to any of them, so the number lives
    in `runbook_data/situation_types.yaml` under its §18.4 citation and this
    module reads it. A literal here would be a magic number by spec-doc6 §10.1
    rule 5, and worse, it would be a magic number nobody could trace to a
    section when they came to argue with it.

    A FLAT MAP RATHER THAN A RAISE for an unknown key, and this is the one place
    in this module where degrading is right. The situation type is confirmed by
    a human at the end of the SWOT session, and a job whose intake predates this
    feature has none. Refusing to weight such a job would mean refusing to
    generate its matrix; weighting it neutrally means it is generated from
    Layers 1 and 2 alone, which is precisely what "no situation type expressed"
    should mean. Note the difference from a silent fallback: this is the honest
    reading of an ABSENT input, not a substitute for a FAILED lookup. A known
    situation whose magnitudes cannot be read raises.
    """
    situation = get(key)
    if situation is None:
        return {dimension: 1.0 for dimension in _ALL_DIMENSIONS}
    magnitudes = _arrow_magnitudes()
    modifiers = {dimension: 1.0 for dimension in _ALL_DIMENSIONS}
    for dimension, arrow in situation.effects.items():
        if arrow not in magnitudes:
            raise layers.RunbookDataUnavailable(
                f"situation_types data has no magnitude for the §18.4 arrow "
                f"{arrow!r} used by {situation.key!r}. Nothing is substituted "
                f"for it."
            )
        modifiers[dimension] = float(magnitudes[arrow])
    return modifiers


def evidence_emphasis(key: str | None) -> str:
    """§18.4's fourth column, or an empty string for an unknown type.

    Read by Sutra's stage 3 and by Vaada's questioning. It steers what gets
    ASKED, which is the half of §18.4 the weights alone do not carry: a
    situation that re-weighted the matrix without changing what evidence was
    sought would re-rank candidates on evidence nobody went looking for.
    """
    situation = get(key)
    return situation.evidence_emphasis if situation else ""


def classify_signals(texts: Iterable[str]) -> list[tuple[str, int, list[str]]]:
    """Deterministic signal counting over SWOT text. PROPOSES, never decides.

    Returns [(situation_key, hits, matched_phrases)], strongest first. It calls
    no model, which is the point: this runs before the model classification as a
    prior and after it as a sanity check, and a guard that needs a provider
    fails open exactly when the provider is down.

    A tie is left as a tie. Breaking one arbitrarily would hand a coin flip the
    authority to re-weight a whole matrix, and the confirmation step exists
    precisely so an ambiguous role gets a person's answer rather than a rule's.
    """
    haystack = " ".join(t.lower() for t in texts if t)
    scored: list[tuple[str, int, list[str]]] = []
    for key, situation in SITUATIONS.items():
        matched = [phrase for phrase in situation.signals if phrase in haystack]
        if matched:
            scored.append((key, len(matched), matched))
    scored.sort(key=lambda row: (-row[1], row[0]))
    return scored


def confirmation_prompt(key: str, *, evidence: Iterable[str] = ()) -> str:
    """What Bodha reads back to the Hiring Manager before closing the session.

    Three parts, and each is load-bearing:

      * the DESCRIPTION, in the manager's language, so they are confirming a
        situation and not a taxonomy term;
      * the CONSEQUENCE, so they understand they are agreeing to something with
        an effect rather than answering a survey question; and
      * the ALTERNATIVE it is most often confused with, because "is it this or
        that" is a far easier question to answer correctly than "is this right",
        which people agree to.

    Carries no numbers. The weights behind it are internal, and a hiring manager
    reading "Track Record x1.35" would be reading a number the product does not
    show and could not usefully argue with.
    """
    situation = SITUATIONS[key]
    # Read off the §18.4 ARROWS rather than off the resolved multipliers, so
    # the sentence a hiring manager hears does not depend on the data package
    # being reachable. A confirmation step that stopped working during a data
    # outage would close the session on an unconfirmed classification, which is
    # the error §18.4 calls the most expensive available at intake.
    _order = {STRONG_UP: 0, UP: 1}
    lifted = [
        dimension.replace("_", " ")
        for dimension, arrow in sorted(
            situation.effects.items(), key=lambda kv: _order.get(kv[1], 9)
        )
        if arrow in _order
    ][:2]
    lines = [
        f"Before we finish, I want to check I have understood the shape of this "
        f"role. I have it as {situation.label}: {situation.description}",
    ]
    quoted = [e.strip() for e in evidence if e and e.strip()][:2]
    if quoted:
        lines.append("That is mostly from what you said about " + " and ".join(quoted) + ".")
    if lifted:
        lines.append(
            "If that is right, the assessment will lean harder on "
            + " and ".join(lifted)
            + " than it otherwise would."
        )
    if situation.confused_with:
        alternatives = ", ".join(
            SITUATIONS[other].label for other in situation.confused_with
        )
        lines.append(f"If it is closer to {alternatives}, tell me and I will change it.")
    return " ".join(lines)


def as_dict(key: str) -> dict[str, Any]:
    """The artifact projection. Carries the key and the label, never the
    modifiers -- those are internal ranking data like every other weight."""
    situation = SITUATIONS[key]
    return {
        "key": situation.key,
        "label": situation.label,
        "description": situation.description,
        # §18.4's evidence-emphasis column. A WORD, never a weight, so it is
        # safe on an artifact a recruiter reads.
        "evidence_emphasis": situation.evidence_emphasis,
    }


def apply_to(
    baseline: float, dimension: str, situation_key: str | None
) -> tuple[float, float]:
    """(weighted, multiplier). Returned as a PAIR so the caller records both.

    A function that returned only the result would make the provenance
    unreconstructable, and provenance is the entire acceptance criterion for
    this part of the spec.
    """
    multiplier = dimension_modifiers(situation_key).get(dimension, 1.0)
    return baseline * multiplier, multiplier
