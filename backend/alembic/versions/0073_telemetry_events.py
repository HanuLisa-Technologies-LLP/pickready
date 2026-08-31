"""Micro-event telemetry store (Master Directive Part 2 section 5.1).

Revision ID: 0073_telemetry_events
Revises: 0072_credit_purchases

One append-only row per lifecycle milestone, keyed by the EV_* codes Part 2
section 5.1 defines. The metric engines in services/metrics.py read this
table; the emitters in services/telemetry_events.py write it.

Only `tenant_id` carries a foreign key. The other reference columns
(job_id, candidate_id, job_candidate_link_id, actor_user_id) are plain UUIDs
on purpose: telemetry is a historical record, and deleting a job must not
cascade away the evidence it once existed or shift the metric baselines. A
tenant deletion is the one case where the history should genuinely go.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0073_telemetry_events"
down_revision = "0072_credit_purchases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "telemetry_events",
        # PK is client-generated uuid4, same as every UUIDPKMixin table.
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_code", sa.String(30), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("job_id", UUID(as_uuid=True), nullable=True),
        sa.Column("candidate_id", UUID(as_uuid=True), nullable=True),
        sa.Column("job_candidate_link_id", UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("payload", JSONB(), nullable=False, server_default="{}"),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # The metric engines' access path (one tenant, one code, a time window)
    # and the per-requisition drill-down.
    op.create_index(
        "ix_telemetry_events_tenant_code_occurred",
        "telemetry_events",
        ["tenant_id", "event_code", "occurred_at"],
    )
    op.create_index("ix_telemetry_events_job", "telemetry_events", ["job_id"])

    # Tenant-scoped table, so the standard RLS pair, same shape as 0070.
    op.execute("ALTER TABLE telemetry_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE telemetry_events FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY telemetry_events_tenant_isolation ON telemetry_events
        USING (
            tenant_id = current_setting('app.tenant_id', true)::uuid
            OR current_setting('app.bypass_rls', true) = 'on'
        )
        WITH CHECK (
            tenant_id = current_setting('app.tenant_id', true)::uuid
            OR current_setting('app.bypass_rls', true) = 'on'
        )
        """
    )


def downgrade() -> None:
    op.drop_table("telemetry_events")
