"""Add tenant-scoped indexes for high-frequency list endpoints.

Revision ID: 0047_latency_indexes
Revises: 0046_private_gcs_resumes
"""
from alembic import op

revision = "0047_latency_indexes"
down_revision = "0046_private_gcs_resumes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_jcl_tenant_job_created",
        "job_candidate_links",
        ["tenant_id", "job_id", "created_at"],
    )
    op.create_index(
        "ix_profiles_source_tenant_created",
        "profiles",
        ["source_tenant_id", "created_at"],
    )
    op.create_index(
        "ix_candidates_tenant_created",
        "candidates",
        ["tenant_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_candidates_tenant_created", table_name="candidates")
    op.drop_index("ix_profiles_source_tenant_created", table_name="profiles")
    op.drop_index("ix_jcl_tenant_job_created", table_name="job_candidate_links")
