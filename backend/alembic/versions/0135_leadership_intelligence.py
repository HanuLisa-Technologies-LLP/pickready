"""Leadership Intelligence: versioned leadership input, frozen per job.

Revision ID: 0135_leadership_intelligence
Revises: 0134_company_registrations

Owner spec 2026-09-29, sections 16 to 22 and 30 ("Migration C"). Drishti is
reworked into Leadership Intelligence: a CEO, an MD and each department's
Functional Head save their own input, every save is a NEW version, and the
version that shaped a job's skills is frozen onto the job and its contract.

WHAT IT ADDS
------------
1. `leadership_profiles`, INSERT-ONLY versions. One stream per author ROLE for
   the CEO and the MD (company-wide), one per DEPARTMENT for a Functional Head
   (`(author_role = 'functional_head') = (department_id IS NOT NULL)`). The
   latest version of a stream is the one in force; nothing is ever rewritten,
   so "which leadership version influenced this job" always has an answer.
2. `leadership_department_expectations`: a CEO's or MD's expectation for one
   department, a child of the version it was saved with.
3. `job_leadership_contexts`, INSERT-ONLY: the compiled leadership context a
   job's skills were saved with (`services/skills.save`), the three profile
   versions it came from and where each saved skill came from. Written only
   when a context exists.
4. `job_skill_snapshots.leadership_context_id`: the frozen contract names the
   leadership context it was saved with, so a leadership edit next month can
   never move how an applied candidate is assessed (spec 21, rule 9).
5. `functional_skills_reports.leadership_alignment_json`: the PRISM Report's
   Leadership Alignment section, words only, written in the report's one
   INSERT (the report stays insert-only, 0130).

INSERT-ONLY IS THE DATABASE'S, TWICE, exactly as `job_skill_snapshots` (0118):
UPDATE is revoked from `pickready_app` (0014's default privileges grant it to
every new table, so it is REVOKED rather than merely not granted), and a
trigger refuses every UPDATE and every DELETE that is not the cascade of the
tenant or job the row belongs to (`pg_trigger_depth() > 1`). The foreign keys
onto users, departments and profiles take NO referential action: a user and a
department are never hard-deleted (a staff member is disabled, a department
retired), and a tenant or job delete removes the child rows in the same
statement, so the NO ACTION check at the end of it finds nothing to refuse.
A SET NULL here would be an UPDATE waiting for the day the parent is deleted.

DRISHTI, CARRIED ACROSS AND KEPT AS HISTORY (S4)
------------------------------------------------
Every `drishti_profiles` row becomes version 1 of the Functional Head stream
of the department its function names (created, as 0133 did, when the tenant
has no department of that name), with its five sections folded into the two
Functional Head fields and its compiled context lines carried as department
priorities. The Drishti table itself is NOT dropped: it holds a customer's
own words. Pilot held zero rows on 2026-09-29.

THE CAPABILITY `author_drishti_profile` LEAVES WITH ITS ROUTES: every
`role_permissions` row, global and per tenant, and every per-user overlay key.
`author_leadership_intelligence` and `view_leadership_intelligence` were seeded
by 0133.

DOWNGRADE refuses while any leadership row exists (a count that RAISES, after
proving the connection sees every row), because a leader's saved input is
their own words and this migration will not delete it.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0135_leadership_intelligence"
down_revision = "0134_company_registrations"
branch_labels = None
depends_on = None

BYPASS = "current_setting('app.bypass_rls', true) = 'on'"

#: Mirrors `models.leadership.AUTHOR_ROLES`, written as a literal because a
#: migration applies to the schema as it stood.
AUTHOR_ROLES = ("ceo", "md", "functional_head")

RETIRED_CAPABILITY = "author_drishti_profile"

#: The SQL twin of `services/departments.normalize` (0133 states the same).
_NORMALIZED = "lower(btrim(regexp_replace({col}, '\\s+', ' ', 'g')))"

#: The Drishti carry-over, as two statements a test can run against seeded
#: rows (`tests/test_leadership_migration.py`). Both are idempotent: a
#: department or profile that exists is left alone.
_NORMALIZED_FN = _NORMALIZED.format(col="d.function_name")
CARRY_DRISHTI_DEPARTMENTS_SQL = f"""
        INSERT INTO company_departments (id, tenant_id, name, normalized_name)
        SELECT gen_random_uuid(), picked.tenant_id, picked.name, picked.normalized
        FROM (
            SELECT DISTINCT ON (d.tenant_id, {_NORMALIZED_FN})
                   d.tenant_id,
                   btrim(regexp_replace(d.function_name, '\\s+', ' ', 'g')) AS name,
                   {_NORMALIZED_FN} AS normalized
            FROM drishti_profiles d
            WHERE {_NORMALIZED_FN} <> ''
            ORDER BY d.tenant_id, {_NORMALIZED_FN}, d.created_at, d.id
        ) AS picked
        WHERE NOT EXISTS (
            SELECT 1 FROM company_departments c
            WHERE c.tenant_id = picked.tenant_id
              AND c.normalized_name = picked.normalized
        )
        """
CARRY_DRISHTI_PROFILES_SQL = f"""
        INSERT INTO leadership_profiles (
            id, tenant_id, author_user_id, author_role, department_id, version,
            department_requirements, ideal_employee_expectations,
            compiled_json, draft_provenance_json, created_at
        )
        SELECT gen_random_uuid(), picked.tenant_id, picked.author, 'functional_head',
               picked.department_id, 1,
               NULLIF(concat_ws(E'\\n\\n',
                   NULLIF(btrim(picked.strategic_purpose), ''),
                   NULLIF(btrim(picked.non_negotiables), ''),
                   NULLIF(btrim(picked.strategic_gap), '')), ''),
               NULLIF(concat_ws(E'\\n\\n',
                   NULLIF(btrim(picked.people_philosophy), ''),
                   NULLIF(btrim(picked.culture_expectations), '')), ''),
               jsonb_build_object(
                   'schema_version', 1,
                   'migrated_from', 'drishti_profiles',
                   'role', 'functional_head',
                   'department_id', picked.department_id::text,
                   'department_priorities',
                       COALESCE(picked.compiled -> 'context_lines', '[]'::jsonb),
                   'company_priorities', '[]'::jsonb,
                   'observable_competencies', '[]'::jsonb,
                   'non_negotiables', '[]'::jsonb,
                   'success_outcomes', '[]'::jsonb,
                   'culture_expectations', '[]'::jsonb,
                   'strategic_gaps', '[]'::jsonb,
                   'excluded_or_unsafe_claims', '[]'::jsonb,
                   'provenance', '[]'::jsonb
               ),
               jsonb_build_object('ai_draft_used', false, 'migrated_from', 'drishti_profiles'),
               COALESCE(picked.updated_at, picked.created_at)
        FROM (
            SELECT DISTINCT ON (c.id)
                   d.tenant_id, c.id AS department_id,
                   (SELECT u.id FROM users u
                     WHERE u.id = COALESCE(d.functional_head_user_id, d.updated_by)) AS author,
                   d.strategic_purpose, d.people_philosophy, d.non_negotiables,
                   d.culture_expectations, d.strategic_gap,
                   d.compiled_json AS compiled, d.updated_at, d.created_at
            FROM drishti_profiles d
            JOIN company_departments c
              ON c.tenant_id = d.tenant_id AND c.normalized_name = {_NORMALIZED_FN}
            ORDER BY c.id, d.updated_at DESC NULLS LAST, d.created_at DESC
        ) AS picked
        WHERE NOT EXISTS (
            SELECT 1 FROM leadership_profiles p
            WHERE p.tenant_id = picked.tenant_id
              AND p.department_id = picked.department_id
        )
        """
CARRY_DRISHTI_SQL = (CARRY_DRISHTI_DEPARTMENTS_SQL, CARRY_DRISHTI_PROFILES_SQL)

_INSERT_ONLY_TABLES = (
    "leadership_profiles",
    "leadership_department_expectations",
    "job_leadership_contexts",
)

_IMMUTABILITY = """
CREATE OR REPLACE FUNCTION leadership_row_is_immutable()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        RAISE EXCEPTION
            '% is insert-only: row % cannot be rewritten; save a new version',
            TG_TABLE_NAME, OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    -- A DELETE is legal only as the cascade of the tenant, job or profile the
    -- row belongs to: the referential action runs as a trigger, so it arrives
    -- one level deeper than a DELETE statement issued directly.
    IF pg_trigger_depth() <= 1 THEN
        RAISE EXCEPTION
            '% is insert-only: row % cannot be deleted',
            TG_TABLE_NAME, OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN OLD;
