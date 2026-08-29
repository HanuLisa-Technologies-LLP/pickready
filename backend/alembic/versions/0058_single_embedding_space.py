"""Widen jobs.reach_embedding to the platform's one embedding width.

Revision ID: 0058_single_embedding_space
Revises: 0057_report_review

WHY
---
spec-doc5 §B.2 makes voyage-context-4 the sole embedding model for every RAG
surface in the platform. AI Reach's role search was the one surface running a
different model: `BAAI/bge-small-en-v1.5` on CPU through `fastembed`, at 384
dimensions, with `jobs.reach_embedding` declared `vector(384)` to match.

Two embedding models mean two vector spaces that look interchangeable in the
schema and are not. A cosine distance computed between a vector from one and a
vector from the other is a number with no meaning, and nothing about it looks
wrong -- the search still returns roles, just less relevant ones.

WHY EVERY EXISTING VECTOR IS SET TO NULL, AND WHY THAT IS NOT DATA LOSS
------------------------------------------------------------------------
A `vector(384)` column cannot be widened in place while it holds 384-dimension
rows: Postgres refuses the type change. More importantly, KEEPING those numbers
would be the actual mistake. A bge-small vector and a Voyage vector share a
column name and nothing else, so a preserved row would be a stale coordinate in
a space nothing else is in -- silently mis-ranking that job against every
freshly embedded sibling.

`bd_leads.similar_to_customers` already re-embeds any candidate whose vector is
NULL on the next search and writes the result back, which is the same path a
brand-new job takes. So the repair is automatic, needs no backfill job, and the
only cost is that the first AI Reach search after this migration embeds the
corpus once.

ROLLING-DEPLOY SAFETY
---------------------
Not additive, and it cannot be: a column's declared width is not something two
code versions can disagree about. The old code writes 384-dim vectors and the
new code writes 1024-dim ones, so during a rolling deploy the OLD revision's
writes will fail against the widened column.

That failure is contained and non-destructive, which is what makes this
acceptable rather than merely unavoidable:

  * the only writer is the re-embed inside `similar_to_customers`, which is
    already wrapped in `except ReachEmbeddingError` -- an old revision's write
    fails, the search falls back to exact distinctive-role matching, and the
    BD portal keeps working;
  * nothing else in the platform reads or writes this column; and
  * once the rollout completes, the next search repopulates it.

The alternative -- a second column, dual-write, backfill, drop -- would be the
right shape for a column the product depends on. For one derived, automatically
repaired ranking prior on one internal portal, it would be four migrations of
ceremony to protect a few minutes of a degraded sort order on a screen ReadyPick
staff use.

The index is dropped and recreated because an HNSW index is built over a
specific vector width and cannot survive the type change.
"""
from alembic import op

revision = "0058_single_embedding_space"
# The revision ID of 0057, NOT its filename. They differ:
# `0057_report_needs_human_review.py` declares `revision = "0057_report_review"`.
# Naming the file here broke the chain outright -- `alembic upgrade head` died
# with KeyError('0057_report_needs_human_review') before running a single
# statement, so no fresh database could be created at all. Corrected towards the
# revision id rather than by renaming 0057, because every existing database is
# already stamped `0057_report_review` and a rename would orphan all of them.
down_revision = "0057_report_review"
branch_labels = None
depends_on = None

_NEW_DIM = 1024
_OLD_DIM = 384


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_jobs_reach_embedding_hnsw")
    # NULL first: the type change is refused while 384-dim rows are present, and
    # these particular numbers are worth nothing in the new space anyway.
    op.execute("UPDATE jobs SET reach_embedding = NULL WHERE reach_embedding IS NOT NULL")
    op.execute(
        f"ALTER TABLE jobs ALTER COLUMN reach_embedding TYPE vector({_NEW_DIM})"
    )
    op.execute(
        "CREATE INDEX ix_jobs_reach_embedding_hnsw ON jobs "
        "USING hnsw (reach_embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    """Reversible in SHAPE, and honest that it is not reversible in DATA.

    The vectors this migration discarded were computed by a model the
    downgraded code no longer ships with, so there is nothing to restore even in
    principle. The downgrade puts the column back at its old width and leaves it
    empty, which is exactly the state the old code already knows how to repair.
    """
    op.execute("DROP INDEX IF EXISTS ix_jobs_reach_embedding_hnsw")
    op.execute("UPDATE jobs SET reach_embedding = NULL WHERE reach_embedding IS NOT NULL")
    op.execute(
        f"ALTER TABLE jobs ALTER COLUMN reach_embedding TYPE vector({_OLD_DIM})"
    )
    op.execute(
        "CREATE INDEX ix_jobs_reach_embedding_hnsw ON jobs "
        "USING hnsw (reach_embedding vector_cosine_ops)"
    )
