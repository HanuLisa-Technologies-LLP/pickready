"""A fresher's own verification documents, and one correction to an HR address.

TWO TABLES, ONE OWNER, AND THAT OWNER IS THE CANDIDATE
--------------------------------------------------------
Both hang off `candidates` with NO `tenant_id`, exactly like
`candidate_employments` and `bgv_inquiries`, and for the reason that table's
docstring already gives: a person's degree certificate and a person's HR
contact are facts about the PERSON. They travel between applications and
between customers, and a tenant column would make a customer the owner of a
document the candidate uploaded before that customer existed.

The consequence that matters is erasure. Nothing but `candidates` reaches
these rows, so Delete My Profile cascades to them, and
`bgv_documents.candidate_document_keys` is what reaches the BYTES, which no
cascade can touch.

WHY THE CORRECTION IS NOT AN EDIT OF `candidate_employments`
--------------------------------------------------------------
That table is immutable by database trigger, deliberately, and the trigger is
not weakened here. It holds the candidate's CLAIM: employer, title, dates, and
the HR contact as they first declared it. An employer who bounces has not made
the claim wrong; the routing is wrong. So a correction is a NEW FACT recorded
beside the claim rather than a rewrite of it, `bgv_contact_corrections` is
APPEND-ONLY so every address ever tried is readable in order, and the effective
send address is resolved by `bgv_delivery.effective_hr_email`, which reads the
newest correction or falls back to the declaration.

A candidate who could edit the declaration could quietly redirect a
verification to a mailbox they control, and nothing downstream would be able to
tell. A correction that is appended, timestamped, and carries the address it
replaced cannot hide that.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

# ── What a fresher supplies ──────────────────────────────────────────────────
#
# The brief, verbatim: "Freshers: no employer BGV. Academic certificates and
# address proof only." Two kinds, and they are DATA rather than a free-text
# label because a recruiter reading a list needs to know which slot is filled
# without reading filenames somebody typed.

DOCUMENT_ACADEMIC_CERTIFICATE = "academic_certificate"
DOCUMENT_ADDRESS_PROOF = "address_proof"

BGV_DOCUMENT_TYPES: tuple[str, ...] = (
    DOCUMENT_ACADEMIC_CERTIFICATE,
    DOCUMENT_ADDRESS_PROOF,
)

#: Shown beside each slot. Served from the server, like `SUBMISSION_WARNING`,
#: so the label a candidate reads and the value the column stores cannot drift.
DOCUMENT_TYPE_LABELS: dict[str, str] = {
    DOCUMENT_ACADEMIC_CERTIFICATE: "Academic certificate",
    DOCUMENT_ADDRESS_PROOF: "Address proof",
}

# ── Why a correction was made ────────────────────────────────────────────────
#
# A closed set, not free text. The only correction the product accepts today is
# one to an address the provider refused, and naming the reason on the row is
# what stops a future route quietly reusing this table as a general-purpose
# editor for a table that is supposed to be final.

CORRECTION_REASON_BOUNCE = "bounced"

CORRECTION_REASONS: frozenset[str] = frozenset({CORRECTION_REASON_BOUNCE})


class CandidateBGVDocument(Base, UUIDPKMixin, CreatedAtMixin):
    """One uploaded verification document belonging to one candidate."""

    __tablename__ = "candidate_bgv_documents"
    __table_args__ = (
        CheckConstraint(
            "document_type IN ('academic_certificate', 'address_proof')",
            name="ck_candidate_bgv_document_type",
        ),
        CheckConstraint("size_bytes > 0", name="ck_candidate_bgv_document_size"),
        # Content-addressed, so a candidate who submits the same scan twice
        # (a double click, a retry after a lost response) ends with one row
        # rather than two identical entries a recruiter has to reconcile.
        UniqueConstraint(
            "candidate_id",
            "document_type",
            "sha256",
            name="uq_candidate_bgv_document",
        ),
        Index(
            "ix_candidate_bgv_documents_candidate", "candidate_id", "document_type"
        ),
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    #: THE OBJECT KEY NEVER CROSSES AN API BOUNDARY. It is stored so erasure
    #: can enumerate the bytes and for no other purpose; every response names
    #: the row id and the filename the candidate chose.
    object_key: Mapped[str] = mapped_column(String(500), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class BGVContactCorrection(Base, UUIDPKMixin, CreatedAtMixin):
    """A candidate's replacement HR address for one employment row.

    APPEND-ONLY. There is no update path and no delete path anywhere in the
    product, so the full sequence of addresses tried for one employer is
    readable in order, together with the address each one replaced. That is the
    record that makes a correction auditable without making the claim editable.
    """

    __tablename__ = "bgv_contact_corrections"
    __table_args__ = (
        Index(
            "ix_bgv_contact_corrections_employment",
            "candidate_employment_id",
            "created_at",
        ),
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_employment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidate_employments.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: The address this one replaced, copied at write time. Stored rather than
    #: re-derived, because the row it came from is by then two corrections ago.
    previous_hr_email: Mapped[str] = mapped_column(String(320), nullable=False)
    hr_email: Mapped[str] = mapped_column(String(320), nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)
