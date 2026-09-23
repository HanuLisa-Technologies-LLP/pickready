"""Agent tool policy, and provenance on every agent learning (W3.2 to W3.6).

Revision ID: 0089_agent_policy_provenance
Revises: 0088_remove_company_dna
Create Date: 2026-09-09

The revision id is shorter than the file name, deliberately:
`alembic_version.version_num` is varchar(32) and the assigned file name is 38
characters, so the full name cannot be stored and the INSERT fails at the very
end of a successful upgrade. The id is what the chain is built from; the file
name is what a person greps for.

WHAT THIS IS FOR
------------------
`agent_learnings` had no tenant column. A learning is extracted from a run
whose prompt carried a real candidate's answers and a real client's job
description, and every learning was retrievable by every later run for every
customer. That is the single place in this architecture where one tenant's
untrusted input can reach another tenant's decision, and RPN-AI-UP-001 W3.5
requires it closed one of two ways. The decision, with its reasoning, is in
`app/services/memory/provenance.py`: LEARNINGS ARE SCOPED PER TENANT, because a
tenant scope is enforceable structurally by RLS while an approval-before-
promotion step is a process somebody performs under deadline.

THE EXISTING ROWS ARE DELETED, AND THAT IS THE HONEST OPTION
--------------------------------------------------------------
`tenant_id` is NOT NULL, so every pre-existing row needs a value, and there is
no value to give them. A learning that predates provenance cannot say which
customer's text it was extracted from, which is exactly the row this control
exists to stop being read. Backfilling a placeholder tenant would attribute one
customer's rows to another; leaving the column nullable would keep the
cross-tenant read path open for every legacy row, permanently, since a NULL
tenant matches no filter and would have to be special-cased forever by the very
query that is supposed to be doing the scoping.

It deletes nothing in practice. The only writer is
`services/memory/experience.record_failure`, reached only from
`services/reasoning/runner.py`, which `tests/test_ai_reachability.py` records as
having no route or worker into it: no environment has ever executed it. The
DELETE is written anyway rather than assumed, because "the table should be
empty" is a claim about production that a migration must not depend on.

WHY `agent_tool_approval_rules` HAS NO BOOLEAN
------------------------------------------------
Risk class and agent capability stay Python constants (the Layer 1 argument: a
table has an UPDATE, an UPDATE gets an admin screen, and an admin screen makes
tool permissions client editable). The one thing that is data is a tenant
asking for MORE approval than the platform baseline. A row's EXISTENCE is that
request. A `requires_approval` boolean would have a False, and a False row
would be a customer switching off a platform requirement, so the column does
not exist and no future screen can write it.

RLS
-----
Both tables are tenant-scoped with FORCE, on the standard tenant-or-bypass
policy this schema uses everywhere. `agent_learnings` is written by background
work that runs with bypass and read by tenant-scoped sessions, so the WITH
CHECK is the same expression as the USING clause: a tenant-scoped writer may
write its own rows and nobody else's.

DOWNGRADE
-----------
Drops the new table and the added columns, restores the pre-0089 unique index,
and removes the seeded capability rows. It does NOT restore the deleted rows:
they are gone and inventing replacements would be worse than their absence.
"""
from alembic import op

revision = "0089_agent_policy_provenance"
down_revision = "0088_remove_company_dna"
branch_labels = None
depends_on = None

TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"
BYPASS = "current_setting('app.bypass_rls', true) = 'on'"

#: The trust vocabulary of `app/services/memory/provenance.py`, written out
#: rather than imported: a migration must keep meaning what it meant on the day
#: it ran, even after the module it mirrors is edited.
_TRUST_LEVELS = ("platform", "operator", "tenant_authored", "candidate_authored")

#: The risk classes of `app/services/tools/policy.py`, written out for the same
#: reason.
_RISK_CLASSES = ("read", "write_internal", "write_external", "policy_change")

