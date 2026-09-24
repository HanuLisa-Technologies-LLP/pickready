"""Delivery's surface: G4 mints the only clearance, and nothing else reaches the PDF.

PLAN-p5 P5-D7. `siddhi.delivery` keeps `gate_delivery`, `clearance_or_reason`
and `prism_pdf`; the other exports had no consumer and were deleted. These
tests are pure (no database): the committed-state half, G4 reading dispositions
written on another connection, is `test_siddhi_committed_trail.py`.
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from app.services.siddhi import delivery


async def test_an_absent_report_is_refused_rather_than_cleared() -> None:
    """`getattr(None, "needs_human_review", False)` reads as "nothing to review".

    Without the explicit refusal an absent report would mint a clearance for a
    document that does not exist, and a route that forgot its own 404 would
    render whatever it had built instead.
    """
    with pytest.raises(ValueError):
        await delivery.gate_delivery(None, None)
    with pytest.raises(ValueError):
        await delivery.clearance_or_reason(None, None)


async def test_an_unflagged_report_clears_through_the_non_raising_form() -> None:
    clearance, reason = await delivery.clearance_or_reason(
        None, SimpleNamespace(needs_human_review=False, job_candidate_link_id=None, id=None)
    )
    assert reason is None
    assert isinstance(clearance, delivery.DeliveryClearance)
    assert clearance.as_dict() == {
        "gate": "G4_human_review",
        "needed_review": False,
        "disposition": None,
        "decided_by": None,
    }


def test_a_clearance_cannot_be_built_outside_the_gate() -> None:
    with pytest.raises(TypeError):
        delivery.DeliveryClearance(needed_review=False)
    with pytest.raises(TypeError):
        delivery.DeliveryClearance(needed_review=False, _minted=object())


def test_the_pdf_renderer_refuses_anything_but_a_clearance() -> None:
    with pytest.raises(TypeError):
        delivery.prism_pdf(
            {"needed_review": False},  # a look-alike, not a clearance
            object(),
            candidate_name="Candidate",
            job_title="Role",
            tenant_name="Tenant",
            generated_at=None,
        )


def test_the_deleted_exports_stay_deleted() -> None:
    """One export path, the PDF. The other four had no consumer (P5-D7)."""
    assert set(delivery.__all__) == {
        "PDF_BLOCKED_REASON",
        "DeliveryBlocked",
        "DeliveryClearance",
        "gate_delivery",
        "clearance_or_reason",
        "prism_pdf",
    }
    for gone in ("prism_json", "prism_email_body", "prism_attachment", "deliver"):
        assert not hasattr(delivery, gone), gone


def test_the_blocked_reason_is_words_only() -> None:
    """Served verbatim to the screen and the 409: no digit, no em dash."""
    assert not re.search(r"\d", delivery.PDF_BLOCKED_REASON)
    assert chr(8212) not in delivery.PDF_BLOCKED_REASON
