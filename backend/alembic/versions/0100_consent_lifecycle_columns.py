"""Consent renewal and the inactivity clock: four stamps on the candidate.

Revision ID: 0100_consent_lifecycle_columns
Revises: 0099_dashboard_refresh_grant

`VIVEKIUM_SPRINT_FEATURES.md` feature 8. Four nullable timestamps and nothing
else: no status column, no flag, no counter.

WHY THERE IS NO STATUS COLUMN
-------------------------------
The stage a candidate is at is DERIVED by
`services/consent_lifecycle.stage_for` from these stamps, the same way
`posting_status`, `profile_age` and `bgv_workflow.derive_status` are derived. A
stored stage is a second copy of something these columns already determine, and
the two can disagree: the sweep would then be erasing people on the strength of
a value some earlier run wrote and nothing has re-checked since.

WHY EVERY ONE OF THEM IS NULLABLE, AND WHAT NULL MEANS
--------------------------------------------------------
NULL is a real state for all four and each has exactly one meaning:

  consent_renewed_at        never renewed. Reads through to `created_at`, which
                            is the ordinary state of everybody who registered
                            less than a renewal cycle ago.
  consent_reminder_sent_at  the six-month letter has not gone out. This is what
                            makes the sweep safe to run late: the grace window
                            starts when the letter is ACTUALLY sent, so a
                            scheduler outage delays the cycle instead of
                            skipping a candidate to deletion.
  consent_final_warning_at  the second letter has not gone out. Same argument.
  last_engagement_at        nothing recorded yet. Reads through to
                            `created_at`, deliberately: treating it as "never
                            dormant" would make the inactivity rule silently
                            inapplicable to exactly the accounts it exists for,
                            the ones that registered and did nothing.

NO BACKFILL, AND THAT IS WHY THE DELETION DEFAULTS OFF
--------------------------------------------------------
Every existing candidate gets four NULLs, so every one of them reads through to
`created_at`. That is correct for consent, because nobody has renewed and there
was nothing to renew against.

For engagement it measures from REGISTRATION rather than from activity, which
would mark a genuinely active candidate dormant. That is exactly why
`consent_auto_deletion_enabled` defaults to off: the measurement is safe to run
from day one, the deletion is not, and the setting is what keeps those two
apart until `last_engagement_at` has been recording for longer than
`consent_inactivity_months`.
"""
import sqlalchemy as sa
from alembic import op

revision = "0100_consent_lifecycle_columns"
down_revision = "0099_dashboard_refresh_grant"
branch_labels = None
depends_on = None


COLUMNS = (
    "consent_renewed_at",
    "consent_reminder_sent_at",
    "consent_final_warning_at",
    "last_engagement_at",
)


def upgrade() -> None:
    for name in COLUMNS:
        op.add_column(
            "candidates",
            sa.Column(name, sa.DateTime(timezone=True), nullable=True),
        )
    # The sweep asks "who is overdue" across the whole databank on a schedule,
    # which is a scan over these columns and `created_at`. Indexed now rather
    # than when it first hurts, because the query is written once and the table
    # only grows.
    op.create_index(
        "ix_candidates_consent_clock",
        "candidates",
        ["consent_renewed_at", "created_at"],
    )
    op.create_index(
        "ix_candidates_engagement_clock",
        "candidates",
        ["last_engagement_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_candidates_engagement_clock", table_name="candidates")
    op.drop_index("ix_candidates_consent_clock", table_name="candidates")
    for name in reversed(COLUMNS):
        op.drop_column("candidates", name)
