"""G1-G4: the four pipeline gates, as real checks (spec-doc5 §A.5, Runbook §56).

    G1  SCORECARD APPROVED    before evaluation can run at all
    G2  EVIDENCE SUFFICIENCY  checked at aggregation
    G3  INTEGRITY             authenticity / contradiction, at aggregation
    G4  HUMAN REVIEW          disposition recorded before delivery

spec-doc5 §A.5 says "Implement all four gates from Runbook §56 as real checks,
not documentation", and the acceptance criterion repeats it: "Gates G1-G4 are
real checks with tests, not documentation."

HOW THESE DIFFER FROM `services/agents/gates.py`
--------------------------------------------------
That module has six gates, one per NAMED AGENT, each checking the artifact that
agent publishes at the boundary where it publishes it. This module has four
gates on the PIPELINE, each checking a precondition of the next phase. They are
different axes and both are wanted: `sutra_gate` asks "is this matrix
well-formed", `G1` asks "has a human approved it yet". A matrix can be perfectly
well-formed and unapproved.

They are kept separate rather than merged because merging them would mean one
gate answering two questions, and a caller reading a single verdict could not
tell which one failed.

NOTHING HERE CALLS A MODEL
---------------------------
Same argument the per-agent gates make, and it is the reason they can be trusted
at all: the moment a guard matters most is the moment the provider is down. A
gate that needed a provider would fail open exactly when it is needed, and an
LLM judging its own pipeline's output makes the criterion unfalsifiable as well
as adding a second flaky dependency. Every check below is a count, a set
comparison or a null check, so a reviewer can reconstruct any verdict by hand.

G4 IS THE ONE THAT PROVES THE NO-AUTO-REJECT RULE
----------------------------------------------------
spec-doc5: "No flag has ever caused an auto-rejection; every flag has a human
disposition recorded." G4 is where that becomes checkable. It does not ask
whether a human APPROVED -- it asks whether a human DECIDED. A gate that
required approval would be a gate the pipeline could satisfy by nagging until
somebody clicked; a gate that requires a recorded disposition is satisfied by a
person having actually looked, and is unsatisfiable by anything the pipeline can
do on its own.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from app.services.verification import base as verification

__all__ = [
    "G1",
    "G2",
    "G3",
    "G4",
    "GATES",
    "GateResult",
    "scorecard_gate",
    "evidence_sufficiency_gate",
    "integrity_gate",
    "human_review_gate",
    "run_gate",
    "DISPOSITIONS",
    "DISPOSITION_CLEARED",
    "DISPOSITION_ESCALATED",
    "DISPOSITION_OVERRIDDEN",
    "DISPOSITION_REJECTED",
]

G1 = "G1_scorecard_approved"
G2 = "G2_evidence_sufficiency"
G3 = "G3_integrity"
G4 = "G4_human_review"


@dataclass(frozen=True)
class GateResult:
    """Passed or not, and -- when not -- exactly what is owed.

    `blocking` is separate from `passed` because two of these gates legitimately
    fail without stopping the pipeline. G2 failing means the report must carry
    lower confidence and go to a human; it does not mean the candidate cannot be
    assessed. Collapsing the two would force a choice between blocking a
    legitimate assessment and silently ignoring a real finding.
    """

    gate: str
    passed: bool
    blocking: bool
    reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": self.gate,
            "passed": self.passed,
            "blocking": self.blocking,
            "reasons": list(self.reasons),
        }

    def as_verdict(self) -> verification.Verdict:
        """For a caller inside `agent_loop`, which already knows what to do with
        a `Verdict`. Reuses the existing severity arithmetic rather than adding
        a second accept/reject rule to keep in step."""
        if self.passed:
            return verification.verdict(self.gate, [])
        severity = (
            verification.SEVERITY_HIGH if self.blocking else verification.SEVERITY_MEDIUM
        )
        return verification.verdict(
            self.gate,
            [
                verification.Finding(severity, self.gate, self.gate, reason, reason)
                for reason in self.reasons
            ],
        )


# ── G1: the scorecard must be approved ───────────────────────────────────────


def scorecard_gate(
    *,
    matrix_items: Sequence[Mapping[str, Any]],
    approved_at: Any,
    frozen: bool = False,
) -> GateResult:
    """BLOCKING. Nothing may be evaluated against an unapproved scorecard.

    The reason is the product's only comparability guarantee. The matrix is the
    fixed criteria EVERY candidate on this job is graded against, and it is
    frozen once anyone has been assessed. If a candidate could be scored before
    a human confirmed those criteria, the first candidate would set the criteria
    for everyone by being assessed against a draft.

    THE APPROVAL STAMP IS NOT ENOUGH ON ITS OWN, and this codebase has already
    paid for believing otherwise: 19 of 35 live jobs carried
    `framework_generated_at` and had ZERO competency rows, so every one was
    permanently stuck with an empty framework nobody could approve. A TIMESTAMP
    IS NOT EVIDENCE THAT WORK HAPPENED. This gate asks the TABLE first and the
    stamp second, in that order, for exactly that reason.
    """
    reasons: list[str] = []
    if not matrix_items:
        reasons.append(
            "The scorecard has no items. A stamp is not evidence that generation "
            "happened -- check the table, not the timestamp."
        )
    if not approved_at:
        reasons.append(
            "The scorecard has not been approved by a human. It is the fixed "
            "criteria every candidate on this job is graded against."
        )
    return GateResult(G1, not reasons, blocking=True, reasons=tuple(reasons))


# ── G2: evidence sufficiency ─────────────────────────────────────────────────

#: Independent SOURCE GROUPS needed before a result reads as evidenced.
#: Two: one source is an account, two is corroboration.
MIN_INDEPENDENT_SOURCES = 2

#: How many of the five dimensions must have been judged on real evidence.
MIN_JUDGED_DIMENSIONS = 3

#: A Must-have graded on nothing is the worst case here, because a Must-have is
#: the one item whose grade caps the entire report.
MIN_MUST_HAVE_EVIDENCE = 1


def evidence_sufficiency_gate(
    *,
    independent_sources: int,
    judged_dimensions: int,
    must_have_coverage: Mapping[str, int] | None = None,
) -> GateResult:
    """NON-BLOCKING. Failing means lower confidence and human review.

    NOT BLOCKING, and that is the whole fairness argument of this gate.

    A blocking sufficiency gate would refuse to produce a report for exactly the
    candidates who most need a person to look: the career-changer, the returner,
    the non-traditional background, the candidate whose evidence is thin because
    their history is unusual rather than because they are weak. Refusing them a
    report is not neutrality, it is a silent rejection with better manners.

    So failing this gate does what spec-doc5 asks: reduces CONFIDENCE, does not
    reduce score, and routes to a human. Insufficient evidence and negative
    evidence are not the same thing.
    """
    reasons: list[str] = []
    if independent_sources < MIN_INDEPENDENT_SOURCES:
        reasons.append(
            f"Only {independent_sources} independent source group(s); "
            f"{MIN_INDEPENDENT_SOURCES} are needed before anything reads as "
            f"corroborated rather than merely asserted."
        )
    if judged_dimensions < MIN_JUDGED_DIMENSIONS:
        reasons.append(
            f"Only {judged_dimensions} of the internal dimensions could be judged "
            f"on evidence; {MIN_JUDGED_DIMENSIONS} are needed for a confident "
            f"composite."
        )
    for name, count in sorted((must_have_coverage or {}).items()):
        if count < MIN_MUST_HAVE_EVIDENCE:
            reasons.append(
                f"Must-have {name!r} has no evidence mapped to it. It is the one "
                f"kind of item whose grade caps the whole report."
            )
    return GateResult(G2, not reasons, blocking=False, reasons=tuple(reasons))


# ── G3: integrity ────────────────────────────────────────────────────────────


def integrity_gate(
    *,
    unresolved_contradictions: int,
    contradiction_severity: str = "none",
    authenticity_band: str | None = None,
) -> GateResult:
    """NON-BLOCKING, and this is the most important non-blocking in the file.

    A BLOCKING INTEGRITY GATE WOULD BE AN AUTO-REJECTION. spec-doc5 states as a
    hard constraint that "no flag ever auto-rejects; every flag routes to human
    review with its underlying evidence attached". A gate that stopped the
    pipeline on an authenticity finding would end the candidacy without a person
    ever seeing the finding or the evidence under it -- which is precisely the
    thing the constraint forbids, implemented as infrastructure rather than as a
    decision.

    So this gate FAILS LOUDLY and BLOCKS NOTHING. What it does is set the
    reasons that reach `needs_human_review`, and the report goes to a person
    with the contradiction and its evidence attached.
    """
    reasons: list[str] = []
    if unresolved_contradictions:
        reasons.append(
            f"{unresolved_contradictions} contradiction(s) reached material "
            f"severity with no supported benign explanation."
        )
    if contradiction_severity in {"material", "critical"}:
        reasons.append(
            f"Cross-source consistency is {contradiction_severity}. A person "
            f"should read the disagreement and its evidence."
        )
    if authenticity_band in {"partial", "absent"}:
        reasons.append(
            f"The account's internal consistency graded {authenticity_band}."
        )
    return GateResult(G3, not reasons, blocking=False, reasons=tuple(reasons))


# ── G4: human review disposition ─────────────────────────────────────────────

DISPOSITION_CLEARED = "cleared"        # a person looked and the flag was not a problem
DISPOSITION_ESCALATED = "escalated"    # a person looked and wants more done
DISPOSITION_OVERRIDDEN = "overridden"  # a person disagreed with the pipeline
DISPOSITION_REJECTED = "rejected"      # a person, not the pipeline, ended it

#: Every disposition is a PERSON's decision. There is no `auto_cleared`, and
#: there must never be one: an automatic disposition would satisfy G4 without a
#: human, which is the entire thing G4 exists to prevent.
DISPOSITIONS: frozenset[str] = frozenset(
    {
        DISPOSITION_CLEARED,
        DISPOSITION_ESCALATED,
        DISPOSITION_OVERRIDDEN,
        DISPOSITION_REJECTED,
    }
)


def human_review_gate(
    *,
    needs_review: bool,
    disposition: str | None,
    decided_by: Any = None,
) -> GateResult:
    """BLOCKING when review is needed. A report may not be delivered without it.

    Note what is checked: that a person DECIDED, not that they approved. Every
    one of the four dispositions passes this gate, including `rejected` and
    `escalated`. A gate that required approval would be a gate the pipeline
    could satisfy by nagging until somebody clicked yes; a gate that requires a
    recorded decision is satisfied only by someone having actually looked, and
    is unsatisfiable by anything the pipeline can do on its own.

    `decided_by` is required alongside the disposition. A disposition with no
    person attached is a row that says a human decided and cannot say who, which
    is indistinguishable from the pipeline having written it itself.
    """
    if not needs_review:
        return GateResult(G4, True, blocking=True)
    reasons: list[str] = []
    if not disposition:
        reasons.append(
            "This assessment was flagged for human review and no disposition has "
            "been recorded. No flag may auto-resolve."
        )
    elif disposition not in DISPOSITIONS:
        reasons.append(
            f"Unknown disposition {disposition!r}; expected one of "
            f"{sorted(DISPOSITIONS)}."
        )
    elif decided_by is None:
        reasons.append(
            "A disposition was recorded with no person attached. A decision "
            "nobody is named for is indistinguishable from the pipeline having "
            "written it itself."
        )
    return GateResult(G4, not reasons, blocking=True, reasons=tuple(reasons))


GATES: tuple[str, ...] = (G1, G2, G3, G4)


def run_gate(name: str, **kwargs: Any) -> GateResult:
    """Dispatch by name, so a caller can iterate the pipeline's gates."""
    #: Annotated because the four gates carry different signatures, so an
    #: unannotated literal widens to `object` and the `handler(**kwargs)` call
    #: below becomes a call on an unknown type under `mypy --strict`.
    handlers: dict[str, Callable[..., GateResult]] = {
        G1: scorecard_gate,
        G2: evidence_sufficiency_gate,
        G3: integrity_gate,
        G4: human_review_gate,
    }
    try:
        handler = handlers[name]
    except KeyError as exc:
        raise ValueError(f"Unknown gate {name!r}; expected one of {GATES}") from exc
    return handler(**kwargs)
