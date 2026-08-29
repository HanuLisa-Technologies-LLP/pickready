"""Which Company DNA version a job's scorecard was frozen against.

THE `company_dna` TABLE ITSELF IS NOT DEFINED HERE, AND THAT IS DELIBERATE.
`app/models/hiring.CompanyDNA` already maps it (migration 0059) and a second
mapped class over one table is not a convenience, it is two answers to "what
is this client's philosophy" with nothing to choose between them. Import it
from `app.models.hiring`; this module adds only what 0059 did not have.

WHAT 0059 DID NOT HAVE
----------------------
spec-doc6 §4.2 requires that "every Role references the exact CompanyDNA
version in force when its scorecard was frozen". `evaluations.company_dna_version`
records the version one CANDIDATE was scored under, which is a different
question asked later: it is the version at SCORING time, and a job whose
scorecard was frozen in March and first scored against in June would answer
both questions with June.

WHY A BINDING ROW RATHER THAN A COLUMN ON `jobs`
------------------------------------------------
Because a scorecard can be frozen more than once. spec-doc6 §5 requires an
explicit, auditable revision workflow for post-finalisation changes, and a
column would be overwritten by the second freeze, taking with it the answer to
"which philosophy was in force when this job was first locked" for every
candidate already assessed under it. The row is APPEND-ONLY: the freeze in
force is the highest `freeze_sequence` for that job, and the earlier rows are
the record.

It also keeps the Layer 2 reference in one place a reader can find. A job's
relationship to Company DNA is a Layer 2 concern; putting it on `jobs` would
scatter the layer across the schema.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

__all__ = ["JobCompanyDNABinding"]


class JobCompanyDNABinding(Base, UUIDPKMixin, CreatedAtMixin):
    """One freeze of one job's scorecard against one Company DNA version.

    APPEND-ONLY. Nothing updates a binding; a re-freeze writes a new row with a
    higher `freeze_sequence`. That is what makes the trail answer the question
    a candidate's evaluation has to answer years later, which is not "what is
    this job built on now" but "what was it built on when I applied".

    `company_dna_version` is COPIED alongside the id rather than joined for.
    Same rule `report_dimensions.required_level` and
    `evaluations.scorecard_version` already follow: a permanent record of the
    criteria in force must not change when the thing it points at is revised,
    and a version number that survives a deleted row is a version number
    somebody can still investigate.
    """

    __tablename__ = "job_company_dna_bindings"
    __table_args__ = (
        UniqueConstraint(
            "job_id", "freeze_sequence", name="uq_job_company_dna_binding_sequence"
        ),
        Index("ix_job_company_dna_bindings_job", "job_id", "freeze_sequence"),
        Index("ix_job_company_dna_bindings_tenant", "tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    #: ON DELETE RESTRICT would be wrong here and SET NULL would be worse: the
    #: artifact is never deleted while the tenant exists (versions are
    #: superseded, not removed), and the CASCADE that does reach it comes from
    #: the tenant, which takes the job and the binding with it anyway.
    company_dna_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("company_dna.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Copied. See the class docstring.
    company_dna_version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 1 for the first freeze of this job, then 2, 3 and so on. A number rather
    #: than an `is_current` flag because the sequence is the history, and a flag
    #: would need a partial unique index to say the same thing less clearly.
    #:
    #: `server_default` as well as `default`, and that is not belt and braces:
    #: migration 0060 writes `DEFAULT 1` and a model carrying only the
    #: Python-side default produces a DIFFERENT table when the schema is built
    #: from metadata. Two schemas for one table is exactly the kind of drift
    #: that shows up first as a test failing for a reason that has nothing to do
    #: with what it was testing.
    freeze_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    #: The scorecard version this freeze produced, so the two versions a
    #: candidate was evaluated under can be read from one row.
    scorecard_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    #: ON DELETE SET NULL, unlike `review_dispositions.decided_by`. This row
    #: records a configuration event, not a human judgement about a person, so
    #: a departed employee must not make the job un-freezable.
    frozen_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    #: The correlation id spec-doc6 §4.1 requires to be traceable through
    #: Bodha, Sutra, Yukti, Vaada, Miti and Siddhi. Written here so a frozen
    #: matrix can be tied back to the intake session that configured it.
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
