"""Job grade as a first-class field, missing RLS-role grants, and removal of
the manual technical-question approval gate.

Revision ID: 0014_job_grade_and_grants
Revises: 0013_assessment_rls
"""
from alembic import op
import sqlalchemy as sa

revision = "0014_job_grade_and_grants"
down_revision = "0013_assessment_rls"
branch_labels = None
depends_on = None

# Mirrors app.services.functional_assessment.infer_grade_fallback exactly.
GRADE_CASE = """
CASE
    WHEN lower(title || ' ' || coalesce(level, '')) LIKE '%chief%'
      OR lower(title || ' ' || coalesce(level, '')) LIKE '%cxo%'
      OR lower(title || ' ' || coalesce(level, '')) LIKE '%ceo%'
      OR lower(title || ' ' || coalesce(level, '')) LIKE '%cto%'
      OR lower(title || ' ' || coalesce(level, '')) LIKE '%cfo%'
      OR lower(title || ' ' || coalesce(level, '')) LIKE '%coo%'
        THEN 'cxo'
    WHEN lower(title || ' ' || coalesce(level, '')) LIKE '%director%'
      OR lower(title || ' ' || coalesce(level, '')) LIKE '%head%'
      OR lower(title || ' ' || coalesce(level, '')) LIKE '%vice president%'
      OR lower(title || ' ' || coalesce(level, '')) LIKE '%vp%'
      OR lower(title || ' ' || coalesce(level, '')) LIKE '%leader%'
        THEN 'leadership'
    WHEN lower(title || ' ' || coalesce(level, '')) LIKE '%manager%'
      OR lower(title || ' ' || coalesce(level, '')) LIKE '%lead%'
      OR lower(title || ' ' || coalesce(level, '')) LIKE '%supervisor%'
        THEN 'managerial'
    ELSE 'non_managerial'
END
"""


def upgrade() -> None:
    # ── (a) LIVE BUG FIX: staff_invites (0008) was never granted to the RLS app
    # role, so every staff query raised InsufficientPrivilegeError. Grant it
    # explicitly, then blanket-grant every other table as a safety net for any
    # table added since without its own grant.
    op.execute("GRANT USAGE ON SCHEMA public TO pickready_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON staff_invites TO pickready_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO pickready_app")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO pickready_app")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO pickready_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT USAGE, SELECT ON SEQUENCES TO pickready_app"
    )
    # audit_log stays append-only (0001 revoked UPDATE/DELETE) — the blanket
    # grant above would have silently re-granted them. Re-assert the revoke.
    op.execute("REVOKE UPDATE, DELETE ON audit_log FROM pickready_app")
    # dashboard_job_metrics is a MATERIALIZED VIEW, so it is not covered by
    # "ALL TABLES IN SCHEMA public" — re-grant it explicitly (0001 §dashboard).
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_matviews WHERE matviewname = 'dashboard_job_metrics') THEN
                EXECUTE 'GRANT SELECT ON dashboard_job_metrics TO pickready_app';
            END IF;
        END $$;
        """
    )

    # ── (b) Grade becomes a first-class, non-null job field. jobs.assessment_grade
    # is reused as the canonical store (additive — no duplicate column).
    op.execute(f"UPDATE jobs SET assessment_grade = {GRADE_CASE} WHERE assessment_grade IS NULL")
    op.execute(
        "UPDATE jobs SET assessment_grade = 'non_managerial' "
        "WHERE assessment_grade NOT IN ('non_managerial', 'managerial', 'leadership', 'cxo')"
    )
    op.alter_column(
        "jobs",
        "assessment_grade",
        existing_type=sa.String(40),
        nullable=False,
        server_default="non_managerial",
    )

    # ── (c) The manual question-bank approval gate is removed (user decision,
    # 2026-07-25). Auto-approve everything still sitting in review so no
    # candidate is blocked, and make ready_for_candidates the column default.
    op.execute(
        "UPDATE jobs SET assessment_status = 'ready_for_candidates', "
        "questions_approved_at = COALESCE(questions_approved_at, now()) "
        "WHERE assessment_status = 'questions_pending_review'"
    )
    op.alter_column(
        "jobs",
        "assessment_status",
        existing_type=sa.String(40),
        existing_nullable=False,
        server_default="ready_for_candidates",
    )


def downgrade() -> None:
    op.alter_column(
        "jobs",
        "assessment_status",
        existing_type=sa.String(40),
        existing_nullable=False,
        server_default="questions_pending_review",
    )
    op.alter_column(
        "jobs",
        "assessment_grade",
        existing_type=sa.String(40),
        nullable=True,
        server_default=None,
    )
    # Grants: drop the default-privileges rule and the blanket sequence grant.
    # The per-table grants from 0001/0013 are intentionally left in place —
    # revoking them wholesale would break the app on any partial downgrade.
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM pickready_app"
    )
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "REVOKE USAGE, SELECT ON SEQUENCES FROM pickready_app"
    )
    op.execute("REVOKE SELECT, INSERT, UPDATE, DELETE ON staff_invites FROM pickready_app")
