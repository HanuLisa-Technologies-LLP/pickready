"""Credit-pack purchases (Master Directive Part 5 — Pricing Model).

Revision ID: 0072_credit_purchases
Revises: 0071_credit_warnings

Four columns on `tenants` carry the per-account purchase state:

* `trial_used` — Rule 1: the 20-credit trial is available exactly once, so
  the flag is checked on EVERY purchase attempt, not just the first.
* `setup_fee_paid` — Rule 6: the ₹5,000 setup fee is one-time. TRUE also when
  the fee was waived, so it is never charged later.
* `setup_fee_waived` — §5.1: the first 15 client accounts pay no setup fee.
  Stored per tenant so `COUNT(*) WHERE setup_fee_waived` IS the running
  waiver tally — no separate counter to drift.
* `gstin` — client GSTIN for the B2B invoice (§5.2). Optional.

`credit_purchases` is one row per Razorpay Order, created BEFORE payment so
the browser-verify call and the webhook can race safely on the status flip
(see services/credit_packs.settle_purchase). `razorpay_order_id` is UNIQUE
for the same double-delivery reason `billing_transactions.razorpay_payment_id`
is.

`credit_invoice_seq` feeds sequential GST invoice numbers (§7.3): a Postgres
sequence is the only generator that stays gapless-enough and race-free under
concurrent settlements without a lock of our own.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0072_credit_purchases"
down_revision = "0071_credit_warnings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A settled pack writes a `billing_transactions` row like every other
    # money event; 0026's CHECK predates the pack flow and must admit the new
    # type or every settlement fails on the constraint.
    op.drop_constraint("ck_billing_transactions_type", "billing_transactions")
    op.create_check_constraint(
        "ck_billing_transactions_type",
        "billing_transactions",
        "transaction_type IN ('subscription_charge', 'plan_change', 'refund', "
        "'credit_pack')",
    )

    for column in ("trial_used", "setup_fee_paid", "setup_fee_waived"):
        op.add_column(
            "tenants",
            sa.Column(column, sa.Boolean(), nullable=False, server_default="false"),
        )
    op.add_column("tenants", sa.Column("gstin", sa.String(20), nullable=True))

    op.create_table(
        "credit_purchases",
        # PK is client-generated uuid4, same as every UUIDPKMixin table.
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("pack_slug", sa.String(30), nullable=False),
        sa.Column("credits_purchased", sa.Integer(), nullable=False),
        sa.Column("bonus_credits", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("subtotal_inr", sa.Integer(), nullable=False),
        sa.Column("setup_fee_inr", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "setup_fee_waived", sa.Boolean(), nullable=False, server_default="false"
        ),
        sa.Column("gst_inr", sa.Integer(), nullable=False),
        sa.Column("total_inr", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="created"),
        sa.Column("razorpay_order_id", sa.String(100), nullable=True),
        sa.Column("razorpay_payment_id", sa.String(100), nullable=True),
        sa.Column("invoice_number", sa.String(40), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("razorpay_order_id", name="uq_credit_purchases_order"),
    )
    op.create_index(
        "ix_credit_purchases_tenant_at",
        "credit_purchases",
        ["tenant_id", "created_at"],
    )

    # Tenant-scoped table → the standard RLS pair, same shape as 0001/0070.
    op.execute("ALTER TABLE credit_purchases ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE credit_purchases FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY credit_purchases_tenant_isolation ON credit_purchases
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

    # Sequential GST invoice numbers (§7.3): "RP-YYYY-NNNNNN" is formatted in
    # code from nextval() at settlement time.
    op.execute("CREATE SEQUENCE IF NOT EXISTS credit_invoice_seq")


def downgrade() -> None:
    op.drop_constraint("ck_billing_transactions_type", "billing_transactions")
    op.create_check_constraint(
        "ck_billing_transactions_type",
        "billing_transactions",
        "transaction_type IN ('subscription_charge', 'plan_change', 'refund')",
    )
    op.execute("DROP SEQUENCE IF EXISTS credit_invoice_seq")
    op.drop_index("ix_credit_purchases_tenant_at", table_name="credit_purchases")
    op.drop_table("credit_purchases")
    op.drop_column("tenants", "gstin")
    op.drop_column("tenants", "setup_fee_waived")
    op.drop_column("tenants", "setup_fee_paid")
    op.drop_column("tenants", "trial_used")
