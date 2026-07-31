"""Provision the first Business Development account.

Migration 0023 created the BD Portal's tables and capability rows, but nothing
could create a `bd` USER: every invite path in the product is tenant-scoped and
a BD user deliberately has `tenant_id = NULL`. The Owner console now has a
route for this (`/admin/bd-users`), and this migration is the bootstrap so the
fourth portal is usable the moment the stack comes up.

It follows the precedent of `0005_provision_pickready_team.py`: an idempotent
DATA migration, no password anywhere. Firebase owns credentials (claude.md rule
2), so the row is reserved in `invited` status with a NULL `firebase_uid`; the
first proven sign-in on this email binds the uid and flips the row to `active`
(`api/auth._finalize_single`). A Firebase identity therefore has to exist for
this address before the first login: create it once in the Firebase console
(Authentication, Add user) or sign in with Google if the address is a Google
account.

The address is a plus-alias of the platform owner's mailbox. It is deliberately
NOT `owner_email` itself: `api/auth.firebase_session` resolves the owner email
to the seeded super_admin before any other lookup, so a BD row on that exact
address could never sign in.
"""
from alembic import op
import sqlalchemy as sa


revision = "0024_provision_bd_account"
down_revision = "0023_business_development_portal"
branch_labels = None
depends_on = None


BD_USER_ID = "20000000-0000-4000-8000-000000000101"
BD_EMAIL = "manjuchro+bd@gmail.com"
BD_NAME = "PickReady Business Development"
BD_PHONE = "9652802233"


def upgrade() -> None:
    connection = op.get_bind()
    # NULLs do not collide under a Postgres UNIQUE constraint, so
    # uq_users_tenant_email_role cannot make this idempotent on its own.
    connection.execute(
        sa.text(
            """
            INSERT INTO users (id, tenant_id, role, email, phone, full_name, status, auth_providers)
            SELECT CAST(:id AS uuid), NULL, 'bd', CAST(:email AS varchar),
                   CAST(:phone AS varchar), CAST(:full_name AS varchar),
                   'invited', CAST('[]' AS jsonb)
            WHERE NOT EXISTS (
                SELECT 1 FROM users
                WHERE tenant_id IS NULL AND role = 'bd'
                  AND lower(email) = lower(CAST(:email AS varchar))
            )
            """
        ),
        {"id": BD_USER_ID, "email": BD_EMAIL, "phone": BD_PHONE, "full_name": BD_NAME},
    )
    # Keep the details current on a re-run without ever touching firebase_uid or
    # a status an operator has since changed (disabled must stay disabled).
    connection.execute(
        sa.text(
            """
            UPDATE users
               SET full_name = CAST(:full_name AS varchar),
                   phone = CAST(:phone AS varchar)
             WHERE tenant_id IS NULL AND role = 'bd'
               AND lower(email) = lower(CAST(:email AS varchar))
            """
        ),
        {"email": BD_EMAIL, "phone": BD_PHONE, "full_name": BD_NAME},
    )


def downgrade() -> None:
    # Remove only the seeded bootstrap account, and only while it is still
    # untouched: once someone has signed in as it, or an operator has changed
    # its status, it is a real account with real leads behind it and a
    # migration must not delete it.
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            DELETE FROM users
             WHERE id = CAST(:id AS uuid)
               AND role = 'bd'
               AND tenant_id IS NULL
               AND firebase_uid IS NULL
               AND status = 'invited'
               AND NOT EXISTS (
                   SELECT 1 FROM bd_leads WHERE owner_user_id = CAST(:id AS uuid)
               )
            """
        ),
        {"id": BD_USER_ID},
    )
