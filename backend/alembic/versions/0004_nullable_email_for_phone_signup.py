"""Allow NULL email so phone-only Firebase candidate signup can persist.

Candidates may sign up with a phone number and no email (Firebase phone
provider). The users/candidates email columns were NOT NULL; relax them so a
phone-only candidate row is legal. The (tenant_id, email, role) uniqueness on
users is unaffected — Postgres treats NULLs as distinct, so multiple phone-only
candidates coexist without collision.
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("users", "email", existing_type=sa.String(320), nullable=True)
    op.alter_column("candidates", "email", existing_type=sa.String(320), nullable=True)


def downgrade() -> None:
    op.alter_column("candidates", "email", existing_type=sa.String(320), nullable=False)
    op.alter_column("users", "email", existing_type=sa.String(320), nullable=False)
