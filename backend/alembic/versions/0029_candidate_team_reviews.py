"""Candidate panel ratings, remarks and AI clarity rewrites.

Revision ID: 0029_candidate_team_reviews
Revises: 0028_performance_indexes
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0029_candidate_team_reviews"
down_revision = "0028_performance_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_team_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_candidate_link_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("job_candidate_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "reviewer_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("rating", sa.String(20), nullable=False),
        sa.Column("remarks", sa.Text(), nullable=False),
        sa.Column("ai_rewritten_remarks", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "rating IN ('very_high','high','medium','low','developing')",
            name="ck_candidate_team_reviews_rating",
        ),
        sa.UniqueConstraint(
            "job_candidate_link_id",
            "reviewer_user_id",
            name="uq_candidate_team_review_reviewer",
        ),
    )
    op.create_index(
        "ix_candidate_team_reviews_link",
        "candidate_team_reviews",
        ["job_candidate_link_id"],
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON candidate_team_reviews TO pickready_app"
    )
    op.execute("ALTER TABLE candidate_team_reviews ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE candidate_team_reviews FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY candidate_team_reviews_tenant_isolation
        ON candidate_team_reviews
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
    op.execute(
        "DROP POLICY IF EXISTS candidate_team_reviews_tenant_isolation "
        "ON candidate_team_reviews"
    )
    op.execute(
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON candidate_team_reviews FROM pickready_app"
    )
    op.drop_index("ix_candidate_team_reviews_link", table_name="candidate_team_reviews")
    op.drop_table("candidate_team_reviews")
