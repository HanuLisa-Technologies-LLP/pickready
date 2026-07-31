"""Grant application access and enforce tenant RLS on assessment tables.

Revision ID: 0013_assessment_rls
Revises: 0012_functional_skills
"""
from alembic import op

revision = "0013_assessment_rls"
down_revision = "0012_functional_skills"
branch_labels = None
depends_on = None

TABLES = (
    "technical_questions",
    "assessment_conversations",
    "assessment_messages",
    "functional_skills_reports",
    "report_dimensions",
)


def upgrade() -> None:
    for table in TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO pickready_app")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            USING (
                tenant_id = current_setting('app.tenant_id', true)::uuid
                OR current_setting('app.bypass_rls', true) = 'on'
            )
            WITH CHECK (
                tenant_id = current_setting('app.tenant_id', true)::uuid
                OR current_setting('app.bypass_rls', true) = 'on'
            )
            """
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.execute(f"REVOKE SELECT, INSERT, UPDATE, DELETE ON {table} FROM pickready_app")
