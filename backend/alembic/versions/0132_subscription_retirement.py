"""Subscription retirement: the product sells credits only.

Revision ID: 0132_subscription_retirement
Revises: 0131_freeze_at_application

Owner spec (2026-09-29), sections 2.1 and 23: the monthly subscription plans
are retired from the live product and credits are bought only as one-time
Razorpay Orders. The runtime code (routes, webhook events, the usage-summary
sweep, the schemas and the screens) leaves in the same change; this migration
removes the schema it wrote.

WHAT IT DROPS
-------------
* `pricing_plans`, the four monthly tiers 0026 seeded (Starter, Growth,
  Scale, Pro). A platform catalogue rather than customer data, so it is not
  guarded by a row count: its four rows are the seed, and the downgrade
  seeds them again. A `razorpay_plan_id` minted lazily on a first subscribe
  is NOT restored by the downgrade; the Razorpay-side plan, if one exists,
  stays in the Razorpay dashboard untouched.
* `tenants.razorpay_customer_id`, `razorpay_subscription_id`,
  `current_plan_id` (and its foreign key), `subscription_status` (and
  `ck_tenants_subscription_status`), `subscription_current_end`,
  `subscription_started_at` and `usage_alert_last_month`. The Orders path
  reads none of them; `razorpay_customer_id` in particular was read only by
  the deleted subscribe route and never written by anything.
* `billing_transactions.razorpay_subscription_id` and `plan_id`, and
  `credit_ledger.plan_id` (each with its foreign key).

WHAT IT NARROWS
---------------
`ck_billing_transactions_type` to `credit_pack` and `refund`. The two
subscription types (`subscription_charge`, `plan_change`) have no writer.

WHY IT REFUSES RATHER THAN DROPS WHEN ANY OF IT IS IN USE
---------------------------------------------------------
Every column above that a CUSTOMER'S ACTIVITY could have filled is customer
history: a subscription id is a contract Razorpay may still be charging, a
plan-change row is a money event, a plan id on a ledger grant is the answer
to "what was this grant for". CLAUDE.md S4 forbids an irreversible drop of
customer rows, so each is preceded by a count that RAISES, naming what it
found, and aborts the whole upgrade. Deleting or archiving that history is
an owner decision, never a side effect of a deploy. The guard conditions,
exactly (each must count ZERO):

    tenants:               razorpay_customer_id IS NOT NULL
                           razorpay_subscription_id IS NOT NULL
                           current_plan_id IS NOT NULL
                           subscription_status IS NOT NULL
                           subscription_current_end IS NOT NULL
                           subscription_started_at IS NOT NULL
                           usage_alert_last_month IS NOT NULL
    billing_transactions:  transaction_type IN ('subscription_charge',
                                                'plan_change')
                           razorpay_subscription_id IS NOT NULL
                           plan_id IS NOT NULL
    credit_ledger:         plan_id IS NOT NULL

The first guard proves the connection can SEE every row (the pattern 0128
set): `billing_transactions` and `credit_ledger` carry row level security,
and a count taken outside the bypass scope reads ZERO over a full table,
which is exactly the answer that would let the drop proceed.

DOWNGRADE recreates the structure as 0026 and 0111 left it (columns, the
foreign keys with ON DELETE SET NULL, the status CHECK, the transaction type
CHECK with its four values, the SELECT grant on `pricing_plans`) and re-seeds
the four plans with 0026's figures. It restores no data, because by the
guard there was none to lose.
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0132_subscription_retirement"
down_revision = "0131_freeze_at_application"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration.0132_subscription_retirement")

BYPASS = "current_setting('app.bypass_rls', true) = 'on'"

#: Each tenant column the subscription model wrote, all guarded NULL.
TENANT_COLUMNS: tuple[str, ...] = (
    "razorpay_customer_id",
    "razorpay_subscription_id",
    "current_plan_id",
    "subscription_status",
    "subscription_current_end",
    "subscription_started_at",
    "usage_alert_last_month",
)

SUBSCRIPTION_TRANSACTION_TYPES: tuple[str, ...] = ("subscription_charge", "plan_change")
SURVIVING_TRANSACTION_TYPES: tuple[str, ...] = ("credit_pack", "refund")

# ── For the downgrade only: 0026's exact structure and seed ─────────────────
_SUBSCRIPTION_STATUSES = ("active", "past_due", "cancelled", "halted")
_PREVIOUS_TRANSACTION_TYPES = (
    "subscription_charge",
    "plan_change",
    "refund",
    "credit_pack",
)
_PLANS = (
    # slug,     name,      applications/mo, price INR, rate/application INR, order
    ("starter", "Starter", 50, 10000, 200, 1),
    ("growth", "Growth", 100, 18000, 180, 2),
    ("scale", "Scale", 150, 24000, 160, 3),
    ("pro", "Pro", 200, 28000, 140, 4),
)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def _require_unfiltered_reads() -> None:
    """RAISE unless this connection sees every row of an RLS-protected table.

    The same check 0128 makes, for the same reason: without the bypass scope
    (which `alembic/env.py` sets), a superuser or a BYPASSRLS role, the
    emptiness guards below would count zero over rows they cannot see.
    """
    message = (
        "0132 refuses to run: this connection reads row level security "
        "filtered rows, so a guard could report zero over a table that holds "
        "rows. Set app.bypass_rls as alembic/env.py does."
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT ({BYPASS})
               AND NOT (SELECT rolsuper OR rolbypassrls
                          FROM pg_roles WHERE rolname = current_user) THEN
                RAISE EXCEPTION '{message}';
            END IF;
        END
        $$;
        """
    )


