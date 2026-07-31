"""Add reversible archive state for jobs and candidate applications.

Revision ID: 0011_safe_archive_lifecycle
Revises: 0010_mock_candidate_matrix
"""
from alembic import op
import sqlalchemy as sa


revision = "0011_safe_archive_lifecycle"
down_revision = "0010_mock_candidate_matrix"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "job_candidate_links",
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_jobs_archived_at", "jobs", ["archived_at"])
    op.create_index(
        "ix_job_candidate_links_archived_at",
        "job_candidate_links",
        ["archived_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_job_candidate_links_archived_at", table_name="job_candidate_links"
    )
    op.drop_index("ix_jobs_archived_at", table_name="jobs")
    op.drop_column("job_candidate_links", "archived_at")
    op.drop_column("jobs", "archived_at")
