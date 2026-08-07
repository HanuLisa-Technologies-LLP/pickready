"""AI Reach role embeddings and automatic invalidation.

Revision ID: 0042_ai_reach_embeddings
Revises: 0041_carry_in_flight
"""

from alembic import op

revision = "0042_ai_reach_embeddings"
down_revision = "0041_carry_in_flight"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # This vector is deliberately separate from jobs.embedding. The existing
    # vector represents the complete JD for candidate matching; AI Reach needs
    # a focused title + primary-skill representation.
    op.execute("ALTER TABLE jobs ADD COLUMN reach_embedding vector(384)")
    op.execute(
        "CREATE INDEX ix_jobs_reach_embedding_hnsw ON jobs "
        "USING hnsw (reach_embedding vector_cosine_ops)"
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION invalidate_job_reach_embedding()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_TABLE_NAME = 'jobs' THEN
                IF OLD.title IS DISTINCT FROM NEW.title
                   OR OLD.jd_json IS DISTINCT FROM NEW.jd_json THEN
                    NEW.reach_embedding := NULL;
                END IF;
                RETURN NEW;
            END IF;

            UPDATE jobs
               SET reach_embedding = NULL
             WHERE id = COALESCE(NEW.job_id, OLD.job_id);
            RETURN COALESCE(NEW, OLD);
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_jobs_invalidate_reach_embedding
        BEFORE UPDATE OF title, jd_json ON jobs
        FOR EACH ROW EXECUTE FUNCTION invalidate_job_reach_embedding()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_competencies_invalidate_reach_embedding
        AFTER INSERT OR UPDATE OR DELETE ON job_competencies
        FOR EACH ROW EXECUTE FUNCTION invalidate_job_reach_embedding()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_competencies_invalidate_reach_embedding "
        "ON job_competencies"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_jobs_invalidate_reach_embedding ON jobs"
    )
    op.execute("DROP FUNCTION IF EXISTS invalidate_job_reach_embedding()")
    op.execute("DROP INDEX IF EXISTS ix_jobs_reach_embedding_hnsw")
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS reach_embedding")
