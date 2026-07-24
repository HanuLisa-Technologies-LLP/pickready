"""Add Firebase identity linkage while retaining application RBAC."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("firebase_uid", sa.String(128), nullable=True))
    op.add_column("users", sa.Column("auth_providers", JSONB(), nullable=False, server_default="[]"))
    op.create_unique_constraint("uq_users_firebase_uid", "users", ["firebase_uid"])


def downgrade() -> None:
    op.drop_constraint("uq_users_firebase_uid", "users", type_="unique")
    op.drop_column("users", "auth_providers")
    op.drop_column("users", "firebase_uid")
