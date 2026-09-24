"""Delivery: gate G4, then the number ban, then the PDF somebody downloads.

ONE EXPORT FORMAT, AND THE OTHER THREE WERE DELETED (PLAN-p5 P5-D7)
---------------------------------------------------------------------
This module used to offer four exports (`prism_json`, `prism_pdf`,
`prism_email_body`, `prism_attachment`) and a `deliver` that ran all four behind
G4. None of them had a production caller: the PDF download route rendered the
document itself, straight through `report_pdf.render_report_pdf`, so G4 guarded
nothing a client ever received. The master prompt's instruction was "wire it
into the PDF and export path, or delete it", and both halves apply:

  * WIRED: `gate_delivery` then `prism_pdf` is the PDF route's path. The route
    is the one export a client receives, and it is the one place G4 now gates.
  * DELETED: `prism_json` (the on-screen report is deliberately NOT gated: it
    is where the person who must record the G4 decision reads the report, so
    gating it would make the gate unsatisfiable), `prism_email_body` and
    `prism_attachment` (no email carries a report, and inventing one to keep
    a function alive is inventing a caller), and `deliver`.

A PDF CANNOT BE RENDERED WITHOUT A CLEARANCE
----------------------------------------------
`prism_pdf` takes the `DeliveryClearance` as its first argument, and a
clearance can be minted only by `gate_delivery` (the constructor refuses any
other caller). So "the PDF path runs G4 first" is a property of the types
rather than of the order two lines happen to be written in. The number ban runs
inside the renderer (`report_pdf.render_report_pdf` calls `numbers.assert_clean`
on its own input), which is the one implementation of it on this path.

G4 ASKS WHETHER A HUMAN DECIDED
---------------------------------
Not whether they approved. All four dispositions pass, `rejected` included. A
gate requiring approval is a gate the pipeline can satisfy by nagging until
somebody clicks yes; a gate requiring a recorded decision is satisfiable only by
someone having actually looked. There is no `auto_cleared` disposition, a
Postgres CHECK refuses one, and `review_dispositions.decided_by` is ON DELETE
RESTRICT so a decision can never survive the erasure of the person who made it.

WHY BLOCKING HERE IS SAFE AND BLOCKING AT G2/G3 IS NOT
--------------------------------------------------------
G2 and G3 are non-blocking on purpose: a blocking sufficiency gate refuses a
report to exactly the candidates who most need a person to look at it, and a
blocking integrity gate would end a candidacy without anybody seeing the
finding. G4 blocks in the other direction. It withholds a document from a
client until a human has made a decision, and decides nothing about the
candidate.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "PDF_BLOCKED_REASON",
    "DeliveryBlocked",
    "DeliveryClearance",
    "gate_delivery",
    "clearance_or_reason",
    "prism_pdf",
]

#: The sentence the PDF route answers with while G4 blocks, and the reason the
#: report payload carries so the screen hides a dead Download button. SERVED BY
#: THE SERVER, so the refusal and the explanation cannot drift apart.
PDF_BLOCKED_REASON = (
    "A person with integrity review authority must record a decision on this "
    "candidate before the PRISM Report can be downloaded."
)

#: The only thing that can mint a clearance. Module private, compared by
#: identity: a caller elsewhere cannot construct a `DeliveryClearance` without
#: reaching into this module's internals, which is a diff a reviewer sees.
_MINT = object()


class DeliveryBlocked(RuntimeError):
    """G4 has not been satisfied, so the PDF is not produced.

    Carries the gate's own reasons verbatim for the log and the audit; the
    client is told `PDF_BLOCKED_REASON`, which is the actionable form of every
    one of them.
    """

    def __init__(self, reasons: tuple[str, ...]) -> None:
        self.reasons = reasons
        super().__init__(
            "The PRISM Report is not deliverable: "
            + " ".join(reasons)
            + " (gate G4, human review disposition)"
        )


@dataclass(frozen=True)
class DeliveryClearance:
    """The recorded fact that G4 passed, and on what basis. Minted by `gate_delivery` only."""

    needed_review: bool
    disposition: str | None = None
    decided_by: Any = None
    _minted: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._minted is not _MINT:
            raise TypeError(
                "A DeliveryClearance is minted by siddhi.delivery.gate_delivery "
                "and nothing else; a clearance a caller builds itself would "
                "satisfy G4 without the gate running."
            )

    def as_dict(self) -> dict[str, Any]:
        return {
            "gate": "G4_human_review",
            "needed_review": self.needed_review,
            "disposition": self.disposition,
            "decided_by": str(self.decided_by) if self.decided_by else None,
        }


async def gate_delivery(session: Any, report: Any) -> DeliveryClearance:
    """Run G4 against this report's recorded dispositions, or refuse.

    Reads the disposition from the database rather than accepting one from the
    caller. A gate whose input its own caller supplies is a gate the caller can
    satisfy, and G4's entire purpose is to be unsatisfiable by the pipeline.

    THE DECISION MUST POSTDATE THE REPORT. A disposition is recorded against
    the application's EVALUATION, and an evaluation can exist before the report
    does (a run held because a skill could not be assessed writes one and no
    report). A person who decided on that earlier state has not read this
    report, so a disposition created before `synthesized_at` does not clear it.
    A report with no `synthesized_at` has no such boundary and any recorded
    decision counts, which is the pre-release behaviour.
    """
    from app.models.hiring import ReviewDisposition
    from app.services.hiring import gates
    from sqlalchemy import select

    needs_review = bool(getattr(report, "needs_human_review", False))
    disposition: str | None = None
    decided_by: Any = None

    if needs_review:
        link_id = getattr(report, "job_candidate_link_id", None)
        if link_id is None:
            # `link_id == None` would compile to IS NULL and match every
            # disposition whose application was purged, from any candidate:
            # a report with no application is a defect, never a lookup.
            raise ValueError("a PRISM report without an application cannot be gated")
        query = select(ReviewDisposition).where(ReviewDisposition.link_id == link_id)
        written_at = getattr(report, "synthesized_at", None)
        if written_at is not None:
            query = query.where(ReviewDisposition.created_at >= written_at)
        row = (
            await session.execute(
                query.order_by(ReviewDisposition.created_at.desc()).limit(1)
            )
        ).scalars().first()
        if row is not None:
            disposition = row.disposition
            decided_by = row.decided_by

    result = gates.run_gate(
        gates.G4,
        needs_review=needs_review,
        disposition=disposition,
        decided_by=decided_by,
    )
    if not result.passed:
        logger.warning(
            "siddhi.delivery.blocked report_id=%s reasons=%s",
            getattr(report, "id", None),
            list(result.reasons),
        )
        raise DeliveryBlocked(tuple(result.reasons))
    return DeliveryClearance(
        needed_review=needs_review,
        disposition=disposition,
        decided_by=decided_by,
        _minted=_MINT,
    )


async def clearance_or_reason(
    session: Any, report: Any
) -> tuple[DeliveryClearance | None, str | None]:
    """G4 in a non-raising form, for a payload that must say whether the PDF is available.

    The SAME gate, not a second reading of it: this calls `gate_delivery` and
    converts its refusal into `PDF_BLOCKED_REASON`, so the screen can never
    offer a download the route would then refuse.
    """
    try:
        return await gate_delivery(session, report), None
    except DeliveryBlocked:
        return None, PDF_BLOCKED_REASON


def prism_pdf(
    clearance: DeliveryClearance,
    report_out: Any,
    *,
    candidate_name: str,
    job_title: str,
    tenant_name: str,
    generated_at: datetime,
) -> bytes:
    """The downloadable document, for a report G4 has cleared.

    `clearance` is REQUIRED and is the first argument: the only way to hold
    one is to have run `gate_delivery`. The renderer runs the number ban on its
    own input, so the ban cannot be skipped by calling it from elsewhere.
    """
    if not isinstance(clearance, DeliveryClearance):
        raise TypeError(
            "prism_pdf needs the DeliveryClearance gate_delivery returned; "
            "a PDF is never rendered before G4 has run"
        )
    from app.services.report_pdf import render_report_pdf

    return render_report_pdf(
        report_out,
        candidate_name=candidate_name,
        job_title=job_title,
        tenant_name=tenant_name,
        generated_at=generated_at,
    )