def _refuse_if_any(sql: str, message: str) -> None:
    """RAISE with `message` (which takes the count as `%`) when `sql` counts
    anything."""
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


def _guard_all_null(table: str, column: str) -> None:
    _refuse_if_any(
        f"SELECT count(*) FROM {table} WHERE {column} IS NOT NULL",
        f"{table}: % row(s) carry {column}. That is subscription history, and "
        "it may be dropped only when every value is NULL; archiving or "
        "deleting it is an owner decision, not a deploy.",
    )


def upgrade() -> None:
    _require_unfiltered_reads()

    # ── 1. Every guard, before any DDL ──────────────────────────────────────
    for column in TENANT_COLUMNS:
        _guard_all_null("tenants", column)
    _refuse_if_any(
        "SELECT count(*) FROM billing_transactions WHERE "
        + _in_list("transaction_type", SUBSCRIPTION_TRANSACTION_TYPES),
        "billing_transactions: % row(s) are of type "
        + " or ".join(SUBSCRIPTION_TRANSACTION_TYPES)
        + ". Those are subscription money events; the types may be retired "
        "only when none exist. Archiving them is an owner decision, not a "
        "deploy.",
    )
    _guard_all_null("billing_transactions", "razorpay_subscription_id")
    _guard_all_null("billing_transactions", "plan_id")
    _guard_all_null("credit_ledger", "plan_id")

    # ── 2. The plan references, then the table they referenced ──────────────
    op.execute("ALTER TABLE credit_ledger DROP COLUMN plan_id")
    op.execute("ALTER TABLE billing_transactions DROP COLUMN plan_id")
    op.execute("ALTER TABLE billing_transactions DROP COLUMN razorpay_subscription_id")
    op.execute(
        "ALTER TABLE tenants DROP CONSTRAINT IF EXISTS ck_tenants_subscription_status"
    )
    for column in TENANT_COLUMNS:
        # DROP COLUMN takes the column's foreign key with it
        # (fk_tenants_current_plan on current_plan_id).
        op.execute(f"ALTER TABLE tenants DROP COLUMN {column}")
    op.execute("DROP TABLE pricing_plans")

    # ── 3. The surviving transaction types ──────────────────────────────────
    op.execute(
        "ALTER TABLE billing_transactions DROP CONSTRAINT ck_billing_transactions_type"
    )
    op.create_check_constraint(
        "ck_billing_transactions_type",
        "billing_transactions",
        _in_list("transaction_type", SURVIVING_TRANSACTION_TYPES),
    )
    logger.info(
        "0132 dropped_table=pricing_plans dropped_tenant_columns=%s "
        "dropped_columns=billing_transactions.plan_id,"
        "billing_transactions.razorpay_subscription_id,credit_ledger.plan_id",
        ",".join(TENANT_COLUMNS),
    )


def downgrade() -> None:
    # ── 3 ────────────────────────────────────────────────────────────────────
    op.execute(
        "ALTER TABLE billing_transactions DROP CONSTRAINT ck_billing_transactions_type"
    )
    op.create_check_constraint(
        "ck_billing_transactions_type",
        "billing_transactions",
        _in_list("transaction_type", _PREVIOUS_TRANSACTION_TYPES),
    )

    # ── 2. The catalogue, then everything that referenced it ────────────────
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
    for slug, name, applications, price, rate, order in _PLANS:
        op.execute(
            sa.text(
                "INSERT INTO pricing_plans "
                "(slug, name, applications_per_month, price_inr, "
                " rate_per_application_inr, sort_order) "
                "VALUES (:slug, :name, :apps, :price, :rate, :order)"
            ).bindparams(slug=slug, name=name, apps=applications, price=price,
                         rate=rate, order=order)
        )

    op.add_column("tenants", sa.Column("razorpay_customer_id", sa.String(length=100)))
    op.add_column("tenants", sa.Column("razorpay_subscription_id", sa.String(length=100)))
    op.add_column("tenants", sa.Column("current_plan_id", postgresql.UUID(as_uuid=True)))
    op.add_column("tenants", sa.Column("subscription_status", sa.String(length=20)))
    op.add_column(
        "tenants", sa.Column("subscription_current_end", sa.DateTime(timezone=True))
    )
    op.add_column(
        "tenants", sa.Column("subscription_started_at", sa.DateTime(timezone=True))
    )
    op.add_column("tenants", sa.Column("usage_alert_last_month", sa.Integer()))
    op.create_foreign_key(
        "fk_tenants_current_plan", "tenants", "pricing_plans",
        ["current_plan_id"], ["id"], ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_tenants_subscription_status", "tenants",
        "subscription_status IS NULL OR "
        + _in_list("subscription_status", _SUBSCRIPTION_STATUSES),
    )

    op.add_column(
        "billing_transactions",
        sa.Column("razorpay_subscription_id", sa.String(length=100)),
    )
    op.add_column(
        "billing_transactions",
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pricing_plans.id", ondelete="SET NULL"),
        ),
    )
    op.add_column(
        "credit_ledger",
        sa.Column(
            "plan_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pricing_plans.id", ondelete="SET NULL"),
        ),
    )
