"""RBAC conformance: job assignments, cardinality invariants, RBAC 30 audit fields.

Revision ID: 0061_rbac_cardinality_audit
Revises: 0060_company_dna_versioning

CHAINING NOTE. Two revisions numbered 0060 landed on this branch, both
chaining from 0059_hiring_intelligence: `0060_company_dna_versioning` and
`0060_resume_provider_s3`. 0062_embedding_provenance took the second one,
so the graph already had two heads before this revision was written. This
one chains from `0060_company_dna_versioning`, the head that was otherwise
unclaimed. The split itself is not fixed here: both 0060s and 0062 are
owned by other work and a merge revision written blind would reorder
somebody else's migration. It is reported rather than papered over.

WHAT THIS ADDS, AND WHY EACH PIECE IS AT THE DATABASE
-----------------------------------------------------
`docs/spec/RBAC_SPECIFICATION.md` arrived in this repository on 2026-08-29 and
is precedence rank 1 for authorization, tenant isolation, role ownership, job
lifecycle and audit. Three of its requirements cannot be met in application
code alone.

1. `job_assignments`. RBAC 23 separates RBAC from OWNERSHIP: holding the
   Recruiter role does not make you the Recruiter for a job (9.2), and the
   same sentence appears again for the Hiring Manager (10.2). This codebase
   had no per-job assignment of any kind, so every scoped rule in the
   specification was unenforceable. `jobs.created_by` is not a substitute:
   it records who typed, not who owns.

2. Cardinality. RBAC 5 and 39: exactly one active Super Admin per client,
   exactly one Recruiter per job, exactly one Hiring Manager per job, many
   Interview Managers. Partial unique indexes are the right instrument for
   "exactly one ACTIVE" because they let the historical rows stay. An
   application-level check is not equivalent: two concurrent requests both
   read zero and both insert, and the loser of that race is a row nobody
   knows about.

3. RBAC 30's audit fields as COLUMNS (spec-doc6 4.1 says so explicitly). The
   Super Admin activity view (31) has to answer "what was the previous
   state" with a WHERE clause; a question only answerable by parsing every
   row's JSON is one a dashboard answers by not asking.

SAFETY UNDER A ROLLING DEPLOY
-----------------------------
Every column added here is NULLABLE with no default backfill required, so an
old writer and a new reader coexist. `jobs.lifecycle_state` is backfilled from
data the row already carries, and is nullable so a writer that does not know
about it still inserts successfully.

The two unique indexes are the exception: a unique index cannot be added to a
table that already violates it. So this migration SURVEYS FIRST and raises with
the offending ids named. It does not delete a row, does not deactivate a user,
and does not pick a winner: which of two Super Admins is the real one is a
decision for the customer, not for a migration. The survey queries are repeated
in the module docstring below so an operator can run them before deploying.

    -- tenants with more than one active Super Admin
    SELECT tenant_id, count(*), array_agg(id)
    FROM users
    WHERE role = 'client' AND status <> 'disabled' AND tenant_id IS NOT NULL
    GROUP BY tenant_id HAVING count(*) > 1;

`job_assignments` is created empty, so its indexes can never fail on existing
data. Backfilling it from `jobs.created_by` was considered and rejected: the
creator is frequently not the assigned Recruiter, and inventing an ownership
record that grants access is the opposite of the restrictive direction.
"""
import sqlalchemy as sa
from alembic import op

revision = "0061_rbac_cardinality_audit"
down_revision = "0060_company_dna_versioning"
branch_labels = None
depends_on = None


#: RBAC 17's eight semantic states. Written as a CHECK rather than a Postgres
#: ENUM type so adding a state later is an ALTER of one constraint instead of
#: an ENUM migration that locks the table.
JOB_LIFECYCLE_STATES = (
    "DRAFT",
    "SENT_TO_HIRING_MANAGER",
    "IN_REVIEW",
    "FINALIZED",
    "PUBLISHED",
    "CANDIDATE_APPLICATIONS",
    "HIRING_PROCESS",
    "CLOSED_ARCHIVED",
)

ASSIGNMENT_ROLES = ("recruiter", "hiring_manager", "interview_manager")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _survey_super_admins(connection) -> None:
    """Refuse the unique index rather than fail opaquely on existing data.

    A migration that dies inside CREATE UNIQUE INDEX tells an operator the
    index failed. This tells them which tenants to fix and what the rule is,
    which is the difference between a five-minute fix and a rollback.
    """
    rows = connection.execute(
        sa.text(
            "SELECT tenant_id, count(*) AS n, array_agg(id::text) AS ids "
            "FROM users "
            "WHERE role = 'client' AND status <> 'disabled' "
            "  AND tenant_id IS NOT NULL "
            "GROUP BY tenant_id HAVING count(*) > 1"
        )
    ).mappings().all()
    if not rows:
        return
    detail = "; ".join(
        f"tenant {row['tenant_id']} has {row['n']} active Super Admins {row['ids']}"
        for row in rows
    )
    raise RuntimeError(
        "RBAC 5 and 39 require exactly one active Super Admin per client, and "
        f"the data violates it: {detail}. Deactivate the duplicates (set "
        "users.status = 'disabled') or move them to another role, then re-run "
        "this migration. This migration will not choose which one is real."
    )


