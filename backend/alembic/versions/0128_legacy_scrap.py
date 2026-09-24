"""The legacy scrap: four empty tables, two dead columns, one retired capability.

Revision ID: 0128_legacy_scrap
Revises: 0118_skills_contract

Phase 7 Wave B (WP-B2) of the Vivekium release. Every object below lost its
last live reader in the same change, and CONTRACT v3 (the read-only pilot
probe of 2026-09-24) found every table empty and the column all NULL. S4's
"no irreversible drop of a table that may hold customer rows" is therefore
kept by a GUARD rather than by keeping the table: each drop is preceded by a
count that RAISES, naming the table and the count, and aborts the whole
upgrade when there is anything there. On a database that holds rows the
migration refuses; deleting those rows is an owner decision, never a side
effect of a deploy.

DROPPED, EACH ONLY WHEN EMPTY
-----------------------------
* `technical_questions`, the per-job preset bank retired on 2026-08-06 and
  read by nothing since.
* `candidate_technical_questions`, the per-candidate technical track folded
  into Must-have by Draft v4. Its last readers (the transcript label resolver,
  the report evidence extraction, the scoring task, the job-closure erasure)
  stop naming it in this change.
* `llm_provider_keys`, the multi-vendor key roster. The router has resolved
  credentials from settings alone since the single-vendor consolidation.
* `otp_challenges`, the login one-time codes. Firebase owns identity; the code
  login was deleted in Wave A. The table would only ever have held expired
  challenges keyed by an email or a phone number, which is personal data with
  no purpose left.

`verification_requests` is NOT here: Phase 6's candidate communications
migration drops it behind the same guard, and one drop per table is the rule.

`job_swot_intakes` and `jobs.level` are KEPT: pilot holds rows in both
(3 transcripts, 30 levels), so they stay as history.

COLUMNS
-------
* `jobs.questions_approved_at`, stamped by nothing and read by nothing since
  the technical approval stopped gating (2026-08-04). Dropped only when every
  row is NULL, for the same reason as the tables: a non-NULL stamp is a
  record somebody might want, and this migration does not decide that.
* `tenants.credit_deficit`, a derived cache of `SUM(credit_ledger.
  subunits_delta) < 0` that nothing reads. Nothing is lost by dropping it:
  the downgrade recomputes it from the ledger.

`profiles.resume_storage_provider` keeps its data and changes its DEFAULT:
the server default was 'cloudinary' and the ORM default 'gcs', while every
writer stores objects in S3 and says so explicitly. A writer that forgot the
column therefore labelled an S3 object with a provider it never touched. Both
defaults are 's3' now. No row is rewritten: a stored label is history.

THE CAPABILITY
--------------
`revoke_agent_learnings` guarded the revocation route of the experience
memory, which the same wave deletes as unreachable. Every `role_permissions`
row for it, global and per-tenant, is deleted: a capability and its seeding
are one change, and so is its removal (`test_capability_seed_parity`). A stale
key in a user's `permissions_json` overlay needs no rewrite, because
`rbac.sanitize_overrides` drops an unknown capability on every read.

WHY THE GUARD CHECKS THE RLS SCOPE FIRST
----------------------------------------
Two of the tables carry FORCE ROW LEVEL SECURITY, which binds the table OWNER
too. A count taken by a migration role that is neither a superuser nor in the
bypass scope reads ZERO over a table full of rows, and "zero" is exactly the
answer that lets the drop proceed. `alembic/env.py` sets `app.bypass_rls` for
every migration, so this should never fire; `_require_unfiltered_reads`
refuses the upgrade if it ever stops being true, because a guard that can be
fooled into reporting empty is worse than no guard.

DOWNGRADE recreates every table exactly as `pg_dump -s` showed it at
0118_skills_contract (columns, defaults, keys, indexes, FORCE RLS, policies,
grants), re-adds both columns (`credit_deficit` recomputed from the ledger),
restores the 'cloudinary' server default and re-seeds 0089's single global
grant. What it cannot restore, and says so:
rows (there were none to drop), and per-tenant copies of the capability row,
which pointed at a route that no longer exists.
"""
from __future__ import annotations

import logging

from alembic import op

revision = "0128_legacy_scrap"
down_revision = "0118_skills_contract"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration.0128_legacy_scrap")

TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"
BYPASS = "current_setting('app.bypass_rls', true) = 'on'"

#: Dropped in this order: children before parents is irrelevant (nothing
#: references any of them), so the order is simply the order they retired in.
DROPPED_TABLES: tuple[str, ...] = (
    "technical_questions",
    "candidate_technical_questions",
    "llm_provider_keys",
    "otp_challenges",
)

