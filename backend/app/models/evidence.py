"""The shared evidence ledger tables (migration 0056).

Mapped for reads and for tests. Writes go through `services/evidence/ledger`,
which UPSERTs on the claim identity and on the (claim, evidence) pair in one
statement each. A per-row ORM write would lose that idempotence, and a scoring
pass that ran twice would file one piece of evidence as two, which reads to
anything counting support as corroboration.

Note what is NOT here: no text column on `EvidenceItem`, and no support-state
column on `EvidenceClaim`. Both absences are load-bearing and both are argued in
the migration's docstring.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin


class EvidenceItemRow(Base, UUIDPKMixin, CreatedAtMixin):
    """One addressable piece of evidence, stored as a REFERENCE to its source."""

    __tablename__ = "evidence_items"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    #: NULL for evidence about the ROLE (a JD line, a SWOT statement), which
    #: belongs to the job and to no candidate.
    link_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_candidate_links.id", ondelete="CASCADE")
    )
    #: resume | answer | jd | swot | validation | memory
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    #: WHERE the sentence lives, never what it says.
    text_ref: Mapped[str] = mapped_column(Text, nullable=False)
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    freshness: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    #: authoritative | validated | observed | inferred. An ORDER, not a weight.
    trust: Mapped[str] = mapped_column(String(20), nullable=False)
    #: INTERNAL ENGINEERING METADATA. Orders evidence in a prompt and in an
    #: operator view; never reaches a client-facing schema.
    relevance: Mapped[Decimal] = mapped_column(
        Numeric(5, 4), nullable=False, default=0
    )
    #: active | superseded | revoked. Retired rows are kept, because a written
    #: report is a permanent record of what it was written from.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_items.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvidenceClaim(Base, UUIDPKMixin, CreatedAtMixin):
    """One assertion the product is making about a subject on a dimension.

    `claim` is the ledger's own normalised wording, written by the product. It
    is not lifted from the candidate, for the same reason `text_ref` is not the
    text.
    """

    __tablename__ = "evidence_claims"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    link_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_candidate_links.id", ondelete="CASCADE")
    )
    subject: Mapped[str] = mapped_column(String(160), nullable=False)
    dimension: Mapped[str] = mapped_column(String(160), nullable=False)
    claim: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvidenceClaimLink(Base, UUIDPKMixin, CreatedAtMixin):
    """Which side of a claim one piece of evidence sits on.

    A row rather than a pair of uuid arrays on the claim: two arrays make "the
    same item on both sides" representable, and no reader could do anything with
    that state except guess.
    """

    __tablename__ = "evidence_claim_links"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    claim_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence_claims.id", ondelete="CASCADE"),
        nullable=False,
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("evidence_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: supports | contradicts
    stance: Mapped[str] = mapped_column(String(20), nullable=False)
