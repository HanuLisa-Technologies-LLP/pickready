"""Micro-event telemetry store (Master Directive Part 2 section 5.1).

One append-only row per lifecycle event, keyed by the EV_* event codes that
Part 2 section 5.1 defines. The metric engines (services/metrics.py) and the
future dashboard tiers read these rows; nothing ever updates or deletes one.

The reference columns (job_id, candidate_id, job_candidate_link_id,
actor_user_id) are deliberately plain UUIDs with NO foreign keys: telemetry is
a historical record, and deleting a job must not cascade away the evidence
that it once existed or take the metric baselines with it. `tenant_id` is the
one exception because a tenant deletion is the one case where the history
should genuinely go.

`occurred_at` is when the milestone happened (server-stamped unless the
emitter knows better); `created_at` is when the row landed. They differ only
for backfills and replays, and keeping both is what makes that visible.
"""
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin


class TelemetryEvent(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "telemetry_events"
    __table_args__ = (
        # The metric engines' access path: one tenant, one event code, a time
        # window (Part 2 section 3's formulas all slice exactly this way).
        Index(
            "ix_telemetry_events_tenant_code_occurred",
            "tenant_id",
            "event_code",
            "occurred_at",
        ),
        # Per-requisition drill-down (TTF deconstruction, stagnation per job).
        Index("ix_telemetry_events_job", "job_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    #: One of services/telemetry_events.EVENT_CODES (EV_REQ_CREATED, ...).
    #: A varchar rather than an enum so a new milestone is a constant and a
    #: deploy, never a migration.
    event_code: Mapped[str] = mapped_column(String(30), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    job_candidate_link_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    #: Part 2 section 5.1's per-event payload fields, as given by the emitter.
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: Copied from the job's spec-doc6 4.1 correlation id on every job-scoped
    #: event, so a whole requisition flow is one query.
    correlation_id: Mapped[str | None] = mapped_column(String(64))
