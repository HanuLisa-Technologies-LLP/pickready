"""The four-level customer-portal hierarchy.

Revision ID: 0050_role_hierarchy
Revises: 0049_job_setup_agents

Spec §29:

    Super Admin -> Recruitment Manager -> Recruiter -> Hiring Manager

with each higher role deciding the permissions of the role beneath it.

WHAT THIS REVERSES
------------------
CLAUDE.md rule 3 recorded a FLAT staff model as non-negotiable: HR Manager,
Recruiter and Hiring Manager equal, all three holding the same operational
capability set. That was the client's decision in 2026-07-24 and this is the
client's decision now. The rule is rewritten rather than quietly worked around.

WHY THIS IS ONLY TWO COLUMNS
----------------------------
`users.role` is a varchar, not a Postgres enum, so a new role needs no type
change. And the hierarchy itself is expressed as DATA the existing capability
engine already reads: `users.permissions_json` is a sparse per-user overlay, so
"a Recruitment Manager decides a Recruiter's permissions" is that manager
writing that overlay, checked against a rank ordering in
`services/role_hierarchy`. No new permission table, and `require_capability(...)`
stays the only authorisation call anywhere (CLAUDE.md rule 3's surviving half).

`manager_user_id` records who a person reports to inside the tenant. It is
nullable and ON DELETE SET NULL: a manager leaving must not cascade away their
whole team, and a user whose manager is gone falls back to the rank rule alone.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0050_role_hierarchy"
down_revision = "0049_job_setup_agents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "manager_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_users_manager", "users", ["manager_user_id"])

    # A person cannot report to themselves. Cheap to state, and the shape of
    # mistake an "assign manager" form makes on its first day.
    op.create_check_constraint(
        "ck_users_manager_not_self",
        "users",
        "manager_user_id IS NULL OR manager_user_id <> id",
    )

    # Seed the new role into the capability matrix by COPYING the row an
    # HR Manager already has in each tenant, which is the tier a Recruitment
    # Manager replaces. Copying rather than inventing means an existing
    # customer's per-tenant overrides carry across instead of being silently
    # reset to a template nobody chose.
    op.execute(
        """
        INSERT INTO role_permissions (id, tenant_id, role, capability, allowed)
        SELECT gen_random_uuid(), tenant_id, 'recruitment_manager', capability, allowed
        FROM role_permissions
        WHERE role = 'hr_manager'
          AND NOT EXISTS (
            SELECT 1 FROM role_permissions existing
            WHERE existing.role = 'recruitment_manager'
              AND existing.capability = role_permissions.capability
              AND existing.tenant_id IS NOT DISTINCT FROM role_permissions.tenant_id
          )
        """
    )

def downgrade() -> None:
    op.execute("DELETE FROM role_permissions WHERE role = 'recruitment_manager'")
    op.drop_constraint("ck_users_manager_not_self", "users", type_="check")
    op.drop_index("ix_users_manager", table_name="users")
    op.drop_column("users", "manager_user_id")
