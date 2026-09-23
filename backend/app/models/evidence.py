"""The evidence tables: the JOB SCOPED ledger (0056) and the PORTABLE layer (0113).

Mapped for reads and for tests. Writes to the job scoped ledger go through
`services/evidence/ledger`, which UPSERTs on the claim identity and on the
(claim, evidence) pair in one statement each. A per-row ORM write would lose
that idempotence, and a scoring pass that ran twice would file one piece of
evidence as two, which reads to anything counting support as corroboration.

Note what is NOT here: no text column on `EvidenceItem`, and no support-state
column on `EvidenceClaim`. Both absences are load-bearing and both are argued in
the migration's docstring.

TWO STORES, AND THE SECOND ONE IS NOT AN EXTENSION OF THE FIRST (0113)
-----------------------------------------------------------------------
`PortableEvidenceItem` sits at the bottom of this file and is a DIFFERENT TABLE
on purpose. The change request asked whether to make `evidence_items.job_id`
nullable instead, and the answer is no, for three reasons that each stand alone:

* `evidence_items.tenant_id` is NOT NULL with an RLS policy on tenant equality.
  Portable evidence follows the CANDIDATE across tenants, the way
  `candidate_employments` already does. A portable row living here would have
  to carry some tenant's id, and would then be invisible to the next employer
  who needs it, which is the whole feature not working. Dropping the tenant
  column instead would rewrite the policy every existing reader depends on.
* `job_id` is NOT NULL ON DELETE CASCADE, and job closure is being given a
  purge of job scoped assessment data. Both of those are CORRECT for a job
  scoped ledger and fatal for a candidate's standing record. A nullable
  `job_id` would leave the portable rows one mistyped `WHERE` away from a
  sweep written for the job scoped ones.
* The tables answer different questions. This one answers "what did we read
  when we graded this application"; that one answers "what do we know about
  this person". One table would make every future reader decide which it was
  holding, from a nullable column.

So the job reference on the portable row is NULLABLE and ON DELETE SET NULL: a
deleted job forgets where a fact came from and never takes the fact with it.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin


class EvidenceItemRow(Base, UUIDPKMixin, CreatedAtMixin):
    """One addressable piece of evidence, stored as a REFERENCE to its source."""

    __tablename__ = "evidence_items"
    __table_args__ = (
        # ONE LIVE ROW PER ANSWER (migration 0118). The conflict arbiter of
        # `ledger.record_evidence`, which is what makes the per-answer writer
        # idempotent under concurrency and not only in sequence: the writer runs
        # during the conversation AND again as the scoring backfill.
        Index(
            "ux_evidence_items_live_answer",
            "tenant_id",
            "link_id",
            "source_id",
            unique=True,
            postgresql_where=text(
                "source_type = 'answer' AND status = 'active' AND link_id IS NOT NULL"
            ),
        ),
    )

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


class PortableEvidenceItem(Base, UUIDPKMixin, CreatedAtMixin):
    """One fact about a PERSON that outlives the job it was first read on (0113).

    THE OWNER RULING OF 2026-09-22 reversed the 2026-07-30 "reuse is retired"
    decision, and this table is where the reversal lives. Read
    `services/retake.py` for the reversal itself; what matters here is the
    shape, because the shape is what makes the reversal safe.

    WHAT IS ABSENT IS THE POINT
    -----------------------------
    There is no score column. No grade, no band, no percent, no rating, no
    required level, no verdict, no composite, no tier. That is not an
    oversight to be corrected the first time somebody wants to carry a report
    forward: it is the enforcement of the rule the whole feature stands on.

        Portability is of the underlying EVIDENCE, judged against the NEW
        matrix. It is never of a verdict.

    A prior job's grade was reached against a matrix generated from a
    different JD, by a rubric written for different questions. Carrying it
    would state a verdict about criteria the candidate was never assessed on,
    which is the exact error the 2026-07-30 retirement was written to prevent
    and which the reversal does not license. A column is the only place a
    verdict could travel, so there is no column, and
    `tests/test_portable_evidence.py` reads `information_schema` and fails on
    one appearing.

    `statement` is the product's OWN normalised wording, like
    `evidence_claims.claim` and for the same reason: a verbatim copy of what a
    candidate wrote, in a row that follows them to every employer they ever
    apply to, is a far wider disclosure than the transcript it came from.
    `source_ref` is a locator, never the text.

    NO TENANT COLUMN, DELIBERATELY
    --------------------------------
    A person's education did not happen inside a tenant. This table follows
    `candidate_employments` and `bgv_inquiries`: candidate owned, tenant free,
    reached through the candidate's own session or through an audited bypass.
    `source_tenant_id` and `source_job_id` are PROVENANCE, nullable, and
    `ON DELETE SET NULL`, so a closed job or a departed customer costs the row
    its origin and never the row.

    `uq_portable_evidence_subject` is UNIQUE with no predicate, and `status`
    is a soft retirement, which is the exact pairing that produced a 500 on
    the matrix editor in pilot on 2026-09-20. It is safe here for the one
    reason it was not safe there: the only writer is an UPSERT that REVIVES
    the row on conflict (`services/portable_evidence.record`). There is no
    INSERT path that can land on an occupied key.
    """

    __tablename__ = "portable_evidence_items"
    __table_args__ = (
        Index("ix_portable_evidence_candidate", "candidate_id", "kind"),
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: One of `portable_evidence.PORTABLE_KINDS`, pinned by a CHECK. A closed
    #: vocabulary rather than free text: an unrecognised kind is a new claim
    #: about what may be reused, and it should cost a reviewed line.
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    #: What the fact is ABOUT: a skill, an employer, a qualification. This is
    #: what a matrix criterion is matched against.
    subject: Mapped[str] = mapped_column(String(200), nullable=False)
    #: The product's own wording of the fact. Never the candidate's verbatim.
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    #: Structured detail, swept for verdict-shaped keys at write time.
    facts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    #: The ledger's own four trust levels, in the ledger's own order.
    trust: Mapped[str] = mapped_column(String(20), nullable=False)
    #: resume | validation | bgv | profile. WHO originated it, which is what
    #: `evidence_confidence` counts and what keeps one person saying one thing
    #: twice from reading as corroboration.
    source_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    #: `table:row_id[#fragment]`, the ledger's own locator grammar.
    source_ref: Mapped[str] = mapped_column(Text, nullable=False)
    source_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL")
    )
    source_tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="SET NULL")
    )
    #: When the fact was TRUE, which is routinely not when it was recorded.
    #: A date, not a timestamp: it comes from a document that states a month
    #: at best, and a timestamptz would invent a time of day and a zone.
    observed_on: Mapped[date | None] = mapped_column(Date)
    #: An event in OUR system, so it is an instant.
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    #: active | superseded | revoked. Retired rows are kept: a delivered report
    #: is a permanent record of what it was written from.
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
