"""Track bounded re-asks and evidence gaps in assessment conversations.

Revision ID: 0045_assessment_relevance_state
Revises: 0044_report_skill_evidence
"""

from alembic import op
import sqlalchemy as sa

revision = "0045_assessment_relevance_state"
down_revision = "0044_report_skill_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assessment_conversations",
        sa.Column("pending_kind", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "assessment_conversations",
        sa.Column("reasks_used", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "assessment_messages",
        sa.Column("answer_label", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "assessment_messages",
        sa.Column(
            "evidence_gap",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("assessment_messages", "evidence_gap")
    op.drop_column("assessment_messages", "answer_label")
    op.drop_column("assessment_conversations", "reasks_used")
    op.drop_column("assessment_conversations", "pending_kind")
