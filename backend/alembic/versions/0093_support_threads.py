"""In-product customer support, replacing the deleted Intercom sync.

Revision ID: 0093_support_threads
Revises: 0092_security_provenance

WHY THIS EXISTS
-----------------
The Intercom integration was deleted on 2026-09-10 by owner decision. The need
it served is real and did not go away: ReadyPick staff have to be able to see
and answer a customer's questions. Answering it inside the product removes the
outbound projection entirely, which is what the deleted module's closed
allowlist was spending its whole design on.

TWO TABLES, AND WHY THE MESSAGE CARRIES ITS OWN `tenant_id`
-------------------------------------------------------------
A message could reach its tenant through its thread, and the RLS policy could
join to get there. It does not, and the difference is not cosmetic: a policy
that resolves the boundary through a JOIN evaluates against rows the session
may not be able to see, so an INSERT would be admitted against a thread
belonging to somebody else and refused only later, on a read that may never
happen. The boundary is answered from the row being written.

THE RLS SHAPE, AND HOW IT DIFFERS FROM `candidate_updates` (0079)
-------------------------------------------------------------------
0079 could not use plain tenant equality, because a candidate spans tenants by
design and carries `tenant_id NULL` when they arrived through the databank, so
an equality policy would have hidden a candidate's own feed from them. None of
that applies here. Everybody in a support thread is one tenant's own staff, so
the policy is plain equality in BOTH directions, which is the strictest
available shape and the correct one when nothing needs more.

ReadyPick staff read and write across tenants through `superadmin_scope`, which
sets `app.bypass_rls`, is audit-logged on every request by `get_superadmin_db`,
and is the same path every other Provider cross-tenant read already takes.

THE CAPABILITY ROWS, SEEDED HERE BECAUSE A CONSTANT IS HALF A CHANGE
----------------------------------------------------------------------
`open_support_threads` for the customer roles, `handle_support_threads` for the
platform's own staff. The engine reads ROWS; a capability that exists only in
`services/capabilities.py` resolves to False for everybody, which is the defect
migration 0075 exists to record.

`handle_support_threads` is seeded for `super_admin` as a GLOBAL row and is
DELIBERATELY NOT in `DEFAULT_PERMISSION_MATRIX`. That dict is copied into
per-tenant rows for every customer the Owner console creates
(`api/admin._seed_permissions`), and a `super_admin` has no tenant, so putting
it there would write a tenant-scoped row for a tenantless role into every new
customer forever. It is not a route gate either: the Provider routes are gated
by `get_superadmin_db`, which already enforces the owner audience, and
`require_capability` structurally cannot serve them because it resolves through
`get_tenant_db`. What the capability IS is the notification routing list, asked
of the rows rather than branched on by role, so a future ReadyPick support role
is a seeded row rather than an edit to a task. `tests/test_support_rbac.py`
asserts the global row exists.

Idempotent without ON CONFLICT for the reason 0031 and 0075 both record: the
unique constraint is (tenant_id, role, capability) with NULLS DISTINCT, so
global rows never collide and ON CONFLICT never fires for them.

Downgrade drops the tables and leaves the capability rows, as every earlier
seed migration does: this migration cannot tell a row it created from one
`seed_dev_data` created, and deleting either breaks a database that depends
on it.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0093_support_threads"
down_revision = "0092_security_provenance"
branch_labels = None
depends_on = None

THREADS = "support_threads"
MESSAGES = "support_messages"

#: Restated as literals rather than imported, the convention every earlier seed
#: migration follows: a historical migration's effect must not shift if a code
#: constant is later renamed.
_CUSTOMER_ROLES = (
    "client",
    "hr_manager",
    "recruitment_manager",
    "recruiter",
    "hiring_manager",
    "interview_manager",
)

SEED_ROWS: list[tuple[str, str, bool]] = [
    # Every customer role may raise a ticket, including the Interview Manager,
    # who holds the narrowest set in the product. Support is not a recruitment
    # capability: somebody locked out of a screen has to be able to say so, and
    # a role that could not would have to ask a colleague to report it, which
    # is how a bug report loses the detail that made it actionable.
    *((role, "open_support_threads", True) for role in _CUSTOMER_ROLES),
    # The platform's own staff. See the module docstring for why this one is
    # not in DEFAULT_PERMISSION_MATRIX.
    ("super_admin", "handle_support_threads", True),
]


def upgrade() -> None:
    op.create_table(
        THREADS,
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # SET NULL rather than RESTRICT: a thread whose author has left the
        # company must survive, and losing the attribution is a smaller loss
        # than blocking the deletion of a departed employee's account.
        sa.Column(
            "opened_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column(
            "status", sa.String(30), nullable=False, server_default="open"
        ),
        sa.Column(
            "assigned_to",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_message_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # The vocabulary lives in the database as well as in the constants, for
        # the reason `PipelineStatus` records: a value the column accepts but
        # the Python side does not know 500s every read of every row carrying
        # it, permanently, for that whole tenant. The CHECK makes the two
        # halves fail at WRITE time instead.
        sa.CheckConstraint(
            "status IN ('open', 'awaiting_customer', 'resolved')",
            name="ck_support_threads_status",
        ),
        # Whitespace is not a subject. The same lesson the retrieval sweep
        # learned about `btrim`: state the emptiness test once, in SQL, so two
        # definitions of "has text" cannot disagree.
        sa.CheckConstraint(
            "btrim(subject) <> ''", name="ck_support_threads_subject_not_blank"
        ),
    )
    op.create_index(
        "ix_support_threads_tenant", THREADS, ["tenant_id", "last_message_at"]
    )
    op.create_index(
        "ix_support_threads_queue", THREADS, ["status", "last_message_at"]
    )

    op.create_table(
        MESSAGES,
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "thread_id",
            UUID(as_uuid=True),
            sa.ForeignKey("support_threads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("author_side", sa.String(20), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Denormalised at write time on purpose. Derived from the author's role
        # at read time it would be rewritten by any later role change, so a
        # recruiter promoted to Super Admin would retroactively become the
        # author of every customer message they ever sent.
        sa.CheckConstraint(
            "author_side IN ('customer', 'staff')",
            name="ck_support_messages_side",
        ),
        sa.CheckConstraint(
            "btrim(body) <> ''", name="ck_support_messages_body_not_blank"
        ),
    )
    op.create_index(
        "ix_support_messages_thread", MESSAGES, ["thread_id", "created_at"]
    )

    for table in (THREADS, MESSAGES):
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO pickready_app"
        )
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
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

    for role, capability, allowed in SEED_ROWS:
        allowed_sql = "true" if allowed else "false"
        op.execute(
            f"""
            UPDATE role_permissions SET allowed = {allowed_sql}
            WHERE tenant_id IS NULL
              AND role = '{role}' AND capability = '{capability}'
              AND allowed IS DISTINCT FROM {allowed_sql}
            """
        )
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
    op.drop_index("ix_support_messages_thread", table_name=MESSAGES)
    op.drop_table(MESSAGES)
    op.drop_index("ix_support_threads_queue", table_name=THREADS)
    op.drop_index("ix_support_threads_tenant", table_name=THREADS)
    op.drop_table(THREADS)
