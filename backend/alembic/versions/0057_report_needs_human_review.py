"""A report that failed its own quality gate says so, in the row.

Revision ID: 0057_report_review
Revises: 0056_evidence_ledger

WHY A COLUMN AND NOT A LOG LINE
--------------------------------
Siddhi's gate (`services/agents/gates.siddhi_gate`) runs on the assembled PRISM
report before it is written: required sections present, every claim carrying an
evidence ref, grades identical to Miti's, gap probes grounded in a real answer,
Validation reproduced field for field, and no number anywhere a client can read.
When it fails after the loop's bounded attempts, the report still has to be
produced -- refusing to write one would take the product's whole output away
over a defect that may be a single ungrounded phrase.

So the report ships, and the honest half is that it ships MARKED. Logging it
instead was the option considered and rejected: a recruiter opens the report
from the candidate table and never sees the worker's log, so a log-only record
means the one person acting on the document is the one person who cannot know it
was flagged. This is the same posture `reliability/degradation` already takes,
where what makes a stub honest rather than misleading is `needs_human_review`
and never the stub reading like a result.

WHY NOT OVERLOAD `scoring_mode`
--------------------------------
`scoring_mode` answers "how was this scored" and already carries
`deterministic_fallback` for an LLM outage. A gate failure is a different
question about a different stage, and a column answering two questions is a
column that answers neither once somebody filters on it.

DEFAULT FALSE, AND BACKFILLED FALSE. Every report written before today was
written without a gate, so it was neither passed nor failed by one. False is the
truthful value: it means "not flagged", not "verified clean", and the column
name says flagged rather than verified for exactly that reason.
"""
from alembic import op
import sqlalchemy as sa

revision = "0057_report_review"
down_revision = "0056_evidence_ledger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "functional_skills_reports",
        sa.Column(
            "needs_human_review",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "functional_skills_reports",
        # The gate's findings, as structured records. NEVER the report text they
        # were found in: a finding's `detail` can quote the prose, and the same
        # rule that made `agent_execution_traces` drop a defect's detail applies
        # to a row a support engineer can read. Issue, location and severity are
        # enough to act on and quote nothing.
        sa.Column("review_findings_json", sa.dialects.postgresql.JSONB(), nullable=True),
    )
    # Partial: the flagged reports are the ones anybody queries for, and they are
    # the small minority. A full index would be almost entirely false rows.
    op.create_index(
        "ix_reports_needing_review",
        "functional_skills_reports",
        ["tenant_id", "synthesized_at"],
        postgresql_where=sa.text("needs_human_review"),
    )


def downgrade() -> None:
    op.drop_index("ix_reports_needing_review", table_name="functional_skills_reports")
    op.drop_column("functional_skills_reports", "review_findings_json")
    op.drop_column("functional_skills_reports", "needs_human_review")
