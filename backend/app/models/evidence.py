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
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text
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
    # ── Temporal validity (migration 0091, RPN-AI-UP-001 W6.4) ───────────────
    # A claim has a validity interval. These five columns are what let Miti
    # distinguish "used Kafka five years ago" from "currently operates Kafka
    # systems", which is what the Trajectory and Potential dimension needs and
    # could not previously express.
    #
    # They are five columns on the ledger rather than a graph database: the
    # ledger is already an entity-claim graph in relational form, and a second
    # store would be a second answer to "where does evidence live".
    #
    # `event_date` and `source_date` are ROUTINELY DIFFERENT and live only
    # here, on the row that references a source. When the thing happened is not
    # when the document saying so was written, and conflating them is how a
    # 2019 achievement described in a resume uploaded yesterday reads as
    # recent. `freshness` above is derived from an `as_of` the caller passes;
    # these are the stated facts it should be derived FROM.
    #
    # Dates, not timestamps, for the first four: they come from documents that
    # state a month at best, and storing "March 2021" as a timestamptz invents
    # a time of day and a zone that every later comparison then depends on.
    event_date: Mapped[date | None] = mapped_column(Date)
    source_date: Mapped[date | None] = mapped_column(Date)
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)
    #: An event in OUR system rather than in a document, so it is an instant.
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
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
    # ── Temporal validity (migration 0091, RPN-AI-UP-001 W6.4) ───────────────
    # THREE of the five, not all of them, and the omission is the point. A
    # claim is an assertion the PRODUCT makes, so it has a validity interval
    # and a last-checked stamp. It has no event and no source of its own:
    # those belong to the `evidence_items` standing behind it. An `event_date`
    # here would invite a reader to take the claim as authoritative over the
    # items it summarises, and the two would then disagree with nothing able to
    # say which was right.
    #
    # `evidence_claim_links` gets none of them. It records which SIDE of a
    # claim an item sits on, and a stance has no validity interval; the item's
    # does.
    valid_from: Mapped[date | None] = mapped_column(Date)
    valid_until: Mapped[date | None] = mapped_column(Date)
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
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
