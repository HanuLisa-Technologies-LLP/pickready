"""email_log records the From address and where it came from.

Revision ID: 0139_email_log_from_identity
Revises: 0138_background_jobs

Register I-20: mail sent on a company's behalf leaves from the company's
active sender, or, when the company has none, from the platform address under
the company name. The second case is a fallback and is recorded on the row,
not left to be inferred: `from_source` is `company_sender`,
`platform_fallback`, `platform` or `security`.

Both columns are nullable on purpose. Rows sent before this migration do not
know their From identity, and back-filling a guess would make an unknown look
like a recorded fact. No RLS change: the columns sit on a table that already
has its policy.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0139_email_log_from_identity"
down_revision = "0138_background_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("email_log", sa.Column("from_email", sa.String(320), nullable=True))
    op.add_column("email_log", sa.Column("from_source", sa.String(30), nullable=True))


def downgrade() -> None:
    op.drop_column("email_log", "from_source")
    op.drop_column("email_log", "from_email")
