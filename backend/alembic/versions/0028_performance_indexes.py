"""Indexes for the hot WHERE / JOIN / ORDER BY columns (latency pass).

Every index here answers a query that runs on a request path a user waits on.
Grouped by why it exists, because "add an index" without the query it serves is
how a schema accumulates dead indexes that only slow writes down:

1. **RLS tenant predicates.** The Postgres policies append
   `tenant_id = current_setting('app.tenant_id')` to EVERY query on a
   tenant-scoped table. Without a leading `tenant_id` index that predicate is
   evaluated by a sequential scan, so the security boundary becomes the
   performance ceiling. The four biggest tenant tables get one.

2. **Identity lookups on the auth path.** `POST /auth/firebase/session` matches
   on `users.phone`, and every single candidate-portal request resolves
   `candidates.user_id`. Neither was indexed, so both were a full scan on the
   two tables that grow fastest.

3. **The candidate's own views.** "My applications" filters
   `job_candidate_links.candidate_id`; the composite unique constraint leads
   with `job_id`, so it could not serve that predicate.

4. **Child rows fetched per parent.** `interviews` had nothing but its primary
   key, so the interview panel on a job page scanned the whole table.

5. **Partial indexes for the "still open" queries** — a WHERE clause on the
   index keeps it small and keeps it matching the query it exists for.

`IF NOT EXISTS` throughout: this migration is additive and safe to re-run, and
a couple of these may already exist under a different name in an older
environment.

Revision ID: 0028_performance_indexes
Revises: 0027_seed_billing_capabilities
"""
from alembic import op

revision = "0028_performance_indexes"
down_revision = "0027_seed_billing_capabilities"
branch_labels = None
depends_on = None

INDEXES: tuple[tuple[str, str], ...] = (
    # ── 1. RLS tenant predicates ────────────────────────────────────────────
    ("ix_jcl_tenant", "job_candidate_links (tenant_id)"),
    # `profiles` is deliberately NOT tenant-scoped the way the others are — a
    # candidate's profile spans tenants via the Databank, so the column is
    # `source_tenant_id` (where the resume was first collected), and that is
    # what the Databank queries filter on.
    ("ix_profiles_source_tenant", "profiles (source_tenant_id)"),
    ("ix_assessment_messages_tenant", "assessment_messages (tenant_id)"),
    ("ix_report_dimensions_tenant", "report_dimensions (tenant_id)"),
    ("ix_technical_questions_tenant", "technical_questions (tenant_id)"),
    ("ix_assessment_conversations_tenant", "assessment_conversations (tenant_id)"),
    ("ix_interviews_tenant", "interviews (tenant_id)"),
    ("ix_pipeline_status_tenant", "pipeline_status (tenant_id)"),

    # ── 2. Auth identity lookups ────────────────────────────────────────────
    # Firebase sign-in matches phone aliases; a seq scan of `users` on every
    # login attempt is the cheapest thing on this list to fix.
    ("ix_users_phone", "users (phone)"),
    ("ix_users_tenant_role", "users (tenant_id, role)"),

    # ── 3. The candidate's own views ────────────────────────────────────────
    ("ix_candidates_user", "candidates (user_id)"),
    ("ix_jcl_candidate", "job_candidate_links (candidate_id)"),
    # The applications list orders by recency within a candidate.
    ("ix_jcl_candidate_created", "job_candidate_links (candidate_id, created_at DESC)"),

    # ── 4. Child rows fetched per parent ────────────────────────────────────
    ("ix_interviews_link", "interviews (job_candidate_link_id)"),
    ("ix_interviews_scheduled", "interviews (scheduled_at)"),
    ("ix_pipeline_status_link_at", "pipeline_status (job_candidate_link_id, at DESC)"),

    # ── 5. Partial indexes for the "still open" queries ─────────────────────
    # The candidate job board filters live postings; the partial WHERE keeps
    # the index to just the rows the board can ever return.
    (
        "ix_jobs_live",
        "jobs (tenant_id, posting_start_date DESC) "
        "WHERE archived_at IS NULL AND ratified_at IS NOT NULL",
    ),
    # The active question bank per job — every assessment start reads it.
    (
        "ix_technical_questions_active",
        "technical_questions (job_id, ordinal) WHERE is_active",
    ),
    # OTP verification looks a challenge up by identifier while it is unspent.
    (
        "ix_otp_challenges_open",
        "otp_challenges (identifier, expires_at DESC) WHERE consumed_at IS NULL",
    ),
    # Ranked candidate lists exclude archived rows on every page.
    (
        "ix_jcl_job_active",
        "job_candidate_links (job_id, created_at) WHERE archived_at IS NULL",
    ),
)


def upgrade() -> None:
    for name, definition in INDEXES:
        op.execute(f"CREATE INDEX IF NOT EXISTS {name} ON {definition}")
    # Fresh statistics so the planner actually uses what we just built rather
    # than continuing with the estimates it formed before they existed.
    for table in (
        "job_candidate_links", "profiles", "users", "candidates", "jobs",
        "interviews", "pipeline_status", "assessment_conversations",
        "technical_questions", "otp_challenges",
    ):
        op.execute(f"ANALYZE {table}")


def downgrade() -> None:
    for name, _definition in INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
