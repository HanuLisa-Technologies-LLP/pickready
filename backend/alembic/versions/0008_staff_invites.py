"""Staff invitations: single-use, expiring, hashed tokens.

Revision ID: 0008_staff_invites
Revises: 0007_resume_cloudinary_metadata
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0008_staff_invites"
down_revision = "0007_resume_cloudinary_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "staff_invites",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        # SHA-256 hex of the raw token — the raw value is never stored.
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "invited_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_staff_invites_user", "staff_invites", ["tenant_id", "user_id"])
    op.create_index(
        "ix_staff_invites_token_hash", "staff_invites", ["token_hash"], unique=True
    )

    # RLS (ESD §3 / claude.md rule 1) — identical policy shape to the other
    # tenant tables. The PUBLIC /join lookup runs under the bypass scope
    # (get_public_db) because the tenant is unknown until the token resolves,
    # exactly like the employer-verification token path.
    op.execute("ALTER TABLE staff_invites ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE staff_invites FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY staff_invites_tenant_isolation ON staff_invites
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
    op.execute("DROP POLICY IF EXISTS staff_invites_tenant_isolation ON staff_invites")
    op.drop_index("ix_staff_invites_token_hash", table_name="staff_invites")
    op.drop_index("ix_staff_invites_user", table_name="staff_invites")
    op.drop_table("staff_invites")
