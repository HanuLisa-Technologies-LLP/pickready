"""SWOT intake and the job's own Matching category list.

Revision ID: 0049_job_setup_agents
Revises: 0048_must_have_matrix

Two tables, both inputs to job setup, both generated once per job and then
finalised by a human:

  `job_swot_intakes`      the reporting authority's Strengths / Weaknesses /
                          Opportunities / Threats for the ROLE, captured in a
                          short conversation at every job setup and fed to the
                          PPI agent alongside the JD (spec 5.1).

  `job_matching_categories` the job's own Matching category list, proposed by
                          the AI at job creation and edited by the recruiter to
                          a final list of at least five (spec 3.2). Matching is
                          no longer four parameters fixed across the product.

WHY THE SWOT TRANSCRIPT IS A COLUMN AND NOT A TABLE
---------------------------------------------------
`assessment_messages` is a table because a candidate interview runs to 120
messages, is paginated for the recruiter, is scored, and its rows are grouped by
question key. None of that is true here: a SWOT intake is one authenticated
member of the hiring team answering four areas, it is read whole or not at all,
and nothing scores it. A JSONB array on the intake row keeps the whole intake in
one place and one read, and a separate table would buy pagination nobody uses at
the cost of another RLS policy to keep correct.

WHY THE FOUR AREAS ARE SEPARATE COLUMNS AS WELL
-----------------------------------------------
The transcript is what was SAID; the four arrays are what was CAPTURED. The PPI
agent reads the arrays, so a change to how the conversation is conducted cannot
change what the matrix is generated from, and a half-finished intake is visibly
half-finished rather than being inferred by re-reading prose.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0049_job_setup_agents"
down_revision = "0048_must_have_matrix"
branch_labels = None
depends_on = None

TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"
BYPASS = "current_setting('app.bypass_rls', true) = 'on'"


def _tenant_isolate(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} "
        f"USING ((tenant_id = {TENANT}) OR ({BYPASS})) "
        f"WITH CHECK ((tenant_id = {TENANT}) OR ({BYPASS}))"
    )


def upgrade() -> None:
    op.create_table(
        "job_swot_intakes",
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
        # The reporting authority who answered. Nullable and ON DELETE SET NULL:
        # a hiring manager can leave the company, and their departure must not
        # take the criteria every candidate on the job is graded against with it.
        sa.Column(
            "conducted_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        # Which of the four areas the conversation is currently on, 0..3, then 4
        # when every area is captured. Persisted rather than derived from the
        # arrays so an area the authority genuinely had nothing to add to does
        # not send the conversation back round to it.
        sa.Column("area_index", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("follow_ups_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("strengths", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("weaknesses", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("opportunities", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("threats", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("transcript_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="[]"),
        sa.Column("pending_prompt", sa.Text(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        # One intake per job. The SWOT is a property of the ROLE, not of whoever
        # happened to open the setup screen, and two intakes would leave the PPI
        # agent with no defensible way to choose between them.
        sa.UniqueConstraint("job_id", name="uq_job_swot_intake_job"),
        sa.CheckConstraint("status IN ('active', 'complete')", name="ck_job_swot_status"),
    )
    op.create_index("ix_job_swot_intakes_tenant", "job_swot_intakes", ["tenant_id"])
    _tenant_isolate("job_swot_intakes")

    op.create_table(
        "job_matching_categories",
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
        # A stable slug the scorer keys on, so renaming a category's display
        # name in the review screen does not orphan the scores already filed
        # under it.
        sa.Column("key", sa.String(60), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("job_id", "key", name="uq_job_matching_category_key"),
    )
    op.create_index(
        "ix_job_matching_categories_job", "job_matching_categories", ["job_id", "ordinal"]
    )
    op.create_index("ix_job_matching_categories_tenant", "job_matching_categories", ["tenant_id"])
    _tenant_isolate("job_matching_categories")

    # Stamped when the recruiter finalises the category list. The PPI matrix and
    # the Matching list are finalised in ONE setup session (spec 10), so the job
    # needs a stamp for each half to know whether that session is finished.
    op.add_column(
        "jobs",
        sa.Column("matching_categories_finalized_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jobs", "matching_categories_finalized_at")
    op.execute("DROP POLICY IF EXISTS job_matching_categories_tenant_isolation ON job_matching_categories")
    op.drop_table("job_matching_categories")
    op.execute("DROP POLICY IF EXISTS job_swot_intakes_tenant_isolation ON job_swot_intakes")
    op.drop_table("job_swot_intakes")
