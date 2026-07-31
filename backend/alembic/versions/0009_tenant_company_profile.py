"""Tenant company profile: industry, culture, details.

Captured by the Owner console at onboarding and shown on the org Company page.
The legacy `domain` / `spf_dkim_status` columns are intentionally LEFT IN PLACE
(harmless; `domain` is still the stable UNIQUE key) — they are simply no longer
collected in the UI, since outbound mail is provider-agnostic SMTP and there is
no per-tenant sending domain to verify (claude.md rule 5).

Revision ID: 0009_tenant_company_profile
Revises: 0008_staff_invites
"""
from alembic import op
import sqlalchemy as sa

revision = "0009_tenant_company_profile"
down_revision = "0008_staff_invites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenants", sa.Column("industry", sa.String(length=100), nullable=True))
    op.add_column("tenants", sa.Column("culture", sa.Text(), nullable=True))
    op.add_column("tenants", sa.Column("details", sa.Text(), nullable=True))

    # Backfill: existing tenants pre-date the profile fields, so give them a
    # neutral, truthful industry rather than leaving the console showing a
    # blank slate. Culture/details stay NULL — the UI prompts for them.
    op.execute("UPDATE tenants SET industry = 'Other' WHERE industry IS NULL")


def downgrade() -> None:
    for column in ("details", "culture", "industry"):
        op.drop_column("tenants", column)
