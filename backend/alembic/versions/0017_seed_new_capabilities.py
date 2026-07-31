"""Seed the two capabilities added by the 2026-07-27 build spec.

`role_permissions` is the RBAC engine's data, and the engine DENIES anything it
has no row for (services/rbac.resolve_permission). Adding a constant to
services/capabilities.py is therefore only half the change — without these rows
`edit_company_profile` and `publish_job` would resolve to False for everyone,
including the roles the default matrix grants them to.

Grants mirror services/capabilities.DEFAULT_PERMISSION_MATRIX exactly:
  * edit_company_profile — the three staff roles and the Company Admin.
  * publish_job          — same set. On the flat model publishing is bundled
                           into create_job, but the spec's matrix lists it
                           separately so an HR Head can grant JD authorship
                           WITHOUT publishing rights (spec §7.1). Splitting the
                           capability now is what makes that grant expressible.

Only GLOBAL template rows (tenant_id IS NULL) are written. Per-tenant overrides
stay the Super Admin's to make, and the ON CONFLICT guard makes this re-runnable
against a database where an operator already added them by hand.

Revision ID: 0017_seed_new_capabilities
Revises: 0016_company_profile_email_log
"""
from alembic import op

revision = "0017_seed_new_capabilities"
down_revision = "0016_company_profile_email_log"
branch_labels = None
depends_on = None

NEW_CAPABILITIES = ("edit_company_profile", "publish_job")
GRANTED_ROLES = ("hr_manager", "recruiter", "hiring_manager", "client")


def upgrade() -> None:
    for capability in NEW_CAPABILITIES:
        for role in GRANTED_ROLES:
            op.execute(
                f"""
                INSERT INTO role_permissions (id, tenant_id, role, capability, allowed)
                VALUES (gen_random_uuid(), NULL, '{role}', '{capability}', true)
                ON CONFLICT ON CONSTRAINT uq_role_permissions DO NOTHING
                """
            )


def downgrade() -> None:
    for capability in NEW_CAPABILITIES:
        op.execute(
            f"DELETE FROM role_permissions "
            f"WHERE tenant_id IS NULL AND capability = '{capability}'"
        )
