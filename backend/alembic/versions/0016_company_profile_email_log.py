"""Company profile sections, per-job JD overrides, per-user permission grants,
and the email delivery log (2026-07-27 build spec).

Four additive changes, no deletions and no renames:

1. `companies.about_company / work_life / benefits_text` — the company-wide
   defaults edited on Company Portal -> Profile. `benefits_text` is a NEW
   column rather than a reuse of the existing `companies.benefits`: that column
   is already populated from the old company-page form and is surfaced
   elsewhere, so overloading it would silently change existing copy. The new
   column is seeded FROM `benefits` so nothing looks empty after the upgrade.

2. `jobs.about_company / work_life / benefits` — the PER-JOB snapshot. A job
   seeds these from the company profile at creation; editing them on the job is
   a per-job override that never writes back to the company. NULL means
   "never overridden" so a job created before this migration keeps rendering
   the live company values.

3. `users.permissions_json` — the HR Head's per-user permission grants. NULL
   means "inherit the role default" (services/capabilities.DEFAULT_PERMISSION_
   MATRIX via the role_permissions engine); a JSON object is a sparse overlay
   of {capability: bool} that wins over the role default for that ONE user.
   Sparse, so a capability the HR Head never touched keeps tracking its role.

4. `email_log` — one row per outbound message across all six spec email types,
   with the AI-generated subject/body actually sent, delivery status, and the
   failure reason when there is one. Tenant-scoped with RLS, like every other
   tenant table.

Revision ID: 0016_company_profile_email_log
Revises: 0015_candidate_profile_form
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0016_company_profile_email_log"
down_revision = "0015_candidate_profile_form"
branch_labels = None
depends_on = None

#: The six email types (spec §6.1). Stored as a plain VARCHAR with a CHECK
#: constraint rather than a native PG enum: adding a seventh type later is then
#: an ALTER CONSTRAINT instead of a locking type migration.
EMAIL_TYPES = (
    "application_confirmation",
    "assessment_reminder",
    "shortlist",
    "rejected",
    "hold",
    "question_bank_reminder",
)


def upgrade() -> None:
    # ── 1. Company-wide profile sections ────────────────────────────────────
    op.add_column("companies", sa.Column("about_company", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("work_life", sa.Text(), nullable=True))
    op.add_column("companies", sa.Column("benefits_text", sa.Text(), nullable=True))
    # Seed the new profile fields from whatever the company page already holds,
    # so Profile is populated on first open instead of looking wiped.
    op.execute(
        """
        UPDATE companies
        SET about_company  = COALESCE(about_company, brief),
            work_life      = COALESCE(work_life, culture),
            benefits_text  = COALESCE(benefits_text, benefits)
        """
    )

    # ── 2. Per-job JD sections (NULL = inherit from the company profile) ────
    op.add_column("jobs", sa.Column("about_company", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("work_life", sa.Text(), nullable=True))
    op.add_column("jobs", sa.Column("benefits", sa.Text(), nullable=True))

    # ── 3. Per-user permission overlay (NULL = inherit the role default) ────
    op.add_column(
        "users",
        sa.Column("permissions_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    # ── 4. Email delivery log ───────────────────────────────────────────────
    op.create_table(
        "email_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email_type", sa.String(length=40), nullable=False),
        sa.Column("recipient_email", sa.String(length=320), nullable=False),
        # Nullable so an internal reminder (type 6, sent to a recruiter) and a
        # candidate email share one table without fake foreign keys.
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("job_candidate_link_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("subject", sa.String(length=500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        # queued -> sent | failed. The worker owns the transition.
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="queued"
        ),
        sa.Column("error", sa.Text(), nullable=True),
        # True when the recruiter edited the AI draft before sending — this is
        # the audit answer to "did a human read this before it went out?".
        sa.Column(
            "edited_by_human",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("generated_by_ai", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sent_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["candidate_id"], ["candidates.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["job_candidate_link_id"], ["job_candidate_links.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["sent_by"], ["users.id"], ondelete="SET NULL"),
        sa.CheckConstraint(
            "email_type IN (" + ", ".join(f"'{t}'" for t in EMAIL_TYPES) + ")",
            name="ck_email_log_type",
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'sent', 'failed')", name="ck_email_log_status"
        ),
    )
    op.create_index("ix_email_log_tenant_created", "email_log", ["tenant_id", "created_at"])
    op.create_index("ix_email_log_job", "email_log", ["job_id"])
    op.create_index("ix_email_log_candidate", "email_log", ["candidate_id"])

    # RLS, identical in shape to every other tenant table (claude.md rule 1).
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON email_log TO pickready_app")
    op.execute("ALTER TABLE email_log ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE email_log FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY email_log_tenant_isolation ON email_log
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


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS email_log_tenant_isolation ON email_log")
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON email_log FROM pickready_app")
    op.drop_index("ix_email_log_candidate", table_name="email_log")
    op.drop_index("ix_email_log_job", table_name="email_log")
    op.drop_index("ix_email_log_tenant_created", table_name="email_log")
    op.drop_table("email_log")

    op.drop_column("users", "permissions_json")

    op.drop_column("jobs", "benefits")
    op.drop_column("jobs", "work_life")
    op.drop_column("jobs", "about_company")

    op.drop_column("companies", "benefits_text")
    op.drop_column("companies", "work_life")
    op.drop_column("companies", "about_company")
