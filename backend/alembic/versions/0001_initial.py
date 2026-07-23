"""Initial schema: all tables (ESD §4), pgvector + FTS indexes, RLS policies,
append-only audit log, max-5 hiring-manager trigger, dashboard matview.

Revision ID: 0001
Revises:
Create Date: 2026-07-23

Notes:
- RLS: every tenant-scoped table gets ENABLE + FORCE ROW LEVEL SECURITY —
  FORCE because the dev docker connection is the table owner (postgres), and
  owners bypass plain RLS. The escape hatch for workers/super-admin is
  `current_setting('app.bypass_rls', true) = 'on'` inside each policy.
- candidates/profiles are cross-tenant Databank tables with softer policies.
- audit_log is append-only for the app role (UPDATE/DELETE revoked).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _uuid_pk() -> sa.Column:
    return sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False)


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )


# Tables whose isolation is a plain tenant_id match (+ worker/super-admin bypass)
_TENANT_TABLES = [
    "companies",
    "hiring_managers",
    "email_templates",
    "jobs",
    "job_approvals",
    "job_candidate_links",
    "verification_requests",
    "interviews",
    "pipeline_status",
]


def upgrade() -> None:
    # ── Extensions ──────────────────────────────────────────────────────────
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ── Global tables ───────────────────────────────────────────────────────
    op.create_table(
        "tenants",
        _uuid_pk(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False, unique=True),
        sa.Column(
            "spf_dkim_status", sa.String(50), nullable=False, server_default="pending"
        ),
        _created_at(),
    )

    op.create_table(
        "users",
        _uuid_pk(),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.UniqueConstraint("tenant_id", "email", "role", name="uq_users_tenant_email_role"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "role_permissions",
        _uuid_pk(),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("role", sa.String(30), nullable=False),
        sa.Column("capability", sa.String(100), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("tenant_id", "role", "capability", name="uq_role_permissions"),
    )
    op.create_index(
        "ix_role_permissions_lookup", "role_permissions", ["tenant_id", "role", "capability"]
    )

    op.create_table(
        "otp_challenges",
        _uuid_pk(),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("identifier", sa.String(320), nullable=False),
        sa.Column("channel", sa.String(10), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
    )

    op.create_table(
        "audit_log",
        _uuid_pk(),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),
        sa.Column("actor_user_id", UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(50), nullable=True),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column("metadata_json", JSONB(), nullable=True),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.create_index("ix_audit_log_tenant_at", "audit_log", ["tenant_id", "at"])

    op.create_table(
        "llm_provider_keys",
        _uuid_pk(),
        sa.Column("provider", sa.String(30), nullable=False),
        sa.Column("key_encrypted", sa.Text(), nullable=False),
        sa.Column("role_hint", sa.String(30), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("healthy", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
    )

    # ── Tenant-scoped tables ────────────────────────────────────────────────
    op.create_table(
        "companies",
        _uuid_pk(),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("brief", sa.Text(), nullable=True),
        sa.Column("culture", sa.Text(), nullable=True),
        sa.Column("policies", sa.Text(), nullable=True),
        sa.Column("benefits", sa.Text(), nullable=True),
        sa.Column("approval_levels_config", JSONB(), nullable=True),
        _created_at(),
        sa.UniqueConstraint("tenant_id", name="uq_companies_tenant"),
    )

    op.create_table(
        "hiring_managers",
        _uuid_pk(),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("approval_level", sa.String(20), nullable=True),
        _created_at(),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_hiring_managers_user"),
    )

    op.create_table(
        "email_templates",
        _uuid_pk(),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("subject", sa.String(500), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        _created_at(),
        sa.UniqueConstraint("tenant_id", "name", "version", name="uq_email_templates_version"),
    )

    op.create_table(
        "jobs",
        _uuid_pk(),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("department", sa.String(255), nullable=True),
        sa.Column("level", sa.String(100), nullable=True),
        sa.Column("jd_json", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("compensation_json", JSONB(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("requirement_period", sa.String(100), nullable=True),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("ratified_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
    )
    op.create_index("ix_jobs_tenant_status", "jobs", ["tenant_id", "status"])
    # JD embedding for the semantic stage — intentionally NOT on the SQLAlchemy
    # model (set by the matching worker via raw SQL).
    op.execute("ALTER TABLE jobs ADD COLUMN embedding vector(1024)")

    op.create_table(
        "job_approvals",
        _uuid_pk(),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("level", sa.String(20), nullable=False),
        sa.Column(
            "approver_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decision", sa.String(20), nullable=False),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index("ix_job_approvals_job", "job_approvals", ["job_id"])

    # ── Databank tables (cross-tenant) ──────────────────────────────────────
    op.create_table(
        "candidates",
        _uuid_pk(),
        sa.Column("tenant_id", UUID(as_uuid=True), nullable=True),  # NULL = shared Databank
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("phone", sa.String(20), nullable=True),
        sa.Column("city", sa.String(120), nullable=True),
        sa.Column("age", sa.Integer(), nullable=True),
        sa.Column("gender", sa.String(30), nullable=True),
        sa.Column(
            "consent_databank", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        _created_at(),
    )
    op.create_index("ix_candidates_email", "candidates", ["email"])

    op.create_table(
        "profiles",
        _uuid_pk(),
        sa.Column(
            "candidate_id",
            UUID(as_uuid=True),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_tenant_id", UUID(as_uuid=True), nullable=True),
        sa.Column("resume_url", sa.String(1000), nullable=True),
        sa.Column("resume_text", sa.Text(), nullable=True),
        sa.Column("aspects_json", JSONB(), nullable=True),
        sa.Column("parsed_fields_json", JSONB(), nullable=True),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("aspects_completed_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
    )
    op.create_index("ix_profiles_candidate", "profiles", ["candidate_id"])
    # Full-text column for the keyword stage (ESD §8.2.2) + GIN index
    op.execute(
        "ALTER TABLE profiles ADD COLUMN resume_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', coalesce(resume_text, ''))) STORED"
    )
    op.execute("CREATE INDEX ix_profiles_resume_tsv ON profiles USING gin (resume_tsv)")
    # HNSW cosine index for the semantic stage (ESD §8.2.1)
    op.execute(
        "CREATE INDEX ix_profiles_embedding_hnsw ON profiles "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    op.create_table(
        "job_candidate_links",
        _uuid_pk(),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "candidate_id",
            UUID(as_uuid=True),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "profile_id",
            UUID(as_uuid=True),
            sa.ForeignKey("profiles.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("match_score", sa.Float(), nullable=True),
        sa.Column("match_rationale", sa.Text(), nullable=True),
        sa.Column("tier", sa.String(25), nullable=True),
        sa.Column(
            "hm_access_granted", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        _created_at(),
        sa.UniqueConstraint("job_id", "candidate_id", name="uq_jcl_job_candidate"),
    )
    op.create_index("ix_jcl_job", "job_candidate_links", ["job_id"])

    op.create_table(
        "verification_requests",
        _uuid_pk(),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "profile_id",
            UUID(as_uuid=True),
            sa.ForeignKey("profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("employer_seq", sa.Integer(), nullable=False),
        sa.Column("employer_email", sa.String(320), nullable=False),
        sa.Column("employer_name", sa.String(255), nullable=True),
        sa.Column("token", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("submitted_via", sa.String(15), nullable=True),
        sa.Column("response_json", JSONB(), nullable=True),
        sa.Column("override_reason", sa.Text(), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.UniqueConstraint("profile_id", "employer_seq", name="uq_verification_employer_seq"),
    )

    op.create_table(
        "interviews",
        _uuid_pk(),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_candidate_link_id",
            UUID(as_uuid=True),
            sa.ForeignKey("job_candidate_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sent_from_email", sa.String(320), nullable=True),
        sa.Column("ics_uid", sa.String(255), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        _created_at(),
    )

    op.create_table(
        "pipeline_status",
        _uuid_pk(),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_candidate_link_id",
            UUID(as_uuid=True),
            sa.ForeignKey("job_candidate_links.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(15), nullable=False),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.Column(
            "set_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )
    op.create_index("ix_pipeline_status_link", "pipeline_status", ["job_candidate_link_id"])

    # ── Row-Level Security (ESD §3) ─────────────────────────────────────────
    # FORCE so even the table owner (postgres superuser owns tables in dev
    # docker) obeys the policies; workers/super-admin use app.bypass_rls.
    # NOTE: actual superusers bypass RLS regardless — FORCE protects against
    # the owner-but-not-superuser case (managed Postgres app roles).
    for table in _TENANT_TABLES:
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

    # role_permissions: global template rows (tenant_id IS NULL) must stay
    # readable by every tenant session.
    op.execute("ALTER TABLE role_permissions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE role_permissions FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY role_permissions_tenant_or_global ON role_permissions
        USING (
            tenant_id IS NULL
            OR tenant_id = current_setting('app.tenant_id', true)::uuid
            OR current_setting('app.bypass_rls', true) = 'on'
        )
        WITH CHECK (
            tenant_id IS NULL
            OR tenant_id = current_setting('app.tenant_id', true)::uuid
            OR current_setting('app.bypass_rls', true) = 'on'
        )
        """
    )

    # candidates: cross-tenant Databank — tenant_id NULL rows are shared.
    op.execute("ALTER TABLE candidates ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE candidates FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY candidates_databank ON candidates
        USING (
            tenant_id IS NULL
            OR tenant_id = current_setting('app.tenant_id', true)::uuid
            OR current_setting('app.bypass_rls', true) = 'on'
        )
        WITH CHECK (
            tenant_id IS NULL
            OR tenant_id = current_setting('app.tenant_id', true)::uuid
            OR current_setting('app.bypass_rls', true) = 'on'
        )
        """
    )

    # profiles: keyed on source_tenant_id (no tenant_id column). Reads also
    # allow consent-gated Databank profiles from other tenants (Aspect 40 /
    # FR-4.2); writes never cross tenants — Databank profiles are reused
    # as-is, not modified by the borrowing tenant (claude.md rule 7).
    # ASSUMPTION: cross-tenant read visibility of a profile is governed by the
    # owning candidate's consent_databank flag, matching the Databank model.
    op.execute("ALTER TABLE profiles ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE profiles FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY profiles_databank ON profiles
        USING (
            source_tenant_id IS NULL
            OR source_tenant_id = current_setting('app.tenant_id', true)::uuid
            OR current_setting('app.bypass_rls', true) = 'on'
            OR EXISTS (
                SELECT 1 FROM candidates c
                WHERE c.id = profiles.candidate_id AND c.consent_databank = true
            )
        )
        WITH CHECK (
            source_tenant_id IS NULL
            OR source_tenant_id = current_setting('app.tenant_id', true)::uuid
            OR current_setting('app.bypass_rls', true) = 'on'
        )
        """
    )

    # ── App role + append-only audit log (ESD §16) ──────────────────────────
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pickready_app') THEN
                CREATE ROLE pickready_app NOLOGIN;
            END IF;
        END
        $$
        """
    )
    op.execute("GRANT USAGE ON SCHEMA public TO pickready_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO pickready_app")
    op.execute("REVOKE UPDATE, DELETE ON audit_log FROM pickready_app")
    op.execute("REVOKE UPDATE, DELETE ON audit_log FROM PUBLIC")

    # ── Max 5 hiring managers per tenant (FR-2.2) ───────────────────────────
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_max_hiring_managers() RETURNS trigger AS $$
        BEGIN
            IF (SELECT COUNT(*) FROM hiring_managers WHERE tenant_id = NEW.tenant_id) >= 5 THEN
                RAISE EXCEPTION 'Maximum of 5 hiring managers per tenant (FR-2.2)';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_max_hiring_managers
        BEFORE INSERT ON hiring_managers
        FOR EACH ROW EXECUTE FUNCTION enforce_max_hiring_managers()
        """
    )

    # ── Dashboard materialized view (ESD §14, FR-10.1) ──────────────────────
    # UNIQUE index on job_id is required for REFRESH ... CONCURRENTLY.
    op.execute(
        """
        CREATE MATERIALIZED VIEW dashboard_job_metrics AS
        WITH latest_status AS (
            SELECT DISTINCT ON (job_candidate_link_id)
                   job_candidate_link_id, status
            FROM pipeline_status
            ORDER BY job_candidate_link_id, at DESC
        )
        SELECT j.id AS job_id,
               j.tenant_id AS tenant_id,
               COUNT(l.id) FILTER (WHERE l.source = 'databank')      AS databank_matched,
               COUNT(l.id) FILTER (WHERE l.source = 'fresh')         AS fresh_sourced,
               COUNT(l.id) FILTER (WHERE ls.status = 'shortlisted')  AS shortlisted,
               COUNT(l.id) FILTER (WHERE ls.status = 'offered')      AS offered,
               COUNT(l.id) FILTER (WHERE ls.status = 'joined')       AS joined
        FROM jobs j
        LEFT JOIN job_candidate_links l ON l.job_id = j.id
        LEFT JOIN latest_status ls ON ls.job_candidate_link_id = l.id
        GROUP BY j.id, j.tenant_id
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX ux_dashboard_job_metrics_job ON dashboard_job_metrics (job_id)"
    )
    op.execute("GRANT SELECT ON dashboard_job_metrics TO pickready_app")


def downgrade() -> None:
    op.execute("DROP MATERIALIZED VIEW IF EXISTS dashboard_job_metrics")
    op.execute("DROP TRIGGER IF EXISTS trg_max_hiring_managers ON hiring_managers")
    op.execute("DROP FUNCTION IF EXISTS enforce_max_hiring_managers()")

    for table in (
        "pipeline_status",
        "interviews",
        "verification_requests",
        "job_candidate_links",
        "profiles",
        "candidates",
        "job_approvals",
        "jobs",
        "email_templates",
        "hiring_managers",
        "companies",
        "llm_provider_keys",
        "audit_log",
        "otp_challenges",
        "role_permissions",
        "users",
        "tenants",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
    # Extensions and the pickready_app role are intentionally left in place —
    # they may be shared with other databases/schemas.
