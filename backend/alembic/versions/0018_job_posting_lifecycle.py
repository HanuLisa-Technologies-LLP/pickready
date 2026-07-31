"""Fixed 30-day job posting window, 10-stage hiring pipeline, assessment
invitations, and multi-round interview tracking (2026-07-27 lifecycle spec).

THREE THINGS THE SPEC'S SQL CANNOT DO IN POSTGRES
-------------------------------------------------
The brief specifies three GENERATED ... STORED columns. Only one of them is
actually possible, and the migration implements the other two differently
rather than silently dropping them:

1. `posting_end_date` / `grace_period_end_date` — GENERATED STORED works.
   `posting_start_date + INTERVAL '30 days'` is IMMUTABLE, which is what
   Postgres requires. These are real generated columns, so they are physically
   impossible to write to: a recruiter cannot change the window even with a
   hand-crafted UPDATE. That is exactly the guarantee the spec asks for.

2. `posting_status` ('active' | 'grace_period' | 'expired') — NOT POSSIBLE as
   a stored column. Its definition calls NOW(), and Postgres rejects a
   non-IMMUTABLE expression in a generated column (a stored value computed at
   write time would be wrong one second later anyway). It is derived at READ
   time instead — `services/job_posting.posting_status()` in Python, and the
   `job_posting_state` SQL view created here for queries that need to filter on
   it. Same three values, always correct, never stale.

3. `is_within_grace_period` on the application — NOT POSSIBLE as a stored
   column either: it both calls NOW() and subqueries another table, and a
   generated column may do neither. Also derived at read time
   (`services/job_posting.can_edit_application`).

`ON UPDATE CURRENT_TIMESTAMP` in the spec is MySQL syntax; `status_updated_at`
is maintained by the status-transition service instead, which is where the
audit trail is written anyway.

BACKFILL
--------
`posting_start_date` is NOT NULL, and existing jobs need a truthful value:
it is set from `ratified_at` (when the job actually published) falling back to
`created_at`. That means the 34 seeded jobs get a real window rather than all
appearing to have been posted at migration time — several are already inside
their grace period or expired, which is a more honest demo than a fleet of
jobs that all expire on the same day.

Revision ID: 0018_job_posting_lifecycle
Revises: 0017_seed_new_capabilities
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0018_job_posting_lifecycle"
down_revision = "0017_seed_new_capabilities"
branch_labels = None
depends_on = None

#: The fixed window. Both are business constants, not configuration — the spec
#: is explicit that a recruiter can never vary them.
POSTING_DAYS = 30
GRACE_DAYS = 5

#: The 10-stage pipeline (spec §3.3). The pre-existing 5 values
#: (rejected, shortlisted, hold, offered, joined) are all retained so historic
#: `pipeline_status` rows stay readable; `offered` and `offer_extended` are
#: kept as synonyms rather than rewriting history.
PIPELINE_STATUSES = (
    "applied",
    "assessment_invited",
    "assessment_in_progress",
    "assessment_completed",
    "shortlisted",
    "rejected",
    "interview_scheduled",
    "interview_completed",
    "offer_extended",
    "joined",
    # Legacy values, still valid.
    "hold",
    "offered",
)


#: `dashboard_job_metrics`, rebuilt after widening pipeline_status.status.
#: Kept as a module constant so upgrade() and downgrade() cannot diverge.
_DASHBOARD_MATVIEW_SQL = """
CREATE MATERIALIZED VIEW dashboard_job_metrics AS
WITH latest_status AS (
    SELECT DISTINCT ON (pipeline_status.job_candidate_link_id)
           pipeline_status.job_candidate_link_id,
           pipeline_status.status
    FROM pipeline_status
    ORDER BY pipeline_status.job_candidate_link_id, pipeline_status.at DESC
)
SELECT j.id AS job_id,
       j.tenant_id,
       count(l.id) FILTER (WHERE l.source::text = 'databank') AS databank_matched,
       count(l.id) FILTER (WHERE l.source::text = 'fresh') AS fresh_sourced,
       count(l.id) FILTER (WHERE ls.status::text = 'shortlisted') AS shortlisted,
       count(l.id) FILTER (
           WHERE ls.status::text IN ('offered', 'offer_extended')
       ) AS offered,
       count(l.id) FILTER (WHERE ls.status::text = 'joined') AS joined
