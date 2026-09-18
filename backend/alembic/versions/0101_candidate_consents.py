"""The per-item consent record: one row per candidate per catalogue item.

Revision ID: 0101_candidate_consents
Revises: 0100_consent_lifecycle_columns

`VIVEKIUM_SPRINT_FEATURES.md` feature 6. The catalogue itself (the wording,
the two stages, the seven keys) lives in `services/consent_catalog.py`; this
table holds only WHICH item a candidate consented to and WHEN, individually
timestamped as the brief requires.

WHY UNIQUE (candidate_id, item_key). The row answers "does this consent
stand, since when". A re-affirmation (the six-month renewal, an updated
acceptance) moves `consented_at` in place rather than appending, because two
live rows for one item would make "since when" ambiguous. The history of
acts stays in `audit_log`, where history lives.

WHY THERE IS NO `stage` CHECK against the catalogue. The catalogue is Python
data; a database CHECK enumerating its keys is a second copy that drifts the
day an item is added. `record_items` refuses an unknown key at the only
write path.

TENANT-FREE, NO RLS, like `candidates` and `candidate_employments` (0095): a
consent is the candidate's own act, reached through their session or the
audited bypass, and a tenant-equality policy on a row with no tenant would
hide it from everybody.

ON DELETE CASCADE: Delete My Profile (feature 7) erases the candidate row,
and a consent record surviving its subject would itself be a retention the
subject withdrew.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0101_candidate_consents"
down_revision = "0100_consent_lifecycle_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_consents",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "candidate_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_key", sa.String(64), nullable=False),
        sa.Column("stage", sa.String(1), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "candidate_id", "item_key", name="uq_candidate_consents_item"
        ),
    )
    op.create_index(
        "ix_candidate_consents_candidate", "candidate_consents", ["candidate_id"]
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON candidate_consents TO pickready_app"
    )


def downgrade() -> None:
    op.drop_table("candidate_consents")
