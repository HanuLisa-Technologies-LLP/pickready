"""One user, one candidate: the index the single resolver reads through.

Revision ID: 0120_candidate_identity
Revises: 0119_job_approval_chain_removed

`services/candidate_identity` is now the only answer to "which candidate record
is this signed-in person". It resolves a request by `candidates.user_id` and
nothing else, and it matches an address case-insensitively at sign-in and at
upload. This migration gives both of those reads their index and makes the
first one a RULE rather than a habit.

1. `ix_candidates_email_lower` on `lower(email)`. The existing
   `ix_candidates_email` is case-sensitive, so every `lower(email) = :e` the
   resolver and the upload paths run would otherwise scan. It is KEPT: seed
   scripts still match exactly, and dropping an index a reader uses is a
   performance regression with no benefit.
2. `uq_candidates_user_id`, UNIQUE on `user_id` WHERE it is set. Before this
   the portal's profile update linked EVERY same-address record to one user,
   so one person could own several candidates and each screen picked a
   different one. With the index a second link is a constraint violation at
   the write, which is where the mistake is made, rather than a question every
   reader answers on its own. Unlinked rows (`user_id IS NULL`, the
   recruiter-sourced records) are untouched by the predicate.

THE GUARD RUNS FIRST AND REFUSES LOUDLY. If any user already owns more than one
candidate the upgrade RAISES naming how many users are affected, instead of
failing inside CREATE UNIQUE INDEX with a message that names no row. Merging
duplicate candidates is an owner decision (twenty-odd tables reference
`candidates.id`), never something a migration does quietly. The pilot probe of
2026-09-24 (CONTRACT v3) counted one candidate and zero duplicates.

No new table, so no new RLS policy or grant; an index inherits its table's.
Downgrade drops both indexes and changes no row.
"""
from __future__ import annotations

from alembic import op

revision = "0120_candidate_identity"
down_revision = "0119_job_approval_chain_removed"
branch_labels = None
depends_on = None

EMAIL_INDEX = "ix_candidates_email_lower"
USER_INDEX = "uq_candidates_user_id"


def upgrade() -> None:
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {EMAIL_INDEX} ON candidates (lower(email))"
    )
    op.execute(
        """
        DO $$
        DECLARE
            affected integer;
        BEGIN
            SELECT count(*) INTO affected FROM (
                SELECT user_id FROM candidates
                WHERE user_id IS NOT NULL
                GROUP BY user_id
                HAVING count(*) > 1
            ) AS duplicated;
            IF affected > 0 THEN
                RAISE EXCEPTION
                    'candidates: % user(s) own more than one candidate record. '
                    'Merging them is an owner decision; this migration will '
                    'not choose which record survives.', affected;
            END IF;
        END
        $$;
        """
    )
    op.execute(
        f"CREATE UNIQUE INDEX {USER_INDEX} ON candidates (user_id) "
        "WHERE user_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {USER_INDEX}")
    op.execute(f"DROP INDEX IF EXISTS {EMAIL_INDEX}")
