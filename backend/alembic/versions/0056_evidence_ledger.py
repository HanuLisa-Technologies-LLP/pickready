"""The shared evidence ledger: claims, the evidence under them, and the stance.

Revision ID: 0056_evidence_ledger
Revises: 0055_agent_traces

WHY THREE TABLES AND NOT ONE
-----------------------------
A claim and a piece of evidence have different lifetimes. A resume is replaced
and every item cut from it is superseded at once, while the claims those items
supported are still the claims -- some of them now standing on the new resume
instead. Folding them into one table would mean either duplicating a claim per
item or losing the ability to retire an item on its own.

The third table is the stance, and it is a table rather than two uuid arrays on
the claim because an array pair makes "the same item on both sides" a
representable state, and there is nothing a reader could do with that state
except guess. `stance` as a column also makes the query that matters -- which
claims does this evidence touch -- an index lookup instead of an array scan.

`text_ref`, AND DELIBERATELY NO TEXT COLUMN
--------------------------------------------
The column holds a locator: a table name, a row id, and optionally a fragment.
It never holds the sentence. Same rule that made `agent_execution_traces` drop a
defect's `detail`: an excerpt quotes a real candidate's answer, and this table
is far more widely readable than the transcript it points at -- reading a
transcript needs `view_review_screen`, while reading a row here needs database
access. A `text` column would be a quiet route around that capability, and it
would be filled the first week it existed. Resolution goes back to the source
table under the caller's own tenant scope.

`relevance` IS INTERNAL ENGINEERING METADATA
---------------------------------------------
It orders evidence inside a prompt and inside an operator view. It is not a
score and it must never reach a client, which is the product's oldest standing
rule. It is `numeric` rather than `double precision` for the same reason every
other number here is: a float that renders as 0.30000000000000004 in an
operator view is a number nobody can reconcile with the one beside it.

WHY `trust` IS A WORD AND NOT A WEIGHT
---------------------------------------
authoritative / validated / observed / inferred is an ORDER, not a scale. The
CHECK constraint is what stops a future caller storing 0.8 in it and starting
the arithmetic that would let two inferences add up to an observation.

TENANT SCOPE IS THE POSTGRES POLICY
------------------------------------
All three tables carry `tenant_id` NOT NULL and all three get the same policy
`context_chunks` has. Evidence is a pointer into a candidate's resume and a
client's JD; it is exactly the data claude.md rule 1 exists for, and the
application's WHERE clause is defence in depth rather than the boundary. The
`bd_leads`-style NULL-tenant escape is deliberately absent: there is no
platform-level evidence, so a NULL tenant here could only be a bug.
"""

from alembic import op

revision = "0056_evidence_ledger"
down_revision = "0055_agent_traces"
branch_labels = None
depends_on = None

TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"
BYPASS = "current_setting('app.bypass_rls', true) = 'on'"

_POLICY = (
    "CREATE POLICY {name} ON {table} "
    f"USING ((tenant_id = {TENANT}) OR ({BYPASS})) "
    f"WITH CHECK ((tenant_id = {TENANT}) OR ({BYPASS}))"
)


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE evidence_items (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            link_id uuid REFERENCES job_candidate_links(id) ON DELETE CASCADE,
            source_type varchar(20) NOT NULL,
            source_id uuid NOT NULL,
            text_ref text NOT NULL,
            provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
            freshness jsonb NOT NULL DEFAULT '{}'::jsonb,
            trust varchar(20) NOT NULL,
            relevance numeric(5, 4) NOT NULL DEFAULT 0,
            status varchar(20) NOT NULL DEFAULT 'active',
            superseded_by uuid REFERENCES evidence_items(id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_evidence_items_source_type CHECK (
                source_type IN ('resume', 'answer', 'jd', 'swot', 'validation', 'memory')
            ),
            CONSTRAINT ck_evidence_items_trust CHECK (
                trust IN ('authoritative', 'validated', 'observed', 'inferred')
            ),
            CONSTRAINT ck_evidence_items_status CHECK (
                status IN ('active', 'superseded', 'revoked')
            )
        )
        """
    )
    # `link_id` is NULLABLE and that is the JD/SWOT case: evidence about the
    # ROLE belongs to the job and to no candidate. Making it NOT NULL would
    # force a sentinel application row for every job description.

    op.execute(
        """
        CREATE TABLE evidence_claims (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            link_id uuid REFERENCES job_candidate_links(id) ON DELETE CASCADE,
            subject varchar(160) NOT NULL,
            dimension varchar(160) NOT NULL,
            claim text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # There is NO support-state column, and its absence is the design. A stored
    # state is a mirror that goes stale the moment an item is revoked by another
    # path, and the stale value would be the one a report gets written from. The
    # state is derived from the joined rows every time it is asked for.

    op.execute(
        """
        CREATE TABLE evidence_claim_links (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            claim_id uuid NOT NULL REFERENCES evidence_claims(id) ON DELETE CASCADE,
            evidence_id uuid NOT NULL REFERENCES evidence_items(id) ON DELETE CASCADE,
            stance varchar(20) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_evidence_claim_links_stance CHECK (
                stance IN ('supports', 'contradicts')
            )
        )
        """
    )

    # One claim per (tenant, job, application, subject, dimension, wording). The
    # failure it prevents is two rows asserting the same thing with half the
    # evidence each, which reads to anybody scanning the ledger as two
    # independent findings and is one. NULLS NOT DISTINCT because a job-level
    # claim has a NULL link_id and must still collide with itself.
    op.execute(
        "CREATE UNIQUE INDEX ux_evidence_claims_identity ON evidence_claims "
        "(tenant_id, job_id, link_id, subject, dimension, claim) NULLS NOT DISTINCT"
    )
    # The read path: every claim on one job, optionally narrowed to one
    # application. Exactly what `ledger.load_claims` asks for and nothing wider.
    op.execute(
        "CREATE INDEX ix_evidence_claims_scope ON evidence_claims "
        "(tenant_id, job_id, link_id)"
    )
    # Attaching evidence twice is an UPSERT rather than a second row, so a
    # re-run of a scoring pass does not make one item look like two.
    op.execute(
        "CREATE UNIQUE INDEX ux_evidence_claim_links_pair ON evidence_claim_links "
        "(claim_id, evidence_id)"
    )
    # The join in `_CLAIM_SELECT` walks items from links, and supersede/revoke
    # walk items from their source document.
    op.execute(
        "CREATE INDEX ix_evidence_claim_links_evidence ON evidence_claim_links "
        "(evidence_id)"
    )
    op.execute(
        "CREATE INDEX ix_evidence_items_source ON evidence_items "
        "(tenant_id, source_type, source_id)"
    )

    for table, policy in (
        ("evidence_items", "evidence_items_tenant_isolation"),
        ("evidence_claims", "evidence_claims_tenant_isolation"),
        ("evidence_claim_links", "evidence_claim_links_tenant_isolation"),
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(_POLICY.format(name=policy, table=table))


def downgrade() -> None:
    # Links first: it references both of the others.
    for table, policy in (
        ("evidence_claim_links", "evidence_claim_links_tenant_isolation"),
        ("evidence_claims", "evidence_claims_tenant_isolation"),
        ("evidence_items", "evidence_items_tenant_isolation"),
    ):
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
    op.execute("DROP TABLE IF EXISTS evidence_claim_links")
    op.execute("DROP TABLE IF EXISTS evidence_claims")
    op.execute("DROP TABLE IF EXISTS evidence_items")
