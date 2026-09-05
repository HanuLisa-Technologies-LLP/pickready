"""Early job closure: a client stops a posting once the requirement is met.

WHY A COLUMN AND NOT A BACK-DATED WINDOW
------------------------------------------
The obvious cheap trick is to move `posting_start_date` backwards so the
generated `posting_end_date` lands in the past. It is wrong twice over. The
start date is what `services/job_candidates` reads to decide which applications
are Old Profiles, and what `job_posting.can_edit_application` reads to decide
whether an application was made in-window. Both of those are HISTORY, and
rewriting history to express a decision made today loses the decision and
corrupts the history in one move.

So closure is its own fact: `closed_at`, plus the client's own words in
`closed_reason`.

THE VIEW IS REPLACED IN THE SAME MIGRATION
--------------------------------------------
`job_posting_state` duplicates `job_posting.posting_status` in SQL, and
0018 says in as many words that the two MUST agree. Adding a fifth state to the
Python function and not to the view would leave every query that filters on the
view reporting a closed job as active, which is exactly the class of silent
divergence the note exists to prevent. `closed` is checked FIRST here for the
same reason it is checked first there: a decision a person made outranks
arithmetic over dates.

NOTHING IS BACKFILLED
-----------------------
No existing job has been closed early, because until now nothing could close
one. A backfill would have to guess, and a guessed closure removes a live
posting from every candidate's board.

Revision ID: 0077_job_early_closure
Revises: 0076_proctoring_formats
"""
from alembic import op
import sqlalchemy as sa

revision = "0077_job_early_closure"
down_revision = "0076_proctoring_formats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column("jobs", sa.Column("closed_reason", sa.Text(), nullable=True))
    op.create_index("ix_jobs_closed_at", "jobs", ["closed_at"])

    # A reason without a closure is a row nobody can read: it claims the client
    # said something about a closure that never happened. The reverse is fine —
    # closing without a note is a legitimate, common choice.
    op.create_check_constraint(
        "ck_jobs_closed_reason_needs_closure",
        "jobs",
        "closed_reason IS NULL OR closed_at IS NOT NULL",
    )

    op.execute("DROP VIEW IF EXISTS job_posting_state")
    op.execute(
        """
        CREATE VIEW job_posting_state AS
        SELECT
            j.id AS job_id,
            j.posting_start_date,
            j.posting_end_date,
            j.grace_period_end_date,
            j.closed_at,
            CASE
                WHEN j.closed_at IS NOT NULL AND now() >= j.closed_at
                    THEN 'closed'
                WHEN now() < j.posting_start_date THEN 'scheduled'
                WHEN now() <= j.posting_end_date THEN 'active'
                WHEN now() <= j.grace_period_end_date THEN 'grace_period'
                ELSE 'expired'
            END AS posting_status
        FROM jobs j
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS job_posting_state")
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
    op.drop_constraint("ck_jobs_closed_reason_needs_closure", "jobs", type_="check")
    op.drop_index("ix_jobs_closed_at", table_name="jobs")
    op.drop_column("jobs", "closed_reason")
    op.drop_column("jobs", "closed_at")
