"""Which scorecard version a job was frozen against, and when.

WHY A BINDING ROW RATHER THAN A COLUMN ON `jobs`
------------------------------------------------
Because a scorecard can be frozen more than once. spec-doc6 5 requires an
explicit, auditable revision workflow for post-finalisation changes, and a
column would be overwritten by the second freeze, taking with it the answer to
"which criteria were in force when this job was first locked" for every
candidate already assessed under it. The row is APPEND-ONLY: the freeze in
force is the highest `freeze_sequence` for that job, and the earlier rows are
the record.

WHAT READS IT
-------------
`services/orchestration/versioning.resolve_for_application` answers "what was
this job built on when I applied" by taking the binding with the greatest
`frozen_at` that is not after the application's `created_at`. A candidate's
evaluation context is COPIED from that answer rather than joined for, so a
later re-freeze cannot reach backwards into an application that predates it.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

__all__ = ["JobScorecardBinding"]


class JobScorecardBinding(Base, UUIDPKMixin, CreatedAtMixin):
    """One freeze of one job's scorecard.

    APPEND-ONLY. Nothing updates a binding; a re-freeze writes a new row with a
    higher `freeze_sequence`. That is what makes the trail answer the question
    a candidate's evaluation has to answer years later, which is not "what is
    this job built on now" but "what was it built on when I applied".
    """

    __tablename__ = "job_scorecard_bindings"
    __table_args__ = (
        UniqueConstraint(
            "job_id", "freeze_sequence", name="uq_job_scorecard_binding_sequence"
        ),
        Index("ix_job_scorecard_bindings_job", "job_id", "freeze_sequence"),
        Index("ix_job_scorecard_bindings_tenant", "tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    #: 1 for the first freeze of this job, then 2, 3 and so on. A number rather
    #: than an `is_current` flag because the sequence is the history, and a flag
    #: would need a partial unique index to say the same thing less clearly.
    #:
    #: `server_default` as well as `default`, and that is not belt and braces:
    #: the migration writes `DEFAULT 1` and a model carrying only the
    #: Python-side default produces a DIFFERENT table when the schema is built
    #: from metadata. Two schemas for one table is exactly the kind of drift
    #: that shows up first as a test failing for a reason that has nothing to do
    #: with what it was testing.
    freeze_sequence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    #: The scorecard version this freeze produced.
    scorecard_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    #: ON DELETE SET NULL, unlike `review_dispositions.decided_by`. This row
    #: records a configuration event, not a human judgement about a person, so
    #: a departed employee must not make the job un-freezable.
    frozen_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    #: The correlation id spec-doc6 4.1 requires to be traceable through
    #: Sutra, Yukti, Vaada, Miti and Siddhi. Written here so a frozen matrix
    #: can be tied back to the intake session that configured it.
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
