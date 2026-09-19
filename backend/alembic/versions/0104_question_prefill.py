"""Resume pre-fill provenance on the per-candidate question row.

Revision ID: 0104_question_prefill
Revises: 0103_bgv_maintenance_trigger

Vivekium feature 2, under C2 (owner-ruled). `prefilled_answer` holds the
resume-derived answer the conversation records instead of asking;
`prefill_source` says where it came from. Both nullable: NULL means the
question is asked aloud, which is every question written before today and
every question the resume does not settle.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0104_question_prefill"
down_revision = "0103_bgv_maintenance_trigger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidate_questions",
        sa.Column("prefilled_answer", sa.Text(), nullable=True),
    )
    op.add_column(
        "candidate_questions",
        sa.Column("prefill_source", sa.String(30), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("candidate_questions", "prefill_source")
    op.drop_column("candidate_questions", "prefilled_answer")
