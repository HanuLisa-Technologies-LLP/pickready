"""Admit `rejected` to client_email_senders.status.

WHY A NEW VALUE RATHER THAN REUSING `revoked`
---------------------------------------------
Revoked withdraws an authorization that once existed: the sender was active and
may have sent mail under the company's name. Rejected was never authorized at
all. Collapsing them destroys the difference between "we stopped trusting this
address" and "we never did", which is precisely the question an audit of a
sender decision has to answer.

WHY THE TWO OTP STATES SURVIVE
------------------------------
`email_verified` and `verification_expired` stay in the CHECK even though the
mailbox OTP that produced them was withdrawn in the same change. Rows still
carry them. Narrowing a CHECK under live data either fails the migration or --
worse -- succeeds against a table that happens to be empty in one environment
and fails in the one that has customers. The application keeps both in its
status vocabulary and gives each an edge out, so a sender caught mid-flight at
deploy time can still be approved or refused rather than stranded.

This REWRITES the constraint rather than adding a second one: two overlapping
CHECKs on one column is two places to look when a write is refused.

Revision ID: 0087_sender_rejected_status
Revises: 0086_email_delivery_fields
"""
from alembic import op

revision = "0087_sender_rejected_status"
down_revision = "0086_email_delivery_fields"
branch_labels = None
depends_on = None

_CONSTRAINT = "ck_client_email_senders_status"
_TABLE = "client_email_senders"

_WITH_REJECTED = (
    "status IN ('pending_verification', 'email_verified', 'active', "
    "'verification_expired', 'disabled', 'revoked', 'rejected')"
)
_WITHOUT_REJECTED = (
    "status IN ('pending_verification', 'email_verified', 'active', "
    "'verification_expired', 'disabled', 'revoked')"
)


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _WITH_REJECTED)


def downgrade() -> None:
    # A row already rejected has no legal value under the old vocabulary, and
    # inventing one rewrites a decision somebody made. `revoked` is the nearest
    # terminal state and is what this maps it to -- deliberately and visibly,
    # rather than letting the constraint fail on live data.
    op.execute(f"UPDATE {_TABLE} SET status = 'revoked' WHERE status = 'rejected'")
    op.drop_constraint(_CONSTRAINT, _TABLE, type_="check")
    op.create_check_constraint(_CONSTRAINT, _TABLE, _WITHOUT_REJECTED)
