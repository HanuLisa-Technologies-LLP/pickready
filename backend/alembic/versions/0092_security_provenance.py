"""Security provenance: intake limitation flags and chunk-level ACL metadata.

RPN-AI-UP-001 W9.2 and W9.5.

WHAT THIS ADDS, AND THE ONE SENTENCE OF WHY FOR EACH
------------------------------------------------------
* `profiles.intake_scan_json` and `candidate_projects.intake_scan_json`. A
  measured one percent of real resumes carry a prompt injection attempt, rising
  sevenfold between July 2024 and November 2025, and the carrier is content a
  human reader cannot see. `services/projects/invisible_text` detects it
  deterministically at intake; these columns are where the detection LANDS.
  They are provenance, never a decision: nothing may read them to reject, rank
  or filter a candidate. A manipulated screen that advances an unqualified
  candidate or buries a qualified one is a discrimination liability event if the
  decision is later challenged, and an audit trail gap makes it indefensible.
  This column IS the audit trail.

* `context_chunks.acl_capability` and `context_chunks.acl_candidate_id`. An
  embedding is not a one-way hash: published inversion work recovers 50 to 70%
  of the input words from popular sentence embeddings, and because the embedding
  model is public and queryable, dictionary attacks against stolen vectors are
  practical, structurally like cracking password hashes. `context_chunks.embedding`
  is therefore PII at rest, and it is classified as such in
  `services/erasure.VECTOR_COLUMNS` alongside `profiles.embedding`,
  `jobs.embedding` and `jobs.reach_embedding`.

WHY THE ACL IS ON THE CHUNK AND MAINTAINED BY A TRIGGER
---------------------------------------------------------
On the CHUNK, not only on the source document, because retrieval reads chunks
and a join to the document is a join a future query will forget. Enforced at
RETRIEVAL time, not at write time, because permissions change after storage: a
recruiter loses `view_review_screen` and every chunk written before that moment
is still in the index.

By a TRIGGER, not only by the indexer, because a column that only a remembering
caller populates is a column that is NULL on the row that mattered. `rag/index`
UPSERTs chunks in one statement today; a second writer tomorrow inherits the
classification for free, and the seeding-migration lesson (a capability constant
shipped without its rows for a whole phase) is exactly this failure in another
table.

WHY `jobs.reach_embedding` IS DELIBERATELY NOT GIVEN AN ACL COLUMN
--------------------------------------------------------------------
AI Reach is the ONE place in this product where cross-tenant vector similarity
is a feature rather than a leak, and the justification is written down here so
it does not read as the finding in the first security review:

  `bd_leads` AI Reach compares a prospect's role description against ReadyPick's
  OWN customer catalogue -- `jobs.reach_embedding` over `tenants` this company
  onboarded -- and it is reached only by platform staff (`Role.bd`,
  `tenant_id IS NULL`, the OWNER token audience). It embeds a JOB TITLE and the
  job's primary skill names, which the client publishes on a public application
  page. It embeds no resume, no candidate, no assessment and no score, and it
  returns a word (High, Medium, Low), never a number. The cross-tenant read is
  the product: a BD rep asking "which of our customers already hire this role"
  cannot be answered inside one tenant.

  What it needs and does not yet have is the AUDIT TRAIL. Every AI Reach search
  should write one `audit.record_action` row naming the BD actor and the query.
  The call site is in `api/bd.py`, which this workstream does not own; the exact
  diff is reported with this change.

Revision ID: 0092_security_provenance
Revises: 0091_contextual_evidence
Create Date: 2026-09-09
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0092_security_provenance"
down_revision = "0091_contextual_evidence"
branch_labels = None
depends_on = None

#: The capability a reader must hold to retrieve a chunk carrying a candidate's
#: own words. It is `capabilities.VIEW_REVIEW_SCREEN`, spelled as a literal here
#: because a migration must keep meaning what it meant on the day it ran even if
#: the constant is later renamed in Python.
_CANDIDATE_CHUNK_CAPABILITY = "view_review_screen"

#: The source types whose chunks are a person's own text. `jd` is absent: a job
#: description is text the tenant itself wrote and published.
_CANDIDATE_SOURCE_TYPES = ("resume", "assessment")


def upgrade() -> None:
    # ── W9.2: intake limitation flags ────────────────────────────────────────
    op.add_column("profiles", sa.Column("intake_scan_json", JSONB(), nullable=True))
    op.add_column(
        "candidate_projects", sa.Column("intake_scan_json", JSONB(), nullable=True)
    )
    # NULL means "this row predates the scan", which is a different fact from
    # "scanned and clean" and is deliberately not backfilled to one. Backfilling
    # would assert that a file nobody inspected was inspected.

    # ── W9.5: chunk-level ACL ────────────────────────────────────────────────
    op.add_column(
        "context_chunks", sa.Column("acl_capability", sa.String(64), nullable=True)
    )
    op.add_column(
        "context_chunks", sa.Column("acl_candidate_id", UUID(as_uuid=True), nullable=True)
    )
    op.create_check_constraint(
        "ck_context_chunks_acl_capability_not_blank",
        "context_chunks",
        "acl_capability IS NULL OR length(btrim(acl_capability)) > 0",
    )
    # Partial: the erasure cascade and the ACL predicate both read only the rows
    # that carry a candidate, and a full index would carry a NULL entry for
    # every JD chunk in the product.
    op.execute(
        "CREATE INDEX ix_context_chunks_acl_candidate ON context_chunks "
        "(acl_candidate_id) WHERE acl_candidate_id IS NOT NULL"
    )

    # The trigger. `BEFORE INSERT OR UPDATE`, so every writer is covered without
    # any writer knowing this exists. The lookups may legitimately find nothing
    # under a tenant-scoped session, in which case the capability is still set:
    # failing towards MORE restriction is the only safe direction for an ACL.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION context_chunks_set_acl()
        RETURNS trigger AS $$
        BEGIN
            IF NEW.source_type IN {_CANDIDATE_SOURCE_TYPES!r} THEN
                IF NEW.acl_capability IS NULL THEN
                    NEW.acl_capability := '{_CANDIDATE_CHUNK_CAPABILITY}';
                END IF;
                IF NEW.acl_candidate_id IS NULL THEN
                    IF NEW.source_type = 'resume' THEN
                        SELECT p.candidate_id INTO NEW.acl_candidate_id
                          FROM profiles p WHERE p.id = NEW.source_id;
                    ELSE
                        SELECT l.candidate_id INTO NEW.acl_candidate_id
                          FROM job_candidate_links l WHERE l.id = NEW.source_id;
                    END IF;
                END IF;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER context_chunks_set_acl_trigger "
        "BEFORE INSERT OR UPDATE ON context_chunks "
        "FOR EACH ROW EXECUTE FUNCTION context_chunks_set_acl()"
    )

    # Backfill. Every chunk already in the index was written before the trigger
    # existed, and an unclassified resume chunk is exactly the row the ACL is
    # for. `alembic/env.py` runs migrations with `app.bypass_rls = 'on'`, so the
    # tenant policy does not hide rows from this statement.
    op.execute(
        """
        UPDATE context_chunks c
           SET acl_candidate_id = p.candidate_id,
               acl_capability = COALESCE(c.acl_capability, 'view_review_screen')
          FROM profiles p
         WHERE c.source_type = 'resume' AND c.source_id = p.id
        """
    )
    op.execute(
        """
        UPDATE context_chunks c
           SET acl_candidate_id = l.candidate_id,
               acl_capability = COALESCE(c.acl_capability, 'view_review_screen')
          FROM job_candidate_links l
         WHERE c.source_type = 'assessment' AND c.source_id = l.id
        """
    )
    op.execute(
        """
        UPDATE context_chunks
           SET acl_capability = 'view_review_screen'
         WHERE source_type IN ('resume', 'assessment')
           AND acl_capability IS NULL
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS context_chunks_set_acl_trigger ON context_chunks"
    )
    op.execute("DROP FUNCTION IF EXISTS context_chunks_set_acl()")
    op.execute("DROP INDEX IF EXISTS ix_context_chunks_acl_candidate")
    op.drop_constraint(
        "ck_context_chunks_acl_capability_not_blank", "context_chunks", type_="check"
    )
    op.drop_column("context_chunks", "acl_candidate_id")
    op.drop_column("context_chunks", "acl_capability")
    op.drop_column("candidate_projects", "intake_scan_json")
    op.drop_column("profiles", "intake_scan_json")
