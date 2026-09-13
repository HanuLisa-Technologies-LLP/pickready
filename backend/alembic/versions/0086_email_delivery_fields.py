"""Delivery-tracking columns on email_log: failed_at, transport, template_id.

WHY THREE COLUMNS AND NOT A SECOND TABLE
----------------------------------------
`email_log` is already the outbound delivery record and already carries
`provider_message_id`, `delivered_at`, `bounced_at` and `complained_at` from
the Corporate Email System work. A separate delivery table would force every
reader to join two rows that are one-to-one, and would give the product two
answers to "what happened to this email". These extend the row that exists.

WHY EACH ONE EARNS ITS PLACE
----------------------------
`failed_at` is SES REJECT and RENDERING_FAILURE: the message never reached a
receiver at all. That is a different event from `bounced_at`, which means a
receiver took it and refused it. Folding them together would make the two
indistinguishable afterwards when they have different causes and different
fixes.

`transport` records which path actually carried the message. Inferring it from
today's `settings.email_transport` is wrong the moment a deployment switches:
every historical row would silently relabel. It also disambiguates `sent`,
which is TERMINAL under smtp (Gmail reports no delivery outcome at all) and
merely provisional under ses.

`template_id` is finer-grained than `email_type`: several templates share one
type, so a bounce cannot otherwise be traced back to the copy that produced it.

ALL THREE ARE NULLABLE, DELIBERATELY. Existing rows predate the tracking and
genuinely do not know their transport or template; back-filling a guess would
make an unknown look like a recorded fact. NULL reads as "not recorded", which
is the truth.

NO INDEX IS ADDED HERE, and that was a correction rather than an omission.
This migration originally created `ix_email_log_provider_message_id` for the
webhook's lookup; migration 0080 already creates exactly that index, and the
duplicate failed the suite with DuplicateTableError on a fresh database. A
partial variant under a second name would have applied cleanly and been worse:
two indexes serving one query, both maintained on every write.

Revision ID: 0086_email_delivery_fields
Revises: 0085_bgv_inquiries
"""
from alembic import op
import sqlalchemy as sa

revision = "0086_email_delivery_fields"
down_revision = "0085_bgv_inquiries"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "email_log",
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "email_log",
        sa.Column("transport", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "email_log",
        sa.Column("template_id", sa.String(length=120), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("email_log", "template_id")
    op.drop_column("email_log", "transport")
    op.drop_column("email_log", "failed_at")
