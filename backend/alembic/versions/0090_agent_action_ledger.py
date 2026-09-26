"""The agent action ledger: every side effect recorded before it is attempted.

RPN-AI-UP-001 W5.1. One table, `agent_actions`, unique on `idempotency_key`.

WHAT THE UNIQUE CONSTRAINT BUYS
---------------------------------
It is the dedupe, and it is the ONLY dedupe. A prior SELECT would let two
workers racing the same logical action both read "not present" and both issue
the effect. The constraint is the one place the race is settled, exactly as
`credit_ledger_entries.idempotency_key` settles a double grant when Razorpay
redelivers a webhook and the checkout-verify path runs for the same payment.

WHY THE STATE LIST IS A CHECK AND NOT AN ENUM TYPE
----------------------------------------------------
The same reason every other status column in this schema is a CHECK: a
Postgres enum needs a type migration to gain a value, and a status column that
is expensive to extend is a status column somebody eventually writes a free
string into. `PipelineStatus` already taught this product that an enum missing
a value the column accepts 500s every read of every row carrying it.

RLS: TENANT ISOLATION, WITH THE WRITE ADMITTED
------------------------------------------------
A recruiter's own session writes these rows, because the actions recorded are
the ones their portal starts. So the WITH CHECK admits a tenant-scoped insert
attributed to that same tenant, rather than being bypass-only. Same reasoning
as `candidate_updates` (0079), and unlike `candidate_projects` (0074), whose
writers are all bypass.

`approval_id` CARRIES NO FOREIGN KEY, DELIBERATELY
----------------------------------------------------
The approval record belongs to the policy layer. A foreign key here would make
the action ledger unwritable whenever that layer is mid-change, and an action
that cannot be RECORDED is an action that gets taken with no record.

Revision ID: 0090_agent_action_ledger
Revises: 0089_agent_policy_provenance
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0090_agent_action_ledger"
down_revision = "0089_agent_policy_provenance"
branch_labels = None
depends_on = None

TABLE = "agent_actions"

#: Mirrors `app.models.agent_action.ACTION_STATES`. Stated here rather than
#: imported: a migration must describe the schema at the moment it ran, and an
#: import would make an already-applied migration change meaning when the model
#: gains a state.
STATES = (
    "PENDING",
    "RUNNING",
    "SUCCEEDED",
    "FAILED",
    "UNKNOWN",
    "ROLLED_BACK",
)

RISK_CLASSES = ("reversible", "irreversible")

AUTHORIZATIONS = ("policy", "human_approval")


def _in_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("agent", sa.String(50), nullable=False),
        sa.Column("tool", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False, unique=True),
        sa.Column("args_sha256", sa.String(64), nullable=False),
        sa.Column("risk_class", sa.String(20), nullable=False),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "requested_by",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("authorization", sa.String(30), nullable=False),
        sa.Column("approval_id", UUID(as_uuid=True)),
        sa.Column("state", sa.String(20), nullable=False, server_default="PENDING"),
        sa.Column("external_id", sa.String(200)),
        sa.Column("attempt", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sealed_at", sa.DateTime(timezone=True)),
        sa.Column("committed_at", sa.DateTime(timezone=True)),
        sa.Column("verified_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            f"state IN ({_in_list(STATES)})", name="ck_agent_actions_state"
        ),
        sa.CheckConstraint(
            f"risk_class IN ({_in_list(RISK_CLASSES)})",
            name="ck_agent_actions_risk_class",
        ),
        sa.CheckConstraint(
            f'"authorization" IN ({_in_list(AUTHORIZATIONS)})',
            name="ck_agent_actions_authorization",
        ),
        # An irreversible action may only be sealed by a HUMAN APPROVAL, and
        # the approval it rests on must be named. Enforced at the database
        # because the gate in `services/agent_actions/gate.py` is the writer
        # today and will not be the only writer for ever: a rule held at one
        # call site is a rule the next call site breaks.
        sa.CheckConstraint(
            "risk_class <> 'irreversible' OR sealed_at IS NULL "
            "OR (\"authorization\" = 'human_approval' AND approval_id IS NOT NULL)",
            name="ck_agent_actions_irreversible_needs_approval",
        ),
        # A committed effect is one the adapter was actually allowed to reach,
        # and the adapter is only reachable once the action is sealed.
        sa.CheckConstraint(
            "committed_at IS NULL OR sealed_at IS NOT NULL",
            name="ck_agent_actions_commit_follows_seal",
        ),
    )
    op.create_index("ix_agent_actions_unresolved", TABLE, ["state", "requested_at"])

    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TABLE} TO pickready_app")
    op.execute(f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {TABLE}_tenant_isolation ON {TABLE}
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


def downgrade() -> None:
    op.drop_index("ix_agent_actions_unresolved", table_name=TABLE)
    op.drop_table(TABLE)
