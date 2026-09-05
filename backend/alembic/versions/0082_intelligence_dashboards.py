"""Seed the Talent Intelligence dashboards capability (2026-09-05 spec).

Revision ID: 0082
Revises: 0081_dual_mode_assessment

CHAIN NOTE: this migration was authored in a parallel wave. Its revision id is
the bare string "0082" because migration 0083 (retention consents), written in
the same wave, already names "0082" as its down_revision; the down_revision
here points at 0081's full id ("0081_dual_mode_assessment"), which is what
that migration declares as its revision.

WHY THERE IS NO TABLE IN THIS MIGRATION
---------------------------------------
The Talent Intelligence spec's telemetry store (`telemetry_events`, section
5.1) was already created by migration 0073 with the exact shape the spec asks
for: tenant-scoped RLS, the (tenant_id, event_code, occurred_at) index, the
job_id index, and JSONB payloads. One implementation per concept (claude.md),
so this migration does not create a second one. What the dashboard suite adds
at the schema level is exactly one thing: the capability rows below.

WHAT IS SEEDED
--------------
`view_intelligence_dashboards`, the grant behind GET /intelligence/*. The
grant engine reads ROWS, not the code matrix, so a capability constant added
to services/capabilities.py without this migration resolves to False for
every role on a migrations-only database (the 0075 defect, and its rule: a
capability constant is only HALF a change). Granted True to the four
hiring-side org roles plus the Client Super Admin; an explicit False row is
seeded for the Interview Manager so the template state is observable, matching
what seed_dev_data reconciles to (same convention as 0075/0080).

Values are string literals rather than imports so this historical migration's
effect can never shift if the code constants are renamed (0017/0027/0031/0075
convention). `tests/test_capability_seed_parity.py` compares the migrated
database against the code matrix.

Idempotent WITHOUT relying on ON CONFLICT, for the reason 0031 documents: the
unique constraint is (tenant_id, role, capability) with NULLS DISTINCT, so
global rows never collide and ON CONFLICT never fires for them.

Downgrade is a no-op, as for every earlier seed migration: this migration
cannot tell a row it created from one seed_dev_data created.
"""
from alembic import op

revision = "0082"
down_revision = "0081_dual_mode_assessment"
branch_labels = None
depends_on = None

SEED_ROWS: list[tuple[str, str, bool]] = [
    ("client", "view_intelligence_dashboards", True),
    ("recruitment_manager", "view_intelligence_dashboards", True),
    ("hr_manager", "view_intelligence_dashboards", True),
    ("recruiter", "view_intelligence_dashboards", True),
    ("hiring_manager", "view_intelligence_dashboards", True),
    ("interview_manager", "view_intelligence_dashboards", False),
]


def upgrade() -> None:
    for role, capability, allowed in SEED_ROWS:
        allowed_sql = "true" if allowed else "false"
        # 1. Reconcile any existing global row to this table's value.
        op.execute(
            f"""
            UPDATE role_permissions SET allowed = {allowed_sql}
            WHERE tenant_id IS NULL
              AND role = '{role}' AND capability = '{capability}'
              AND allowed IS DISTINCT FROM {allowed_sql}
            """
        )
        # 2. Collapse duplicates the NULLS DISTINCT constraint never blocked.
        op.execute(
            f"""
            DELETE FROM role_permissions a
            USING role_permissions b
            WHERE a.tenant_id IS NULL AND b.tenant_id IS NULL
              AND a.role = b.role AND a.capability = b.capability
              AND a.role = '{role}' AND a.capability = '{capability}'
              AND a.ctid < b.ctid
            """
        )
        # 3. Insert the pair if still absent.
        op.execute(
            f"""
            INSERT INTO role_permissions (id, tenant_id, role, capability, allowed)
            SELECT gen_random_uuid(), NULL, '{role}', '{capability}', {allowed_sql}
            WHERE NOT EXISTS (
                SELECT 1 FROM role_permissions
                WHERE tenant_id IS NULL
                  AND role = '{role}' AND capability = '{capability}'
            )
            """
        )


def downgrade() -> None:
    # Intentional no-op; see the module docstring.
    pass
