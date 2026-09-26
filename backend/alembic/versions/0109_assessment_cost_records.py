"""Per-assessment cost telemetry (change 28D) + prompt-cache accounting (28A).

Revision ID: 0109_assessment_cost_records
Revises: 0108_candidate_deletion_requests

WHY
-----
The platform could say what a PROCESS had spent since it started, and nothing
else. `llm_router.model_stats()` counts tokens and estimated cost per model, in
memory, and that is the right shape for the health endpoint and the wrong shape
for every question the owner actually has: what does one assessment cost, which
client is expensive, is the average drifting. A scoring run is a Fargate
container that exits and a conversation turn is a request on an API task, so
the spend for ONE candidate was spread across both with nothing tying the
pieces together, and a restart erased whatever was left.

This is the durable half. One row per application, accumulated in place by
`services/cost_telemetry`, read only by the owner console.

TENANT SCOPED WITH RLS, LIKE ITS NEIGHBOURS
---------------------------------------------
`tenant_id` is a real column and the policy is plain tenant equality in both
directions, the same shape `support_threads` uses. The row is written by two
very different sessions -- a scoring worker running under the tenant, and a
candidate's request running in the bypass scope, since a candidate has no
tenant -- and both are admitted by the same policy for the same reason the
`candidate_updates` policy admits a tenant-scoped write.

NOTHING HERE IS CANDIDATE-FACING OR CLIENT-FACING
---------------------------------------------------
Every number on this table is operational: tokens, calls, dollars. No score, no
grade, no candidate text. The boundary is the AUDIENCE, structurally: the only
reader is `GET /admin/cost/assessments` behind `get_superadmin_db`, and there
is no employer-side or candidate-side route that selects from it.

THE NULLS ARE DELIBERATE AND MUST NOT BE DEFAULTED TO ZERO
------------------------------------------------------------
`media_cost_usd` and `proctoring_cost_usd` are nullable with NO default. The
analysis service is a standing ECS service with a flat bill and the vendor
invoices per account per month, so there is no per-assessment figure to read
today. NULL says that. A zero would say the assessment used no media, which for
a video interview is simply false, and a metric that cannot be computed reports
unavailable rather than 0.0.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0109_assessment_cost_records"
down_revision = "0108_candidate_deletion_requests"
branch_labels = None
depends_on = None

TABLE = "assessment_cost_records"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_candidate_link_id",
            UUID(as_uuid=True),
            sa.ForeignKey("job_candidate_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # BIGINT rather than INTEGER: a long assessment is already several
        # hundred thousand prompt tokens and an INTEGER tops out at two
        # billion. The column is cheap; an overflow here would be a hard
        # failure in the middle of somebody's interview.
        sa.Column(
            "input_tokens", sa.BigInteger(), nullable=False, server_default="0"
        ),
        sa.Column(
            "output_tokens", sa.BigInteger(), nullable=False, server_default="0"
        ),
        # `usage.prompt_tokens_details.cached_tokens`, summed over the calls
        # that REPORTED one. A SUBSET of input_tokens, never an addition to it.
        sa.Column(
            "cached_input_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
        # `calls` minus this is the number of calls whose tokens are an
        # estimate rather than a vendor measurement, which is the one thing a
        # reader needs in order to know how much of a total was measured.
        sa.Column(
            "calls_with_usage", sa.Integer(), nullable=False, server_default="0"
        ),
        # Zero here beside zero cached tokens means the endpoint said nothing
        # about caching, which is a different fact from a cache that missed.
        sa.Column(
            "calls_reporting_cache",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        # Siddhi's own line. Report synthesis is seven sections in one
        # reasoning-tier response and is routinely the largest single call in
        # an assessment, so a total that hides it cannot answer "is the report
        # or the conversation what costs money".
        sa.Column(
            "synthesis_input_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "synthesis_output_tokens",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "synthesis_cost_usd",
            sa.Numeric(14, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "conversation_turns", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "questions_asked", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column(
            "estimated_cost_usd",
            sa.Numeric(14, 6),
            nullable=False,
            server_default="0",
        ),
        # NULL means NOT MEASURABLE, never free. See the module docstring.
        sa.Column("media_cost_usd", sa.Numeric(14, 6)),
        sa.Column("proctoring_cost_usd", sa.Numeric(14, 6)),
        sa.Column(
            "cost_basis",
            sa.String(20),
            nullable=False,
            server_default="estimated",
        ),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        # The plan slug AT THE TIME, snapshotted rather than joined: a customer
        # who upgrades next month must not retroactively change which tier last
        # month's assessments are reported under.
        sa.Column("pricing_tier", sa.String(50)),
        sa.Column(
            "models_json",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        # The exact per-million rates that were applied. The price table is
        # explicitly expected to be corrected the day a real sheet is read for
        # these two model ids, and without this every total written before that
        # edit becomes unexplainable.
        sa.Column(
            "pricing_json",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("notes", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "job_candidate_link_id", name="uq_assessment_cost_records_link"
        ),
        # An unknown value read back into the enum 500s every read of every row
        # that carries it, for that whole tenant. The CHECK makes a bad write
        # fail at write time instead.
        sa.CheckConstraint(
            "cost_basis IN ('estimated', 'finalized')",
            name="ck_assessment_cost_records_basis",
        ),
    )
    op.create_index("ix_assessment_cost_records_created", TABLE, ["created_at"])
    op.create_index(
        "ix_assessment_cost_records_tenant", TABLE, ["tenant_id", "created_at"]
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} TO pickready_app")
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {TABLE}_tenant_isolation ON {TABLE}
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
    op.execute(f"DROP POLICY IF EXISTS {TABLE}_tenant_isolation ON {TABLE}")
    op.drop_index("ix_assessment_cost_records_tenant", table_name=TABLE)
    op.drop_index("ix_assessment_cost_records_created", table_name=TABLE)
    op.drop_table(TABLE)
