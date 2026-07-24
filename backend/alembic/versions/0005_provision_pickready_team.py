"""Provision the permanent PickReady development team roster.

This is deliberately an idempotent *data migration*, not an executable seed
script.  It creates the three requested client tenants and their staff rows in
PostgreSQL exactly once, preserving any Firebase UID subsequently linked at
sign-in.  Passwords are intentionally absent: Firebase Authentication owns
password hashes and no application database migration may store them.
"""
from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


TENANTS = (
    ("10000000-0000-4000-8000-000000000001", "Sarkar Corp", "sarkar-corp.local"),
    ("10000000-0000-4000-8000-000000000002", "ACRM Corp", "acrm-corp.local"),
    ("10000000-0000-4000-8000-000000000003", "Specter & Co.", "specter-co.local"),
)

# id, tenant domain (None for the platform owner), role, name, email, phone
USERS = (
    ("20000000-0000-4000-8000-000000000001", None, "super_admin", "Platform Owner", "manjuchro@gmail.com", "9652802233"),
    ("20000000-0000-4000-8000-000000000002", "sarkar-corp.local", "hr_manager", "Sarkar HR Manager", "saravankumar2503@gmail.com", "9398687358"),
    ("20000000-0000-4000-8000-000000000003", "sarkar-corp.local", "recruiter", "Sarkar Recruiter", "sarkar120806@gmail.com", "9652802233"),
    ("20000000-0000-4000-8000-000000000004", "sarkar-corp.local", "hiring_manager", "Sarkar Hiring Manager", "126004238@sastra.ac.in", None),
    ("20000000-0000-4000-8000-000000000005", "acrm-corp.local", "hr_manager", "ACRM HR Manager", "manjuhr.m@gmail.com", None),
    ("20000000-0000-4000-8000-000000000006", "acrm-corp.local", "recruiter", "ACRM Recruiter", "kvsr101112@gmail.com", None),
    ("20000000-0000-4000-8000-000000000007", "acrm-corp.local", "hiring_manager", "ACRM Hiring Manager", "saravankumarmk@gmail.com", None),
    ("20000000-0000-4000-8000-000000000008", "specter-co.local", "hr_manager", "Specter HR Manager", "manjuhr@outlook.com", None),
    ("20000000-0000-4000-8000-000000000009", "specter-co.local", "recruiter", "Specter Recruiter", "126004238@sastra.ac.in", "9398687358"),
    ("20000000-0000-4000-8000-000000000010", "specter-co.local", "hiring_manager", "Specter Hiring Manager", "saravankumarmk@gmail.com", "9652802233"),
)


def upgrade() -> None:
    connection = op.get_bind()
    for tenant_id, name, domain in TENANTS:
        connection.execute(
            sa.text(
                """
                INSERT INTO tenants (id, name, domain, spf_dkim_status)
                VALUES (CAST(:id AS uuid), CAST(:name AS varchar), CAST(:domain AS varchar), 'pending')
                ON CONFLICT (domain) DO UPDATE SET name = EXCLUDED.name
                """
            ),
            {"id": tenant_id, "name": name, "domain": domain},
        )

    for user_id, domain, role, full_name, email, phone in USERS:
        if domain is None:
            # Nulls do not collide under PostgreSQL UNIQUE constraints, so the
            # Owner needs an explicit lookup to retain its singleton identity.
            connection.execute(
                sa.text(
                    """
                    INSERT INTO users (id, tenant_id, role, email, phone, full_name, status, auth_providers)
                    SELECT CAST(:id AS uuid), NULL, CAST(:role AS varchar), CAST(:email AS varchar), CAST(:phone AS varchar), CAST(:full_name AS varchar), 'active', CAST('[]' AS jsonb)
                    WHERE NOT EXISTS (
                        SELECT 1 FROM users
                        WHERE tenant_id IS NULL AND role = CAST(:role AS varchar) AND lower(email) = lower(CAST(:email AS varchar))
                    )
                    """
                ),
                {"id": user_id, "role": role, "email": email, "phone": phone, "full_name": full_name},
            )
            connection.execute(
                sa.text(
                    """
                    UPDATE users SET full_name = CAST(:full_name AS varchar), phone = CAST(:phone AS varchar), status = 'active'
                    WHERE tenant_id IS NULL AND role = CAST(:role AS varchar) AND lower(email) = lower(CAST(:email AS varchar))
                    """
                ),
                {"role": role, "email": email, "phone": phone, "full_name": full_name},
            )
            continue

        connection.execute(
            sa.text(
                """
                INSERT INTO users (id, tenant_id, role, email, phone, full_name, status, auth_providers)
                SELECT CAST(:id AS uuid), t.id, CAST(:role AS varchar), CAST(:email AS varchar), CAST(:phone AS varchar), CAST(:full_name AS varchar), 'active', CAST('[]' AS jsonb)
                FROM tenants t
                WHERE t.domain = CAST(:domain AS varchar)
                  AND NOT EXISTS (
                    SELECT 1 FROM users u
                    WHERE u.tenant_id = t.id AND u.role = CAST(:role AS varchar) AND lower(u.email) = lower(CAST(:email AS varchar))
                  )
                """
            ),
            {
                "id": user_id, "domain": domain, "role": role, "email": email,
                "phone": phone, "full_name": full_name,
            },
        )

    # Hiring-manager assignments are required by the approval workflow and are
    # safe to provision alongside the corresponding permanent user records.
    connection.execute(
        sa.text(
            """
            INSERT INTO hiring_managers (id, tenant_id, user_id, approval_level)
            SELECT
                u.id, u.tenant_id, u.id, NULL
            FROM users u
            WHERE u.role = 'hiring_manager'
              AND u.id IN (
                CAST('20000000-0000-4000-8000-000000000004' AS uuid),
                CAST('20000000-0000-4000-8000-000000000007' AS uuid),
                CAST('20000000-0000-4000-8000-000000000010' AS uuid)
              )
              AND NOT EXISTS (
                SELECT 1 FROM hiring_managers hm
                WHERE hm.tenant_id = u.tenant_id AND hm.user_id = u.id
              )
            """
        )
    )


def downgrade() -> None:
    # The rows are intentionally retained: they are explicit developer-provided
    # identities, not disposable test fixtures. Removing accounts on downgrade
    # would be a destructive and surprising authentication side effect.
    pass
