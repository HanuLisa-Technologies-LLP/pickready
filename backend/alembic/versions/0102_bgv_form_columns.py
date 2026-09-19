"""The employer checkbox form's columns on bgv_verifications.

Revision ID: 0102_bgv_form_columns
Revises: 0101_candidate_consents

Vivekium feature 4, under C4 (a structured employer submission may set the
status) and C8 (this is the SURVIVING verification system; the tokenised
form moves here from the retiring one).

* `form_token`            the single-use credential in the employer's email.
  UNIQUE so a lookup by token can never be ambiguous; nullable because a
  verification that was never sent has no link.
* `form_token_issued_at`  when the link was minted. Expiry is DERIVED
  (`bgv_form.expires_at`, issued + verification_link_ttl_days), never
  stored, the posting_status pattern.
* `form_submitted_at`     single-use latch: a second submission on the same
  token is refused because this is set, not because the token was deleted,
  so the audit trail keeps the token it can be matched against.
* `form_answers_json`     exactly what the employer ticked, key per item.
* `reminder_sent_at`      the day-3 chase, sent once: the sweep asks "sent,
  unanswered, not yet chased", stamps and never asks about that row again.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0102_bgv_form_columns"
down_revision = "0101_candidate_consents"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bgv_verifications", sa.Column("form_token", sa.String(64), nullable=True)
    )
    op.add_column(
        "bgv_verifications",
        sa.Column("form_token_issued_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "bgv_verifications",
        sa.Column("form_submitted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "bgv_verifications",
        sa.Column("form_answers_json", sa.dialects.postgresql.JSONB(), nullable=True),
    )
    op.create_unique_constraint(
        "uq_bgv_verifications_form_token", "bgv_verifications", ["form_token"]
    )
    op.add_column(
        "bgv_verifications",
        sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True),
    )
    # C4 widens the decided rule: a decided row names its decider, and the
    # employer's own form submission IS a decider. "Who cleared this
    # employer" still always has an answer; for a form-decided row it is the
    # employer, at form_submitted_at, with the ticks stored beside it.
    op.execute(
        "ALTER TABLE bgv_verifications DROP CONSTRAINT ck_bgv_verifications_decided"
    )
    op.execute(
        """
        ALTER TABLE bgv_verifications ADD CONSTRAINT ck_bgv_verifications_decided
        CHECK (
            status NOT IN ('verified', 'not_verified')
            OR (decided_by IS NOT NULL AND decided_at IS NOT NULL)
            OR form_submitted_at IS NOT NULL
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE bgv_verifications DROP CONSTRAINT ck_bgv_verifications_decided"
    )
    op.execute(
        """
        ALTER TABLE bgv_verifications ADD CONSTRAINT ck_bgv_verifications_decided
        CHECK (
            status NOT IN ('verified', 'not_verified')
            OR (decided_by IS NOT NULL AND decided_at IS NOT NULL)
        )
        """
    )
    op.drop_constraint(
        "uq_bgv_verifications_form_token", "bgv_verifications", type_="unique"
    )
    for column in (
        "form_token",
        "form_token_issued_at",
        "form_submitted_at",
        "form_answers_json",
        "reminder_sent_at",
    ):
        op.drop_column("bgv_verifications", column)
