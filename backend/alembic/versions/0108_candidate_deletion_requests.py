"""The deletion request record, the dormancy warning stamp, the renewal token.

Revision ID: 0108_candidate_deletion_requests
Revises: 0107_report_evidence_confidence

Three changes that belong to one story: what happens to a candidate's data
when they ask for it to go, when their consent lapses, and when they stop
coming back.

WHY A DELETION NEEDS A RECORD AT ALL
--------------------------------------
Until now an erasure was one inline transaction: it committed or it 500'd.
That is a complete answer for ROWS, because Postgres decides them together.
It is not an answer for OBJECTS. A resume, an assessment recording and a
staged project original live in an object store that fails independently of
the database, so the two halves of an erasure can disagree, and the half that
survives is the one holding the person's actual documents.

`candidate_deletion_requests` is the record that makes the disagreement
VISIBLE and RETRYABLE. It is the same argument `candidate_projects`
already makes for staged originals: "the delete call returned" is the same
class of non-evidence as "the pipeline was green", so the deletion is
counted, verified with a HEAD, and swept until it is actually done.

WHY IT HAS NO FOREIGN KEY TO `candidates`
-------------------------------------------
Because the row it is about is deleted a few statements after the request is
opened. A foreign key would cascade the record away at exactly the moment it
becomes the only thing that knows which objects still have to go. Same
reasoning as `audit_log.candidate_id`, which is also a plain column for a
reason that is about survival rather than about laziness.

`object_keys_json` is captured BEFORE the rows are erased, and that is
load bearing rather than an optimisation: after the cascade nothing in the
database names a single one of those objects, so an enumeration performed
afterwards would return an empty list and the sweep would report a complete
deletion of nothing.

WHAT IT DELIBERATELY DOES NOT HOLD
------------------------------------
No name, no email, no resume text, no score. An object KEY is an opaque
content hash or an id-addressed path, and the reason to keep it is that it is
the only way to delete the object. Keeping the person's details in the record
of their own erasure would defeat the erasure.

TENANT-FREE, NO RLS, the `candidates` and `candidate_consents` rule: a
candidate spans tenants through the databank, the deletion is reached through
the candidate's own session or the audited bypass scope, and a tenant-equality
policy on a row with no tenant would hide it from everybody including the
sweep that has to finish it.

THE DORMANCY STAMP
--------------------
`dormancy_warning_sent_at` is the fourth letter stamp, and it exists for the
same reason the other three do: a window that starts when a letter was
ACTUALLY SENT is safe to evaluate late, and a window measured from a due date
erases people for not answering a letter nobody sent. Before this column the
inactivity rule had no letter at all: the sweep read `is_dormant` and erased,
with zero notice, which is the one shape of this feature nobody would defend.

THE RENEWAL TOKEN
-------------------
`consent_renewal_token_hash` and `consent_renewal_token_expires_at` are what
make the reminder letter's own sentence true. The letter has said "sign in and
confirm" since it was written, and there was nothing to confirm with:
`consent_renewed_at` was read in three places and written in none, so every
candidate advanced to the deletion stage whatever they did.

ONLY THE HASH IS STORED. The token itself exists in the letter and nowhere
else, so a database copy cannot be replayed by anybody who can read the table,
which is the same posture `services/otp` takes for the sender code. It is
single use because renewal CLEARS the hash, short lived because the expiry is
checked, and bound to one candidate because the hash is looked up on the row.
It authorises exactly one act and is not a session: see
`services/consent_renewal`.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0108_candidate_deletion_requests"
down_revision = "0107_report_evidence_confidence"
branch_labels = None
depends_on = None


#: Mirrors `services/deletion_requests.ALL_STATES`. Stated as a CHECK because
#: this table is what a sweep resumes from: a state nobody planned for would
#: make the sweep skip a half-finished erasure silently, for ever.
STATES = ("pending", "rows_erased", "completed")

CANDIDATE_COLUMNS = (
    "dormancy_warning_sent_at",
    "consent_renewal_token_expires_at",
)


def upgrade() -> None:
    op.create_table(
        "candidate_deletion_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # NO FOREIGN KEY. See the module docstring: the candidate row is gone
        # long before this record is finished with.
        sa.Column("candidate_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        # Which of the three roads brought the person here: they asked, their
        # consent lapsed, or they went dormant. "deleted" with no reason is a
        # fact nobody can answer a complaint with.
        sa.Column("reason", sa.String(60), nullable=False),
        # Who pressed it. Nullable and NOT a foreign key: a sweep has no actor
        # at all, and a staff account may itself be deleted later.
        sa.Column("requested_by_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("rows_erased_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        # The keys, captured before the rows went. A list of
        # {"key": ..., "kind": ...} objects; `kind` is what the object was, so
        # an operator reading a stuck request can tell a resume from a
        # recording without going back to rows that no longer exist.
        sa.Column(
            "object_keys_json",
            postgresql.JSONB,
            nullable=False,
            server_default="[]",
        ),
        sa.Column("objects_total", sa.Integer, nullable=False, server_default="0"),
        sa.Column("objects_deleted", sa.Integer, nullable=False, server_default="0"),
        sa.Column("deletion_attempts", sa.Integer, nullable=False, server_default="0"),
        # The last failure, as a TYPE NAME and a count, never a payload. A
        # message can quote a row, and this record is the one place a quoted
        # row would survive the erasure that was supposed to remove it.
        sa.Column("last_failure", sa.String(200)),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "state IN ('" + "', '".join(STATES) + "')",
            name="ck_candidate_deletion_requests_state",
        ),
        sa.CheckConstraint(
            "objects_deleted >= 0 AND objects_total >= 0 "
            "AND objects_deleted <= objects_total",
            name="ck_candidate_deletion_requests_counts",
        ),
        # A completed request has to have finished both halves. Stated in the
        # database because this is the row a sweep uses to decide it has
        # nothing left to do.
        sa.CheckConstraint(
            "state <> 'completed' OR ("
            "rows_erased_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND objects_deleted = objects_total)",
            name="ck_candidate_deletion_requests_completed",
        ),
    )
    # The sweep's own query: everything not yet finished, oldest first. Partial,
    # because a completed request is the overwhelming majority of the table
    # within a day of being written and the sweep never looks at one.
    op.create_index(
        "ix_candidate_deletion_requests_open",
        "candidate_deletion_requests",
        ["requested_at"],
        postgresql_where=sa.text("state <> 'completed'"),
    )
    op.create_index(
        "ix_candidate_deletion_requests_candidate",
        "candidate_deletion_requests",
        ["candidate_id"],
    )
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON candidate_deletion_requests "
        "TO pickready_app"
    )

    op.add_column(
        "candidates",
        sa.Column("dormancy_warning_sent_at", sa.DateTime(timezone=True)),
    )
    op.add_column(
        "candidates",
        sa.Column("consent_renewal_token_hash", sa.String(64)),
    )
    op.add_column(
        "candidates",
        sa.Column("consent_renewal_token_expires_at", sa.DateTime(timezone=True)),
    )
    # The token is looked up BY HASH by an unauthenticated route, which is the
    # one query in this product that scans `candidates` on a column that is not
    # an id. Unique as well as indexed: two candidates sharing a token hash
    # would make "whose renewal is this" unanswerable, and the answer must
    # never be "whichever row came back first".
    op.create_index(
        "uq_candidates_renewal_token",
        "candidates",
        ["consent_renewal_token_hash"],
        unique=True,
        postgresql_where=sa.text("consent_renewal_token_hash IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_candidates_renewal_token", table_name="candidates")
    op.drop_column("candidates", "consent_renewal_token_hash")
    for name in reversed(CANDIDATE_COLUMNS):
        op.drop_column("candidates", name)
    op.drop_index(
        "ix_candidate_deletion_requests_candidate",
        table_name="candidate_deletion_requests",
    )
    op.drop_index(
        "ix_candidate_deletion_requests_open",
        table_name="candidate_deletion_requests",
    )
    op.drop_table("candidate_deletion_requests")
