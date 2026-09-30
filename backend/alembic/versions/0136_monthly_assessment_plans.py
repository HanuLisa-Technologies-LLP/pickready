"""Recurring monthly assessment plans alongside historical credit purchases.

Revision ID: 0136_monthly_assessment_plans
Revises: 0135_leadership_intelligence
"""
from alembic import op
import sqlalchemy as sa

revision = "0136_monthly_assessment_plans"
down_revision = "0135_leadership_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "monthly_gateway_plans",
        sa.Column("slug", sa.String(20), primary_key=True),
        sa.Column("razorpay_plan_id", sa.String(100), nullable=False, unique=True),
        sa.Column("total_inr", sa.Integer(), nullable=False),
    )
    op.execute("GRANT SELECT, INSERT, UPDATE ON monthly_gateway_plans TO pickready_app")
    op.add_column("tenants", sa.Column("current_plan_slug", sa.String(20)))
    op.add_column("tenants", sa.Column("pending_plan_slug", sa.String(20)))
    op.add_column("tenants", sa.Column("razorpay_subscription_id", sa.String(100)))
    op.add_column("tenants", sa.Column("subscription_status", sa.String(20)))
    op.add_column("tenants", sa.Column("subscription_current_end", sa.DateTime(timezone=True)))
    op.add_column("tenants", sa.Column("subscription_started_at", sa.DateTime(timezone=True)))
    op.create_index(
        "uq_tenants_razorpay_subscription_id", "tenants", ["razorpay_subscription_id"],
        unique=True, postgresql_where=sa.text("razorpay_subscription_id IS NOT NULL"),
    )
    op.add_column("billing_transactions", sa.Column("razorpay_subscription_id", sa.String(100)))
    op.add_column("billing_transactions", sa.Column("plan_slug", sa.String(20)))
    op.add_column("billing_transactions", sa.Column("assessments_granted", sa.Integer()))
    op.add_column("billing_transactions", sa.Column("subtotal_inr", sa.Integer()))
    op.add_column("billing_transactions", sa.Column("gst_inr", sa.Integer()))
    op.add_column("billing_transactions", sa.Column("invoice_number", sa.String(40)))
    op.create_index("uq_billing_transactions_invoice_number", "billing_transactions", ["invoice_number"], unique=True)
    op.execute("ALTER TABLE billing_transactions DROP CONSTRAINT ck_billing_transactions_type")
    op.create_check_constraint(
        "ck_billing_transactions_type", "billing_transactions",
        "transaction_type IN ('credit_pack', 'refund', 'subscription_charge')",
    )
    op.execute("UPDATE jobs SET credit_cost_per_report = 1.0 WHERE credit_cost_per_report <> 1.0")
    op.drop_constraint("ck_jobs_credit_cost_per_report", "jobs")
    op.create_check_constraint("ck_jobs_credit_cost_per_report", "jobs", "credit_cost_per_report = 1.0")


def downgrade() -> None:
    op.drop_constraint("ck_jobs_credit_cost_per_report", "jobs")
    op.create_check_constraint(
        "ck_jobs_credit_cost_per_report", "jobs",
        "credit_cost_per_report IN (1.0, 1.5)",
    )
    op.execute("UPDATE jobs SET credit_cost_per_report = 1.5 WHERE role_classification = 'STEM'")
    op.execute("ALTER TABLE billing_transactions DROP CONSTRAINT ck_billing_transactions_type")
    op.create_check_constraint(
        "ck_billing_transactions_type", "billing_transactions",
        "transaction_type IN ('credit_pack', 'refund')",
    )
    op.drop_index("uq_billing_transactions_invoice_number", table_name="billing_transactions")
    for column in ("invoice_number", "gst_inr", "subtotal_inr", "assessments_granted", "plan_slug", "razorpay_subscription_id"):
        op.drop_column("billing_transactions", column)
    op.drop_index("uq_tenants_razorpay_subscription_id", table_name="tenants")
    for column in ("subscription_started_at", "subscription_current_end", "subscription_status", "razorpay_subscription_id", "pending_plan_slug", "current_plan_slug"):
        op.drop_column("tenants", column)
    op.drop_table("monthly_gateway_plans")
