"""Drishti: one strategic profile per function per tenant.

Revision ID: 0105_drishti_profiles
Revises: 0104_question_prefill

Vivekium feature 1 under C3 (owner-ruled). The five captured sections stay
as the functional head wrote them; `compiled_json` is the ONLY thing any
prompt or weight ever reads, produced deterministically by
`hiring/drishti.compile_profile`. UNIQUE per (tenant, function): the brief
is explicit that Drishti is once per function, updated in place by the
functional head, never accumulated per job.

Tenant-scoped RLS, the plain equality policy every tenant table carries.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0105_drishti_profiles"
down_revision = "0104_question_prefill"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drishti_profiles",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("function_name", sa.String(120), nullable=False),
        sa.Column("strategic_purpose", sa.Text(), nullable=True),
        sa.Column("people_philosophy", sa.Text(), nullable=True),
        sa.Column("non_negotiables", sa.Text(), nullable=True),
        sa.Column("culture_expectations", sa.Text(), nullable=True),
        sa.Column("strategic_gap", sa.Text(), nullable=True),
        sa.Column("compiled_json", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column(
            "updated_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_drishti_tenant_function",
        "drishti_profiles",
        ["tenant_id", sa.text("lower(function_name)")],
        unique=True,
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON drishti_profiles TO pickready_app"
    )
    op.execute("ALTER TABLE drishti_profiles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE drishti_profiles FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY drishti_profiles_tenant_isolation ON drishti_profiles
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
    op.drop_table("drishti_profiles")
