"""Two-tier credit warning flags on tenants (Master Directive Part 5 §4).

Revision ID: 0071_credit_warnings
Revises: 0070_stem_classification

Warning 1 fires when the balance falls to or below 20 credits, Warning 2 at
10. The thresholds are FIXED SYSTEM VALUES (§4.1) — deliberately not columns,
not tenant-configurable. What is stored is only whether each warning email
has been sent since the last purchase, because Rule 5 resets both flags on
every top-up and the check runs after every deduction.
"""
import sqlalchemy as sa
from alembic import op

revision = "0071_credit_warnings"
down_revision = "0070_stem_classification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in ("credit_warning_1_sent", "credit_warning_2_sent"):
        op.add_column(
            "tenants",
            sa.Column(column, sa.Boolean(), nullable=False, server_default="false"),
        )


def downgrade() -> None:
    op.drop_column("tenants", "credit_warning_2_sent")
    op.drop_column("tenants", "credit_warning_1_sent")
