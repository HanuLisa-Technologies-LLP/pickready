"""Job closure becomes a thirty day soft deletion, with a dispute path.

Revision ID: 0112_job_closure_soft_deletion
Revises: 0115_drishti_functional_head

OWNER RULING 2026-09-22, REVERSING THE ONE OF 2026-09-18.

The vivekium C5 ruling made `POST /jobs/{id}/close` a hard delete: the PRISM
reports, the Tatva evaluations, the transcripts, the question sets and the
assessment chunks were removed inline, in the close transaction. Closure is
terminal and has no reopen, so that left no answer at all to a wrong job id, a
misclick, or a dispute raised the following week. The data is now WITHHELD at
closure and deleted thirty days later by a sweep, with one named,
capability-gated, audited way to retrieve it in between.

WHAT THIS MIGRATION ADDS, and why each column exists rather than being derived:

* `assessment_purge_due_at` is the promise. The confirmation dialog tells a
  hiring manager they have thirty days, so the deadline must be a fact the row
  holds and not a recomputation from a module constant somebody can edit.
  Indexed, because it is the sweep's whole query.
* `assessment_purged_at` is the convergence condition. Null means the sweep
  still owes this job work.
* `assessment_purge_attempts` and `assessment_purge_last_failure` are the
  never-give-up pair `candidate_deletion_requests` already carries. There is
  no terminal failure state here either: a persistent failure moves the
  counter and escalates the log, it never stops the retry. The failure column
  holds an exception CLASS NAME, never a message, because a message can quote
  a row.
* The three `assessment_dispute_*` columns are the retrieval path. Written on
  the row rather than read back out of `audit_log`, for the reason
  `lifecycle_state` is written: a state that lives only in a log is a state
  nothing can index, refuse against, or render.

THE BACKFILL IS THE HONEST HALF. Every job already closed had its assessment
data destroyed by the ruling this one supersedes. Those rows are stamped
`assessment_purged_at = closed_at` and left with no due date, so the sweep
never enumerates them and the dispute path never offers to retrieve something
that is not there. Back-dating a due date for them would be worse than
useless: it would promise a retrieval that cannot succeed.

THE CAPABILITY. `retrieve_disputed_assessment`, seeded here as global template
rows, because the grant engine reads ROWS and a capability constant is only
half a change. The three operational roles are seeded as explicit
allowed=false rows rather than absent ones: both deny, and only the explicit
row makes the refusal visible in the template.

Seed rows are string LITERALS rather than imported constants, the convention
every earlier seed migration follows (0017, 0027, 0031, 0075, 0080, 0115): a
historical migration's effect must not shift if a code constant is later
renamed. Idempotent without ON CONFLICT for the reason 0031 records, that the
unique constraint is (tenant_id, role, capability) with NULLS DISTINCT so
global rows never collide.

Downgrade drops the columns only. The permission rows are left, as in every
earlier seed migration: this cannot tell a row it created from one
`seed_dev_data` created, and deleting either breaks a database that
legitimately depends on it. Note what a downgrade CANNOT undo: data this
release retained instead of deleting is still present afterwards, which is the
safe direction.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0112_job_closure_soft_deletion"
down_revision = "0115_drishti_functional_head"
branch_labels = None
depends_on = None


#: (role, capability, allowed), restating DEFAULT_PERMISSION_MATRIX for this
#: one capability as of this revision.
SEED_ROWS: list[tuple[str, str, bool]] = [
    ("client", "retrieve_disputed_assessment", True),
    ("hr_manager", "retrieve_disputed_assessment", False),
    ("recruitment_manager", "retrieve_disputed_assessment", False),
    ("recruiter", "retrieve_disputed_assessment", False),
    ("hiring_manager", "retrieve_disputed_assessment", False),
    ("interview_manager", "retrieve_disputed_assessment", False),
]


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "assessment_purge_due_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "jobs",
        sa.Column("assessment_purged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "assessment_purge_attempts",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "jobs",
        sa.Column("assessment_purge_last_failure", sa.String(200), nullable=True),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "assessment_dispute_opened_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "assessment_dispute_opened_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "jobs", sa.Column("assessment_dispute_reason", sa.Text(), nullable=True)
    )
    # The sweep's whole query: due, and not yet purged. Partial, so a purge
    # that finds nothing costs nothing, which is the state a healthy
    # deployment is in almost all the time.
    op.create_index(
        "ix_jobs_assessment_purge_due",
        "jobs",
        ["assessment_purge_due_at"],
        postgresql_where=sa.text("assessment_purged_at IS NULL"),
    )
    # A CHECK rather than a comment: the two stamps describe one lifecycle and
    # a purged job that was never closed is a row no code path can produce and
    # no reader could interpret.
    op.create_check_constraint(
        "ck_jobs_assessment_purge_requires_closure",
        "jobs",
        "(assessment_purge_due_at IS NULL AND assessment_purged_at IS NULL) "
        "OR closed_at IS NOT NULL",
    )
    # Already-closed jobs: their assessment data is GONE, deleted by the
    # ruling this revision supersedes. Say so, rather than leaving them
    # looking like jobs with a retention window nobody started.
    op.execute(
        "UPDATE jobs SET assessment_purged_at = closed_at "
        "WHERE closed_at IS NOT NULL AND assessment_purged_at IS NULL"
    )

    # ── THE CONSENT RECORD WAS DYING WITH THE THING IT AUTHORISED ───────────
    #
    # `erasure.job_closure_erasure` has claimed since the day it was written
    # that it keeps "the `assessment_consents` rows (a consent record is the
    # LEGITIMACY of this very deletion)". It did not.
    # `assessment_consents.conversation_id` was ON DELETE CASCADE, and the
    # function deletes `assessment_conversations`, so closing a job destroyed
    # the evidence that the candidate had agreed to any of it. The prose was
    # right about what should happen and nothing enforced it, which is why
    # `tests/test_job_closure_erasure.py` now asserts the survival rather than
    # trusting the sentence.
    #
    # SET NULL rather than RESTRICT. RESTRICT would refuse the conversation
    # delete outright and turn a correct erasure into a 500. NULL says exactly
    # what is true afterwards: the session is gone and the consent stands. The
    # row keeps the candidate, the application, the mode, the moment and the
    # three accepted versions, which is everything a dispute about what was
    # agreed actually reads. The UNIQUE on (conversation_id, assessment_mode)
    # is untouched: NULLS DISTINCT keeps every orphaned row legal, the same
    # property the global `role_permissions` rows rely on.
    op.execute(
        "ALTER TABLE assessment_consents "
        "ALTER COLUMN conversation_id DROP NOT NULL"
    )
    op.drop_constraint(
        "assessment_consents_conversation_id_fkey",
        "assessment_consents",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "assessment_consents_conversation_id_fkey",
        "assessment_consents",
        "assessment_conversations",
        ["conversation_id"],
        ["id"],
        ondelete="SET NULL",
    )

    for role, capability, allowed in SEED_ROWS:
        allowed_sql = "true" if allowed else "false"
        # 1. Reconcile an existing global row to this table's value.
        op.execute(
            f"""
            UPDATE role_permissions SET allowed = {allowed_sql}
            WHERE tenant_id IS NULL
              AND role = '{role}' AND capability = '{capability}'
              AND allowed IS DISTINCT FROM {allowed_sql}
            """
        )
        # 2. Collapse duplicates the NULLS DISTINCT constraint never blocked.
        op.execute(
            f"""
            DELETE FROM role_permissions a
            USING role_permissions b
            WHERE a.tenant_id IS NULL AND b.tenant_id IS NULL
              AND a.role = b.role AND a.capability = b.capability
              AND a.role = '{role}' AND a.capability = '{capability}'
              AND a.ctid < b.ctid
            """
        )
        # 3. Insert the pair if still absent.
        op.execute(
            f"""
            INSERT INTO role_permissions (id, tenant_id, role, capability, allowed)
            SELECT gen_random_uuid(), NULL, '{role}', '{capability}', {allowed_sql}
            WHERE NOT EXISTS (
                SELECT 1 FROM role_permissions
                WHERE tenant_id IS NULL
                  AND role = '{role}' AND capability = '{capability}'
            )
            """
        )


def downgrade() -> None:
    # The consent FK goes back to CASCADE. A row already orphaned by a purge
    # is DELETED first, because NOT NULL cannot be restored over it and a
    # downgrade that fails half way is worse than one that states its cost:
    # the session those rows described is gone either way.
    op.execute("DELETE FROM assessment_consents WHERE conversation_id IS NULL")
    op.drop_constraint(
        "assessment_consents_conversation_id_fkey",
        "assessment_consents",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "assessment_consents_conversation_id_fkey",
        "assessment_consents",
        "assessment_conversations",
        ["conversation_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.execute(
        "ALTER TABLE assessment_consents "
        "ALTER COLUMN conversation_id SET NOT NULL"
    )
    op.drop_constraint("ck_jobs_assessment_purge_requires_closure", "jobs")
    op.drop_index("ix_jobs_assessment_purge_due", table_name="jobs")
    op.drop_column("jobs", "assessment_dispute_reason")
    op.drop_column("jobs", "assessment_dispute_opened_by")
    op.drop_column("jobs", "assessment_dispute_opened_at")
    op.drop_column("jobs", "assessment_purge_last_failure")
    op.drop_column("jobs", "assessment_purge_attempts")
    op.drop_column("jobs", "assessment_purged_at")
    op.drop_column("jobs", "assessment_purge_due_at")
