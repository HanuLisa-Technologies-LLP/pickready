"""Evidence vs Claim Summary: what was asserted, and what actually stands behind it.

WHAT THIS SECTION IS FOR
--------------------------
A PRISM Report states grades. It has never stated, in one place, which of the
candidate's own assertions the record actually reaches. This section does: the
material claims, the evidence identified for each, and how well corroborated
that evidence is.

A CLAIM IS NOT A FACT, and this section is the one place the report says so out
loud. "Led the migration to Kafka" on a resume is an assertion by an interested
party; it may be true, and a report that files it as a fact has already lost the
ability to ask whether it holds up. Every entry keeps the two apart: the claim
in one column, the evidence in the next.

SELECTED BY MATERIALITY, NEVER BY WORDING
-------------------------------------------
`miti.claims.materiality_for` derives materiality from the frozen matrix, and
this module reads what it derived. It never reads the claim's own language.
That ordering is the whole guard: a confidently written resume rates its own
assertions material if you let the wording decide, which is precisely the
document this product exists to see through.

ABSENCE OF EVIDENCE IS NEVER RENDERED AS THE CLAIM BEING FALSE
----------------------------------------------------------------
This is the rule the section stands or falls on. A claim nothing in the record
addressed reports exactly that, cited to the `searched` node, which exists for
this reason: the citation is the evidence that was SEARCHED, and it is what
separates

    "we assessed this area and nothing said addressed the claim"

from

    "we never asked"

and both of those from "the claim is untrue", which this section never states
about anything. The wording spells it out rather than leaving it to inference,
because a bare "no evidence found" beside a hiring decision reads as a verdict
however it was meant. `tests/test_claim_evidence.py` sweeps the rendered copy
for that reading and fails on it.

WHERE BGV ENTERS, AND WHERE IT STOPS
--------------------------------------
An employment the candidate declared and a previous employer confirmed is the
only evidence in this product whose ORIGINATOR is not the candidate, so it is
the only thing that can make a claim read as independently corroborated. It
enters here as `evidence.KIND_EMPLOYER` nodes.

What crosses into the report is the employer's NAME, the role the candidate
declared, and this tenant's own verification decision as a word. What does not,
ever, is the HR contact's name or address: it is a third party's personal
contact detail a candidate handed over for one purpose, it reaches the
recruiter running the verification and nobody else, and a delivered report is
the copy that gets forwarded. Nor does another tenant's verification: the
caller reads `bgv_verifications` under its own tenant scope, so a confirmation
run by a different employer is invisible here and stays that way until the
candidate consents to sharing it.

NOTHING HERE INFERS A VERDICT FROM A REPLY. `verified` and `not_verified` are
decisions a person pressed a button to record. This section reports them and
adds nothing to them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from app.models.bgv_verification import (
    VERIFICATION_NOT_VERIFIED,
    VERIFICATION_VERIFIED,
)
from app.services import evidence_confidence
from app.services.miti import claims as miti_claims
from app.services.siddhi import evidence as siddhi_evidence

__all__ = [
    "SECTION_KEY",
    "SECTION_TITLE",
    "MIN_ENTRIES",
    "MAX_ENTRIES",
    "ABSENT_EVIDENCE",
    "CLAIM_SUPPORTED",
    "CLAIM_CONTRADICTED",
    "CLAIM_INFERRED_ONLY",
    "CLAIM_UNSUPPORTED",
    "NO_CLAIMS_STATEMENT",
    "ClaimRecord",
    "build",
    "employment_claims",
    "employment_nodes",
]

#: The evidence ledger's four claim states, restated as literals for the reason
#: `evidence_confidence` restates its vocabularies: importing
#: `app.services.evidence` here walks through `verification` into
#: `functional_assessment` and back out through Siddhi, and Siddhi is on the
#: report side of that cycle. `tests/test_claim_evidence.py` pins all four
#: against `evidence.ledger`, in both directions, so they cannot drift.
CLAIM_SUPPORTED = "supported"
CLAIM_CONTRADICTED = "contradicted"
CLAIM_INFERRED_ONLY = "inferred_only"
CLAIM_UNSUPPORTED = "unsupported"

SECTION_KEY = "claim_evidence"
SECTION_TITLE = "Evidence vs Claim Summary"

#: The brief's three to six. MIN is a target, not a floor that gets padded to:
#: `build` never invents an entry to reach it, for the same reason the
#: validation points section never pads to three.
MIN_ENTRIES = 3
MAX_ENTRIES = 6

#: THE SENTENCE THIS WHOLE MODULE EXISTS TO GET RIGHT.
#:
#: It states a fact about what was EXAMINED and then says in so many words that
#: it is not a fact about the claim. The second half is not redundant: "no
#: evidence found" printed beside a hiring decision is read as a verdict
#: whatever was meant by it, and the reader has no way to recover the
#: distinction from the first half alone.
ABSENT_EVIDENCE = (
    "This area was assessed and nothing in the record addressed the claim. "
    "That is a gap in what was examined, not a finding about the claim itself."
)

#: What an empty section prints. A report with no material claims on record is
#: a real state, and blank space would read as a rendering failure.
NO_CLAIMS_STATEMENT = (
    "No claims material to this role were recorded against the evidence "
    "ledger for this assessment."
)

SECTION_NOTE = (
    "What this person asserted, and what the record holds behind each "
    "assertion. Nothing here states whether a claim is true; it states what was "
    "found when it was looked for."
)

#: Best to worst, the ledger's own claim states, and the sentence each one
#: earns. DATA rather than a chain of conditionals, so a state added to the
#: ledger raises here rather than falling through to a sentence written for a
#: different state.
_STATUS_PREFIX: dict[str, str] = {
    CLAIM_SUPPORTED: "Identified in {sources}.",
    CLAIM_INFERRED_ONLY: (
        "Identified in {sources}, where it is implied rather than stated "
        "directly."
    ),
    CLAIM_CONTRADICTED: (
        "Identified in {sources}, which do not agree with each other. Nobody "
        "has settled which reading is right."
    ),
    CLAIM_UNSUPPORTED: ABSENT_EVIDENCE,
}

#: The verification decisions a person actually recorded, and the sentence the
#: report prints for each. A DECISION, never an inference: nothing reads an
#: employer's reply and concludes anything from it, and the absence of a third
#: entry here is why an unanswered request cannot be reported as either.
#:
#: THE OUTCOME IS A SENTENCE, NOT A SOURCE LABEL, and the difference is the
#: whole reason this table exists. The source is "Previous employer", which is
#: who spoke and is true whichever way they answered. "Employer Confirmed" is
#: the product's display language for a CONFIRMED check, and using it as the
#: name of the source would have printed the word "Confirmed" beside an
#: employment the employer had just declined to confirm.
_EMPLOYER_OUTCOME: dict[str, str] = {
    VERIFICATION_VERIFIED: "The previous employer confirmed this employment.",
    VERIFICATION_NOT_VERIFIED: (
        "The previous employer did not confirm this employment."
    ),
}


@dataclass(frozen=True)
class ClaimRecord:
    """One claim, as this section needs it.

    NOTE THE FIELD LIST. No score, no plausibility, no assessment. The same
    absence `miti.claims.Claim` keeps and for the same reason: extraction must
    not evaluate, and a summary of extraction must not either. Every judgment
    in this section is a word derived from counting sources.
    """

    claim: str
    materiality: str = miti_claims.MATERIALITY_LOW
    #: `evidence_confidence` source KEYS, not labels.
    sources: tuple[str, ...] = ()
    status: str = CLAIM_UNSUPPORTED
    evidence_refs: tuple[str, ...] = ()
    #: One more sentence appended after the status sentence, from a fixed
    #: catalogue the caller owns. It carries a fact the status vocabulary
    #: cannot express on its own, such as which way a previous employer
    #: answered. Never free text from a model: every value in the product comes
    #: from `_EMPLOYER_OUTCOME` below.
    evidence_note: str = ""
    #: The competency or the employer this claim bears on. Carried so a reader
    #: can tie the entry back to a rated line; empty when the claim maps to
    #: nothing on the matrix, which `materiality_for` treats as LOW rather than
    #: dropping.
    area: str = ""


def _materiality_rank(value: str) -> int:
    try:
        return miti_claims.MATERIALITIES.index(str(value))
    except ValueError:
        # An unrecognised materiality sorts LAST rather than first. Assuming a
        # value nobody recognises is critical would push a real Must-have claim
        # out of a six-entry section.
        return len(miti_claims.MATERIALITIES)


def _evidence_sentence(record: ClaimRecord) -> str:
    """What was found, in one sentence, with the sources named.

    A claim with no named source reports the absent-evidence sentence WHATEVER
    its status says. That is deliberate and it is the safe direction: a status
    of `supported` with nothing behind it to name is a defect in the ledger,
    and printing "Identified in ." would state provenance the entry cannot
    produce.
    """
    labels = evidence_confidence.source_labels(record.sources)
    if not labels:
        return ABSENT_EVIDENCE
    template = _STATUS_PREFIX.get(str(record.status))
    if template is None:
        raise ValueError(
            f"Unknown claim status {record.status!r}. A new state in the "
            f"evidence ledger is a decision about what a report asserts and "
            f"needs a sentence written for it, not the one written for "
            f"another state."
        )
    if template is ABSENT_EVIDENCE:
        return ABSENT_EVIDENCE
    sentence = template.format(sources=_join(labels))
    note = " ".join(str(record.evidence_note or "").split())
    return f"{sentence} {note}" if note else sentence


def _join(labels: Sequence[str]) -> str:
    """"a", "a and b", "a, b and c". No Oxford comma and no dash of any kind."""
    items = list(labels)
    if len(items) == 1:
        return items[0]
    return f"{', '.join(items[:-1])} and {items[-1]}"


def build(records: Sequence[ClaimRecord]) -> dict[str, Any]:
    """The whole section: the most material claims, with what stands behind them.

    Sorted by materiality and then by the order the caller assembled them, so
    two runs over the same evaluation produce the same section in the same
    order. A recruiter comparing two candidates reads a reordering as meaning
    something, and it would mean nothing.
    """
    ordered = sorted(
        [record for record in records if str(record.claim or "").strip()],
        key=lambda record: _materiality_rank(record.materiality),
    )
    entries: list[dict[str, Any]] = []
    for record in ordered[:MAX_ENTRIES]:
        confidence = evidence_confidence.confidence_word(record.sources)
        entries.append(
            {
                "area": record.area,
                "claim": " ".join(str(record.claim).split()),
                "evidence": _evidence_sentence(record),
                # The WORD, already converted. A code reaching a renderer is a
                # code that reaches a reader the day somebody prints the field
                # directly.
                "confidence": evidence_confidence.display_word(confidence),
                "evidence_refs": list(record.evidence_refs),
            }
        )
    return {
        "note": SECTION_NOTE,
        "entries": entries,
        "no_claims_statement": NO_CLAIMS_STATEMENT if not entries else None,
    }


# ── The employment half ──────────────────────────────────────────────────────


def _employment_claim_text(employer: str, designation: str) -> str:
    """The declared employment as a sentence, with NO dates in it.

    Dates are deliberately absent. A delivered report states words, a year is a
    digit, and the tenure the candidate declared is on the BGV screen where the
    recruiter who runs the verification reads it. The claim this section is
    reporting on is "I held this role at this employer", and the dates add a
    number to it without adding a claim.
    """
    role = " ".join(str(designation or "").split())
    name = " ".join(str(employer or "").split())
    if role:
        return f"Held the role of {role} at {name}."
    return f"Worked at {name}."


def employment_claims(
    employments: Iterable[Mapping[str, Any]],
) -> list[ClaimRecord]:
    """The candidate's declared employments, as claims with their confirmations.

    `employments` carries `employer_name`, `designation` and `status`, where
    the status is this tenant's own `bgv_verifications` decision or None when
    nobody has run one. THE CALLER READS THOSE ROWS UNDER ITS OWN TENANT SCOPE
    and hands over no contact detail; this function has nowhere to put one.

    MATERIALITY IS DERIVED FROM THE DECISION, NOT FROM THE EMPLOYER. An
    employment a previous employer DECLINED to confirm is critical, because one
    `not_verified` dominates the candidate-level status and holds an offer on
    its own; everything else is high, because a declared employment bears on
    the whole account without being the thing that caps a report. Neither
    reading comes from the claim's wording, which is the property that matters.
    """
    records: list[ClaimRecord] = []
    for row in employments:
        employer = str(row.get("employer_name") or "").strip()
        if not employer:
            continue
        status = str(row.get("status") or "")
        item = siddhi_evidence.employer_item(employer)
        refs = [f"{siddhi_evidence.KIND_SEARCHED}:{item}"]
        # The candidate declared it on their own employment history, which is
        # application data: one originator, themselves.
        sources = [evidence_confidence.SOURCE_VALIDATION]
        if status == VERIFICATION_VERIFIED:
            sources.append(evidence_confidence.SOURCE_BGV)
            refs.append(siddhi_evidence.employer_node(employer).ref)
            claim_status = CLAIM_SUPPORTED
            materiality = miti_claims.MATERIALITY_HIGH
        elif status == VERIFICATION_NOT_VERIFIED:
            # The employer answered, so their answer is a source. It sits on
            # the other side of the claim, which is what CONTRADICTED means and
            # is why the status is not simply "unsupported".
            sources.append(evidence_confidence.SOURCE_BGV)
            refs.append(siddhi_evidence.employer_node(employer).ref)
            claim_status = CLAIM_CONTRADICTED
            materiality = miti_claims.MATERIALITY_CRITICAL
        else:
            claim_status = CLAIM_UNSUPPORTED
            materiality = miti_claims.MATERIALITY_HIGH
        records.append(
            ClaimRecord(
                claim=_employment_claim_text(employer, row.get("designation", "")),
                materiality=materiality,
                sources=tuple(sources),
                status=claim_status,
                evidence_refs=tuple(refs),
                evidence_note=_EMPLOYER_OUTCOME.get(status, ""),
                area=employer,
            )
        )
    return records


def employment_nodes(
    employments: Iterable[Mapping[str, Any]],
) -> tuple[siddhi_evidence.EvidenceNode, ...]:
    """The citable nodes the employment claims need, minted from the same rows.

    A `searched` node for every declared employment, because the employment
    being on the candidate's finalised history and having been carried into
    this assessment IS the record that a confirmation was looked for. An
    `employer` node ON TOP of it only where an employer actually answered.

    Minted here rather than in the caller so the refs a statement carries and
    the refs the report will accept come from one place, which is the whole
    argument `siddhi/evidence.py` makes for the index existing at all.
    """
    nodes: list[siddhi_evidence.EvidenceNode] = []
    seen: set[str] = set()
    for row in employments:
        employer = str(row.get("employer_name") or "").strip()
        if not employer:
            continue
        item = siddhi_evidence.employer_item(employer)
        if item in seen:
            continue
        seen.add(item)
        nodes.append(
            siddhi_evidence.EvidenceNode(
                ref=f"{siddhi_evidence.KIND_SEARCHED}:{item}",
                kind=siddhi_evidence.KIND_SEARCHED,
                item=item,
            )
        )
        if str(row.get("status") or "") in _EMPLOYER_OUTCOME:
            nodes.append(siddhi_evidence.employer_node(employer))
    return tuple(nodes)
