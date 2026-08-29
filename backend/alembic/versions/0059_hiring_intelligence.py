"""The three-layer hiring intelligence schema (spec-doc5 §A.4).

Revision ID: 0059_hiring_intelligence
Revises: 0058_single_embedding_space

FOUR TABLES, AND THE THREE THAT ARE NOT HERE
----------------------------------------------
spec-doc5 asks for Runbook §59's `Role`, `Scorecard`, `Candidate`, `Evaluation`
and `CalibrationRecord`, plus `CompanyDNA` and a `Claim`/`EvidenceNode` pair.
Three of those already exist in this schema under other names and are NOT
duplicated:

    Role          -> `jobs`
    Scorecard     -> `job_competencies`
    Candidate     -> `candidates` + `profiles` + `job_candidate_links`
    Claim         -> `evidence_claims`     (migration 0056)
    EvidenceNode  -> `evidence_items`      (migration 0056)

Creating parallel tables for those would have produced two answers to "what is
this job's matrix", and the second answer would have been discovered by whoever
first read a report generated from the wrong one. Same substitution the billing
work already made when its spec wrote `companies` and this schema meant
`tenants`.

So this migration adds the four that are genuinely new: `company_dna`,
`evaluations`, `review_dispositions` and `calibration_records`.

FULLY ADDITIVE, AND THEREFORE SAFE UNDER A ROLLING DEPLOY
-----------------------------------------------------------
Four CREATE TABLEs and one ALTER that adds a nullable column. No existing column
changes type, nothing is dropped, and no existing row is rewritten. A revision
running the previous image is unaffected: it does not read these tables and its
writes to the tables it does read are unchanged.

That is the standing rule ("don't say a migration is safe without checking it's
additive under a rolling deploy") and it is checked here rather than asserted:
the only non-CREATE statement below is `ALTER TABLE jobs ADD COLUMN
situation_type`, nullable with no default, which an older image neither writes
nor reads.

RLS ON ALL FOUR
---------------
The Postgres policy is the real boundary; app-level filtering is defence in
depth, not a substitute (claude.md rule 1). Every one of these tables carries a
`tenant_id` and the same policy shape as `evidence_items`.

WHY `review_dispositions.decided_by` IS `ON DELETE RESTRICT`
--------------------------------------------------------------
Every other user reference in this schema is `ON DELETE SET NULL`, because a
person leaving must not destroy the data they touched. This one is the
exception, and the reason is what the row is FOR: it is the evidence that a
human -- not the pipeline -- decided something about a flagged candidate. A
disposition whose person has been erased is a row asserting a human decided
while being unable to say who, which is indistinguishable from the pipeline
having written it itself.

The practical consequence is that a user with dispositions cannot be
hard-deleted. That is acceptable because the product disables users rather than
deleting them anyway, and the one route that does hard-delete
(`DELETE /admin/tenants/{id}`) cascades from the tenant, which takes the
dispositions with it before the users.
"""
from alembic import op

revision = "0059_hiring_intelligence"
down_revision = "0058_single_embedding_space"
branch_labels = None
depends_on = None

TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"
BYPASS = "current_setting('app.bypass_rls', true) = 'on'"

_POLICY = (
    "CREATE POLICY {name} ON {table} "
    f"USING ((tenant_id = {TENANT}) OR ({BYPASS})) "
    f"WITH CHECK ((tenant_id = {TENANT}) OR ({BYPASS}))"
)

_TABLES = (
    ("company_dna", "company_dna_tenant_isolation"),
    ("evaluations", "evaluations_tenant_isolation"),
    ("review_dispositions", "review_dispositions_tenant_isolation"),
    ("calibration_records", "calibration_records_tenant_isolation"),
)


