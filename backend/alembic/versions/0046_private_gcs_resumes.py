"""Track private GCS resume objects and their legacy migration mapping.

Revision ID: 0046_private_gcs_resumes
Revises: 0045_assessment_relevance_state
"""

from alembic import op
import sqlalchemy as sa

revision = "0046_private_gcs_resumes"
down_revision = "0045_assessment_relevance_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "profiles",
        sa.Column(
            "resume_storage_provider",
            sa.String(length=20),
            server_default="cloudinary",
            nullable=False,
        ),
    )
    op.add_column(
        "profiles",
        sa.Column("resume_legacy_public_id", sa.String(length=512), nullable=True),
    )
    op.create_check_constraint(
        "ck_profiles_resume_storage_provider",
        "profiles",
        "resume_storage_provider IN ('cloudinary', 'gcs')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_profiles_resume_storage_provider", "profiles", type_="check"
    )
    op.drop_column("profiles", "resume_legacy_public_id")
    op.drop_column("profiles", "resume_storage_provider")
