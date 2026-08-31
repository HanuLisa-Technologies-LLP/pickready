"""STEM / Non-STEM classification on jobs, and the jd_drafts audit table.

Revision ID: 0070_stem_classification
Revises: 0069_calibration_divergence

Master Implementation Directive Part 3 §5.1 (Job record fields) and Rule 3
(classification locks to the RAW AI-generated JD at generation time, which is
what `jd_drafts` persists).

DEPLOYMENT RULE, VERBATIM (Part 3 §11): "All existing jobs created before
this feature is deployed → assign Non-STEM classification by default,
confidence 0.00, classification_overridden false. No retroactive credit
adjustments." The server defaults below ARE that rule — every existing row
comes out of this migration as NON_STEM / 0.00 / not overridden / 1.0 credit,
and nothing touches the ledger.

`credit_cost_per_report` is stored explicitly rather than derived so the
deduction path reads one column the job carried at completion time, and so a
support reclassification before first assessment updates both fields in one
audited write.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0070_stem_classification"
down_revision = "0069_calibration_divergence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "role_classification",
            sa.String(10),
            nullable=False,
            server_default="NON_STEM",
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "classification_confidence",
            sa.Float(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "classification_signals",
            JSONB(),
            nullable=False,
            server_default="[]",
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "classification_locked",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "credit_cost_per_report",
            sa.Numeric(3, 1),
            nullable=False,
            server_default="1.0",
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "classification_tentative",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "classification_overridden",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "classification_override_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("jobs", sa.Column("raw_jd_text", sa.Text(), nullable=True))

    # Only STEM and NON_STEM ever reach the column; the engine's error path
    # already resolves to NON_STEM before the write (Part 3 §8 row one).
    op.create_check_constraint(
        "ck_jobs_role_classification",
        "jobs",
        "role_classification IN ('STEM', 'NON_STEM')",
    )
    op.create_check_constraint(
        "ck_jobs_credit_cost_per_report",
        "jobs",
        "credit_cost_per_report IN (1.0, 1.5)",
    )

    op.create_table(
        "jd_drafts",
        # PK is client-generated uuid4, same as every UUIDPKMixin table.
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("raw_jd_text", sa.Text(), nullable=False),
        sa.Column("role_classification", sa.String(10), nullable=False),
        sa.Column("classification_confidence", sa.Float(), nullable=False),
        sa.Column("stem_score", sa.Float(), nullable=False),
        sa.Column(
            "classification_signals", JSONB(), nullable=False, server_default="[]"
        ),
        sa.Column(
            "tentative", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "engine_error", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_jd_drafts_tenant", "jd_drafts", ["tenant_id"])

    # Tenant-scoped table → the standard RLS pair, same shape as 0001.
    op.execute("ALTER TABLE jd_drafts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE jd_drafts FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY jd_drafts_tenant_isolation ON jd_drafts
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
    op.drop_table("jd_drafts")
    op.drop_constraint("ck_jobs_credit_cost_per_report", "jobs")
    op.drop_constraint("ck_jobs_role_classification", "jobs")
    for column in (
        "raw_jd_text",
        "classification_override_by",
        "classification_overridden",
        "classification_tentative",
        "credit_cost_per_report",
        "classification_locked",
        "classification_signals",
        "classification_confidence",
        "role_classification",
    ):
        op.drop_column("jobs", column)
