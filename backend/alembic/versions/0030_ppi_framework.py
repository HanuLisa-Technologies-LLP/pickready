"""PPI framework, per-candidate questions, application validation, review gate.

Revision ID: 0030_ppi_framework
Revises: 0029_candidate_team_reviews

Five changes, one release (spec 2026-07-30):

1. `job_competencies` -- the job's PPI evaluation framework. Primary Skills,
   Secondary Skills and Behavioural Competencies, generated once per job from
   its JD and fixed once the Hiring Manager saves it.
2. `candidate_questions` -- the PPI questions generated for ONE candidate
   against that framework. Per candidate, unlike `technical_questions`.
3. `job_candidate_links.validation_json` -- the six mandatory application
   fields (spec §7). Never scored; shown to the recruiter as submitted.
4. `report_dimensions.required_level` -- the job's required level for the same
   item, so a radar chart can plot BOTH shapes and stays readable after the
   job's framework is later edited.
5. The review gate is reinstated: `jobs.assessment_status` now defaults to
   `questions_pending_review`, and `framework_generated_at` /
   `framework_approved_at` track the PPI half of that gate.

BACKFILL POLICY. Existing jobs are NOT dragged back into review. A job that was
already `ready_for_candidates` keeps that status and gets `framework_approved_at`
stamped from `questions_approved_at`, because candidates may already be mid
conversation on it and re-gating would strand them. Only jobs created after this
migration enter the gate. Their frameworks are generated lazily, on first
assessment run, exactly as an empty technical bank already is.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0030_ppi_framework"
down_revision = "0029_candidate_team_reviews"
branch_labels = None
depends_on = None

NEW_TABLES = ("job_competencies", "candidate_questions")

CATEGORIES = ("primary_skill", "secondary_skill", "behavioural")


def upgrade() -> None:
    # ── 1. The job's PPI framework ───────────────────────────────────────────
    op.create_table(
        "job_competencies",
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
        sa.Column("category", sa.String(20), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        # The job's required level, as the representative internal score of one
        # of the four grade bands. Never displayed as a number: the API projects
        # it through services.rating.grade_for_percent.
        sa.Column("required_level", sa.Integer(), nullable=False, server_default="82"),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("job_id", "category", "name", name="uq_job_competency_name"),
        sa.CheckConstraint(
            "category IN ('primary_skill', 'secondary_skill', 'behavioural')",
            name="ck_job_competencies_category",
        ),
        # Culture is refused as a Behavioural Competency (spec §6.2), and the
        # database is the last line of that refusal: the generator is told not
        # to produce it and the save handler rejects it, but a direct write
        # would otherwise slip past both.
        sa.CheckConstraint(
            "category <> 'behavioural' OR name !~* '(^|[^a-z])cultur(e|al)([^a-z]|$)'",
            name="ck_job_competencies_no_culture",
        ),
    )
    op.create_index(
        "ix_job_competencies_job", "job_competencies", ["job_id", "category", "ordinal"]
    )

    # ── 2. Per-candidate PPI questions ───────────────────────────────────────
    op.create_table(
        "candidate_questions",
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
        sa.Column(
            "competency_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("job_competencies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "job_candidate_link_id", "ordinal", name="uq_candidate_question_ordinal"
        ),
    )
    op.create_index(
        "ix_candidate_questions_link",
        "candidate_questions",
        ["job_candidate_link_id", "ordinal"],
    )

    for table in NEW_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO pickready_app")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (
                tenant_id = current_setting('app.tenant_id', true)::uuid
                OR current_setting('app.bypass_rls', true) = 'on'
            )
            WITH CHECK (
                tenant_id = current_setting('app.tenant_id', true)::uuid
                OR current_setting('app.bypass_rls', true) = 'on'
            )
            """
        )

    # ── 3. Mandatory application fields ──────────────────────────────────────
    op.add_column(
        "job_candidate_links",
        sa.Column("validation_json", postgresql.JSONB(), nullable=True),
    )

    # ── 4. The job-requirement shape on the radar ────────────────────────────
    op.add_column(
        "report_dimensions", sa.Column("required_level", sa.Integer(), nullable=True)
    )
    # The PPI Assessment's own overall score, and the run's scoring health.
    # `scoring_mode` previously lived inside `validation_json`, where a field
    # about the RUN sat among fields the candidate submitted.
    op.add_column(
        "functional_skills_reports", sa.Column("overall_score", sa.Integer(), nullable=True)
    )
    op.add_column(
        "functional_skills_reports", sa.Column("scoring_mode", sa.String(30), nullable=True)
    )
    op.execute(
        """
        UPDATE functional_skills_reports
           SET scoring_mode = validation_json ->> 'scoring_mode'
         WHERE validation_json ? 'scoring_mode'
        """
    )

    # ── 5. The review gate ───────────────────────────────────────────────────
    op.add_column(
        "jobs", sa.Column("framework_generated_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "jobs", sa.Column("framework_approved_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.alter_column(
        "jobs",
        "assessment_status",
        server_default="questions_pending_review",
        existing_type=sa.String(40),
        existing_nullable=False,
    )
    # Existing live jobs are grandfathered. They were approved under the
    # no-gate regime and candidates may be mid conversation on them; pulling
    # them back into review would block those candidates on a recruiter action
    # nobody asked for.
    op.execute(
        """
        UPDATE jobs
           SET framework_approved_at = COALESCE(questions_approved_at, created_at),
               framework_generated_at = COALESCE(questions_generated_at, created_at)
         WHERE assessment_status = 'ready_for_candidates'
        """
    )


def downgrade() -> None:
    op.alter_column(
        "jobs",
        "assessment_status",
        server_default="ready_for_candidates",
        existing_type=sa.String(40),
        existing_nullable=False,
    )
    op.drop_column("jobs", "framework_approved_at")
    op.drop_column("jobs", "framework_generated_at")
    op.drop_column("functional_skills_reports", "scoring_mode")
    op.drop_column("functional_skills_reports", "overall_score")
    op.drop_column("report_dimensions", "required_level")
    op.drop_column("job_candidate_links", "validation_json")
    for table in reversed(NEW_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {table} FROM pickready_app")
    op.drop_index("ix_candidate_questions_link", table_name="candidate_questions")
    op.drop_table("candidate_questions")
    op.drop_index("ix_job_competencies_job", table_name="job_competencies")
    op.drop_table("job_competencies")