END;
$$
"""


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _tenant_policy(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_tenant_isolation ON {table}
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid OR {BYPASS})
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid OR {BYPASS})
        """
    )


def _insert_only(table: str) -> None:
    op.execute(
        f"""
        CREATE TRIGGER trg_{table}_immutable
        BEFORE UPDATE OR DELETE ON {table}
        FOR EACH ROW EXECUTE FUNCTION leadership_row_is_immutable()
        """
    )
    op.execute(f"GRANT SELECT, INSERT, DELETE ON {table} TO pickready_app")
    op.execute(f"REVOKE UPDATE ON {table} FROM pickready_app")


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def upgrade() -> None:
    op.execute(_IMMUTABILITY)

    # ── 1. leadership_profiles ───────────────────────────────────────────────
    op.create_table(
        "leadership_profiles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.id", name="fk_leadership_profiles_author"),
            nullable=True,
        ),
        sa.Column("author_role", sa.String(24), nullable=False),
        sa.Column("department_id", UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("company_requirements", sa.Text(), nullable=True),
        sa.Column("department_requirements", sa.Text(), nullable=True),
        sa.Column("ideal_employee_expectations", sa.Text(), nullable=True),
        sa.Column("compiled_json", JSONB(), nullable=False),
        sa.Column("draft_provenance_json", JSONB(), nullable=True),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["department_id", "tenant_id"],
            ["company_departments.id", "company_departments.tenant_id"],
            name="fk_leadership_profiles_department_same_tenant",
        ),
        sa.UniqueConstraint("id", "tenant_id", name="uq_leadership_profiles_id_tenant"),
        sa.CheckConstraint(
            _in_list("author_role", AUTHOR_ROLES), name="ck_leadership_profiles_role"
        ),
        sa.CheckConstraint(
            "(author_role = 'functional_head') = (department_id IS NOT NULL)",
            name="ck_leadership_profiles_department_iff_functional_head",
        ),
        sa.CheckConstraint("version >= 1", name="ck_leadership_profiles_version"),
    )
    # One version number per stream: the CEO's and the MD's are company-wide,
    # a Functional Head's is per department.
    op.create_index(
        "uq_leadership_profiles_company_stream",
        "leadership_profiles",
        ["tenant_id", "author_role", "version"],
        unique=True,
        postgresql_where=sa.text("department_id IS NULL"),
    )
    op.create_index(
        "uq_leadership_profiles_department_stream",
        "leadership_profiles",
        ["tenant_id", "department_id", "version"],
        unique=True,
        postgresql_where=sa.text("department_id IS NOT NULL"),
    )
    _tenant_policy("leadership_profiles")
    _insert_only("leadership_profiles")

    # ── 2. leadership_department_expectations ───────────────────────────────
    op.create_table(
        "leadership_department_expectations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("profile_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("department_id", UUID(as_uuid=True), nullable=False),
        sa.Column("requirements", sa.Text(), nullable=False),
        sa.Column("compiled_json", JSONB(), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["profile_id", "tenant_id"],
            ["leadership_profiles.id", "leadership_profiles.tenant_id"],
            ondelete="CASCADE",
            name="fk_leadership_expectations_profile_same_tenant",
        ),
        sa.ForeignKeyConstraint(
            ["department_id", "tenant_id"],
            ["company_departments.id", "company_departments.tenant_id"],
            name="fk_leadership_expectations_department_same_tenant",
        ),
        sa.UniqueConstraint(
            "profile_id", "department_id", name="uq_leadership_expectations_department"
        ),
    )
    op.create_index(
        "ix_leadership_expectations_tenant_department",
        "leadership_department_expectations",
        ["tenant_id", "department_id"],
    )
    _tenant_policy("leadership_department_expectations")
    _insert_only("leadership_department_expectations")

    # ── 3. job_leadership_contexts ──────────────────────────────────────────
    profile_fk = {
        "ceo_profile_id": "fk_job_leadership_ceo_profile",
        "md_profile_id": "fk_job_leadership_md_profile",
        "functional_head_profile_id": "fk_job_leadership_fh_profile",
    }
    op.create_table(
        "job_leadership_contexts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("department_id", UUID(as_uuid=True), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        *(
            sa.Column(column, UUID(as_uuid=True), nullable=True)
            for column in profile_fk
        ),
        sa.Column("compiled_context_json", JSONB(), nullable=False),
        sa.Column(
            "skill_sources_json", JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column("digest", sa.String(64), nullable=False),
        _created_at(),
        *(
            sa.ForeignKeyConstraint(
                [column, "tenant_id"],
                ["leadership_profiles.id", "leadership_profiles.tenant_id"],
                name=name,
            )
            for column, name in profile_fk.items()
        ),
        sa.ForeignKeyConstraint(
            ["department_id", "tenant_id"],
            ["company_departments.id", "company_departments.tenant_id"],
            name="fk_job_leadership_department_same_tenant",
        ),
        sa.UniqueConstraint("job_id", "version", name="uq_job_leadership_contexts_version"),
        sa.CheckConstraint("version >= 1", name="ck_job_leadership_contexts_version"),
        sa.CheckConstraint(
            "length(digest) = 64", name="ck_job_leadership_contexts_digest"
        ),
    )
    op.create_index(
        "ix_job_leadership_contexts_job", "job_leadership_contexts", ["job_id"]
    )
    _tenant_policy("job_leadership_contexts")
    _insert_only("job_leadership_contexts")

    # ── 4. The frozen contract names its leadership context ─────────────────
    # A new NULLABLE column on an insert-only table is not an UPDATE of any
    # row: every existing snapshot reads NULL, which is the truth (none was
    # saved with leadership). No referential action, for the reason above.
    op.add_column(
        "job_skill_snapshots",
        sa.Column("leadership_context_id", UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_job_skill_snapshots_leadership_context",
        "job_skill_snapshots",
        "job_leadership_contexts",
        ["leadership_context_id"],
        ["id"],
    )

    # ── 5. The report's Leadership Alignment section ────────────────────────
    op.add_column(
        "functional_skills_reports",
        sa.Column("leadership_alignment_json", JSONB(), nullable=True),
    )

    # ── 6. Drishti, carried across ──────────────────────────────────────────
    for statement in CARRY_DRISHTI_SQL:
        op.execute(statement)

    # ── 7. The retired capability ───────────────────────────────────────────
    op.execute(f"DELETE FROM role_permissions WHERE capability = '{RETIRED_CAPABILITY}'")
    op.execute(
        f"""
        UPDATE users SET permissions_json = permissions_json - '{RETIRED_CAPABILITY}'
        WHERE permissions_json IS NOT NULL
          AND jsonb_typeof(permissions_json) = 'object'
          AND permissions_json ? '{RETIRED_CAPABILITY}'
        """
    )


def downgrade() -> None:
    message_rls = (
        "0135 downgrade refuses to run: this connection reads row level "
        "security filtered rows, so its count of leadership rows could read "
        "zero over tables that hold them. Set app.bypass_rls as alembic/env.py does."
    )
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT ({BYPASS})
               AND NOT (SELECT rolsuper OR rolbypassrls
                          FROM pg_roles WHERE rolname = current_user) THEN
                RAISE EXCEPTION '{message_rls}';
            END IF;
        END
        $$;
        """
    )
    op.execute(
        """
        DO $$
        DECLARE
            held bigint;
        BEGIN
            SELECT (SELECT count(*) FROM leadership_profiles)
                 + (SELECT count(*) FROM job_leadership_contexts)
                 + (SELECT count(*) FROM functional_skills_reports
                     WHERE leadership_alignment_json IS NOT NULL)
              INTO held;
            IF held > 0 THEN
                RAISE EXCEPTION
                    '0135 downgrade refused: % leadership row(s) exist, and a leader''s saved words are not deleted by a migration',
                    held;
            END IF;
        END
        $$;
        """
    )
    op.drop_column("functional_skills_reports", "leadership_alignment_json")
    op.drop_constraint(
        "fk_job_skill_snapshots_leadership_context", "job_skill_snapshots", type_="foreignkey"
    )
    op.drop_column("job_skill_snapshots", "leadership_context_id")
    for table in reversed(_INSERT_ONLY_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_immutable ON {table}")
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
        op.drop_table(table)
    op.execute("DROP FUNCTION IF EXISTS leadership_row_is_immutable()")
