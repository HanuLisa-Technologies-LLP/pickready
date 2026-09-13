"""The AI-assisted Job SWOT Analysis (2026-09-13 spec, sections 23 to 33).

Revision ID: 0087_job_swot_analysis
Revises: 0086_reclassify_historical_jobs

ONE TABLE, AND IT IS NOT `job_swot_intakes`
--------------------------------------------
`job_swot_intakes` (migration 0049) is the reporting authority's TRANSCRIPT:
what the hiring manager said, captured as evidence, read by Sutra when it
compiles the Tatva matrix. Its points are the authority's own words and
rewriting them would launder the evidence the matrix is derived from.

`job_swot_analyses` is a DOCUMENT: four narrative paragraphs the model drafts
about the role's hiring position, which the recruitment team then edits and
owns. Editing it is the entire feature. One table could not be both, so this
is a second table rather than four more columns on the first, and the two are
related by the generator reading the intake as an input.

NO NEW CAPABILITY (spec section 28)
-------------------------------------
`edit_swot` already exists (migration 0075), is already a
Hiring-Manager-controlled field under RBAC 24, and already reaches this
product's SWOT surfaces through `rbac.require_authorized`. Generation and
editing are the same authority over the same artifact, so both routes take it
and no row is seeded here. Viewing rides on `view_company_jobs`, which is what
every other read of a job already asks for.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0087_job_swot_analysis"
down_revision = "0086_reclassify_historical_jobs"
branch_labels = None
depends_on = None

TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"
BYPASS = "current_setting('app.bypass_rls', true) = 'on'"


def upgrade() -> None:
    op.create_table(
        "job_swot_analyses",
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
            "status",
            sa.String(20),
            nullable=False,
            server_default="not_generated",
        ),
        sa.Column("strengths", sa.Text()),
        sa.Column("weaknesses", sa.Text()),
        sa.Column("opportunities", sa.Text()),
        sa.Column("threats", sa.Text()),
        sa.Column("generated_by", sa.String(20)),
        sa.Column("last_generated_at", sa.DateTime(timezone=True)),
        sa.Column("generation_error", sa.Text()),
        sa.Column(
            "human_edited", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column(
            "last_modified_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("last_modified_at", sa.DateTime(timezone=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "previous_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # One analysis per job. The uniqueness is what makes "get or create"
        # safe against two recruiters opening the tab at the same moment.
        sa.UniqueConstraint("job_id", name="uq_job_swot_analysis_job"),
        sa.CheckConstraint(
            "status IN ('not_generated', 'generated', 'failed', 'edited')",
            name="ck_job_swot_analyses_status_vocabulary",
        ),
    )
    op.create_index(
        "ix_job_swot_analyses_tenant", "job_swot_analyses", ["tenant_id"]
    )

    op.execute("ALTER TABLE job_swot_analyses ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE job_swot_analyses FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY job_swot_analyses_tenant_isolation ON job_swot_analyses "
        f"USING ((tenant_id = {TENANT}) OR ({BYPASS})) "
        f"WITH CHECK ((tenant_id = {TENANT}) OR ({BYPASS}))"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS job_swot_analyses_tenant_isolation "
        "ON job_swot_analyses"
    )
    op.drop_index("ix_job_swot_analyses_tenant", table_name="job_swot_analyses")
    op.drop_table("job_swot_analyses")
