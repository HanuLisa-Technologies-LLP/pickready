"""Model and prompt provenance on the delivered report (RPN-AI-UP-001 W'3.9).

Revision ID: 0094_report_provenance
Revises: 0093_support_threads

WHY
-----
A delivered report is immutable and client-facing, and until now it did not
record which model id or which prompt versions produced it. That makes two
questions unanswerable after the fact: "can we replay this report against the
exact configuration that wrote it", and "which delivered reports did prompt
version N actually reach". The second is the one that matters during an
incident, because the alternative is answering it from deploy timestamps,
and a timestamp is not evidence that work happened.

NULLABLE, AND NEVER BACKFILLED
--------------------------------
Every existing row predates the columns. Backfilling a plausible model id into
them would manufacture provenance for reports whose real configuration is
unknowable now, which is worse than an honest NULL. NULL here means "written
before provenance was recorded", and the write path keeps a second meaning
deliberately: a deterministic-fallback report also carries NULL, because no
model and no prompt produced it, and naming one would claim they had.
"""
from alembic import op
import sqlalchemy as sa

revision = "0094_report_provenance"
down_revision = "0093_support_threads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "functional_skills_reports",
        sa.Column("model_id", sa.Text(), nullable=True),
    )
    op.add_column(
        "functional_skills_reports",
        sa.Column("prompt_version", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("functional_skills_reports", "prompt_version")
    op.drop_column("functional_skills_reports", "model_id")
