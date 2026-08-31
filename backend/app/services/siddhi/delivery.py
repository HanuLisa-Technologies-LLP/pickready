"""Delivery: gate G4, then the number ban, then the bytes somebody receives.

FOUR EXPORT FORMATS, ONE RULE
-------------------------------
spec-doc6 D8: the PRISM Report "remains named grades only, zero numbers,
unchanged. This includes any export, PDF, email body or attachment."

So there are four ways a report leaves the building and all four go through one
function each, and every one of those functions calls `numbers.assert_clean`
before it returns. The alternative -- checking at the generator -- holds only
for the paths that go through the generator, and the point of the ruling is that
the dashboard now legitimately holds a 0-100 Ready Pick Score, so the report and
the triage surface read from overlapping state. A rule that lived in one
function would be a rule about that function.

G4 RUNS FIRST, AND IT ASKS WHETHER A HUMAN DECIDED
----------------------------------------------------
Not whether they approved. All four dispositions pass, `rejected` included. A
gate requiring approval is a gate the pipeline can satisfy by nagging until
somebody clicks yes; a gate requiring a recorded decision is satisfiable only by
someone having actually looked, and by nothing the pipeline can do on its own.
There is no `auto_cleared` disposition, a Postgres CHECK refuses one, and
`review_dispositions.decided_by` is ON DELETE RESTRICT so a decision can never
survive the erasure of the person who made it.

WHY BLOCKING HERE IS SAFE AND BLOCKING AT G2/G3 IS NOT
--------------------------------------------------------
G2 (evidence sufficiency) and G3 (integrity) are non-blocking on purpose: a
blocking sufficiency gate refuses a report to exactly the candidates who most
need a person to look at it, and a blocking integrity gate would end a candidacy
without anybody seeing the finding. G4 blocks in the other direction. It does
not withhold a decision from a human; it withholds a document from a client
until a human has made one. Nothing about a candidate is decided by it failing.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.services.siddhi import numbers

logger = logging.getLogger(__name__)

__all__ = [
    "DeliveryBlocked",
    "DeliveryClearance",
    "gate_delivery",
    "prism_json",
    "prism_pdf",
    "prism_email_body",
    "prism_attachment",
    "deliver",
]


def _field(source: Any, name: str) -> Any:
    """One field, whether the payload is a model or a plain dict.

    A dict is asked BEFORE `getattr`, because a payload arriving as a dict
    answers `getattr` with a bound method for any key that shadows one and with
    nothing at all for the rest. `report_pdf._value` had the same hole and the
    same fix.
    """
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


class DeliveryBlocked(RuntimeError):
    """G4 has not been satisfied, so the report is not delivered.

    Carries the gate's own reasons verbatim. A refusal a recruiter cannot act on
    is a refusal they will route around, and the actionable form of this one is
    always the same shape: somebody has to open the flagged assessment and
    record what they decided.
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
    """The recorded fact that G4 passed, and on what basis."""

    needed_review: bool
    disposition: str | None = None
    decided_by: Any = None

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
    """
    from app.models.hiring import ReviewDisposition
    from app.services.hiring import gates
    from sqlalchemy import select

    needs_review = bool(getattr(report, "needs_human_review", False))
    disposition: str | None = None
    decided_by: Any = None

    if needs_review:
        row = (
            await session.execute(
                select(ReviewDisposition)
                .where(
                    ReviewDisposition.link_id
                    == getattr(report, "job_candidate_link_id", None)
                )
                .order_by(ReviewDisposition.created_at.desc())
                .limit(1)
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
        needed_review=needs_review, disposition=disposition, decided_by=decided_by
    )


# ── Export format 1: the JSON response ───────────────────────────────────────


def prism_json(report_out: Any) -> dict[str, Any]:
    """The API response payload, checked field by field before it is returned."""
    payload = (
        report_out.model_dump()
        if hasattr(report_out, "model_dump")
        else dict(report_out)
    )
    numbers.assert_clean(payload, where="prism.json")
    return payload


# ── Export format 2: the PDF ─────────────────────────────────────────────────


def prism_pdf(
    report_out: Any,
    *,
    candidate_name: str,
    job_title: str,
    tenant_name: str,
    generated_at: datetime,
) -> bytes:
    """The downloadable document. `report_pdf` runs the ban on its own input.

    Called through rather than duplicated: the renderer has to run the check
    itself, because the download route reaches it directly and a check that only
    ran in this wrapper would be a check the live route skips.
    """
    from app.services.report_pdf import render_report_pdf

    return render_report_pdf(
        report_out,
        candidate_name=candidate_name,
        job_title=job_title,
        tenant_name=tenant_name,
        generated_at=generated_at,
    )


# ── Export format 3: the email body ──────────────────────────────────────────

#: The email that carries a report says the report exists and says nothing about
#: what is in it. That is not caution about numbers alone: an email is forwarded
#: further than any other surface in this product, and a grade quoted in one
#: outlives every access control on the document it came from.
_EMAIL_BODY = (
    "The PRISM Report for {candidate} on {job} is ready.\n"
    "Predictive Role Intelligence & Suitability Mapping.\n\n"
    "Open it in ReadyPick to read the assessment, the gap analysis and the "
    "candidate's own application answers.\n\n"
    "Reference: {reference}\n"
)


def prism_email_body(
    report_out: Any, *, candidate_name: str, job_title: str
) -> str:
    """One notification body, checked as a bare string."""
    reference = _field(report_out, "reference_code") or "not assigned"
    body = _EMAIL_BODY.format(
        candidate=candidate_name, job=job_title, reference=reference
    )
    numbers.assert_text_clean(body, where="prism.email_body")
    return body


# ── Export format 4: the attachment ──────────────────────────────────────────

_SAFE = re.compile(r"[^A-Za-z0-9_-]+")



def prism_attachment(
    report_out: Any,
    *,
    candidate_name: str,
    job_title: str,
    tenant_name: str,
    generated_at: datetime,
) -> tuple[str, bytes, str]:
    """(filename, bytes, media type). The filename is a client-visible string.

    So it follows the USER-VISIBLE copy rename and says PRISM, while the module,
    the route and the stored columns keep saying ppi. A file lands on somebody's
    desktop; a route is quoted in links already sitting in inboxes.
    """
    payload = prism_pdf(
        report_out,
        candidate_name=candidate_name,
        job_title=job_title,
        tenant_name=tenant_name,
        generated_at=generated_at,
    )
    stem = _SAFE.sub("-", candidate_name).strip("-") or "candidate"
    return f"PRISM-Report-{stem}.pdf", payload, "application/pdf"


# ── The whole delivery, in the order it has to happen ────────────────────────


async def deliver(
    session: Any,
    report: Any,
    report_out: Any,
    *,
    candidate_name: str,
    job_title: str,
    tenant_name: str,
) -> dict[str, Any]:
    """G4, then every export format, then the payloads. Raises or returns.

    The order is the whole value. Gating after serialisation would mean the
    bytes already exist, and bytes that exist get sent; gating first means a
    report awaiting a human decision is never rendered in any format at all.
    """
    clearance = await gate_delivery(session, report)
    generated_at = _field(report_out, "synthesized_at") or _field(
        report, "synthesized_at"
    )
    if not isinstance(generated_at, datetime):
        # A report with no synthesis timestamp is a row that was never
        # finished, and dating the delivered document `now` would put a date on
        # a client's permanent record that no stage of the pipeline ever wrote.
        raise DeliveryBlocked(
            (
                "This report carries no synthesis timestamp, so there is no "
                "date to put on the delivered document.",
            )
        )
    filename, pdf, media_type = prism_attachment(
        report_out,
        candidate_name=candidate_name,
        job_title=job_title,
        tenant_name=tenant_name,
        generated_at=generated_at,
    )
    return {
        "clearance": clearance.as_dict(),
        "json": prism_json(report_out),
        "pdf": pdf,
        "email_body": prism_email_body(
            report_out, candidate_name=candidate_name, job_title=job_title
        ),
        "attachment": {"filename": filename, "media_type": media_type},
    }
