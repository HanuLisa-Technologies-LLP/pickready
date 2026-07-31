"""Razorpay subscriptions + the credit ledger (killer-spec Parts 2 and 3).

    ASSUMPTION (spec §2.5 names `companies`): a customer IS a `tenants` row in
    this schema, exactly as migration 0020 established for the Provider Portal.
    `companies` is the client-AUTHORED, candidate-facing page and does not exist
    until the client signs in — so a subscription hung off `companies` would be
    unreachable for a customer who has just paid on the landing page and not yet
    onboarded. The subscription columns therefore land on `tenants`, and
    `credit_ledger.related_application_id` maps to `job_candidate_links`, which
    is this schema's application row.

Six changes:

1. `pricing_plans` — the four self-serve tiers as DATA. `razorpay_plan_id` is a
   column, never a constant in code, so repricing is a row edit and not a
   redeploy (spec §2.2). Enterprise is deliberately NOT a row: it has no
   self-serve checkout, so it is a static "Contact us" card, not a plan a
   Subscribe button could ever post to.

2. `tenants` gains the Razorpay linkage plus `credit_deficit` — the flag that
   pauses new assessment invitations when the balance has gone negative.

3. `billing_transactions` — one row per money event, UNIQUE on the Razorpay
   payment id so an at-least-once webhook redelivery cannot double-record.

4. `credit_ledger` — append-only, integer sub-units (60 = 1 credit), UNIQUE on
   `idempotency_key` so every writer is safe to retry. The balance is
   SUM(subunits_delta); there is no mutable balance column to drift from it.

5. `old_profile_reviews` — the once-per-(link, reviewer) marker that makes the
   1/20-credit Old Profile charge idempotent.

6. `webhook_events` — provider event-id dedupe, and the assessment columns the
   nightly reconciliation job needs (`reminders_sent`, `credit_reconciled_at`).

Revision ID: 0026_billing_and_credit_ledger
Revises: 0025_strip_em_dashes
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0026_billing_and_credit_ledger"
down_revision = "0025_strip_em_dashes"
branch_labels = None
depends_on = None

SUBSCRIPTION_STATUSES = ("active", "past_due", "cancelled", "halted")
LEDGER_EVENT_TYPES = (
    "grant",
    "completed_assessment",
    "incomplete_assessment",
    "no_show",
    "old_profile_review",
    "adjustment",
)
TRANSACTION_STATUSES = ("success", "failed", "refunded")
TRANSACTION_TYPES = ("subscription_charge", "plan_change", "refund")

#: Exact figures from spec §2.3. Not rounded, not approximated.
PLANS = (
    # slug,     name,      applications/mo, price INR, rate/application INR, order
    ("starter", "Starter", 50, 10000, 200, 1),
    ("growth", "Growth", 100, 18000, 180, 2),
    ("scale", "Scale", 150, 24000, 160, 3),
    ("pro", "Pro", 200, 28000, 140, 4),
)

_TENANT_RLS = """
    tenant_id = current_setting('app.tenant_id', true)::uuid
    OR current_setting('app.bypass_rls', true) = 'on'
