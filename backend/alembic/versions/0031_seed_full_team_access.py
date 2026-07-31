"""Full recruitment-team access re-seed for role_permissions.

Revision ID: 0031_seed_full_team_access
Revises: 0030_ppi_framework

Grants the complete customer-side capability set to the four roles that run a
customer's hiring and pay for it: hr_manager, recruiter, hiring_manager, client.
It makes the three staff roles plus the Company Admin functionally identical
across every capability EXCEPT the ones that are genuine security boundaries,
and it re-affirms grants that earlier piecemeal seed migrations (0017, 0027, ...)
established, so a database that missed any of them converges to one known state.

WHY NOT literally "every capability" (this is a deliberate, flagged decision):
  * edit_role_permissions is Owner-only. Granting it to a recruiter lets them
    rewrite the permission matrix and self-escalate to anything; that is not
    "access to run hiring", it is removal of the boundary itself.
  * manage_bd_leads / view_bd_customers / use_ai_reach are the platform Business
    Development console's capabilities. A `bd` user has tenant_id NULL and the
    OWNER token audience; these grants are inert for a tenant-scoped recruiter
    and semantically wrong. They are left to the `bd` role on purpose.
If you truly want either set on these four roles, add the names to
GRANTED_CAPABILITIES and write a follow-up migration.

Idempotent WITHOUT relying on ON CONFLICT. The unique constraint is
(tenant_id, role, capability) with the default NULLS DISTINCT, so two
tenant_id IS NULL rows for the same (role, capability) do NOT collide and
ON CONFLICT never fires for global rows -- it would silently duplicate on every
run. Upgrade therefore (1) flips any existing global grant to true, (2) collapses
duplicate global rows that this or an earlier NULL-scoped seed left behind, and
(3) inserts only the pairs still absent. Rerunning is a genuine no-op.
tenant_id NULL is the global default template every tenant inherits (rows
resolved by services/rbac.py, which already dedupes by dict-keying on
capability).

Downgrade is intentionally a no-op: these same global-template rows are also
created by several earlier migrations, and this migration cannot tell a row it
created from one it merely re-affirmed. Deleting them would silently break the
flat staff model for a database that legitimately depends on them.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0031_seed_full_team_access"
down_revision = "0030_ppi_framework"
branch_labels = None
depends_on = None

GRANTED_ROLES = ("hr_manager", "recruiter", "hiring_manager", "client")

# The full capability roster from services/capabilities.ALL_CAPABILITIES, MINUS
# the four boundary capabilities named in the docstring. Hardcoded as string
# literals rather than imported, so this historical migration's effect can never
# shift if the code constants are later renamed (same convention as 0017/0027).
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
# Deliberately excluded (security boundaries): edit_role_permissions,
# manage_bd_leads, view_bd_customers, use_ai_reach.


def upgrade() -> None:
    roles_sql = ", ".join(f"'{r}'" for r in GRANTED_ROLES)
    caps_sql = ", ".join(f"'{c}'" for c in GRANTED_CAPABILITIES)

    # 1. Flip any existing global grant for these (role, capability) pairs to
    #    true, so a row previously seeded allowed=false is corrected in place.
    op.execute(
        f"""
        UPDATE role_permissions SET allowed = true
        WHERE tenant_id IS NULL
          AND role IN ({roles_sql})
          AND capability IN ({caps_sql})
        """
    )

    # 2. Collapse duplicate global rows down to one per (role, capability). The
    #    NULLS DISTINCT constraint never blocked them (see the module docstring),
    #    so re-runs and overlapping earlier seeds could leave several. ctid is
    #    the physical row id; keeping the greatest deletes all but one. Scoped to
    #    exactly the rows this migration governs.
    op.execute(
        f"""
        DELETE FROM role_permissions a
        USING role_permissions b
        WHERE a.tenant_id IS NULL AND b.tenant_id IS NULL
          AND a.role = b.role AND a.capability = b.capability
          AND a.role IN ({roles_sql})
          AND a.capability IN ({caps_sql})
          AND a.ctid < b.ctid
        """
    )

    # 3. Insert only the pairs still entirely absent. NOT EXISTS is the guard the
    #    constraint cannot be for NULL tenant_id.
    for role in GRANTED_ROLES:
        for capability in GRANTED_CAPABILITIES:
            op.execute(
                f"""
                INSERT INTO role_permissions (id, tenant_id, role, capability, allowed)
                SELECT gen_random_uuid(), NULL, '{role}', '{capability}', true
                WHERE NOT EXISTS (
                    SELECT 1 FROM role_permissions
                    WHERE tenant_id IS NULL
                      AND role = '{role}' AND capability = '{capability}'
                )
                """
            )


def downgrade() -> None:
    # Intentional no-op. See the module docstring: these global-template rows are
    # also created by earlier seed migrations, and removing them here would break
    # the flat staff model for a database that legitimately depends on them.
    pass