RETIRED_CAPABILITY = "revoke_agent_learnings"
#: 0089's one global grant, for the downgrade only.
_PREVIOUS_GLOBAL_GRANT: tuple[str, str] = ("client", RETIRED_CAPABILITY)


def _require_unfiltered_reads() -> None:
    """RAISE unless this connection sees every row of an RLS-protected table.

    A superuser and a role with BYPASSRLS skip every policy; anybody else needs
    `app.bypass_rls = 'on'`, which `alembic/env.py` sets. Without one of the
    three, the emptiness guards below would count zero over rows they cannot
    see and drop the table anyway.
    """
    message = (
        "0128 refuses to run: this connection reads row level security "
        "filtered rows, so an emptiness check could report zero over a table "
        "that holds rows. Set app.bypass_rls as alembic/env.py does."
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT ({BYPASS})
               AND NOT (SELECT rolsuper OR rolbypassrls
                          FROM pg_roles WHERE rolname = current_user) THEN
                RAISE EXCEPTION '{message}';
            END IF;
        END
        $$;
        """
    )


def _refuse_if_any(sql: str, message: str) -> None:
    """RAISE with `message` (which takes the count as `%`) when `sql` counts
    anything. The same guard shape Phase 6 uses for `verification_requests`."""
    op.execute(
        f"""
        DO $$
        DECLARE
            affected bigint;
        BEGIN
            SELECT ({sql}) INTO affected;
            IF affected > 0 THEN
                RAISE EXCEPTION '{message}', affected;
            END IF;
        END
        $$;
        """
    )


def _guard_empty(table: str) -> None:
    _refuse_if_any(
        f"SELECT count(*) FROM {table}",
        f"{table} holds % row(s). It may be dropped only when it is "
        "empty; deleting those rows is an owner decision, not a deploy.",
    )


def _guard_all_null(table: str, column: str) -> None:
    _refuse_if_any(
        f"SELECT count(*) FROM {table} WHERE {column} IS NOT NULL",
        f"{table}: % row(s) carry {column}. The column may be dropped only "
        "when every value is NULL.",
    )


def upgrade() -> None:
    _require_unfiltered_reads()

    # ── 1. The four retired tables, each only when empty ─────────────────────
    for table in DROPPED_TABLES:
        _guard_empty(table)
        op.execute(f"DROP TABLE {table}")

    # ── 2. jobs.questions_approved_at, only when every stamp is NULL ─────────
    _guard_all_null("jobs", "questions_approved_at")
    op.execute("ALTER TABLE jobs DROP COLUMN questions_approved_at")

    # ── 3. tenants.credit_deficit, a derived cache nothing reads ─────────────
    op.execute("ALTER TABLE tenants DROP COLUMN credit_deficit")

    # ── 4. The resume provider default names the store actually used ────────
    op.execute(
        "ALTER TABLE profiles ALTER COLUMN resume_storage_provider SET DEFAULT 's3'"
    )

    # ── 5. The retired capability's rows, global and per-tenant ──────────────
    deleted = (
        op.get_bind()
        .exec_driver_sql(
            f"DELETE FROM role_permissions WHERE capability = '{RETIRED_CAPABILITY}'"
        )
        .rowcount
    )
    logger.info(
        "0128 dropped_tables=%s dropped_columns=jobs.questions_approved_at,"
        "tenants.credit_deficit %s_role_permissions_deleted=%d",
        ",".join(DROPPED_TABLES), RETIRED_CAPABILITY, deleted,
    )


def _tenant_policy(table: str) -> None:
    expr = f"(tenant_id = {TENANT}) OR ({BYPASS})"
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {table}_tenant_isolation ON {table} "
        f"USING ({expr}) WITH CHECK ({expr})"
    )


def downgrade() -> None:
    # ── 5 ────────────────────────────────────────────────────────────────────
    role, capability = _PREVIOUS_GLOBAL_GRANT
    op.execute(
        "INSERT INTO role_permissions (id, tenant_id, role, capability, allowed) "
        f"SELECT gen_random_uuid(), NULL, '{role}', '{capability}', true "
        "WHERE NOT EXISTS ("
        "  SELECT 1 FROM role_permissions "
        f"  WHERE tenant_id IS NULL AND role = '{role}' "
        f"    AND capability = '{capability}')"
    )

    # ── 4 ────────────────────────────────────────────────────────────────────
    op.execute(
        "ALTER TABLE profiles ALTER COLUMN resume_storage_provider "
        "SET DEFAULT 'cloudinary'"
    )

    # ── 3. Recomputed, never guessed: the ledger is the balance ──────────────
    op.execute(
        "ALTER TABLE tenants ADD COLUMN credit_deficit boolean NOT NULL DEFAULT false"
    )
    op.execute(
        """
        UPDATE tenants t
           SET credit_deficit = TRUE
         WHERE NOT t.is_demo
           AND (SELECT COALESCE(SUM(l.subunits_delta), 0)
                  FROM credit_ledger l WHERE l.tenant_id = t.id) < 0
        """
    )

    # ── 2 ────────────────────────────────────────────────────────────────────
    op.execute("ALTER TABLE jobs ADD COLUMN questions_approved_at timestamptz")

    # ── 1. The tables, exactly as they stood at 0118 ─────────────────────────
    op.execute(
        """
        CREATE TABLE otp_challenges (
            id uuid NOT NULL,
            user_id uuid,
            identifier varchar(320) NOT NULL,
            channel varchar(10) NOT NULL,
            code_hash varchar(64) NOT NULL,
            expires_at timestamptz NOT NULL,
            attempts integer DEFAULT 0 NOT NULL,
            consumed_at timestamptz,
            created_at timestamptz DEFAULT now() NOT NULL,
            CONSTRAINT otp_challenges_pkey PRIMARY KEY (id),
            CONSTRAINT otp_challenges_user_id_fkey FOREIGN KEY (user_id)
                REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_otp_challenges_open ON otp_challenges "
        "(identifier, expires_at DESC) WHERE consumed_at IS NULL"
    )

    op.execute(
        """
        CREATE TABLE llm_provider_keys (
            id uuid NOT NULL,
            provider varchar(30) NOT NULL,
            key_encrypted text NOT NULL,
            role_hint varchar(30) NOT NULL,
            priority integer DEFAULT 0 NOT NULL,
            healthy boolean DEFAULT true NOT NULL,
            last_error_at timestamptz,
            created_at timestamptz DEFAULT now() NOT NULL,
            CONSTRAINT llm_provider_keys_pkey PRIMARY KEY (id)
        )
        """
    )

    op.execute(
        """
        CREATE TABLE candidate_technical_questions (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            job_id uuid NOT NULL,
            job_candidate_link_id uuid NOT NULL,
            ordinal integer NOT NULL,
            skill varchar(255) NOT NULL,
            prompt text NOT NULL,
            rubric_json jsonb NOT NULL,
            generated_at timestamptz,
            created_at timestamptz DEFAULT now() NOT NULL,
            CONSTRAINT candidate_technical_questions_pkey PRIMARY KEY (id),
            CONSTRAINT uq_candidate_technical_ordinal
                UNIQUE (job_candidate_link_id, ordinal),
            CONSTRAINT candidate_technical_questions_job_candidate_link_id_fkey
                FOREIGN KEY (job_candidate_link_id)
                REFERENCES job_candidate_links(id) ON DELETE CASCADE,
            CONSTRAINT candidate_technical_questions_job_id_fkey
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
            CONSTRAINT candidate_technical_questions_tenant_id_fkey
                FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_candidate_technical_link ON candidate_technical_questions "
        "(job_candidate_link_id, ordinal)"
    )
    _tenant_policy("candidate_technical_questions")

    op.execute(
        """
        CREATE TABLE technical_questions (
            id uuid NOT NULL,
            tenant_id uuid NOT NULL,
            job_id uuid NOT NULL,
            ordinal integer NOT NULL,
            skill varchar(255) NOT NULL,
            prompt text NOT NULL,
            rubric_json jsonb NOT NULL,
            is_active boolean DEFAULT true NOT NULL,
            updated_at timestamptz,
            created_at timestamptz DEFAULT now() NOT NULL,
            CONSTRAINT technical_questions_pkey PRIMARY KEY (id),
            CONSTRAINT uq_technical_question_job_ordinal UNIQUE (job_id, ordinal),
            CONSTRAINT technical_questions_job_id_fkey
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE,
            CONSTRAINT technical_questions_tenant_id_fkey
                FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
        )
        """
    )
    op.execute("CREATE INDEX ix_technical_questions_job ON technical_questions (job_id)")
    op.execute(
        "CREATE INDEX ix_technical_questions_tenant ON technical_questions (tenant_id)"
    )
    op.execute(
        "CREATE INDEX ix_technical_questions_active ON technical_questions "
        "(job_id, ordinal) WHERE is_active"
    )
    _tenant_policy("technical_questions")

    for table in DROPPED_TABLES:
        op.execute(
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO pickready_app"
        )
