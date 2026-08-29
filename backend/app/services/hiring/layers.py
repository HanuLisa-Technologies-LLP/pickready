"""The three-layer hiring intelligence framework (spec-doc5 Part A.1).

    LAYER 1  READY PICK HIRING PHILOSOPHY
             Owner: the platform. Universal. Rarely changes. Compiled into the
             department competency models, the evidence-tier rules and the
             five-dimension scoring engine. Not something a client fills in.
                        | constrains
    LAYER 2  COMPANY HIRING PHILOSOPHY
             Owner: the client's HR Manager / CHRO. Captured once per client,
             reusable across every role that client posts.
                        | constrains
    LAYER 3  ROLE SWOT INTELLIGENCE
             Owner: the Hiring Manager for that specific role. Captured once
             per job, before the JD goes live.
                        | produces
             THE FROZEN EVALUATION CONFIGURATION FOR THIS JOB

THE ONE RULE THIS MODULE ENFORCES
----------------------------------
A lower layer may TUNE a higher layer within declared bounds. A lower layer may
never SUSPEND a higher layer's integrity rules.

Those two verbs are the whole design, and the difference between them is the
difference between a configurable product and an unaccountable one. "This role
needs deeper hands-on depth than our usual bar" is tuning: the competency still
exists, still has to be evidenced, and has merely moved in importance. "Skip the
authenticity check for this candidate" is suspension, and there is no bound
inside which it is acceptable -- a client who could switch off contradiction
detection could switch off the only thing standing between a fabricated resume
and a grade.

So bounds are declared as DATA on each modifiable quantity, `apply` clamps
rather than trusts, and `INVARIANTS` names the rules that carry no bound at all
and are refused outright rather than clamped.

WHY REFUSALS ARE RECORDED RATHER THAN SILENTLY CLAMPED
--------------------------------------------------------
A clamp that leaves no trace is indistinguishable from an input that was already
in range, so nobody ever learns that a client asked for something the platform
would not do. `Resolution.adjustments` records every term that moved and why,
and `Resolution.refusals` records every request that was rejected outright.
Sutra carries both onto the matrix, which is what makes the acceptance
criterion -- "a change to a Layer 2 or Layer 3 input demonstrably moves a weight
in the output" -- answerable by reading a row rather than by rerunning the
pipeline and squinting.

PROVENANCE: RPN-PHIL-001 §3.5 (precedence and conflict resolution), §3.6 (what
each layer must never do), §2 (the Decision Contract, C4 and C5), §11.4
(normalisation and clamping) and §12.4 (prohibited disqualifiers).

§3.5 is a seven-row table and `PRECEDENCE_RULES` below is that table, row for
row, in the order the Runbook prints them. It is the reason `resolve` grew a
`company_prohibits` channel: the first row is "L3 asks for something L2
prohibits -- L2 wins, escalate to HR Manager", and a resolver in which Layer 2
and Layer 3 only ever COMPOSE has no way to express it. Composition (§11.4
steps 1 and 2, apply L2 then L3) and prohibition are different relationships
between the same two layers, and the first version of this module implemented
only the first.

Two of the seven rows are refusals with a NAMED ALTERNATIVE rather than bare
refusals, and the alternative is carried on the `Refusal` because a refusal that
does not say what the client may have instead reads as an outage rather than a
position: C5 offers priority human review in place of auto-rejection, and §12.5
offers a weighted constraint in place of a blunt filter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

__all__ = [
    "LAYER_PLATFORM",
    "LAYER_COMPANY",
    "LAYER_ROLE",
    "LAYERS",
    "layer_rank",
    "INVARIANTS",
    "INVARIANT_ALTERNATIVES",
    "is_invariant",
    "Bound",
    "BOUNDS",
    "Adjustment",
    "Refusal",
    "Resolution",
    "resolve",
    "PrecedenceRule",
    "PRECEDENCE_RULES",
    "precedence_rule",
    "RunbookDataUnavailable",
    "runbook_data",
    "runbook_value",
    "clamp_weight_vector",
]


# ── The Runbook data package ─────────────────────────────────────────────────


class RunbookDataUnavailable(RuntimeError):
    """`app.services.hiring.runbook_data` is missing, or is missing a value.

    Raised, never swallowed, and never replaced with a default. Every number
    this framework applies to a candidate is a Runbook value with a section
    citation behind it (spec-doc6 §10.1 rule 5); a module that substituted its
    own figure when the data package was unreachable would produce a grade
    nobody could trace to a source, which is the exact condition the extraction
    exists to end. An unreadable data package is an outage, and an outage that
    silently keeps scoring is worse than one that stops.
    """


def runbook_data() -> Any:
    """The RPN-PHIL-001 data package, imported lazily.

    LAZILY, and that is deliberate rather than incidental: this package is
    import-light by design (see `hiring/__init__.py`), and a module-level import
    of the data package would make the import order of the whole framework
    depend on a directory of YAML being present. Resolving it inside the call
    keeps a missing data file a runtime error at the one call site that needed
    it, naming that call site, rather than an ImportError three modules away.
    """
    try:
        from app.services.hiring import runbook_data as _data
    except ImportError as exc:  # pragma: no cover - exercised only pre-extraction
        raise RunbookDataUnavailable(
            "app.services.hiring.runbook_data is not importable. The mechanical "
            "content of RPN-PHIL-001 (weights, thresholds, band boundaries, tier "
            "strengths and verbatim instrument text) lives there and is not "
            "restated in this module."
        ) from exc
    return _data


def runbook_value(file: str, *path: str) -> Any:
    """One value out of one Runbook data file, by path. Raises if absent.

    `file` is a data file name without its extension, `path` the successive keys
    into it. The error names both, because the useful question when this fails
    is always "which file, and which key" and an unqualified KeyError answers
    neither.
    """
    data = runbook_data()
    try:
        node = data.load(file)
    except FileNotFoundError as exc:
        raise RunbookDataUnavailable(
            f"Runbook data file {file!r} is missing. It carries "
            f"{'.'.join(path) or 'the value'} for this call site."
        ) from exc
    for key in path:
        if not isinstance(node, Mapping) or key not in node:
            raise RunbookDataUnavailable(
                f"Runbook data file {file!r} has no entry at "
                f"{'.'.join(path)!r}. Nothing is substituted for it."
            )
        node = node[key]
    return node

# ── The layers ───────────────────────────────────────────────────────────────

LAYER_PLATFORM = "platform"   # Layer 1
LAYER_COMPANY = "company"     # Layer 2
LAYER_ROLE = "role"           # Layer 3

#: Strictly ordered, outermost first. Index IS the precedence: a lower index
#: constrains every higher one.
LAYERS: tuple[str, ...] = (LAYER_PLATFORM, LAYER_COMPANY, LAYER_ROLE)

_RANK = {name: index for index, name in enumerate(LAYERS)}


def layer_rank(layer: str) -> int:
    """Precedence rank. Raises for an unknown layer.

    An unknown layer is a programming error and must not default to anything: a
    typo defaulting to `platform` would grant a role-level input the authority
    to overrule the platform, which is the precise inversion this whole module
    exists to prevent.
    """
    try:
        return _RANK[layer]
    except KeyError as exc:
        raise ValueError(f"Unknown layer {layer!r}; expected one of {LAYERS}") from exc


# ── Integrity rules: no bound, refused rather than clamped ───────────────────

#: Things a lower layer may never switch off, at any value, for any role.
#:
#: These are not "settings with a narrow range". They are the properties that
#: make a grade defensible, and a range would imply that some amount of
#: switching them off is acceptable. Each is named as the KNOB somebody would
#: reach for, because that is the form a request actually arrives in.
INVARIANTS: frozenset[str] = frozenset(
    {
        # Runbook Part VII / spec-doc5 §A.3: no flag ever auto-rejects, every
        # flag routes to human review with its evidence attached.
        "auto_reject_on_flag",
        "skip_human_review",
        # The Must-have hard cap. Any Must-have graded Not Matching caps Overall
        # at Moderately Matching, with no override -- a client able to lift it
        # would be able to buy a better grade for a candidate who failed a
        # requirement they themselves declared essential.
        "disable_must_have_cap",
        "override_must_have_cap",
        # Authenticity and triangulation. A client who could disable
        # contradiction detection would remove the only thing between a
        # fabricated resume and a grade.
        "skip_authenticity",
        "skip_triangulation",
        "disable_contradiction_detection",
        # Evidence sufficiency. "Insufficient evidence" must reduce CONFIDENCE
        # and never silently become a score, so a layer cannot declare evidence
        # optional.
        "waive_evidence_requirements",
        "skip_evidence_sufficiency",
        # The no-numbers rule. A client asking to see the score is asking for
        # the one thing the product never shows.
        "expose_numeric_scores",
        # Protected-attribute inference. Unlawful in hiring, and not negotiable
        # because a client wrote it in an intake form.
        "infer_protected_attributes",
        # §3.5 row 7, refused under C4: "Every candidate carries a confidence
        # level. We would rather deliver eight High-Confidence candidates and
        # say so than pad to ten with unverifiable profiles."
        #
        # ADDED IN RECONCILIATION. The first version of this list had no entry
        # for it, which meant a client asking to drop the confidence label was
        # asking for something with no bound declared -- and `resolve` raises on
        # an undeclared quantity, so the request would have surfaced as an
        # internal error rather than as the reasoned refusal §3.5 requires. A
        # refusal that arrives as a stack trace is a refusal nobody reads.
        "remove_confidence_label",
        "hide_confidence_label",
        "suppress_low_confidence_label",
    }
)

#: What the client may have INSTEAD, for the invariants §3.5 pairs with an
#: offer. Keyed by invariant.
#:
#: The Runbook offers an alternative on two of its seven rows rather than simply
#: refusing, and the distinction matters commercially as well as ethically: a
#: client asking to auto-reject on an authenticity flag has a real operational
#: problem (reviewer time), and "no" answers the request while leaving the
#: problem. §3.5 answers both.
INVARIANT_ALTERNATIVES: dict[str, str] = {
    "auto_reject_on_flag": (
        "An authenticity flag can route the candidate straight to a priority "
        "human review queue, so the review happens first rather than not at all."
    ),
    "skip_human_review": (
        "An authenticity flag can route the candidate straight to a priority "
        "human review queue, so the review happens first rather than not at all."
    ),
    "remove_confidence_label": (
        "The dossier can lead with the high-confidence candidates, so the label "
        "orders the list rather than qualifying it."
    ),
    "hide_confidence_label": (
        "The dossier can lead with the high-confidence candidates, so the label "
        "orders the list rather than qualifying it."
    ),
    "suppress_low_confidence_label": (
        "The dossier can lead with the high-confidence candidates, so the label "
        "orders the list rather than qualifying it."
    ),
}


def is_invariant(key: str) -> bool:
    return key in INVARIANTS


# ── §3.5, the precedence table ───────────────────────────────────────────────


@dataclass(frozen=True)
class PrecedenceRule:
    """One row of RPN-PHIL-001 §3.5, as an addressable rule.

    The table is data rather than prose in a docstring because two of its
    columns are behavioural -- who wins, and who is told -- and a resolution
    that recorded the outcome without recording the escalation would satisfy
    the first column and quietly drop the second. §12.4 makes the point
    explicitly for the fairness row: a refused prohibited disqualifier is
    "logged, refused, escalated to the client's HR Manager, and reported in the
    quarterly fairness audit", which is four obligations, not one.
    """

    key: str
    #: The conflict, in the Runbook's own terms.
    conflict: str
    #: Which layer prevails. None where the row is a refusal rather than a
    #: contest between two layers.
    wins: str | None
    #: Who has to be told. None where nobody outside the pipeline does.
    escalate_to: str | None
    #: What the client may have instead, where §3.5 names one.
    alternative: str | None
    #: The Runbook section this row comes from.
    source: str


HR_MANAGER = "hr_manager"
STANDARDS_BOARD = "standards_board"
HIRING_MANAGER = "hiring_manager"
FAIRNESS_AUDIT = "fairness_audit"

#: §3.5, row for row, in the Runbook's printed order.
PRECEDENCE_RULES: tuple[PrecedenceRule, ...] = (
    PrecedenceRule(
        key="role_asks_what_company_prohibits",
        conflict="L3 asks for something L2 prohibits",
        wins=LAYER_COMPANY,
        escalate_to=HR_MANAGER,
        alternative=None,
        source="RPN-PHIL-001 §3.5",
    ),
    PrecedenceRule(
        key="company_asks_what_platform_prohibits",
        conflict="L2 asks for something L1 prohibits",
        wins=LAYER_PLATFORM,
        escalate_to=STANDARDS_BOARD,
        alternative=None,
        source="RPN-PHIL-001 §3.5",
    ),
    PrecedenceRule(
        key="role_weight_exceeds_bounds",
        conflict="L3 weight request exceeds declared bounds",
        wins=LAYER_PLATFORM,
        escalate_to=HIRING_MANAGER,
        alternative=None,
        source="RPN-PHIL-001 §3.5",
    ),
    PrecedenceRule(
        key="role_disqualifier_is_a_protected_proxy",
        conflict="L3 disqualifier is a protected-characteristic proxy",
        wins=LAYER_PLATFORM,
        escalate_to=FAIRNESS_AUDIT,
        alternative=(
            "Most of these are better modelled as a weighted constraint a "
            "candidate can partially satisfy, with the risk stated, rather "
            "than as a filter (§12.5)."
        ),
        source="RPN-PHIL-001 §3.5, §12.4",
    ),
    PrecedenceRule(
        key="everything_is_top_priority",
        conflict="Two L3 competencies both demanded as top priority",
        wins=None,
        escalate_to=HIRING_MANAGER,
        alternative=(
            "A force-ranking session settles it. A configuration in which "
            "everything is a must-have is rejected (§20.3)."
        ),
        source="RPN-PHIL-001 §3.5, §20.3",
    ),
    PrecedenceRule(
        key="auto_rejection_on_authenticity_flag",
        conflict="Client requests auto-rejection on an authenticity flag",
        wins=LAYER_PLATFORM,
        escalate_to=None,
        alternative=INVARIANT_ALTERNATIVES["auto_reject_on_flag"],
        source="RPN-PHIL-001 §3.5, C5",
    ),
    PrecedenceRule(
        key="removal_of_the_confidence_label",
        conflict="Client requests removal of the confidence label",
        wins=LAYER_PLATFORM,
        escalate_to=None,
        alternative=INVARIANT_ALTERNATIVES["remove_confidence_label"],
        source="RPN-PHIL-001 §3.5, C4",
    ),
)

_PRECEDENCE_BY_KEY = {rule.key: rule for rule in PRECEDENCE_RULES}


def precedence_rule(key: str) -> PrecedenceRule:
    """The §3.5 row, or a raise. There is no default row.

    A conflict nobody wrote a rule for must not resolve to the permissive
    reading by accident, which is what a `.get` returning None and a caller
    treating None as "no objection" would do.
    """
    try:
        return _PRECEDENCE_BY_KEY[key]
    except KeyError as exc:
        raise ValueError(
            f"No §3.5 precedence rule named {key!r}; expected one of "
            f"{tuple(_PRECEDENCE_BY_KEY)}"
        ) from exc


# ── Bounds: quantities a lower layer MAY tune ────────────────────────────────


@dataclass(frozen=True)
class Bound:
    """The declared range a lower layer may move a quantity within.

    `low`/`high` are MULTIPLIERS on the Layer 1 baseline, not absolute values.
    That is deliberate: a client tuning "we care more about hands-on depth"
    should not have to know what number the platform started from, and a
    platform baseline that is later re-tuned should carry every client's
    relative preference with it rather than silently changing its meaning.
    """

    low: float
    high: float
    #: Why the range is where it is. Carried because a number with no argument
    #: behind it is a number the next person will change by feel.
    rationale: str

    def clamp(self, value: float) -> float:
        return max(self.low, min(self.high, value))

    def contains(self, value: float) -> bool:
        return self.low <= value <= self.high


#: RUNBOOK-AMBIGUITY (§11.2, §11.3, §11.4): the Runbook bounds a layer modifier
#: ADDITIVELY and this module bounds it MULTIPLICATIVELY, and the two cannot be
#: converted into each other without choosing a baseline.
#:
#: §11.2 and §11.3 state every Layer 2 and Layer 3 modifier as a signed delta on
#: a dimension weight with an absolute cap -- "D2 up, D5 down, +-0.06", "+0.08
#: combined" -- and §11.4 then clamps each weight to [0.05, 0.40], floors D4 at
#: 0.12 and renormalises the vector to 1.0. This module's `Bound` is a
#: multiplier around 1.0 on a per-COMPETENCY quantity, which is a different
#: object at a different granularity: §11.2/§11.3 tune the five DIMENSION
#: weights, and `BOUNDS` tunes a competency's weight, its evidence threshold,
#: its dimension threshold and its question emphasis.
#:
#: Both are needed and neither replaces the other, so both now exist:
#: `clamp_weight_vector` below implements §11.4 on the dimension vector exactly
#: as written, and these multipliers remain the bound on the per-competency
#: quantities the Runbook does not give a figure for. The multipliers themselves
#: are therefore still this implementation's judgment. Recorded in
#: RUNBOOK_OPEN_QUESTIONS_PHASE0B.md; the safe reading was implemented, which is
#: the one that restricts more (a competency can never be deleted, and a client
#: may raise a bar freely but lower it only marginally).
#:
#: What is NOT a judgment call is their shape -- every one is a bounded
#: multiplier around 1.0, so "no opinion expressed" is always the identity and a
#: layer that says nothing changes nothing.
BOUNDS: dict[str, Bound] = {
    # How much a company or a role may re-weight one competency relative to the
    # department baseline. 0.5x to 2.0x is a real preference -- it can move a
    # competency from the middle of a matrix to the top or the bottom -- and it
    # cannot make a competency the department model considers material
    # disappear, which a 0.0 floor would.
    "competency_weight": Bound(
        0.5, 2.0,
        "A real preference, but never enough to delete a competency the "
        "department model considers material. A 0.0 floor would let a client "
        "silently remove a requirement rather than argue with it.",
    ),
    # The evidence bar for a competency. A company may ask for MORE evidence
    # than the platform baseline without limit in the useful direction, and may
    # relax it only slightly -- the asymmetry is the point.
    "evidence_threshold": Bound(
        0.8, 3.0,
        "Asymmetric on purpose. Demanding more evidence is always safe; "
        "demanding much less is how a bar stops being a bar.",
    ),
    # The passing threshold for a dimension.
    "dimension_threshold": Bound(
        0.85, 1.5,
        "Same asymmetry. A client may raise their own bar freely and may lower "
        "it only marginally, because the floor is what makes two clients' "
        "grades mean roughly the same thing.",
    ),
    # How many questions a competency's evidence needs. Bounded at both ends by
    # the candidate's experience, not by rigour: an interview that probes one
    # competency twelve times is an interview nobody finishes.
    "question_emphasis": Bound(
        0.5, 2.0,
        "Bounded by what a candidate will actually sit through, not by rigour.",
    ),
}


# ── Resolution ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Adjustment:
    """One term that moved, and everything needed to reconstruct why."""

    key: str
    #: Which competency / dimension the term applies to, when it is scoped.
    subject: str | None
    layer: str
    requested: float
    applied: float
    clamped: bool
    bound: Bound | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "subject": self.subject,
            "layer": self.layer,
            "requested": round(self.requested, 4),
            "applied": round(self.applied, 4),
            "clamped": self.clamped,
            "bound": (
                {"low": self.bound.low, "high": self.bound.high}
                if self.bound
                else None
            ),
        }


@dataclass(frozen=True)
class Refusal:
    """A request a lower layer had no authority to make.

    Recorded rather than dropped. A refusal nobody can see is a refusal the
    client will make again next quarter, and a support conversation nobody has
    the evidence for.
    """

    key: str
    layer: str
    reason: str
    #: The §3.5 row this refusal enforces, where one applies.
    rule: str | None = None
    #: Who has to be told, per §3.5. A refusal that resolved the conflict and
    #: told nobody satisfies half the row.
    escalate_to: str | None = None
    #: What the client may have instead, where §3.5 names something. A refusal
    #: with no alternative reads as an outage rather than as a position.
    alternative: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "layer": self.layer,
            "reason": self.reason,
            "rule": self.rule,
            "escalate_to": self.escalate_to,
            "alternative": self.alternative,
        }


@dataclass
class Resolution:
    """The outcome of folding Layer 2 and Layer 3 onto a Layer 1 baseline."""

    #: {subject: multiplier} -- the product of every accepted adjustment.
    multipliers: dict[str, float] = field(default_factory=dict)
    adjustments: list[Adjustment] = field(default_factory=list)
    refusals: list[Refusal] = field(default_factory=list)
    #: §3.5 row 3 requires the hiring manager to be NOTIFIED when a Layer 3
    #: request is clamped, not merely for the clamp to be recorded. Carried
    #: separately from `adjustments` because the two answer different questions:
    #: an adjustment is what the engine did, a notification is what somebody has
    #: to be told it did.
    notifications: list[dict[str, Any]] = field(default_factory=list)

    def multiplier_for(self, subject: str) -> float:
        return self.multipliers.get(subject, 1.0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "multipliers": {k: round(v, 4) for k, v in sorted(self.multipliers.items())},
            "adjustments": [a.as_dict() for a in self.adjustments],
            "refusals": [r.as_dict() for r in self.refusals],
            "notifications": list(self.notifications),
        }


def _coerce(value: Any) -> float | None:
    """A modifier that is not a number is not a modifier.

    Returns None rather than raising: an intake form is written by a person and
    a stray string in one field must not take down the resolution of every other
    field. The caller records it as a refusal, which is the visible outcome.
    """
    if isinstance(value, bool):  # `True` is not a multiplier of 1
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


# ── §11.4, normalisation and clamping of the DIMENSION weight vector ─────────


def clamp_weight_vector(weights: Mapping[str, float]) -> tuple[dict[str, float], list[str]]:
    """(clamped and renormalised vector, notes). RPN-PHIL-001 §11.4, steps 3-5.

    Three rules, and the order between them is load-bearing:

      3. every weight is clamped to its floor and ceiling, and no dimension is
         ever zero, because "a dimension weighted zero is a dimension nobody is
         accountable for";
      4. the authenticity floor is higher than the general floor and cannot be
         lowered by any client, because authenticity is a Layer 1 integrity
         property rather than a preference; and
      5. the vector is renormalised so the weights sum to 1.0.

    APPLIED TO A FIXED POINT, NOT ONCE THROUGH, and that is a correction to a
    literal reading of §11.4 rather than a departure from it. Taken as a strict
    sequence, step 5 undoes step 3: scaling a clamped vector to sum to 1.0
    lifts every weight, and a weight that was sitting on the ceiling ends up
    above it. Verified on a real vector -- D1 clamped to the 0.40 ceiling came
    back out at 0.4598 with the sum correct and the ceiling breached.

    This is the same argument `resolve` makes about the composed product: a
    bound that holds at each step and not on the result is not a bound. So the
    clamp and the renormalisation are iterated until both properties hold
    together, which is the only reading under which §11.4's own six steps are
    all true of the vector that leaves this function.

    Returns the notes rather than logging them: a floor that bound is a client
    request that was not honoured, and §11.4 step 6 requires the derivation to
    be recorded in the frozen configuration.

    Floors, the ceiling and the D4 floor are Runbook values and are read from
    the data package. Nothing here restates them.
    """
    floor = float(runbook_value("dimensions", "weight_normalisation", "dimension_floor"))
    ceiling = float(runbook_value("dimensions", "weight_normalisation", "dimension_ceiling"))
    d4_floor = float(runbook_value("dimensions", "weight_normalisation", "d4_floor"))
    # §11.4 names the floor against D4; this codebase names D4
    # `authenticity_consistency`. The map is stated once, in `situations`.
    from app.services.hiring.situations import DIMENSION_BY_RUNBOOK_ID

    authenticity_key = DIMENSION_BY_RUNBOOK_ID["D4"]

    def low_for(name: str) -> float:
        return d4_floor if name == authenticity_key else floor

    names = list(weights)
    if not names:
        raise ValueError(
            "An empty weight vector cannot be normalised, and no default vector "
            "is substituted for one."
        )
    if sum(low_for(name) for name in names) > 1.0:
        raise ValueError(
            "The §11.4 floors for this dimension set cannot be satisfied inside "
            "a vector summing to 1.0. Nothing is silently rescaled past a floor."
        )

    notes: list[str] = []
    current = {name: float(weights[name]) for name in names}
    requested = dict(current)

    # Bounded, because an unbounded loop over floating point is a hang rather
    # than a failure. Convergence is fast: each pass can only pin more
    # dimensions to a bound, and there are five of them.
    for _ in range(len(names) + 2):
        clamped = {
            name: max(low_for(name), min(ceiling, value))
            for name, value in current.items()
        }
        total = sum(clamped.values())
        if total <= 0:
            raise ValueError(
                "A weight vector summing to zero cannot be renormalised. Every "
                "dimension carries a floor precisely so this cannot arise from "
                "clamping."
            )
        if abs(total - 1.0) < 1e-9:
            current = clamped
            break
        # Renormalise only the dimensions that are not pinned to a bound, so
        # the scaling cannot push a pinned one back through the bound it was
        # just held at.
        pinned = {
            name: value
            for name, value in clamped.items()
            if value in (low_for(name), ceiling)
        }
        free = {name: v for name, v in clamped.items() if name not in pinned}
        remainder = 1.0 - sum(pinned.values())
        free_total = sum(free.values())
        if not free or free_total <= 0 or remainder <= 0:
            # Every dimension is pinned. Distribute the residual across the
            # dimensions that still have headroom rather than breaching a bound.
            current = _spread_residual(clamped, low_for, ceiling)
            break
        current = {
            name: (pinned[name] if name in pinned else value * remainder / free_total)
            for name, value in clamped.items()
        }
    else:
        raise ValueError(
            "The §11.4 clamp and renormalisation did not reach a vector that "
            "satisfies both. Nothing is returned that breaches a bound."
        )

    for name in names:
        if abs(current[name] - requested[name]) > 1e-9:
            notes.append(
                f"{name} was requested at {requested[name]:.4f} and resolved to "
                f"{current[name]:.4f} under RPN-PHIL-001 §11.4."
            )
    return current, notes


def _spread_residual(
    vector: Mapping[str, float], low_for: Any, ceiling: float
) -> dict[str, float]:
    """Push the residual onto whichever dimensions still have headroom.

    Reached only when every dimension is pinned to a bound and the vector still
    does not sum to 1.0. Raises rather than breaching a bound, because a weight
    vector that sums correctly while violating the authenticity floor is worse
    than one that refuses to be produced.
    """
    result = dict(vector)
    residual = 1.0 - sum(result.values())
    if abs(residual) < 1e-9:
        return result
    for _ in range(len(result) + 1):
        if residual > 0:
            room = {n: ceiling - v for n, v in result.items() if ceiling - v > 1e-12}
        else:
            room = {n: low_for(n) - v for n, v in result.items() if v - low_for(n) > 1e-12}
        available = sum(abs(v) for v in room.values())
        if not room or available <= 0:
            raise ValueError(
                "The §11.4 floors and ceiling cannot be reconciled with a vector "
                "summing to 1.0 for this dimension set."
            )
        share = min(1.0, abs(residual) / available)
        for name, headroom in room.items():
            result[name] += headroom * share if residual > 0 else -abs(headroom) * share
        residual = 1.0 - sum(result.values())
        if abs(residual) < 1e-9:
            return result
    raise ValueError(
        "The §11.4 residual could not be distributed without breaching a bound."
    )

def resolve(
    key: str,
    *,
    company: Mapping[str, Any] | None = None,
    role: Mapping[str, Any] | None = None,
    company_prohibits: Iterable[str] = (),
) -> Resolution:
    """Fold a Layer 2 and a Layer 3 modifier map onto a Layer 1 baseline of 1.0.

    `company` and `role` are `{subject: multiplier}` -- a competency name, a
    dimension name, whatever `key`'s bound is scoped to. The result is the
    PRODUCT of the accepted multipliers per subject, which is what makes the two
    layers compose rather than the later one overwriting the earlier: a company
    that weights delivery highly and a role that also does should end up higher
    than either alone, and a role that pulls the other way should partly cancel
    the company preference rather than erase it.

    The product is clamped to the SAME bound as each term, so two layers each
    applying the maximum cannot compound past what one layer was allowed to ask
    for. Without that, "tuning within declared bounds" would be true of each
    step and false of the outcome, which is the shape of every configuration
    system that ends up somewhere nobody intended.

    `company_prohibits` is the §3.5 row composition cannot express. Layer 2 and
    Layer 3 normally COMPOSE (§11.4 applies L2, then L3), but the Runbook's
    first precedence row is a different relationship: where Layer 3 asks for
    something Layer 2 PROHIBITS, Layer 2 wins outright and the HR Manager is
    told. Pass the subjects the company has declared closed and a role modifier
    on one of them is refused rather than multiplied in. The company's own
    modifier on that subject still applies, because prohibiting a role from
    moving a quantity is not the same as declining to set it.
    """
    if is_invariant(key):
        resolution = Resolution()
        rule = (
            _PRECEDENCE_BY_KEY["auto_rejection_on_authenticity_flag"]
            if key in ("auto_reject_on_flag", "skip_human_review")
            else _PRECEDENCE_BY_KEY["removal_of_the_confidence_label"]
            if key in INVARIANT_ALTERNATIVES
            else _PRECEDENCE_BY_KEY["company_asks_what_platform_prohibits"]
        )
        for layer, source in ((LAYER_COMPANY, company), (LAYER_ROLE, role)):
            if source:
                resolution.refusals.append(
                    Refusal(
                        key=key,
                        layer=layer,
                        reason=(
                            "This is a Layer 1 integrity rule. A lower layer may "
                            "tune a higher layer within declared bounds; it may "
                            "never suspend one."
                        ),
                        rule=rule.key,
                        escalate_to=rule.escalate_to,
                        alternative=INVARIANT_ALTERNATIVES.get(key),
                    )
                )
        return resolution

    bound = BOUNDS.get(key)
    if bound is None:
        raise ValueError(
            f"No bound declared for {key!r}. Every tunable quantity must declare "
            f"a range in BOUNDS, or be listed in INVARIANTS as untunable. An "
            f"undeclared quantity is an unbounded one."
        )

    resolution = Resolution()
    prohibited = {str(subject) for subject in company_prohibits}
    for layer, source in ((LAYER_COMPANY, company), (LAYER_ROLE, role)):
        for subject, raw in (source or {}).items():
            if layer == LAYER_ROLE and subject in prohibited:
                # §3.5 row 1. L2 wins, and the HR Manager is told -- the role is
                # not silently overruled, because a hiring manager whose input
                # vanished without explanation will simply enter it again.
                rule = _PRECEDENCE_BY_KEY["role_asks_what_company_prohibits"]
                resolution.refusals.append(
                    Refusal(
                        key=key,
                        layer=layer,
                        reason=(
                            f"{subject!r} is closed by this company's Layer 2 "
                            f"configuration. Where a role asks for something the "
                            f"company prohibits, the company's declaration stands."
                        ),
                        rule=rule.key,
                        escalate_to=rule.escalate_to,
                    )
                )
                continue
            requested = _coerce(raw)
            if requested is None:
                resolution.refusals.append(
                    Refusal(
                        key=key,
                        layer=layer,
                        reason=f"{subject!r} carried a non-numeric modifier {raw!r}",
                    )
                )
                continue
            applied = bound.clamp(requested)
            resolution.adjustments.append(
                Adjustment(
                    key=key,
                    subject=subject,
                    layer=layer,
                    requested=requested,
                    applied=applied,
                    clamped=applied != requested,
                    bound=bound,
                )
            )
            if applied != requested and layer == LAYER_ROLE:
                # §3.5 row 3: clamp to the bound, NOTIFY the hiring manager,
                # and record the request. The first version did the first and
                # the third; a clamp nobody was told about is a preference the
                # hiring manager believes is in force and is not.
                rule = _PRECEDENCE_BY_KEY["role_weight_exceeds_bounds"]
                resolution.notifications.append(
                    {
                        "rule": rule.key,
                        "notify": rule.escalate_to,
                        "subject": subject,
                        "key": key,
                        "requested": round(requested, 4),
                        "applied": round(applied, 4),
                        "source": rule.source,
                    }
                )
            current = resolution.multipliers.get(subject, 1.0)
            resolution.multipliers[subject] = current * applied

    # Clamp the COMPOSED product too. Each step was in range; the outcome has to
    # be as well, or "within declared bounds" is a claim about the steps and not
    # about the result.
    for subject, value in list(resolution.multipliers.items()):
        resolution.multipliers[subject] = bound.clamp(value)
    return resolution
