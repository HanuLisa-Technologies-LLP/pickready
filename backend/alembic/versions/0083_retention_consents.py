"""Candidate data-retention consents (Consent & Privacy spec, 2026-09-05).

Two consents captured per candidate, each a nullable boolean plus the moment
it was given, extending the consent pattern the platform already carries:
`candidates.consent_databank` (future-opportunity matching, FR-4.2) and the
proctoring session's `consented_at` stamp.

  retain_assessment_consent / retain_assessment_consented_at
      True  = retain the completed assessment for future jobs
      False = this job only
      NULL  = never asked, read as False (the safe direction) by
              services/retention_consent.py, the ONE reader.

  retain_video_consent / retain_video_consented_at
      Same semantics, applied to any consented video record.

Client-portal permission logic reads the flag directly: Download enabled if
Yes, View-only if No. Nothing here touches viewing.

RLS: `candidates` already carries its row-level policy; new columns on an
existing table inherit it, so this migration declares no policy of its own.

CHAIN NOTE: down_revision names "0082" per the 2026-09-05 wave's migration
number assignments (0080 senders, 0081 assessment mode, 0082 telemetry, this
one 0083). The three lower migrations are being authored in parallel; if the
0082 author registered a longer revision id, the coordinator reconciles the
string here in an 0085+ integration fix.

Revision ID: 0083
Revises: 0082
"""
from alembic import op
import sqlalchemy as sa

revision = "0083"
down_revision = "0082"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidates",
        sa.Column("retain_assessment_consent", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "candidates",
        sa.Column(
            "retain_assessment_consented_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "candidates",
        sa.Column("retain_video_consent", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "candidates",
        sa.Column(
            "retain_video_consented_at", sa.DateTime(timezone=True), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("candidates", "retain_video_consented_at")
    op.drop_column("candidates", "retain_video_consent")
    op.drop_column("candidates", "retain_assessment_consented_at")
    op.drop_column("candidates", "retain_assessment_consent")
