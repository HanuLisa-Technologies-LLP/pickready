"""Customer compliance & legal records (Provider Portal spec §3).

Seven documents per customer, in two groups: four mandatory Indian tax /
compliance records and three commercial ones. They are a property of the
CUSTOMER — the `tenants` row — not of the client-authored `companies` page, so
a customer that has not yet signed in can still have its signed agreement on
file (see migration 0020's docstring for why).

Read/write asymmetry is the whole point of the table:
  * the customer's own HR Head UPLOADS and REPLACES (Customer Portal, RLS
    confines them to their own tenant);
  * the PickReady owner only READS, across every tenant, through the audited
    super-admin bypass scope.
Nothing in the Provider path writes here — enforced by the router, not by
convention (api/provider.py exposes no mutating document route at all).
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

# ── The 7 document types (spec §3.2) ─────────────────────────────────────────
# Mirrors the CHECK constraint in migration 0020. The two tuples are ordered:
# the Provider Portal renders each group in exactly this sequence, so a
# customer's document list reads the same way every time.

#: A. Mandatory Indian compliance & tax documents.
TAX_DOCUMENT_TYPES: tuple[str, ...] = (
    "gstin_certificate",
    "pan_card",
    "tan_number",
    "bank_account_details",
)

#: B. Vital commercial & legal records.
COMMERCIAL_DOCUMENT_TYPES: tuple[str, ...] = (
    "signed_agreement",
    "purchase_order",
    "msme_certificate",
)

DOCUMENT_TYPES: tuple[str, ...] = TAX_DOCUMENT_TYPES + COMMERCIAL_DOCUMENT_TYPES

#: Human labels. Server-side so the Provider UI and any future export agree on
#: the wording without each re-deriving it from the enum value.
DOCUMENT_LABELS: dict[str, str] = {
    "gstin_certificate": "GSTIN Certificate",
    "pan_card": "PAN Card",
    "tan_number": "TAN (Tax Deduction Account Number)",
    "bank_account_details": "Bank Account Details",
    "signed_agreement": "Signed Agreement / Schedule of Terms",
    "purchase_order": "Purchase Order (PO)",
    "msme_certificate": "MSME Certificate",
}

DOCUMENT_GROUPS: dict[str, str] = {
    **{value: "tax" for value in TAX_DOCUMENT_TYPES},
    **{value: "commercial" for value in COMMERCIAL_DOCUMENT_TYPES},
}


class ComplianceDocument(Base, UUIDPKMixin, CreatedAtMixin):
    """One stored record per (customer, document_type).

    UNIQUE on that pair: re-uploading a GSTIN certificate REPLACES the current
    one rather than adding a second row, so the Provider never has to guess
    which of two GSTIN entries is current.
    """

    __tablename__ = "compliance_documents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "document_type", name="uq_compliance_documents_type"),
        Index("ix_compliance_documents_tenant", "tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    document_type: Mapped[str] = mapped_column(String(40), nullable=False)
    file_url: Mapped[str] = mapped_column(String(500), nullable=False)
    file_public_id: Mapped[str | None] = mapped_column(String(255))
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str | None] = mapped_column(String(120))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    # NULL once the uploader's account is deleted — the document outlives the
    # person (FK is ON DELETE SET NULL, migration 0020).
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default="now()"
    )
