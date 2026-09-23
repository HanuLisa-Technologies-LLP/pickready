"""An HNSW index on `jobs.embedding`, the one vector column that never had one.

Revision ID: 0098_jobs_embedding_hnsw_index
Revises: 0097_job_swot_analysis

WHY THIS IS DEFENSIVE AND SAYS SO
----------------------------------
`jobs.embedding` has been a `vector(1024)` since 0001 and has never been
indexed, while every other vector column in this schema is: `profiles.embedding`
(0001), `jobs.reach_embedding` (0042, rebuilt at 1024 by 0058) and
`context_chunks.embedding` (0054). That asymmetry was not a decision, it is an
omission nobody had reason to notice, because the one live reader of this
column reaches it through a primary-key prefilter:
`services/job_relevance` issues `j.id = ANY(:job_ids)` and then orders the
handful of rows that survive, which is an index scan on the primary key
followed by a sort over a few distances. No sequential scan, and no measurable
cost today.

What makes it worth a migration anyway is the shape of the failure when it
arrives. The first caller that writes `ORDER BY j.embedding <=> :vec LIMIT n`
without a prefilter gets a correct answer, computed by reading every job row in
the table and distance-scoring each one, in every tenant. It is not an error,
it produces no log line, and it degrades with the size of the customer base
rather than with anything in the request. An index that is already there costs
that caller nothing to remember.

SAME SYNTAX, SAME OPCLASS, ON PURPOSE
--------------------------------------
Mirrors 0058 exactly: `USING hnsw (<column> vector_cosine_ops)`, with the build
parameters left at the extension defaults. All four vector indexes in this
schema are then identical in shape, so a future change to how this product
builds them is one decision applied four times rather than four decisions.
Cosine because that is the distance every retrieval path in this product
already uses, and an opclass that does not match the operator in the query is
an index the planner silently declines to use.

`IF NOT EXISTS` on the create and `IF EXISTS` on the drop, because an
environment that was hand-repaired before this migration ran must not fail the
deploy over already being correct.
"""
from alembic import op

revision = "0098_jobs_embedding_hnsw_index"
down_revision = "0097_job_swot_analysis"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_jobs_embedding_hnsw ON jobs "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    """Fully reversible: this migration writes no data and changes no type.

    Dropping the index returns the column to exactly the state 0097 left it in,
    which is an unindexed `vector(1024)` that every current reader queries
    through a primary-key prefilter and is therefore unaffected.
    """
    op.execute("DROP INDEX IF EXISTS ix_jobs_embedding_hnsw")
