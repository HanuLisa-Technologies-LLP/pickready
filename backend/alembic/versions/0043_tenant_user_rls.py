"""Protect tenant identity rows with row-level security.

Revision ID: 0043_tenant_user_rls
Revises: 0042_ai_reach_embeddings

``tenants`` and ``users`` were the last tenant-bearing business tables that
relied only on application filters. Authentication legitimately needs to find
all memberships for an email before a tenant is known, so auth uses the
explicit ``get_identity_session`` bypass scope. Once a tenant is selected,
ordinary org requests see only that tenant's row and users.
"""

from alembic import op

revision = "0043_tenant_user_rls"
down_revision = "0042_ai_reach_embeddings"
branch_labels = None
depends_on = None

TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"
BYPASS = "current_setting('app.bypass_rls', true) = 'on'"


def upgrade() -> None:
    for table in ("tenants", "users"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    op.execute(
        "CREATE POLICY tenants_tenant_isolation ON tenants "
        f"USING ((id = {TENANT}) OR ({BYPASS})) "
        f"WITH CHECK ((id = {TENANT}) OR ({BYPASS}))"
    )
    op.execute(
        "CREATE POLICY users_tenant_isolation ON users "
        f"USING ((tenant_id = {TENANT}) OR ({BYPASS})) "
        f"WITH CHECK ((tenant_id = {TENANT}) OR ({BYPASS}))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS users_tenant_isolation ON users")
    op.execute("ALTER TABLE users NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE users DISABLE ROW LEVEL SECURITY")
    op.execute("DROP POLICY IF EXISTS tenants_tenant_isolation ON tenants")
    op.execute("ALTER TABLE tenants NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE tenants DISABLE ROW LEVEL SECURITY")
