"""Seed `manage_compliance_documents` into the global permission template.

`role_permissions` is the RBAC engine's data and the engine DENIES anything it
has no row for (services/rbac.resolve_permission), so adding the constant to
services/capabilities.py is only half the change — without this row the HR Head
would be refused their own upload endpoint.

Granted to `client` (Company Admin / HR Head) ONLY, mirroring
DEFAULT_PERMISSION_MATRIX. The three staff roles are otherwise interchangeable
under the flat model, but a GSTIN certificate and a signed agreement are the
company's legal instruments rather than recruitment data — and the Provider
Portal shows them to the platform owner. An HR Head who does want to delegate
can still grant it to one person through the per-user overlay
(`users.permissions_json`), which is exactly what that layer is for.

Only the GLOBAL template row (tenant_id IS NULL) is written; per-tenant
overrides stay the Owner's to make. ON CONFLICT keeps it re-runnable.

Revision ID: 0021_seed_compliance_capability
Revises: 0020_provider_portal
"""
from alembic import op

revision = "0021_seed_compliance_capability"
down_revision = "0020_provider_portal"
branch_labels = None
depends_on = None

CAPABILITY = "manage_compliance_documents"
GRANTED_ROLES = ("client",)


def upgrade() -> None:
    for role in GRANTED_ROLES:
        op.execute(
            f"""
            INSERT INTO role_permissions (id, tenant_id, role, capability, allowed)
            VALUES (gen_random_uuid(), NULL, '{role}', '{CAPABILITY}', true)
            ON CONFLICT ON CONSTRAINT uq_role_permissions DO NOTHING
            """
        )


def downgrade() -> None:
    op.execute(
        f"DELETE FROM role_permissions "
        f"WHERE tenant_id IS NULL AND capability = '{CAPABILITY}'"
    )