#: W3.6's capability and the role that holds it. The grant engine reads ROWS,
#: and a capability constant with no seeding migration is a control that
#: answers 403 for everybody on a migrations-only database (repaired once
#: already, migration 0075).
_GRANTS: tuple[tuple[str, str], ...] = (("client", "revoke_agent_learnings"),)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    # ── agent_learnings: provenance, and a tenant it cannot be without ───────
    #
    # The DELETE comes first: the columns below are NOT NULL and a row with no
    # honest value for them cannot be kept. See the module docstring.
    op.execute("DELETE FROM agent_learnings")

    op.execute(
        "ALTER TABLE agent_learnings "
        "ADD COLUMN tenant_id uuid NOT NULL "
        "REFERENCES tenants(id) ON DELETE CASCADE"
    )
    op.execute("ALTER TABLE agent_learnings ADD COLUMN source varchar(60) NOT NULL")
    op.execute("ALTER TABLE agent_learnings ADD COLUMN source_version varchar(64)")
    op.execute(
        "ALTER TABLE agent_learnings ADD COLUMN created_by uuid "
        "REFERENCES users(id) ON DELETE SET NULL"
    )
    op.execute("ALTER TABLE agent_learnings ADD COLUMN trust_level varchar(24) NOT NULL")
    op.execute(
        "ALTER TABLE agent_learnings "
        "ADD COLUMN evidence_ids jsonb NOT NULL DEFAULT '[]'::jsonb"
    )
    # NOT NULL and no server default. The writer computes it from the trust
    # level, so a default here would be a second answer to how long a learning
    # lives, and the two would disagree the first time one of them moved.
    op.execute(
        "ALTER TABLE agent_learnings ADD COLUMN revalidate_after timestamptz NOT NULL"
    )
    op.execute(
        "ALTER TABLE agent_learnings ADD CONSTRAINT ck_agent_learnings_trust_level "
        f"CHECK (trust_level IN ({_quoted(_TRUST_LEVELS)}))"
    )

    # The unique key gains the tenant. Without this, two customers hitting the
    # same failure pattern collide on ON CONFLICT and one silently increments
    # the other's counter, which is the cross-tenant write the scope exists to
    # prevent, wearing a counter's clothes.
    op.execute("DROP INDEX IF EXISTS ux_agent_learnings_pattern")
    op.execute(
        "CREATE UNIQUE INDEX ux_agent_learnings_pattern ON agent_learnings "
        "(tenant_id, agent_type, task_type, failure_pattern)"
    )
    # The retrieval query's exact shape: one tenant, one agent, one task,
    # active and unexpired.
    op.execute(
        "CREATE INDEX ix_agent_learnings_retrieval ON agent_learnings "
        "(tenant_id, agent_type, task_type) WHERE is_active"
    )

    op.execute("ALTER TABLE agent_learnings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_learnings FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY agent_learnings_tenant_isolation ON agent_learnings "
        f"USING ((tenant_id = {TENANT}) OR ({BYPASS})) "
        f"WITH CHECK ((tenant_id = {TENANT}) OR ({BYPASS}))"
    )

    # ── agent_tool_approval_rules ───────────────────────────────────────────
    op.execute(
        f"""
        CREATE TABLE agent_tool_approval_rules (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            risk_class varchar(20) NOT NULL,
            created_by uuid REFERENCES users(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_agent_tool_approval_rules_risk_class
                CHECK (risk_class IN ({_quoted(_RISK_CLASSES)})),
            CONSTRAINT uq_agent_tool_approval_rules_tenant_risk
                UNIQUE (tenant_id, risk_class)
        )
        """
    )
    op.execute("ALTER TABLE agent_tool_approval_rules ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_tool_approval_rules FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY agent_tool_approval_rules_tenant_isolation "
        "ON agent_tool_approval_rules "
        f"USING ((tenant_id = {TENANT}) OR ({BYPASS})) "
        f"WITH CHECK ((tenant_id = {TENANT}) OR ({BYPASS}))"
    )

    # ── The capability, seeded ──────────────────────────────────────────────
    #
    # A global template row (tenant_id IS NULL), matching how 0031 and 0075
    # seed. ON CONFLICT DO NOTHING is not available without a unique index over
    # a nullable tenant, so the insert is guarded by NOT EXISTS, which is what
    # makes re-running it safe.
    for role, capability in _GRANTS:
        op.execute(
            "INSERT INTO role_permissions (id, tenant_id, role, capability, allowed) "
            f"SELECT gen_random_uuid(), NULL, '{role}', '{capability}', true "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM role_permissions "
            f"  WHERE tenant_id IS NULL AND role = '{role}' "
            f"    AND capability = '{capability}')"
        )


def downgrade() -> None:
    for role, capability in _GRANTS:
        op.execute(
            "DELETE FROM role_permissions "
            f"WHERE role = '{role}' AND capability = '{capability}'"
        )

    op.execute(
        "DROP POLICY IF EXISTS agent_tool_approval_rules_tenant_isolation "
        "ON agent_tool_approval_rules"
    )
    op.execute("DROP TABLE IF EXISTS agent_tool_approval_rules")

    op.execute(
        "DROP POLICY IF EXISTS agent_learnings_tenant_isolation ON agent_learnings"
    )
    op.execute("ALTER TABLE agent_learnings NO FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_learnings DISABLE ROW LEVEL SECURITY")
    op.execute("DROP INDEX IF EXISTS ix_agent_learnings_retrieval")
    op.execute("DROP INDEX IF EXISTS ux_agent_learnings_pattern")
    op.execute(
        "ALTER TABLE agent_learnings "
        "DROP CONSTRAINT IF EXISTS ck_agent_learnings_trust_level"
    )
    for column in (
        "tenant_id",
        "source",
        "source_version",
        "created_by",
        "trust_level",
        "evidence_ids",
        "revalidate_after",
    ):
        op.execute(f"ALTER TABLE agent_learnings DROP COLUMN IF EXISTS {column}")
    op.execute(
        "CREATE UNIQUE INDEX ux_agent_learnings_pattern ON agent_learnings "
        "(agent_type, task_type, failure_pattern)"
    )
