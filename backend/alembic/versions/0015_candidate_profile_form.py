"""Unified candidate profile: the 40 validation aspects become a structured
form on the candidate, and the candidate gains a designated MAIN resume.

Client decision, 2026-07-27: the validation questions are identical for a given
candidate across every job, so asking them inside each job's assessment
conversation re-asked the same 40 questions per application. They move to the
candidate's own profile as an advanced form; each application snapshots the
answers onto its Profile.aspects_json exactly as before, so nothing downstream
of `profiles.aspects_json` changes.

Backfill: the most recent profile that already carries aspect answers seeds the
candidate's form, and the most recent profile holding a resume becomes the main
resume — so existing candidates do not start from an empty profile.

Revision ID: 0015_candidate_profile_form
Revises: 0014_job_grade_and_grants
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0015_candidate_profile_form"
down_revision = "0014_job_grade_and_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidates",
        sa.Column("profile_form_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "candidates",
        sa.Column("profile_form_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "candidates",
        sa.Column("main_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    # ON DELETE SET NULL: deleting a profile must never cascade into deleting a
    # candidate. The reverse FK (profiles.candidate_id) already cascades.
    op.create_foreign_key(
        "fk_candidates_main_profile",
        "candidates",
        "profiles",
        ["main_profile_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_candidates_main_profile", "candidates", ["main_profile_id"])

    # ── Backfill: newest profile holding a resume becomes the main resume ────
    op.execute(
        """
        UPDATE candidates c
           SET main_profile_id = p.id
          FROM (
                SELECT DISTINCT ON (candidate_id) id, candidate_id
                  FROM profiles
                 WHERE resume_public_id IS NOT NULL
              ORDER BY candidate_id, created_at DESC
               ) p
         WHERE p.candidate_id = c.id
           AND c.main_profile_id IS NULL
        """
    )

    # ── Backfill: newest non-empty aspects_json seeds the profile form ───────
    # Legacy aspect answers are keyed by aspect NUMBER; the new form is keyed by
    # name. They are stored as-is (the reader tolerates both shapes) so no data
    # is lost, and the candidate is prompted to complete the named form.
    op.execute(
        """
        UPDATE candidates c
           SET profile_form_json = p.aspects_json
          FROM (
                SELECT DISTINCT ON (candidate_id) id, candidate_id, aspects_json
                  FROM profiles
                 WHERE aspects_json IS NOT NULL
                   AND aspects_json <> '{}'::jsonb
              ORDER BY candidate_id, created_at DESC
               ) p
         WHERE p.candidate_id = c.id
           AND c.profile_form_json IS NULL
        """
    )

    # The RLS app role must be able to read/write the new columns; the table
    # grant already covers column-level access, but re-assert it so a partially
    # migrated environment cannot end up with a table the app cannot write.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON candidates TO pickready_app")


def downgrade() -> None:
    op.drop_index("ix_candidates_main_profile", table_name="candidates")
    op.drop_constraint("fk_candidates_main_profile", "candidates", type_="foreignkey")
    op.drop_column("candidates", "main_profile_id")
    op.drop_column("candidates", "profile_form_updated_at")
    op.drop_column("candidates", "profile_form_json")
