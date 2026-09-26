"""Consent wording versions, and the append-only record of every consent act.

Revision ID: 0117_consent_versioning
Revises: 0115_drishti_functional_head

THIS SUPERSEDES AN ARGUMENT `0101_candidate_consents` MADE DELIBERATELY, so
the argument is answered here rather than quietly dropped. 0101 reasoned: the
row answers "does this consent stand, since when", a re-affirmation moves
`consented_at` in place because two live rows would make "since when"
ambiguous, and "the history of acts stays in `audit_log`, where history
lives."

THE FIRST HALF STANDS AND IS UNCHANGED. `uq_candidate_consents_item` survives,
`consented_at` still moves on a re-affirmation, and the standing row is still
the one place the renewal cycle and an erasure receipt read.

THE SECOND HALF CANNOT WORK, AND `services/erasure.py` IS WHY. `audit_log` is
deliberately UN-ERASABLE: `audit_log.candidate_id` carries no foreign key
precisely so those rows SURVIVE `cascade_erasure`, because "an audit trail a
subject can delete is not an audit trail". A consent record is the exact
opposite: feature 7 deletes consent records with the profile, which is why
`candidate_consents` cascades. Routing consent history into `audit_log` would
make the candidate's consent history the one part of their consent record that
outlives their Delete My Profile, which is the retention they just withdrew.
Two opposite retention rules cannot share a table, so the history gets its own
table with the cascade the subject is owed.

WHY HISTORY IS NEEDED AT ALL. `record_items` upserts, so before this migration
a re-affirmation OVERWROTE `consented_at` and nothing anywhere recorded that
an earlier consent had ever been given, or under what words. Worse, the
wording lives in Python (`services/consent_catalog.py`): editing
`CONSENT_ITEMS[n].text` silently re-described every row already written, so a
compliance read of an old row answered with today's sentence. The brief
requires the opposite: later wording changes must not destroy the evidence of
what was actually consented to.

THREE COLUMNS AND ONE TABLE ANSWER IT:

* `candidate_consents.item_version` and `.text_sha256` say WHICH wording the
  standing consent was given under, so an item edited and version-bumped shows
  up as a standing consent to superseded words rather than reading as current.
* `candidate_consent_events` is the append-only log of every act, carrying the
  VERBATIM text shown at the time. A digest proves a stored sentence was not
  altered; it cannot reconstruct a sentence that no longer exists in the
  source, and a consent record whose words can only be recovered from a git
  history is not evidence anybody can produce.

This is the `pipeline_status` / `job_candidate_links.status` shape exactly: an
append-only history beside a denormalised standing mirror, with ONE writer
(`consent_catalog.record_items`) for both, which is what keeps them from
disagreeing.

NO UPDATE ON THE EVENT TABLE, the `audit_log` pattern, and THE REVOKE IS NOT
DECORATIVE: 0014 set `ALTER DEFAULT PRIVILEGES ... GRANT SELECT, INSERT,
UPDATE, DELETE ON TABLES`, so every new table arrives writable and a grant
list that merely omits UPDATE omits nothing. Re-describing a past act is the
failure this table exists to prevent, so the application role must not be able
to emit the statement that would do it. DELETE IS LEFT, unlike `audit_log`:
the cascade from `candidates` is the erasure path, and a privilege subtlety is
not somewhere to be clever when being wrong means Delete My Profile 500s.

THE BACKFILL IS NULL, ON PURPOSE. Rows written before today were recorded
with no version and no digest. Today's catalogue text is probably what they
were shown, and "probably" is not evidence: stamping them with a digest we
cannot prove would manufacture exactly the provenance this change exists to
create. NULL reads as "recorded before wording versions existed", which is
true, and `items_for` reports it as such rather than as a version number.
Nothing is backfilled into the event table either, for the same reason: an
event row invented for an act nobody witnessed is a fabricated audit trail.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0117_consent_versioning"
down_revision = "0116_assessment_end_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidate_consents",
        sa.Column("item_version", sa.Integer(), nullable=True),
    )
    op.add_column(
        "candidate_consents",
        sa.Column("text_sha256", sa.String(64), nullable=True),
    )

    op.create_table(
        "candidate_consent_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "candidate_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("item_key", sa.String(64), nullable=False),
        sa.Column("stage", sa.String(1), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("item_version", sa.Integer(), nullable=False),
        # The words themselves, not a reference to them. A digest detects a
        # change; only the text answers "what did I agree to" years later.
        sa.Column("consent_text", sa.Text(), nullable=False),
        sa.Column("text_sha256", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    # The read is always "this candidate's acts, newest first".
    op.create_index(
        "ix_candidate_consent_events_candidate",
        "candidate_consent_events",
        ["candidate_id", "recorded_at"],
    )
    op.execute(
        "GRANT SELECT, INSERT, DELETE ON candidate_consent_events "
        "TO pickready_app"
    )
    # 0014's ALTER DEFAULT PRIVILEGES already granted UPDATE on this table the
    # moment it was created, so the revoke is what makes it append-only.
    op.execute("REVOKE UPDATE ON candidate_consent_events FROM pickready_app")


def downgrade() -> None:
    op.drop_index(
        "ix_candidate_consent_events_candidate",
        table_name="candidate_consent_events",
    )
    op.drop_table("candidate_consent_events")
    op.drop_column("candidate_consents", "text_sha256")
    op.drop_column("candidate_consents", "item_version")
