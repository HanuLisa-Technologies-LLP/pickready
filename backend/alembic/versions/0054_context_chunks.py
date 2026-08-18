"""Chunk-level retrieval index for the context engine.

Revision ID: 0054_context_chunks
Revises: 0053_rebrand_email_templates

WHY A SEPARATE TABLE AND NOT MORE COLUMNS ON `profiles`
-------------------------------------------------------
`profiles.embedding` and `jobs.embedding` are ONE vector for a WHOLE document,
and they stay exactly as they are: they rank candidates, which is a
whole-document question and the only question they were ever asked. Retrieval
for an agent prompt is the opposite shape -- many small pieces per document,
each independently addressable -- and it cannot live in a one-vector column
without either losing the pieces or duplicating the row.

`content_tsv` IS GENERATED, NOT WRITTEN
---------------------------------------
Same reasoning as the posting-window columns: a tsvector maintained by
application code is a tsvector that silently stops matching the row the first
time something writes content by another path. Postgres computes it or nothing
does.

HNSW, MATCHING THE REST OF THE SCHEMA
--------------------------------------
`jobs.reach_embedding` and `profiles.embedding` already use HNSW with
`vector_cosine_ops`. A second index type here would mean two recall/latency
profiles in one product for no reason anybody could later reconstruct.
"""

from alembic import op

revision = "0054_context_chunks"
down_revision = "0053_rebrand_email_templates"
branch_labels = None
depends_on = None

TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"
BYPASS = "current_setting('app.bypass_rls', true) = 'on'"


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE context_chunks (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            source_type varchar(20) NOT NULL,
            source_id uuid NOT NULL,
            source_version varchar(64) NOT NULL,
            section_type varchar(30) NOT NULL,
            ordinal integer NOT NULL,
            content text NOT NULL,
            content_sha256 varchar(64) NOT NULL,
            embedding vector(1024),
            content_tsv tsvector GENERATED ALWAYS AS (
                to_tsvector('english', content)
            ) STORED,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    # One row per (document, position). Re-indexing a document UPSERTs on this,
    # so a re-run is idempotent rather than doubling the corpus -- which is the
    # failure mode that makes a retrieval index quietly return the same evidence
    # twice and look like corroboration.
    op.execute(
        "CREATE UNIQUE INDEX ux_context_chunks_source_ordinal "
        "ON context_chunks (source_type, source_id, ordinal)"
    )
    op.execute(
        "CREATE INDEX ix_context_chunks_lookup "
        "ON context_chunks (tenant_id, source_type, source_id)"
    )
    # Partial: the only reason to scan by version is to delete what a superseded
    # document left behind, and that query always names the source too.
    op.execute(
        "CREATE INDEX ix_context_chunks_version "
        "ON context_chunks (source_id, source_version)"
    )
    op.execute(
        "CREATE INDEX ix_context_chunks_embedding_hnsw ON context_chunks "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.execute(
        "CREATE INDEX ix_context_chunks_tsv ON context_chunks USING gin (content_tsv)"
    )

    # A chunk is a verbatim slice of a candidate's resume or a client's JD. It is
    # tenant data of exactly the kind rule 1 exists for, so the Postgres policy
    # is the boundary and the application filter is defence in depth.
    op.execute("ALTER TABLE context_chunks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE context_chunks FORCE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY context_chunks_tenant_isolation ON context_chunks "
        f"USING ((tenant_id = {TENANT}) OR ({BYPASS})) "
        f"WITH CHECK ((tenant_id = {TENANT}) OR ({BYPASS}))"
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS context_chunks_tenant_isolation ON context_chunks")
    op.execute("DROP TABLE IF EXISTS context_chunks")
