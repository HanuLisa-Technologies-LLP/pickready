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
ONE READER IS LIVE. `scorecard.freeze` writes the row and
`scorecard._latest_binding` reads it back: the freeze in force is the highest
`freeze_sequence`, and the next `scorecard_version` is derived from it. That
is why the version lives here rather than being counted from row-creation
batches, because compilation reuses rows and a human edit preserves row
identity, so counting rows is not counting versions.

A READER IS WRITTEN, TESTED AND CALLED BY NOTHING, and saying so here is the
point of this paragraph.

`ppi.published_matrix` would build the A2A matrix artifact from the live rows
at this binding's version. `publish_tatva_matrix`, the pure half it delegates
to, IS called from both compilation and the freeze; the async wrapper that
reads this table has no caller in `app/` or `tests/`.

A versioning resolver used to sit beside it, answering "what was this job
built on when I applied" from this table. Nothing on the live scoring path ever
invoked it, and it was deleted in the Vivekium release. "What was this
candidate assessed against" is answered by `job_skill_snapshots` now: the
first candidate start locks the skills into an immutable snapshot and binds
the conversation to it (`services/assessment_contract.lock_contract`), so the
answer is a ROW with its content and digest rather than a version number over
rows that went on being mutated.

WHAT STILL HOLDS THE LINE for this table is `POST /jobs/{id}/framework/reopen`,
which refuses once any candidate on the job has been invited to an assessment
or has had questions written against the matrix.
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
