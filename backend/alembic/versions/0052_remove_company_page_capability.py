"""Retire the legacy Company Page permission.

Revision ID: 0052_remove_company_page
Revises: 0051_role_hierarchy_permissions

Company Profile remains the sole company-information surface. The legacy
content columns stay in place so retiring the UI is non-destructive, but no
role or user can retain the obsolete capability.
"""
from alembic import op

revision = "0052_remove_company_page"
down_revision = "0051_role_hierarchy_permissions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM role_permissions WHERE capability = 'create_company_page'"
    )


def downgrade() -> None:
    # Historical migrations retain the former defaults if a rollback is ever
    # required. Avoid guessing tenant-specific overrides that were deleted.
    pass
