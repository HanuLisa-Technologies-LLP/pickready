"""The route scrap's database half: three capabilities and their grants go.

Revision ID: 0129_route_scrap
Revises: 0128_legacy_scrap

The Vivekium release (PLAN-p7 WP-B6) deleted a set of routes nothing called,
and three capabilities were read by those routes and by nothing else:

* `approve_job` and `configure_approval_levels`, read only by the
  multi-level job approval chain (the job-side approve route, deleted in
  Phase 1, and `PUT /companies/me/approval-levels`, deleted here), whose
  planner and persistence functions went with them;
* `manage_email_templates`, read only by the company email-template editor
  (`/companies/me/email-templates`), which lost its last screen in Phase 6.

A capability constant and its seeding migration are one change, and so is
their removal: rows outliving their constant are grants nobody can see, and
`test_capability_seed_parity` compares the matrix against exactly these
rows. So every `role_permissions` row for the three, GLOBAL and PER-TENANT,
is deleted. The per-tenant rows were copied from the matrix by the Owner
console's customer onboarding and by migration 0032, and override a route
that no longer exists.

Nothing else is touched. `companies.approval_levels_config` holds data a
customer typed and stays; the `email_templates` table is read by the
delivery path's named templates and stays.

DOWNGRADE re-seeds the GLOBAL rows as they stood at 0118 (every customer
role allowed, which is what 0017 and 0031 wrote). Per-tenant rows are not
restored: which tenants carried an override cannot be known after they are
gone, and every one of them overrode a route that no longer exists.
"""
from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

revision = "0129_route_scrap"
down_revision = "0128_legacy_scrap"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

#: Restated as of this revision. `tests/test_dead_routes_removed.py` asserts none of
#: them survives in `capabilities.py`, the matrix or the migrated database.
RETIRED_CAPABILITIES: tuple[str, ...] = (
    "approve_job",
    "configure_approval_levels",
    "manage_email_templates",
)

#: The global grants each retired capability carried at 0118, for the
#: downgrade only: allowed for all five customer roles.
_PREVIOUS_GLOBAL_ROLES: tuple[str, ...] = (
    "client",
    "hr_manager",
    "recruitment_manager",
    "recruiter",
    "hiring_manager",
)


def delete_retired_capabilities(connection: sa.engine.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for capability in RETIRED_CAPABILITIES:
        counts[capability] = connection.execute(
            sa.text("DELETE FROM role_permissions WHERE capability = :capability"),
            {"capability": capability},
        ).rowcount
    return counts


def upgrade() -> None:
    counts = delete_retired_capabilities(op.get_bind())
    logger.info(
        "0129 role_permissions_deleted %s",
        " ".join(f"{name}={count}" for name, count in counts.items()),
    )


def downgrade() -> None:
    connection = op.get_bind()
    for capability in RETIRED_CAPABILITIES:
        for role in _PREVIOUS_GLOBAL_ROLES:
            connection.execute(
                sa.text(
                    "INSERT INTO role_permissions "
                    "(id, tenant_id, role, capability, allowed) "
                    "SELECT gen_random_uuid(), NULL, :role, :capability, true "
                    "WHERE NOT EXISTS ("
                    "  SELECT 1 FROM role_permissions "
                    "  WHERE tenant_id IS NULL AND role = :role "
                    "    AND capability = :capability)"
                ),
                {"role": role, "capability": capability},
            )