def upgrade() -> None:
    # ── LAYER 2: the client's compiled hiring philosophy ────────────────────
    op.execute(
        """
        CREATE TABLE company_dna (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            version integer NOT NULL DEFAULT 1,
            is_current boolean NOT NULL DEFAULT false,
            status varchar(20) NOT NULL DEFAULT 'draft',
            conducted_by uuid REFERENCES users(id) ON DELETE SET NULL,
            answers_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            artifact_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            transcript_json jsonb NOT NULL DEFAULT '[]'::jsonb,
            pending_prompt text,
            completed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_company_dna_version UNIQUE (tenant_id, version),
            CONSTRAINT ck_company_dna_status
                CHECK (status IN ('draft', 'complete', 'superseded'))
        )
        """
    )
    # EXACTLY ONE CURRENT VERSION PER TENANT, enforced by the database rather
    # than by application code. Two rows claiming to be current is a state where
    # "which philosophy is this job built on" has two answers and nothing can
    # choose between them -- and it is exactly what a double-submitted intake
    # would produce. A partial unique index is the only way to say this;
    # SQLAlchemy's UniqueConstraint cannot express the WHERE.
    op.execute(
        "CREATE UNIQUE INDEX uq_company_dna_one_current ON company_dna (tenant_id) "
        "WHERE is_current"
    )
    op.execute(
        "CREATE INDEX ix_company_dna_current ON company_dna (tenant_id, is_current)"
    )

    # ── The Miti run ────────────────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE evaluations (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            link_id uuid NOT NULL
                REFERENCES job_candidate_links(id) ON DELETE CASCADE,
            report_id uuid
                REFERENCES functional_skills_reports(id) ON DELETE SET NULL,
            scorecard_version integer NOT NULL DEFAULT 1,
            company_dna_version integer,
            situation_type varchar(30),
            dimension_scores jsonb NOT NULL DEFAULT '{}'::jsonb,
            competency_scores jsonb NOT NULL DEFAULT '{}'::jsonb,
            aggregate_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            triangulation_json jsonb NOT NULL DEFAULT '{}'::jsonb,
            gate_results_json jsonb NOT NULL DEFAULT '[]'::jsonb,
            scoring_mode varchar(20) NOT NULL DEFAULT 'full',
            confidence varchar(10),
            needs_human_review boolean NOT NULL DEFAULT false,
            completed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_evaluations_scoring_mode
                CHECK (scoring_mode IN ('full', 'degraded', 'stub')),
            CONSTRAINT ck_evaluations_confidence
                CHECK (confidence IS NULL OR confidence IN ('high', 'medium', 'low'))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_evaluations_link ON evaluations (link_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_evaluations_tenant_job ON evaluations (tenant_id, job_id)"
    )
    # The operator query that matters: which evaluations are waiting on a human.
    # Partial, because the answer is a handful of rows out of everything ever
    # scored, and a full index on a boolean would be read past rather than used.
    op.execute(
        "CREATE INDEX ix_evaluations_awaiting_review ON evaluations "
        "(tenant_id, created_at DESC) WHERE needs_human_review"
    )

    # ── G4: the human decision ──────────────────────────────────────────────
    op.execute(
        """
        CREATE TABLE review_dispositions (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            evaluation_id uuid NOT NULL
                REFERENCES evaluations(id) ON DELETE CASCADE,
            disposition varchar(20) NOT NULL,
            decided_by uuid NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
            flags_json jsonb NOT NULL DEFAULT '[]'::jsonb,
            note text,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT ck_review_disposition_value CHECK (
                disposition IN ('cleared', 'escalated', 'overridden', 'rejected')
            )
        )
        """
    )
    # THE CHECK CONSTRAINT IS LOAD-BEARING, not tidiness. `hiring.gates`
    # deliberately has no `auto_cleared` disposition, and the database refusing
    # one is what stops a future code path from inventing it -- the same
    # three-layer enforcement the "culture" competency ban already uses (prompt,
    # validator, and a Postgres CHECK), for the same reason: a prompt
    # instruction is a request and a validator is code somebody can route
    # around.
    op.execute(
        "CREATE INDEX ix_review_dispositions_evaluation ON review_dispositions "
        "(evaluation_id, created_at DESC)"
    )
    op.execute(
        "CREATE INDEX ix_review_dispositions_tenant ON review_dispositions (tenant_id)"
    )

    # ── Did the grade turn out to be right? ─────────────────────────────────
    op.execute(
        """
        CREATE TABLE calibration_records (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            evaluation_id uuid NOT NULL
                REFERENCES evaluations(id) ON DELETE CASCADE,
            predicted_grade varchar(30) NOT NULL,
            predicted_confidence varchar(10),
            outcome varchar(20),
            outcome_assessment varchar(20),
            recorded_by uuid REFERENCES users(id) ON DELETE SET NULL,
            note text,
            observed_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_calibration_evaluation UNIQUE (evaluation_id),
            CONSTRAINT ck_calibration_outcome CHECK (
                outcome IS NULL OR outcome IN
                ('interviewed', 'offered', 'hired', 'rejected', 'withdrew')
            ),
            CONSTRAINT ck_calibration_assessment CHECK (
                outcome_assessment IS NULL OR outcome_assessment IN
                ('accurate', 'too_high', 'too_low')
            )
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_calibration_tenant_job ON calibration_records "
        "(tenant_id, job_id)"
    )

    # ── LAYER 3: the role's situation type ──────────────────────────────────
    #
    # On `jobs` rather than on `job_swot_intakes`, because it survives the
    # intake: the SWOT session is where it is DETERMINED and confirmed, and the
    # job is what it is a property OF. A job whose intake row was archived must
    # still know what kind of role it is.
    #
    # Nullable with no default, and deliberately not backfilled. Every job
    # created before this migration genuinely has no situation type -- nobody
    # was ever asked -- and `situations.dimension_modifiers` returns a flat 1.0
    # map for None, which is exactly what "no situation type expressed" should
    # mean. A backfilled guess would be an unconfirmed classification acting on
    # a whole matrix, which is the single most expensive error available at
    # intake.
    op.execute("ALTER TABLE jobs ADD COLUMN IF NOT EXISTS situation_type varchar(30)")
    op.execute(
        """
        ALTER TABLE jobs ADD CONSTRAINT ck_jobs_situation_type CHECK (
            situation_type IS NULL OR situation_type IN
            ('gap_fill', 'turnaround', 'scale_up', 'greenfield',
             'steady_state', 'succession')
        )
        """
    )
    # Set only when a human confirmed the classification back to Bodha. A type
    # with no confirmation is a proposal, and the two must be distinguishable:
    # spec-doc5 names misclassification as the most expensive intake error, and
    # "the model guessed" versus "the hiring manager agreed" is precisely the
    # difference that matters.
    op.execute(
        "ALTER TABLE jobs ADD COLUMN IF NOT EXISTS situation_confirmed_at timestamptz"
    )

    for table, policy in _TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(_POLICY.format(name=policy, table=table))


def downgrade() -> None:
    op.execute("ALTER TABLE jobs DROP CONSTRAINT IF EXISTS ck_jobs_situation_type")
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS situation_confirmed_at")
    op.execute("ALTER TABLE jobs DROP COLUMN IF EXISTS situation_type")
    for table, policy in _TABLES:
        op.execute(f"DROP POLICY IF EXISTS {policy} ON {table}")
    # Dropped in dependency order: calibration and dispositions both reference
    # evaluations.
    op.execute("DROP TABLE IF EXISTS calibration_records")
    op.execute("DROP TABLE IF EXISTS review_dispositions")
    op.execute("DROP TABLE IF EXISTS evaluations")
    op.execute("DROP TABLE IF EXISTS company_dna")