def upgrade() -> None:
    connection = op.get_bind()

    # ── 1. Per-job ownership (RBAC 23) ───────────────────────────────────────
    op.create_table(
        "job_assignments",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # RESTRICT, alone among the user references this table could have used.
        # An assignment whose person was erased asserts that somebody owns this
        # job while being unable to say who, which is indistinguishable from
        # nobody owning it. Same argument `review_dispositions.decided_by`
        # already makes for a human disposition.
        sa.Column(
            "user_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("assignment_role", sa.String(30), nullable=False),
        sa.Column(
            "active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "assigned_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"assignment_role IN ({_quoted(ASSIGNMENT_ROLES)})",
            name="ck_job_assignments_role",
        ),
        # An inactive assignment must say when it stopped, and an active one
        # must not claim it has. Without this a revoked row with a null
        # timestamp is indistinguishable from a live one that lost its flag.
        sa.CheckConstraint(
            "(active AND revoked_at IS NULL) OR (NOT active AND revoked_at IS NOT NULL)",
            name="ck_job_assignments_revoked_at_matches_active",
        ),
    )
    op.create_index(
        "ix_job_assignments_job", "job_assignments", ["job_id", "assignment_role"]
    )
    op.create_index(
        "ix_job_assignments_user", "job_assignments", ["user_id", "active"]
    )

    # RBAC 5 / 39: exactly one Recruiter and exactly one Hiring Manager per
    # job. PARTIAL on `active` so a revoked assignment stays in the table as
    # history and a replacement can be inserted beside it.
    op.execute(
        "CREATE UNIQUE INDEX uq_job_assignments_one_active_recruiter "
        "ON job_assignments (job_id) "
        "WHERE assignment_role = 'recruiter' AND active"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_job_assignments_one_active_hiring_manager "
        "ON job_assignments (job_id) "
        "WHERE assignment_role = 'hiring_manager' AND active"
    )
    # 13.1: a job MAY have several Interview Managers, so there is deliberately
    # no singular index for them. What is still refused is the SAME person
    # holding the same assignment twice, which would double their vote in any
    # future count and is never intentional.
    op.execute(
        "CREATE UNIQUE INDEX uq_job_assignments_no_duplicate_holder "
        "ON job_assignments (job_id, user_id, assignment_role) "
        "WHERE active"
    )

    # Tenant isolation is the Postgres policy, not the application filter
    # (claude.md rule 1). Same shape every other tenant-scoped table uses.
    op.execute("ALTER TABLE job_assignments ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE job_assignments FORCE ROW LEVEL SECURITY")
    # The `app.bypass_rls` clause is NOT optional and its absence was a real
    # defect in the first draft of this migration: without it, the Celery
    # workers and the audited Super Admin path (both of which run under
    # `core.db.superadmin_scope`) could read and write every other tenant-scoped
    # table and not this one. The failure is a 500 on a background task, which
    # is the kind that surfaces days later as work that silently did not happen.
    # Copied verbatim from migration 0001's `_TENANT_TABLES` policy so the
    # escape hatch is the same one everywhere.
    op.execute(
        """
        CREATE POLICY job_assignments_tenant_isolation ON job_assignments
        USING (
            tenant_id = current_setting('app.tenant_id', true)::uuid
            OR current_setting('app.bypass_rls', true) = 'on'
        )
        WITH CHECK (
            tenant_id = current_setting('app.tenant_id', true)::uuid
            OR current_setting('app.bypass_rls', true) = 'on'
        )
        """
    )

    # ── 2. Exactly one active Super Admin per client (RBAC 5, 7.1, 39) ───────
    _survey_super_admins(connection)
    op.execute(
        "CREATE UNIQUE INDEX uq_users_one_active_super_admin_per_tenant "
        "ON users (tenant_id) "
        "WHERE role = 'client' AND status <> 'disabled' AND tenant_id IS NOT NULL"
    )

    # ── 3. The RBAC 17 job lifecycle, on the job ────────────────────────────
    # `server_default='DRAFT'` is load-bearing and was NOT in the first draft.
    # Without it, every job inserted by a writer that does not know about this
    # column lands with lifecycle_state NULL, and `rbac.decide` reads an
    # unknown state as "no state rule applies" -- which would have made
    # publishing an unfinalised job possible for exactly the rows written
    # after this migration ran. Observed: 5 such rows appeared in the
    # containerised test database within one suite run.
    #
    # The default is DRAFT rather than nothing because DRAFT is the state that
    # grants least: a job in it cannot be published (21) and its JD is still
    # in Recruiter drafting scope (24***). `decide` also refuses a NULL state
    # outright, so the two guards are independent.
    op.add_column(
        "jobs",
        sa.Column(
            "lifecycle_state",
            sa.String(40),
            nullable=True,
            server_default=sa.text("'DRAFT'"),
        ),
    )
    op.create_check_constraint(
        "ck_jobs_lifecycle_state",
        "jobs",
        f"lifecycle_state IS NULL OR lifecycle_state IN ({_quoted(JOB_LIFECYCLE_STATES)})",
    )
    # Backfill from what the row already knows. Deliberately conservative in
    # both directions: an unpublished job lands in DRAFT (the state that grants
    # least), and a published one lands in PUBLISHED rather than being promoted
    # into CANDIDATE_APPLICATIONS or HIRING_PROCESS on a guess. A job that was
    # published under the old model plainly had its definition settled, so
    # FINALIZED is behind it and 21's publish precondition is satisfied without
    # inventing a finalization event that never happened.
    op.execute(
        """
        UPDATE jobs SET lifecycle_state = CASE
            WHEN archived_at IS NOT NULL THEN 'CLOSED_ARCHIVED'
            WHEN status = 'ratified' THEN 'PUBLISHED'
            ELSE 'DRAFT'
        END
        WHERE lifecycle_state IS NULL
        """
    )
    op.create_index("ix_jobs_lifecycle_state", "jobs", ["tenant_id", "lifecycle_state"])

    # 20: finalization must record who and when. Columns rather than an audit
    # row alone, because 21 has to CHECK the precondition on the job itself and
    # a precondition that requires scanning the audit trail will be skipped.
    op.add_column(
        "jobs",
        sa.Column(
            "finalized_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "jobs", sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True)
    )
    # 35: a candidate's evaluation context should reference the version of the
    # criteria in force when they applied. The counter is here so a revision
    # increments something durable; the per-application reference is a
    # separate piece of work and is recorded in docs/RBAC.md as not yet built.
    op.add_column(
        "jobs",
        sa.Column(
            "criteria_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )

    # ── 4. RBAC 30 audit fields (spec-doc6 4.1) ─────────────────────────────
    for column in (
        sa.Column("actor_role", sa.String(30), nullable=True),
        sa.Column("previous_state", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("new_state", sa.dialects.postgresql.JSONB, nullable=True),
        sa.Column("job_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "application_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column(
            "candidate_id", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True
        ),
        sa.Column("request_method", sa.String(10), nullable=True),
        sa.Column("request_path", sa.String(512), nullable=True),
        sa.Column("request_ip", sa.String(64), nullable=True),
        sa.Column("correlation_id", sa.String(64), nullable=True),
        sa.Column("agent_name", sa.String(50), nullable=True),
        sa.Column("exceptional", sa.Boolean(), nullable=True),
    ):
        op.add_column("audit_log", column)

    # No foreign keys on the three context columns, for the reason the table
    # already carries no FK to `tenants`: the trail must survive the deletion
    # of what it describes. An audit row that vanished with its job is an audit
    # row that was never evidence.
    op.create_index(
        "ix_audit_log_tenant_job", "audit_log", ["tenant_id", "job_id", "at"]
    )
    op.create_index(
        "ix_audit_log_tenant_candidate", "audit_log", ["tenant_id", "candidate_id", "at"]
    )
    op.create_index("ix_audit_log_correlation", "audit_log", ["correlation_id"])

    # 34: an agent row without a human principal is the one shape that must
    # never exist. The service refuses to build it; this refuses to store it,
    # because the service is not the only writer a database ever gets.
    op.create_check_constraint(
        "ck_audit_log_agent_has_principal",
        "audit_log",
        "agent_name IS NULL OR actor_user_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_audit_log_agent_has_principal", "audit_log", type_="check")
    op.drop_index("ix_audit_log_correlation", table_name="audit_log")
    op.drop_index("ix_audit_log_tenant_candidate", table_name="audit_log")
    op.drop_index("ix_audit_log_tenant_job", table_name="audit_log")
    for name in (
        "exceptional",
        "agent_name",
        "correlation_id",
        "request_ip",
        "request_path",
        "request_method",
        "candidate_id",
        "application_id",
        "job_id",
        "new_state",
        "previous_state",
        "actor_role",
    ):
        op.drop_column("audit_log", name)

    op.drop_column("jobs", "criteria_version")
    op.drop_column("jobs", "finalized_at")
    op.drop_column("jobs", "finalized_by")
    op.drop_index("ix_jobs_lifecycle_state", table_name="jobs")
    op.drop_constraint("ck_jobs_lifecycle_state", "jobs", type_="check")
    op.drop_column("jobs", "lifecycle_state")

    op.execute("DROP INDEX IF EXISTS uq_users_one_active_super_admin_per_tenant")

    op.execute(
        "DROP POLICY IF EXISTS job_assignments_tenant_isolation ON job_assignments"
    )
    op.drop_table("job_assignments")
