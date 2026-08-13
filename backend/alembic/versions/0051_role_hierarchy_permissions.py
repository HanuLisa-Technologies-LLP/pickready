"""Remove staff-management access from the bottom hierarchy tier.

Revision ID: 0051_role_hierarchy_permissions
Revises: 0050_role_hierarchy
"""
from alembic import op

revision = "0051_role_hierarchy_permissions"
down_revision = "0050_role_hierarchy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE role_permissions
        SET allowed = false
        WHERE role = 'hiring_manager' AND capability = 'manage_staff'
        """
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE role_permissions
        SET allowed = true
        WHERE role = 'hiring_manager' AND capability = 'manage_staff'
        """
    )
