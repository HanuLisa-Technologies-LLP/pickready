"""Rev 2 role-model corrections + 4-parameter match breakdown storage.

- job_candidate_links.match_breakdown_json (JSONB, NULL): the 4-parameter
  scoring breakdown (API contract rev 2 "Matching results" block).
  Intentionally NOT on the SQLAlchemy model — the matching worker writes it
  via raw SQL, same pattern as jobs.embedding in 0001.
- role_permissions: `create_hiring_managers` renamed in place to
  `manage_staff` (staff management now covers all 3 client-org sub-roles).
- users: single-Owner cleanup — only manjuchro@gmail.com may hold the
  super_admin (Owner) role.
- users: demo-staff email re-parent — HR/Recruiter are client-org members,
  not Hanulisa staff, so the misleading @hanulisa.com demo emails move to
  @acme.example.com (guarded exact matches; tenant_id untouched).

Storage note: users.role / role_permissions.role are varchar-backed enums
(sa.String(30) columns; SQLAlchemy Enum(native_enum=False) stores the member
name, which equals the value for these str-enums) — so the literals below
('super_admin', 'hr_manager', 'recruiter') match the stored format in 0001.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 4-parameter match breakdown (API contract rev 2) ────────────────────
    op.add_column(
        "job_candidate_links",
        sa.Column("match_breakdown_json", JSONB(), nullable=True),
    )

    # ── Capability rename: create_hiring_managers → manage_staff ────────────
    # Guard first: if a manage_staff row already exists for the same
    # (tenant_id, role), renaming would violate uq_role_permissions — drop the
    # stale create_hiring_managers row instead of renaming it.
    op.execute(
        """
        DELETE FROM role_permissions rp
        WHERE rp.capability = 'create_hiring_managers'
          AND EXISTS (
            SELECT 1 FROM role_permissions x
            WHERE x.capability = 'manage_staff'
              AND x.role = rp.role
              AND x.tenant_id IS NOT DISTINCT FROM rp.tenant_id
          )
        """
    )
    op.execute(
        "UPDATE role_permissions SET capability = 'manage_staff' "
        "WHERE capability = 'create_hiring_managers'"
    )

    # ── Single-Owner rule (rev 2) ───────────────────────────────────────────
    # Exactly ONE Owner account: settings.owner_email (manjuchro@gmail.com).
    # Any other super_admin row (e.g. the old admin@hanulisa.com dev seed) is
    # removed; dependent rows follow the FK rules (otp_challenges CASCADE,
    # jobs.created_by SET NULL).
    op.execute(
        "DELETE FROM users "
        "WHERE role = 'super_admin' AND email != 'manjuchro@gmail.com'"
    )

    # ── Demo-staff email re-parent (guarded exact matches only) ─────────────
    # These demo users already carry the correct tenant_id — only the
    # misleading Hanulisa-staff emails change. NOT EXISTS guards the
    # uq_users_tenant_email_role unique constraint.
    op.execute(
        """
        UPDATE users u SET email = 'hr1@acme.example.com'
        WHERE u.email = 'hr1@hanulisa.com' AND u.role = 'hr_manager'
          AND NOT EXISTS (
            SELECT 1 FROM users x
            WHERE x.email = 'hr1@acme.example.com' AND x.role = 'hr_manager'
              AND x.tenant_id IS NOT DISTINCT FROM u.tenant_id
          )
        """
    )
    op.execute(
        """
        UPDATE users u SET email = 'rec1@acme.example.com'
        WHERE u.email = 'rec1@hanulisa.com' AND u.role = 'recruiter'
          AND NOT EXISTS (
            SELECT 1 FROM users x
            WHERE x.email = 'rec1@acme.example.com' AND x.role = 'recruiter'
              AND x.tenant_id IS NOT DISTINCT FROM u.tenant_id
          )
        """
    )


def downgrade() -> None:
    op.drop_column("job_candidate_links", "match_breakdown_json")
    # The data transforms above (capability rename, Owner cleanup, demo email
    # re-parent) are intentionally IRREVERSIBLE: the pre-migration state was
    # incorrect data under the superseded role model, not a schema variant —
    # there is nothing meaningful to restore on downgrade.