"""


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    # ── 1. Pricing plans (global; no RLS — the price list is public) ─────────
    op.create_table(
        "pricing_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("slug", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False),
        sa.Column("applications_per_month", sa.Integer(), nullable=False),
        sa.Column("price_inr", sa.Integer(), nullable=False),
        sa.Column("rate_per_application_inr", sa.Integer(), nullable=False),
        sa.Column("razorpay_plan_id", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("slug", name="uq_pricing_plans_slug"),
        sa.CheckConstraint("price_inr >= 0", name="ck_pricing_plans_price_non_negative"),
        sa.CheckConstraint("applications_per_month > 0",
                           name="ck_pricing_plans_applications_positive"),
    )
    op.execute("GRANT SELECT ON pricing_plans TO pickready_app")
    for slug, name, applications, price, rate, order in PLANS:
        op.execute(
            sa.text(
                "INSERT INTO pricing_plans "
                "(slug, name, applications_per_month, price_inr, "
                " rate_per_application_inr, sort_order) "
                "VALUES (:slug, :name, :apps, :price, :rate, :order)"
            ).bindparams(slug=slug, name=name, apps=applications, price=price,
                         rate=rate, order=order)
        )

    # ── 2. Subscription linkage on the customer ─────────────────────────────
    op.add_column("tenants", sa.Column("razorpay_customer_id", sa.String(length=100), nullable=True))
    op.add_column("tenants", sa.Column("razorpay_subscription_id", sa.String(length=100), nullable=True))
    op.add_column("tenants", sa.Column("current_plan_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("tenants", sa.Column("subscription_status", sa.String(length=20), nullable=True))
    op.add_column("tenants", sa.Column("subscription_current_end", sa.DateTime(timezone=True), nullable=True))
    # The deficit flag is DERIVED (balance < 0) but stored, because the check
    # that blocks an invitation runs on every send and must not re-aggregate the
    # whole ledger to answer it.
    op.add_column(
        "tenants",
        sa.Column("credit_deficit", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_foreign_key(
        "fk_tenants_current_plan", "tenants", "pricing_plans",
        ["current_plan_id"], ["id"], ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_tenants_subscription_status", "tenants",
        "subscription_status IS NULL OR "
        + _in_list("subscription_status", SUBSCRIPTION_STATUSES),
    )

    # ── 3. Billing transactions ─────────────────────────────────────────────
    op.create_table(
        "billing_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("razorpay_payment_id", sa.String(length=100), nullable=True),
        sa.Column("razorpay_subscription_id", sa.String(length=100), nullable=True),
        sa.Column("amount_inr", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("transaction_type", sa.String(length=30), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["plan_id"], ["pricing_plans.id"], ondelete="SET NULL"),
        sa.CheckConstraint(_in_list("status", TRANSACTION_STATUSES),
                           name="ck_billing_transactions_status"),
        sa.CheckConstraint(_in_list("transaction_type", TRANSACTION_TYPES),
                           name="ck_billing_transactions_type"),
        # NULL is not equal to NULL in a UNIQUE index, so rows without a payment
        # id (a failed charge Razorpay never issued one for) still insert.
        sa.UniqueConstraint("razorpay_payment_id", name="uq_billing_transactions_payment"),
    )
    op.create_index("ix_billing_transactions_tenant_at", "billing_transactions",
                    ["tenant_id", "created_at"])

    # ── 4. Credit ledger ────────────────────────────────────────────────────
    op.create_table(
        "credit_ledger",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("subunits_delta", sa.Integer(), nullable=False),
        sa.Column("job_candidate_link_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=200), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_candidate_link_id"], ["job_candidate_links.id"],
                                ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["plan_id"], ["pricing_plans.id"], ondelete="SET NULL"),
        sa.CheckConstraint(_in_list("event_type", LEDGER_EVENT_TYPES),
                           name="ck_credit_ledger_event_type"),
        # A grant adds, everything else subtracts. A zero delta is a no-op row
        # that would only make a statement harder to read.
        sa.CheckConstraint("subunits_delta <> 0", name="ck_credit_ledger_delta_nonzero"),
        sa.UniqueConstraint("idempotency_key", name="uq_credit_ledger_idempotency"),
    )
    op.create_index("ix_credit_ledger_tenant_at", "credit_ledger", ["tenant_id", "created_at"])
    op.create_index("ix_credit_ledger_tenant_event", "credit_ledger", ["tenant_id", "event_type"])

    # ── 5. Old-profile review markers ───────────────────────────────────────
    op.create_table(
        "old_profile_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("job_candidate_link_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewer_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_candidate_link_id"], ["job_candidate_links.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewer_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("job_candidate_link_id", "reviewer_user_id",
                            name="uq_old_profile_review_link_reviewer"),
    )
    op.create_index("ix_old_profile_reviews_tenant", "old_profile_reviews",
                    ["tenant_id", "created_at"])

    # RLS on the three tenant-scoped billing tables (claude.md rule 1).
    for table in ("billing_transactions", "credit_ledger", "old_profile_reviews"):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO pickready_app")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY {table}_tenant_isolation ON {table} "
            f"USING ({_TENANT_RLS}) WITH CHECK ({_TENANT_RLS})"
        )

    # ── 6. Webhook dedupe + assessment reconciliation columns ───────────────
    op.create_table(
        "webhook_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("provider", sa.String(length=30), nullable=False, server_default="razorpay"),
        sa.Column("event_id", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload_json", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("provider", "event_id", name="uq_webhook_events_provider_id"),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON webhook_events TO pickready_app")

    op.add_column("assessment_conversations",
                  sa.Column("reminders_sent", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("assessment_conversations",
                  sa.Column("last_reminder_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("assessment_conversations",
                  sa.Column("credit_reconciled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("assessment_conversations",
                  sa.Column("credit_event", sa.String(length=40), nullable=True))
    # The reconciliation sweep scans for unreconciled invitations only. Without
    # this it is a full table scan of every assessment ever created, every night.
    op.create_index(
        "ix_assessment_conversations_unreconciled",
        "assessment_conversations",
        ["invitation_sent_at"],
        postgresql_where=sa.text("credit_reconciled_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_assessment_conversations_unreconciled",
                  table_name="assessment_conversations")
    for column in ("credit_event", "credit_reconciled_at", "last_reminder_at", "reminders_sent"):
        op.drop_column("assessment_conversations", column)

    op.execute("REVOKE SELECT, INSERT, UPDATE ON webhook_events FROM pickready_app")
    op.drop_table("webhook_events")

    for table in ("old_profile_reviews", "credit_ledger", "billing_transactions"):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {table} FROM pickready_app")

    op.drop_index("ix_old_profile_reviews_tenant", table_name="old_profile_reviews")
    op.drop_table("old_profile_reviews")
    op.drop_index("ix_credit_ledger_tenant_event", table_name="credit_ledger")
    op.drop_index("ix_credit_ledger_tenant_at", table_name="credit_ledger")
    op.drop_table("credit_ledger")
    op.drop_index("ix_billing_transactions_tenant_at", table_name="billing_transactions")
    op.drop_table("billing_transactions")

    op.drop_constraint("ck_tenants_subscription_status", "tenants", type_="check")
    op.drop_constraint("fk_tenants_current_plan", "tenants", type_="foreignkey")
    for column in ("credit_deficit", "subscription_current_end", "subscription_status",
                   "current_plan_id", "razorpay_subscription_id", "razorpay_customer_id"):
        op.drop_column("tenants", column)

    op.execute("REVOKE SELECT ON pricing_plans FROM pickready_app")
    op.drop_table("pricing_plans")
