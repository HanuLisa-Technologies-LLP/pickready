"""Technical questions move from a per-job preset bank to per-candidate.

WHAT CHANGED
------------
`technical_questions` was a bank a company authored, edited and stored on the
job. Every applicant to that job read the same strings, whatever their resume
said, and the Company Portal carried CRUD screens for maintaining them.

That feature is withdrawn (client decision, 2026-08-06). Technical questions are
now written during the conversation from the job description, THIS candidate's
resume and everything said so far, one at a time, at the moment each is asked.

WHY A NEW TABLE RATHER THAN REUSING THE OLD ONE
-----------------------------------------------
`technical_questions` is UNIQUE on (job_id, ordinal) -- the shape of a per-job
bank, and precisely the wrong shape now. Widening it in place would have to drop
that constraint, which is the one thing keeping the historic rows meaningful.

THE INVARIANT THIS TABLE EXISTS TO KEEP
---------------------------------------
A technical answer is scored against ITS OWN rubric
(`functional_assessment._llm_score`). A generated question is therefore only
sound if its rubric is generated WITH it and stored beside it -- otherwise an
answer would be graded against a rubric written for a question nobody was asked,
and the candidate would receive a mark nobody could explain.

So `prompt` and `rubric_json` are written together, in one transaction, before
the candidate reads the question. This is a STRONGER guarantee than the preset
bank offered: there, a recruiter could edit a stored prompt through the UI and
leave its rubric behind, and nothing refused that.

WHAT IS DELIBERATELY NOT DROPPED
--------------------------------
`technical_questions` itself. Reports written before today were scored against
those rows, and dropping the table would turn "what was this candidate actually
asked?" into a question with no answer. Nothing in the application reads it any
more; it is left in place, with its policy and grants intact, as a record.

Rows here are created with a deterministic placeholder prompt and a default
rubric so the conversation always has something askable during a provider
outage, then overwritten in place with the generated pair. Both columns are
therefore NOT NULL; `generated_at` is what distinguishes a written question from
a fallback, and NULL is the honest record of a degraded turn.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
# Under 32 characters: `alembic_version.version_num` is VARCHAR(32).
revision = "0040_candidate_technical"
down_revision = "0039_delivered_prompt"
branch_labels = None
depends_on = None

TABLE = "candidate_technical_questions"

#: The guarded cast introduced by 0034. `nullif` collapses an empty GUC back to
#: NULL, so an unscoped connection reads no rows instead of failing to plan.
GUARDED = "nullif(current_setting('app.tenant_id', true), '')::uuid"
BYPASS = "current_setting('app.bypass_rls', true) = 'on'"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_candidate_link_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("job_candidate_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("skill", sa.String(length=255), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("rubric_json", postgresql.JSONB(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "job_candidate_link_id", "ordinal", name="uq_candidate_technical_ordinal"
        ),
    )
    op.create_index(
        "ix_candidate_technical_link", TABLE, ["job_candidate_link_id", "ordinal"]
    )

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} TO pickready_app")
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {TABLE}_tenant_isolation ON {TABLE}
        USING (tenant_id = {GUARDED} OR {BYPASS})
        WITH CHECK (tenant_id = {GUARDED} OR {BYPASS})
        """
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {TABLE}_tenant_isolation ON {TABLE}")
    op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {TABLE} FROM pickready_app")
    op.drop_index("ix_candidate_technical_link", table_name=TABLE)
    op.drop_table(TABLE)
