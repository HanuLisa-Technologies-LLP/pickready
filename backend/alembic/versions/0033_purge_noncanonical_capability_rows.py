"""Purge non-canonical `role_permissions.capability` rows, and stop them returning.

Revision ID: 0033_purge_noncanonical_caps
Revises: 0032_repair_tenant_overrides

WHAT WENT WRONG
---------------
Ten global rows in `role_permissions` carried the Python CONSTANT NAME instead of
the constant's VALUE: `CREATE_JOB` rather than `create_job`, `VIEW_DASHBOARD`
rather than `view_dashboard`, and so on. They were hand-inserted with psql during
an earlier repair, which is why they survived a database migrated to head.

Measured on production before this migration:

    client         VIEW_DASHBOARD
    hiring_manager VIEW_DASHBOARD
    hr_manager     CREATE_JOB, EDIT_COMPANY_PROFILE, PUBLISH_JOB,
                   VIEW_ASSESSMENTS, VIEW_BILLING, VIEW_DASHBOARD,
                   WRITE_ASSESSMENTS
    recruiter      VIEW_DASHBOARD

Every one of them is INERT. `require_capability(caps.X)` resolves the constant to
its lowercase value before the lookup, so nothing has ever matched an uppercase
row, and deleting them changes no user's access. This migration is a cleanup, not
a repair, and it is deliberately separated from 0032 for that reason.

WHY BOTHER, IF THEY DO NOTHING
------------------------------
Two of them are worse than noise. `VIEW_ASSESSMENTS` and `WRITE_ASSESSMENTS` are
not capabilities at all: they appear nowhere in `capabilities.ALL_CAPABILITIES`
and nowhere in a `require_capability` call. Left in the table they read as
evidence that the assessment routes are permission-gated on a grant that was
never seeded, which is a false lead costing an hour to every future reader
debugging "assessments not available". The grant is not the problem; the rows
only look like it.

THE CONSTRAINT IS THE POINT
---------------------------
Deleting the rows fixes today. The CHECK stops tomorrow. Every canonical
capability in `services/capabilities.py` is lowercase snake_case, so
`capability = lower(capability)` costs nothing and refuses the next hand-written
INSERT that pastes a constant name. A capability that cannot be spelled wrongly
cannot be seeded wrongly.

The CHECK is deliberately weaker than "must be one of ALL_CAPABILITIES": pinning
the enumerated list into the schema would mean an ALTER TABLE for every new
capability, and a historical migration must not constrain what a later release
may add. Case is the invariant that never changes.

Idempotent: the DELETE is a no-op on a clean database and the CHECK is created
only when absent. Downgrade drops the CHECK and does NOT reinsert the rows,
because restoring known-junk data has no value.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0033_purge_noncanonical_caps"
down_revision = "0032_repair_tenant_overrides"
branch_labels = None
depends_on = None

CONSTRAINT_NAME = "ck_role_permissions_capability_lowercase"


def upgrade() -> None:
    # Runs before the CHECK is added, or the CHECK would refuse to validate
    # against the very rows it exists to eliminate.
    op.execute(
        """
        DELETE FROM role_permissions
        WHERE capability <> lower(capability)
        """
    )

    # DO block rather than op.create_check_constraint: Postgres has no
    # ADD CONSTRAINT IF NOT EXISTS, and a bare create fails on a database where
    # this migration has already run once.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = '{CONSTRAINT_NAME}'
            ) THEN
                ALTER TABLE role_permissions
                    ADD CONSTRAINT {CONSTRAINT_NAME}
                    CHECK (capability = lower(capability));
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE role_permissions DROP CONSTRAINT IF EXISTS {CONSTRAINT_NAME}"
    )
