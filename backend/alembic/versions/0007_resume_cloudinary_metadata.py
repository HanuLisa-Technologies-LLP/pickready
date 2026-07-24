"""Persist complete Cloudinary resume metadata.

Revision ID: 0007_resume_cloudinary_metadata
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007_resume_cloudinary_metadata"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("profiles", sa.Column("resume_public_id", sa.String(length=512), nullable=True))
    op.add_column("profiles", sa.Column("resume_original_filename", sa.String(length=255), nullable=True))
    op.add_column("profiles", sa.Column("resume_mime_type", sa.String(length=255), nullable=True))
    op.add_column("profiles", sa.Column("resume_size_bytes", sa.Integer(), nullable=True))
    op.add_column("profiles", sa.Column("resume_uploaded_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("profiles", sa.Column("resume_sha256", sa.String(length=64), nullable=True))
    op.add_column("profiles", sa.Column("resume_metadata_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.create_index("ix_profiles_resume_public_id", "profiles", ["resume_public_id"])
    op.create_index("ix_profiles_resume_sha256", "profiles", ["resume_sha256"])


def downgrade() -> None:
    op.drop_index("ix_profiles_resume_sha256", table_name="profiles")
    op.drop_index("ix_profiles_resume_public_id", table_name="profiles")
    for column in ("resume_metadata_json", "resume_sha256", "resume_uploaded_at", "resume_size_bytes", "resume_mime_type", "resume_original_filename", "resume_public_id"):
        op.drop_column("profiles", column)
