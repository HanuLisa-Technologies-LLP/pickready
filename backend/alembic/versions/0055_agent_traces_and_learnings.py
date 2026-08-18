"""Episodic traces and extracted learnings for the agent framework.

Revision ID: 0055_agent_traces
Revises: 0054_context_chunks

WHY A TABLE AND NOT MORE LOGGING
---------------------------------
Cloud Run logs answer "what happened at 14:02". They do not answer "how often
does the ranking agent degrade for this tenant", because that question needs a
GROUP BY and a retention window longer than a log sink. This product has been
bitten specifically by health checks that asked a stamp instead of a table, so
the agent framework's own health gets a table.

WHAT IS DELIBERATELY NOT IN IT
------------------------------
No prompt text, no answer text, no remark text. A trace row records identifiers,
counts, timings, statuses and typed defects. The standing rule is that telemetry
carries labels and keys and never content, and a trace table is more widely
readable than a LangSmith project, not less -- anyone with database access can
read it, and prompts carry a real candidate's answers.

`agent_learnings` IS SMALL ON PURPOSE
--------------------------------------
It stores a failure pattern and the adjustment that fixed it, scoped to a task
type. It is retrieved at planning time as a hint, never as an instruction that
bypasses a deterministic gate -- a learning that could switch off a criterion
would be a way for one bad run to permanently lower the bar.
"""

from alembic import op

revision = "0055_agent_traces"
down_revision = "0054_context_chunks"
branch_labels = None
depends_on = None

TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"
BYPASS = "current_setting('app.bypass_rls', true) = 'on'"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE agent_execution_traces (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            request_id varchar(64) NOT NULL,
            tenant_id uuid REFERENCES tenants(id) ON DELETE CASCADE,
            agent_type varchar(40) NOT NULL,
            task_type varchar(40) NOT NULL,
            job_id uuid,
            link_id uuid,
            status varchar(20) NOT NULL,
            complexity varchar(20),
            fast_path boolean NOT NULL DEFAULT false,
            attempts integer NOT NULL DEFAULT 0,
            degraded boolean NOT NULL DEFAULT false,
            confidence numeric(5, 4),
            duration_ms integer NOT NULL DEFAULT 0,
            generated_tokens integer NOT NULL DEFAULT 0,
            cost_usd numeric(10, 6) NOT NULL DEFAULT 0,
            tool_calls integer NOT NULL DEFAULT 0,
            stages jsonb NOT NULL DEFAULT '[]'::jsonb,
            defects jsonb NOT NULL DEFAULT '[]'::jsonb,
            failure_category varchar(40),
            created_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_agent_traces_recent ON agent_execution_traces "
        "(agent_type, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_agent_traces_request ON agent_execution_traces (request_id)"
    )
    # The operational query: what is failing, for whom, lately.
    op.execute(
        "CREATE INDEX ix_agent_traces_failures ON agent_execution_traces "
        "(tenant_id, created_at DESC) WHERE status <> 'success'"
    )

    op.execute(
        """
        CREATE TABLE agent_learnings (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            agent_type varchar(40) NOT NULL,
            task_type varchar(40) NOT NULL,
            failure_pattern varchar(120) NOT NULL,
            applied_fix text NOT NULL,
            observations integer NOT NULL DEFAULT 1,
            successes integer NOT NULL DEFAULT 0,
            is_active boolean NOT NULL DEFAULT true,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # One row per (agent, task, pattern): a repeat observation increments a
    # counter rather than adding a row, so "how often" is a column and not a
    # COUNT over an unbounded log.
    op.execute(
        "CREATE UNIQUE INDEX ux_agent_learnings_pattern ON agent_learnings "
        "(agent_type, task_type, failure_pattern)"
    )

    # Traces carry a tenant id and therefore carry the tenant boundary. Learnings
    # deliberately do NOT: a lesson about how the ranking agent misreads a JD is
    # a property of the product, not of a customer, and scoping it per tenant
    # would mean every tenant relearns it separately.
    op.execute("ALTER TABLE agent_execution_traces ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE agent_execution_traces FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY agent_traces_tenant_isolation ON agent_execution_traces "
        f"USING ((tenant_id = {TENANT}) OR (tenant_id IS NULL) OR ({BYPASS})) "
        f"WITH CHECK ((tenant_id = {TENANT}) OR (tenant_id IS NULL) OR ({BYPASS}))"
    )


def downgrade() -> None:
    op.execute(
        "DROP POLICY IF EXISTS agent_traces_tenant_isolation ON agent_execution_traces"
    )
    op.execute("DROP TABLE IF EXISTS agent_learnings")
    op.execute("DROP TABLE IF EXISTS agent_execution_traces")