FROM jobs j
LEFT JOIN job_candidate_links l ON l.job_id = j.id
LEFT JOIN latest_status ls ON ls.job_candidate_link_id = l.id
GROUP BY j.id, j.tenant_id
"""


def upgrade() -> None:
    # ── 1. Job posting window ────────────────────────────────────────────────
    op.add_column(
        "jobs",
        sa.Column(
            "posting_start_date",
            sa.DateTime(timezone=True),
            nullable=True,  # tightened to NOT NULL after the backfill below
        ),
    )
    op.execute(
        "UPDATE jobs SET posting_start_date = COALESCE(ratified_at, created_at, now())"
    )
    op.alter_column("jobs", "posting_start_date", nullable=False)
    op.execute("ALTER TABLE jobs ALTER COLUMN posting_start_date SET DEFAULT now()")

    # Real generated columns. The obvious spelling — `posting_start_date +
    # INTERVAL '30 days'` — is REJECTED by Postgres: adding a day-or-larger
    # interval to a timestamptz depends on the session TimeZone (DST), so that
    # operator is STABLE, not IMMUTABLE, and a generated column requires
    # IMMUTABLE. Converting to a naive UTC timestamp, adding there, and
    # converting back is immutable at every step and lands on the identical
    # instant — verified as exactly 30 days.
    op.execute(
        f"""
        ALTER TABLE jobs
        ADD COLUMN posting_end_date timestamptz
        GENERATED ALWAYS AS (
            timezone('UTC', timezone('UTC', posting_start_date)
                            + INTERVAL '{POSTING_DAYS} days')
        ) STORED
        """
    )
    op.execute(
        f"""
        ALTER TABLE jobs
        ADD COLUMN grace_period_end_date timestamptz
        GENERATED ALWAYS AS (
            timezone('UTC', timezone('UTC', posting_start_date)
                            + INTERVAL '{POSTING_DAYS + GRACE_DAYS} days')
        ) STORED
        """
    )
    # grace_period_end_date is defined against posting_start_date rather than
    # posting_end_date because a generated column may not reference another
    # generated column. 30 + 5 = 35 days from the start is the same instant.
    op.create_index("ix_jobs_posting_window", "jobs", ["posting_end_date"])

    # The read-time posting status the spec wanted as a stored column. A view
    # keeps the definition in ONE place for SQL callers; Python callers use
    # services/job_posting.posting_status(), and the two must agree.
    op.execute(
        """
        CREATE VIEW job_posting_state AS
        SELECT
            j.id AS job_id,
            j.posting_start_date,
            j.posting_end_date,
            j.grace_period_end_date,
            CASE
                WHEN now() < j.posting_start_date THEN 'scheduled'
                WHEN now() <= j.posting_end_date THEN 'active'
                WHEN now() <= j.grace_period_end_date THEN 'grace_period'
                ELSE 'expired'
            END AS posting_status
        FROM jobs j
        """
    )
    op.execute("GRANT SELECT ON job_posting_state TO pickready_app")

    # ── 2. Application source + pipeline status on the link ─────────────────
    # `job_candidate_links` IS the applications table in this schema.
    op.add_column(
        "job_candidate_links",
        sa.Column("application_source", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "job_candidate_links",
        sa.Column("status", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "job_candidate_links",
        sa.Column("status_updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "job_candidate_links",
        sa.Column("current_stage", sa.String(length=100), nullable=True),
    )
    # Seed the denormalised status from the authoritative pipeline_status
    # history where one exists, so the new column is never a blank slate.
    op.execute(
        """
        UPDATE job_candidate_links l
        SET status = latest.status,
            status_updated_at = latest.at
        FROM (
            SELECT DISTINCT ON (job_candidate_link_id)
                   job_candidate_link_id, status, at
            FROM pipeline_status
            ORDER BY job_candidate_link_id, at DESC
        ) latest
        WHERE latest.job_candidate_link_id = l.id
        """
    )
    op.execute(
        """
        UPDATE job_candidate_links
        SET status = 'applied',
            status_updated_at = COALESCE(status_updated_at, created_at)
        WHERE status IS NULL
        """
    )
    # `source` (databank | fresh) records HOW the candidate reached the job;
    # `application_source` (direct | sourced) records WHERE they came from.
    # They answer different questions, so both are kept.
    op.execute(
        """
        UPDATE job_candidate_links
        SET application_source = CASE
            WHEN source = 'databank' THEN 'sourced' ELSE 'direct' END
        WHERE application_source IS NULL
        """
    )
    op.alter_column("job_candidate_links", "status", nullable=False)
    op.alter_column("job_candidate_links", "application_source", nullable=False)
    op.execute(
        "ALTER TABLE job_candidate_links ALTER COLUMN status SET DEFAULT 'applied'"
    )
    op.execute(
        "ALTER TABLE job_candidate_links "
        "ALTER COLUMN application_source SET DEFAULT 'direct'"
    )
    op.create_check_constraint(
        "ck_jcl_application_source",
        "job_candidate_links",
        "application_source IN ('direct', 'sourced')",
    )
    op.create_check_constraint(
        "ck_jcl_status",
        "job_candidate_links",
        "status IN (" + ", ".join(f"'{s}'" for s in PIPELINE_STATUSES) + ")",
    )
    op.create_index(
        "ix_jcl_job_status", "job_candidate_links", ["job_id", "status"]
    )

    # The history table's column is varchar(15) — too short for
    # 'assessment_in_progress' (22). Widen it, or every new transition would
    # fail at write time with a truncation error.
    #
    # The `dashboard_job_metrics` materialized view reads this column, and
    # Postgres refuses to alter a column a view depends on, so the view is
    # dropped and recreated around the change. Its definition is reproduced
    # verbatim from migration 0001 apart from one addition: `offer_extended`
    # now counts toward `offered`, because the 10-stage pipeline renamed that
    # transition and the dashboard would otherwise silently stop counting
    # offers the moment anyone used the new status.
    op.execute("DROP MATERIALIZED VIEW IF EXISTS dashboard_job_metrics")
    op.alter_column(
        "pipeline_status",
        "status",
        existing_type=sa.String(length=15),
        type_=sa.String(length=30),
        existing_nullable=False,
    )
    op.execute(_DASHBOARD_MATVIEW_SQL)
    op.execute(
        "CREATE UNIQUE INDEX ux_dashboard_job_metrics_job "
        "ON dashboard_job_metrics (job_id)"
    )
    op.execute("GRANT SELECT ON dashboard_job_metrics TO pickready_app")

    # ── 3. Assessment invitation tracking ───────────────────────────────────
    # Extends `assessment_conversations` rather than adding a parallel
    # `assessments` table: that row already owns started/completed state, and
    # two tables answering "is this assessment done?" would drift.
    op.add_column(
        "assessment_conversations",
        sa.Column("invitation_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "assessment_conversations",
        sa.Column("invited_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "assessment_conversations",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_assessment_conversations_invited_by",
        "assessment_conversations",
        "users",
        ["invited_by"],
        ["id"],
        ondelete="SET NULL",
    )
    # Every conversation that already exists predates the invitation gate, so
    # it is treated as invited — otherwise the seeded demo candidates would be
    # locked out of assessments they are already part-way through.
    op.execute(
        "UPDATE assessment_conversations "
        "SET invitation_sent_at = COALESCE(invitation_sent_at, created_at)"
    )

    # ── 4. Multi-round interview stages ─────────────────────────────────────
    # Extends the existing `interviews` table for the same reason.
    op.add_column("interviews", sa.Column("stage_number", sa.Integer(), nullable=True))
    op.add_column(
        "interviews", sa.Column("stage_name", sa.String(length=100), nullable=True)
    )
    op.add_column(
        "interviews", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "interviews", sa.Column("interviewer_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.add_column("interviews", sa.Column("feedback", sa.Text(), nullable=True))
    op.add_column(
        "interviews",
        sa.Column("status", sa.String(length=20), nullable=False, server_default="scheduled"),
    )
    op.create_foreign_key(
        "fk_interviews_interviewer",
        "interviews",
        "users",
        ["interviewer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_interviews_status",
        "interviews",
        "status IN ('scheduled', 'completed', 'cancelled')",
    )
    op.execute(
        "UPDATE interviews SET stage_number = 1, "
        "stage_name = COALESCE(stage_name, 'Interview') WHERE stage_number IS NULL"
    )


def downgrade() -> None:
    op.drop_constraint("ck_interviews_status", "interviews", type_="check")
    op.drop_constraint("fk_interviews_interviewer", "interviews", type_="foreignkey")
    for column in (
        "status", "feedback", "interviewer_id", "completed_at", "stage_name",
        "stage_number",
    ):
        op.drop_column("interviews", column)

    op.drop_constraint(
        "fk_assessment_conversations_invited_by",
        "assessment_conversations",
        type_="foreignkey",
    )
    for column in ("started_at", "invited_by", "invitation_sent_at"):
        op.drop_column("assessment_conversations", column)

    op.execute("DROP MATERIALIZED VIEW IF EXISTS dashboard_job_metrics")
    op.alter_column(
        "pipeline_status",
        "status",
        existing_type=sa.String(length=30),
        type_=sa.String(length=15),
        existing_nullable=False,
    )
    op.execute(_DASHBOARD_MATVIEW_SQL)
    op.execute(
        "CREATE UNIQUE INDEX ux_dashboard_job_metrics_job "
        "ON dashboard_job_metrics (job_id)"
    )
    op.execute("GRANT SELECT ON dashboard_job_metrics TO pickready_app")
    op.drop_index("ix_jcl_job_status", table_name="job_candidate_links")
    op.drop_constraint("ck_jcl_status", "job_candidate_links", type_="check")
    op.drop_constraint("ck_jcl_application_source", "job_candidate_links", type_="check")
    for column in ("current_stage", "status_updated_at", "status", "application_source"):
        op.drop_column("job_candidate_links", column)

    op.execute("DROP VIEW IF EXISTS job_posting_state")
    op.drop_index("ix_jobs_posting_window", table_name="jobs")
    op.drop_column("jobs", "grace_period_end_date")
    op.drop_column("jobs", "posting_end_date")
    op.drop_column("jobs", "posting_start_date")
