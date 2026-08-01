"""Repair TENANT-SCOPED role_permissions rows that sit below the baseline.

Revision ID: 0032_repair_tenant_overrides
Revises: 0031_seed_full_team_access

WHAT WENT WRONG
---------------
0031 granted the full customer-side capability set to hr_manager, recruiter,
hiring_manager and client, but only on the GLOBAL template rows (tenant_id IS
NULL). `api/admin._seed_permissions` copies `capabilities.DEFAULT_PERMISSION_MATRIX`
into TENANT-SCOPED rows for every customer it creates, and a tenant-scoped row
BEATS the global template in `rbac.resolve_permission`. That dict was narrower
than 0031, so every tenant created through that path silently overrode 0031 back
down to the smaller set.

Measured on production before this migration, one tenant carried tenant-scoped
rows and was missing 30 grants against the 0031 baseline. Its `client` user, the
company's own owner, held none of view_dashboard, view_databank,
view_review_screen, decide_profile, trigger_matching, send_outreach,
upload_resumes, schedule_interviews, update_pipeline_status,
edit_job_description or add_compensation. Every screen and action behind those
answered 403 for the person who owned the account.

`services/capabilities.py` is fixed in the same change so newly created tenants
are seeded correctly. This migration repairs the rows that already exist.

WHAT THIS DELIBERATELY OVERWRITES
---------------------------------
A Super Admin CAN legitimately revoke a capability for one tenant by setting
`allowed = false` on a tenant row, and this migration flips those back to true
for the four customer roles and the 22 baseline capabilities. That is a real
trade-off, taken knowingly: the product is pre-launch, no deliberate per-tenant
revocation has been made, and every tenant row present today was written by
`_seed_permissions` from the stale template rather than by a human decision.
Per-USER revocations (`users.permissions_json`) are untouched and still win over
everything here, so an individual pin survives this.

Scope is narrow on purpose: only tenants that ALREADY have rows for the role are
touched. This never invents a tenant override where none existed, because an
absent tenant row is what lets a tenant keep tracking the global template.

Idempotent. Downgrade is a no-op: this migration cannot tell a row it corrected
from one that was already correct, and re-narrowing them would restore the
outage.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0032_repair_tenant_overrides"
down_revision = "0031_seed_full_team_access"
branch_labels = None
depends_on = None

GRANTED_ROLES = ("hr_manager", "recruiter", "hiring_manager", "client")

# Identical to 0031's list, and to capabilities._CUSTOMER_FULL_ACCESS. Hardcoded
# as string literals rather than imported so this historical migration's effect
# can never shift if the code constants are later renamed (0017/0027/0031
# convention).
GRANTED_CAPABILITIES = (
    "create_company_page",
    "manage_staff",
    "configure_approval_levels",
    "edit_job_description",
    "create_job",
    "approve_job",
    "add_compensation",
    "view_databank",
    "upload_resumes",
    "trigger_matching",
    "send_outreach",
    "view_review_screen",
    "decide_profile",
    "schedule_interviews",
    "update_pipeline_status",
    "view_dashboard",
    "manage_email_templates",
    "edit_company_profile",
    "publish_job",
    "manage_compliance_documents",
    "manage_billing",
    "view_billing",
)
# Deliberately excluded, exactly as in 0031 (security boundaries):
# edit_role_permissions, manage_bd_leads, view_bd_customers, use_ai_reach.


def upgrade() -> None:
    roles_sql = ", ".join(f"'{r}'" for r in GRANTED_ROLES)
    caps_sql = ", ".join(f"'{c}'" for c in GRANTED_CAPABILITIES)

    # 1. Correct any tenant row that exists but denies a baseline capability.
    op.execute(
        f"""
        UPDATE role_permissions SET allowed = true
        WHERE tenant_id IS NOT NULL
          AND role IN ({roles_sql})
          AND capability IN ({caps_sql})
          AND allowed = false
        """
    )

    # 2. Insert the baseline capabilities that are missing entirely for a
    #    (tenant, role) pair that is ALREADY overridden. The EXISTS guard is
    #    what keeps this from creating overrides for tenants that correctly
    #    inherit the global template.
    op.execute(
        f"""
        INSERT INTO role_permissions (id, tenant_id, role, capability, allowed)
        SELECT gen_random_uuid(), t.tenant_id, t.role, c.capability, true
        FROM (
            SELECT DISTINCT tenant_id, role
            FROM role_permissions
            WHERE tenant_id IS NOT NULL AND role IN ({roles_sql})
        ) t
        CROSS JOIN (SELECT unnest(ARRAY[{caps_sql}]) AS capability) c
        WHERE NOT EXISTS (
            SELECT 1 FROM role_permissions rp
            WHERE rp.tenant_id = t.tenant_id
              AND rp.role = t.role
              AND rp.capability = c.capability
        )
        """
    )


def downgrade() -> None:
    # Intentional no-op. See the module docstring: re-narrowing these rows would
    # restore the 403s this migration exists to clear.
    pass
