"""Persist structured, tenant-scoped evidence behind report dimensions.

Revision ID: 0044_report_skill_evidence
Revises: 0043_tenant_user_rls
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0044_report_skill_evidence"
down_revision = "0043_tenant_user_rls"
branch_labels = None
depends_on = None

TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"
BYPASS = "current_setting('app.bypass_rls', true) = 'on'"


def upgrade() -> None:
    op.create_table(
        "report_skill_evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category", sa.String(length=20), nullable=False),
        sa.Column("skill", sa.String(length=255), nullable=False),
        sa.Column("question_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("technical_precision", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("depth", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("problem_solving_structure", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("role_relevance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("concrete_examples", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("explicit_gaps", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["conversation_id"], ["assessment_conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "category",
            "skill",
            name="uq_report_skill_evidence",
        ),
    )
    op.create_index(
        "ix_report_skill_evidence_conversation",
        "report_skill_evidence",
        ["conversation_id", "category"],
        unique=False,
    )
    op.create_index(
        "ix_report_skill_evidence_tenant",
        "report_skill_evidence",
        ["tenant_id"],
        unique=False,
    )
    op.execute("ALTER TABLE report_skill_evidence ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE report_skill_evidence FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY report_skill_evidence_tenant_isolation "
        "ON report_skill_evidence "
        f"USING ((tenant_id = {TENANT}) OR ({BYPASS})) "
        f"WITH CHECK ((tenant_id = {TENANT}) OR ({BYPASS}))"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS report_skill_evidence_tenant_isolation "
        "ON report_skill_evidence"
    )
    op.drop_index(
        "ix_report_skill_evidence_tenant",
        table_name="report_skill_evidence",
    )
    op.drop_index(
        "ix_report_skill_evidence_conversation",
        table_name="report_skill_evidence",
    )
    op.drop_table("report_skill_evidence")
