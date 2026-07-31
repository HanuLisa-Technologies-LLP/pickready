"""The unified JD document + the candidate procurement type.

Three changes, all from the 2026-07-28 client spec:

1. `jobs.jd_markdown` — the single AI-drafted, recruiter-edited job description
   that replaces the per-section text boxes. NULLABLE on purpose: every job
   that predates this release has none, and api/jobs renders one from
   `jd_json` at read time rather than blanking the posting. Backfilling text
   here would have meant writing a machine rendering into the column the
   recruiter's own edits live in, and there would then be no way to tell an
   untouched job from an edited one.

2. `jobs.experience_min_years` / `experience_max_years` — the experience band
   that replaced the free-text Level box, with a CHECK that the minimum never
   exceeds the maximum. The DB enforces it as well as the schema because a
   backwards band silently publishes a JD advertising a range nobody chose.

3. `job_candidate_links.source_type` — applied | sourced | databank, NOT NULL,
   DB CHECK, default `applied`.

`reportees` and `company_context` were never COLUMNS on `jobs`: both lived
inside the `jd_json` document. Dropping them therefore means stripping the keys
out of that JSONB, which is what this migration does (the `ALTER TABLE ... DROP
COLUMN IF EXISTS` calls are belt and braces for any environment that did add
them by hand).

THE source_type BACKFILL
------------------------
The "via external link" marker the job page already renders is derived from
`job_candidate_links.application_source = 'sourced'` (see
frontend/components/candidate-ranking-table.tsx and api/portal.apply_to_job,
which writes that value when the applicant arrived through a shared job link).
That is the signal reused here, in this precedence:

  source = 'databank'            -> 'databank'   (minted from the Databank pool)
  application_source = 'sourced' -> 'sourced'    (arrived via an external link)
  otherwise                      -> 'applied'

The databank test comes FIRST deliberately. Migration 0018 set
`application_source = 'sourced'` for every `source = 'databank'` row, so
checking application_source first would mislabel the entire databank pool as
sourced. Same ordering as models/candidate._derive_source_type, so rows written
from now on agree with rows backfilled here.

Revision ID: 0022_jd_document_and_procurement
Revises: 0021_seed_compliance_capability
"""
import sqlalchemy as sa
from alembic import op

revision = "0022_jd_document_and_procurement"
down_revision = "0021_seed_compliance_capability"
branch_labels = None
depends_on = None

SOURCE_TYPES = ("applied", "sourced", "databank")


def upgrade() -> None:
    # ── 1. The unified JD document ──────────────────────────────────────────
    op.add_column("jobs", sa.Column("jd_markdown", sa.Text(), nullable=True))

    # ── 2. The experience band ──────────────────────────────────────────────
    op.add_column(
        "jobs", sa.Column("experience_min_years", sa.Integer(), nullable=True)
    )
    op.add_column(
        "jobs", sa.Column("experience_max_years", sa.Integer(), nullable=True)
    )
    # Seed the band from whatever the old JD documents recorded, so an existing
    # job shows a range instead of two empty fields. `experience_years` was
    # written either as a number ("5") or as a range string ("3-5"); both are
    # read here, and anything unparseable is left NULL rather than guessed at.
    #
    # BOTH sides are set in ONE statement, and GREATEST clamps the upper bound
    # up to the lower one. Two separate UPDATEs would leave the row briefly
    # backwards (a historic "100-3", or a value clamped at 60 on one side only)
    # and trip the CHECK before the corrective pass could run. The constraint is
    # created AFTER the backfill for the same reason: a data repair must not be
    # policed by the rule it is repairing the data to satisfy.
    op.execute(
        """
        UPDATE jobs
        SET experience_min_years = low,
            experience_max_years = GREATEST(low, high)
        FROM (
            SELECT id AS jid,
                   LEAST((substring(jd_json->>'experience_years'
                                    from '^\\s*(\\d+)'))::int, 60) AS low,
                   LEAST((substring(jd_json->>'experience_years'
                                    from '(\\d+)\\s*$'))::int, 60) AS high
            FROM jobs
            WHERE jd_json->>'experience_years' ~ '^\\s*\\d+'
        ) parsed
        WHERE jobs.id = parsed.jid
          AND parsed.low IS NOT NULL
          AND experience_min_years IS NULL
          AND experience_max_years IS NULL
        """
    )
    # NULL on either side passes: a legacy job has neither, and a draft may
    # legitimately have only one so far. The band is only checked once both
    # sides are known.
    op.create_check_constraint(
        "ck_jobs_experience_band",
        "jobs",
        "experience_min_years IS NULL OR experience_max_years IS NULL "
        "OR experience_min_years <= experience_max_years",
    )

    # ── 3. Remove `reportees` and `company_context` ─────────────────────────
    # They live inside jd_json in every known environment; the DROP COLUMN
    # calls cover an environment where one was promoted to a real column.
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS reportees")
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS company_context")
    # `jsonb_exists(...)` rather than the `?` operator: `?` is a DBAPI
    # parameter placeholder in some drivers and is not escapable consistently
    # across them (asyncpg leaves `??` literal and then rejects it). The
    # function form means the same thing and cannot be misread.
    op.execute(
        "UPDATE jobs SET jd_json = jd_json - 'reportees' - 'company_context' "
        "WHERE jsonb_exists(jd_json, 'reportees') "
        "   OR jsonb_exists(jd_json, 'company_context')"
    )

    # ── 4. Type of procurement ──────────────────────────────────────────────
    op.add_column(
        "job_candidate_links",
        sa.Column("source_type", sa.String(length=20), nullable=True),
    )
    op.execute(
        """
        UPDATE job_candidate_links
        SET source_type = CASE
            WHEN source = 'databank' THEN 'databank'
            WHEN application_source = 'sourced' THEN 'sourced'
            ELSE 'applied'
        END
        WHERE source_type IS NULL
        """
    )
    op.alter_column("job_candidate_links", "source_type", nullable=False)
    op.execute(
        "ALTER TABLE job_candidate_links "
        "ALTER COLUMN source_type SET DEFAULT 'applied'"
    )
    op.create_check_constraint(
        "ck_jcl_source_type",
        "job_candidate_links",
        "source_type IN (" + ", ".join(f"'{s}'" for s in SOURCE_TYPES) + ")",
    )
    # The job page filters the candidate table by procurement type, and that
    # filter runs in SQL beside the job predicate.
    op.create_index(
        "ix_jcl_job_source_type", "job_candidate_links", ["job_id", "source_type"]
    )


def downgrade() -> None:
    op.drop_index("ix_jcl_job_source_type", table_name="job_candidate_links")
    op.drop_constraint(
        "ck_jcl_source_type", "job_candidate_links", type_="check"
    )
    op.drop_column("job_candidate_links", "source_type")

    op.drop_constraint("ck_jobs_experience_band", "jobs", type_="check")
    op.drop_column("jobs", "experience_max_years")
    op.drop_column("jobs", "experience_min_years")
    op.drop_column("jobs", "jd_markdown")
    # `reportees` / `company_context` are NOT restored: they were JSONB keys,
    # the values are gone, and inventing empty ones would be worse than their
    # absence. Downgrading returns the schema, not the deleted data.
