"""Candidate communications: the outbox, the default sender, candidate unread.

Revision ID: 0122_candidate_comms
Revises: 0121_candidate_identity

Phase 6 WP6-C (messaging and email). Every step is additive except the last,
and the last is guarded.

1. `conversations.candidate_last_read_at` and `candidate_notified_at`. A
   candidate is not a `conversation_participants` row (they have no tenant),
   so their read watermark and the debounce stamp for "you have a new
   message" live on the thread itself. One candidate per candidate thread
   makes that exact rather than an approximation.
2. `email_log.dedupe_key` (partial UNIQUE) and `email_log.conversation_id`.
   The automatic emails were idempotent by "any row of this TYPE for this
   application", which is why the 72 hour reminder was never sent: the 24
   hour one already existed. The key names the STAGE
   (`assessment_reminder:<link>:<hours>`) and the database refuses the second
   write, so a redelivered task is a no-op and the second stage is not.
   Backfilled for the EARLIEST row per application of each automatic type,
   which is the row the old check was protecting. `conversation_id` binds a
   human-sent email to the candidate thread whose Reply-To it carries.
3. `ck_email_log_type` admits `message_notification`, the email a candidate
   receives when a recruiter writes to them.
4. `client_email_senders.is_default`, at most one per tenant (partial UNIQUE).
   Automatic emails had no way to carry a corporate sender at all, because
   there was no request to name one in.
5. `ck_jcl_application_source` admits `external_link`: an applicant who came
   in through a shared job link is an APPLICANT, and the old two-value CHECK
   forced the page to call them `sourced`.
6. `email_log.claimed_at`, stamped by the send worker's atomic claim
   (`queued` to `processing`), and `ix_email_log_pending`, the partial index
   the fifteen minute `reconcile_queued_emails` sweep reads through. A row
   stuck in `processing` is measured from its CLAIM, never from when it was
   queued: a row re-dispatched an hour after it was written is not stuck the
   instant it is claimed.
7. `verification_requests` IS DROPPED, and only when it is EMPTY. The
   tenant-owned employer-verification system was retired on 2026-09-18 (C8)
   and nothing reads or writes the table. CONTRACT v3 counted zero rows in
   pilot on 2026-09-24. The upgrade RAISES, naming the count, if any row
   exists: deleting employer correspondence is an owner decision, never
   something a migration does because a probe once said the table was empty.

No new table, so no new RLS policy. The new columns inherit their tables'
policies and grants. The downgrade recreates `verification_requests` exactly
as it stood (columns, RLS FORCE, the guarded 0034 policy, the grant) and
REFUSES to restore a narrower CHECK while rows use a value it would reject,
rather than rewriting those rows.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0122_candidate_comms"
down_revision = "0121_candidate_identity"
branch_labels = None
depends_on = None


#: Mirrors `models.email_log.LOGGED_EMAIL_TYPES` after this migration.
_EMAIL_TYPES_BEFORE: tuple[str, ...] = (
    "application_confirmation",
    "assessment_reminder",
    "shortlist",
    "rejected",
    "hold",
    "question_bank_reminder",
    "assessment_invitation",
    "assessment_complete",
    "interview_scheduled",
    "interview_completed",
    "offer_extended",
    "joined",
    "databank_invitation",
    "bgv_verification",
)
_EMAIL_TYPES_AFTER: tuple[str, ...] = _EMAIL_TYPES_BEFORE + ("message_notification",)

_SOURCES_BEFORE: tuple[str, ...] = ("direct", "sourced")
_SOURCES_AFTER: tuple[str, ...] = _SOURCES_BEFORE + ("external_link",)

#: The first reminder stage, in hours. `credit_reconciliation.
#: REMINDER_SCHEDULE_HOURS[0]`, written as a literal because a migration must
#: apply to the schema as it stood, not to whatever the module says later.
_FIRST_REMINDER_HOURS = 24

_TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"
_BYPASS = "current_setting('app.bypass_rls', true) = 'on'"


def _in_list(column: str, values: tuple[str, ...]) -> str:
    rendered = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({rendered})"


def _refuse_if_any(sql: str, message: str) -> None:
    """RAISE with `message` (which takes the count as `%`) when `sql` counts
    anything. A guard that names the count, instead of an opaque constraint
    failure halfway through the migration."""
    op.execute(
        f"""
        DO $$
        DECLARE
            affected bigint;
        BEGIN
            SELECT ({sql}) INTO affected;
            IF affected > 0 THEN
                RAISE EXCEPTION '{message}', affected;
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    # ── 1. The candidate's read watermark and notification stamp ─────────────
    op.add_column(
        "conversations",
        sa.Column("candidate_last_read_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("candidate_notified_at", sa.DateTime(timezone=True), nullable=True),
    )

    # ── 2. Dedupe key and thread binding on the outbox ───────────────────────
    op.add_column("email_log", sa.Column("dedupe_key", sa.String(200), nullable=True))
    op.add_column(
        "email_log",
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_email_log_conversation", "email_log", ["conversation_id"])
    # The EARLIEST row per application and type is the one the old
    # "any row of this type" check protected, so it is the one that inherits
    # the key. Later duplicates (a pilot probe found none) keep NULL and stay
    # as history.
    op.execute(
        f"""
        UPDATE email_log e
           SET dedupe_key = CASE
               WHEN ranked.email_type = 'application_confirmation'
                 THEN 'application_confirmation:' || ranked.link_id
               ELSE 'assessment_reminder:' || ranked.link_id
                    || ':' || '{_FIRST_REMINDER_HOURS}'
           END
          FROM (
              SELECT id,
                     email_type,
                     job_candidate_link_id::text AS link_id,
                     row_number() OVER (
                         PARTITION BY job_candidate_link_id, email_type
                         ORDER BY created_at, id
                     ) AS position
                FROM email_log
               WHERE job_candidate_link_id IS NOT NULL
                 AND email_type IN ('application_confirmation', 'assessment_reminder')
          ) AS ranked
         WHERE e.id = ranked.id AND ranked.position = 1
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_email_log_dedupe_key ON email_log (dedupe_key) "
        "WHERE dedupe_key IS NOT NULL"
    )

    # ── 3. The message notification type ─────────────────────────────────────
    op.drop_constraint("ck_email_log_type", "email_log", type_="check")
    op.create_check_constraint(
        "ck_email_log_type", "email_log", _in_list("email_type", _EMAIL_TYPES_AFTER)
    )

    # ── 4. One default corporate sender per tenant ───────────────────────────
    op.add_column(
        "client_email_senders",
        sa.Column(
            "is_default",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_client_email_sender_default "
        "ON client_email_senders (tenant_id) WHERE is_default"
    )

    # ── 5. A public-link applicant is an applicant ───────────────────────────
    op.drop_constraint("ck_jcl_application_source", "job_candidate_links", type_="check")
    op.create_check_constraint(
        "ck_jcl_application_source",
        "job_candidate_links",
        _in_list("application_source", _SOURCES_AFTER),
    )

    # ── 6. What the reconcile sweep reads ────────────────────────────────────
    op.add_column(
        "email_log", sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        "CREATE INDEX ix_email_log_pending ON email_log (status, created_at) "
        "WHERE status IN ('queued', 'processing')"
    )

    # ── 7. The retired table, dropped only when empty ────────────────────────
    _refuse_if_any(
        "SELECT count(*) FROM verification_requests",
        "verification_requests holds % row(s). The retired employer "
        "verification system may be dropped only when it is empty; deleting "
        "that correspondence is an owner decision.",
    )
    op.drop_table("verification_requests")


def downgrade() -> None:
    # ── 7. Recreate the retired table exactly as it stood ────────────────────
    op.create_table(
        "verification_requests",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, nullable=False),
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
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "profile_id", "employer_seq", name="uq_verification_employer_seq"
        ),
    )
    op.execute("ALTER TABLE verification_requests ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE verification_requests FORCE ROW LEVEL SECURITY")
    expr = f"(tenant_id = {_TENANT}) OR ({_BYPASS})"
    op.execute(
        "CREATE POLICY verification_requests_tenant_isolation "
        f"ON verification_requests USING ({expr}) WITH CHECK ({expr})"
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON verification_requests TO pickready_app"
    )

    # ── 6 ────────────────────────────────────────────────────────────────────
    op.execute("DROP INDEX IF EXISTS ix_email_log_pending")
    op.drop_column("email_log", "claimed_at")

    # ── 5 ────────────────────────────────────────────────────────────────────
    _refuse_if_any(
        "SELECT count(*) FROM job_candidate_links "
        "WHERE application_source = 'external_link'",
        "job_candidate_links: % application(s) came in through a job link. "
        "The narrower CHECK would reject them, and this downgrade will not "
        "relabel an applicant.",
    )
    op.drop_constraint("ck_jcl_application_source", "job_candidate_links", type_="check")
    op.create_check_constraint(
        "ck_jcl_application_source",
        "job_candidate_links",
        _in_list("application_source", _SOURCES_BEFORE),
    )

    # ── 4 ────────────────────────────────────────────────────────────────────
    op.execute("DROP INDEX IF EXISTS uq_client_email_sender_default")
    op.drop_column("client_email_senders", "is_default")

    # ── 3 ────────────────────────────────────────────────────────────────────
    _refuse_if_any(
        "SELECT count(*) FROM email_log WHERE email_type = 'message_notification'",
        "email_log: % message notification(s) exist. The narrower CHECK would "
        "reject them, and this downgrade will not delete a delivery record.",
    )
    op.drop_constraint("ck_email_log_type", "email_log", type_="check")
    op.create_check_constraint(
        "ck_email_log_type", "email_log", _in_list("email_type", _EMAIL_TYPES_BEFORE)
    )

    # ── 2 ────────────────────────────────────────────────────────────────────
    op.execute("DROP INDEX IF EXISTS uq_email_log_dedupe_key")
    op.drop_index("ix_email_log_conversation", table_name="email_log")
    op.drop_column("email_log", "conversation_id")
    op.drop_column("email_log", "dedupe_key")

    # ── 1 ────────────────────────────────────────────────────────────────────
    op.drop_column("conversations", "candidate_notified_at")
    op.drop_column("conversations", "candidate_last_read_at")
