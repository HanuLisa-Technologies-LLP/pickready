"""Seed `manage_billing` and `view_billing` into the global permission template.

`role_permissions` is the RBAC engine's data and the engine DENIES anything it
has no row for (services/rbac.resolve_permission), so adding the constants to
services/capabilities.py is only half the change — without these rows the HR
Head would be refused the billing page they own.

`manage_billing` (subscribe / change plan / cancel) goes to `client` alone: it
commits the company to a recurring charge, which is the same class of act as
filing the signed agreement. `view_billing` also goes to the three staff roles,
because a recruiter whose assessment invitations have stopped sending needs to
be able to SEE that the credit pool is in deficit — telling them nothing and
letting them retry is how a paused account reads as a broken one.

Only the GLOBAL template rows (tenant_id IS NULL) are written; per-tenant
overrides stay the Owner's to make. ON CONFLICT keeps it re-runnable.

Revision ID: 0027_seed_billing_capabilities
Revises: 0026_billing_and_credit_ledger
"""
from alembic import op

revision = "0027_seed_billing_capabilities"
down_revision = "0026_billing_and_credit_ledger"
branch_labels = None
depends_on = None

GRANTS = {
    "manage_billing": ("client",),
    "view_billing": ("client", "hr_manager", "recruiter", "hiring_manager"),
}


def upgrade() -> None:
    for capability, roles in GRANTS.items():
        for role in roles:
            op.execute(
                f"""
                INSERT INTO role_permissions (id, tenant_id, role, capability, allowed)
                VALUES (gen_random_uuid(), NULL, '{role}', '{capability}', true)
                ON CONFLICT ON CONSTRAINT uq_role_permissions DO NOTHING
                """
            )


def downgrade() -> None:
    for capability in GRANTS:
        op.execute(
            f"DELETE FROM role_permissions "
            f"WHERE tenant_id IS NULL AND capability = '{capability}'"
        )
