"""Remove the Company DNA feature. The scorecard binding survives, renamed.

Revision ID: 0088_remove_company_dna
Revises: 0087_sender_rejected_status
Create Date: 2026-09-09

WHAT THIS DROPS, AND WHAT IT DELIBERATELY KEEPS
-------------------------------------------------
Owner decision: the Company DNA questionnaire is removed and the Company
Profile replaces it. Gate 1 on `POST /jobs` now asks the `companies` row
whether `about_company` says anything.

DROPPED: the `company_dna` table, its immutability trigger and the function
behind it, the two capability grants the retired router gated on, and
`evaluations.company_dna_version`.

KEPT, RENAMED: `job_company_dna_bindings` becomes `job_scorecard_bindings`.
The table was never only about Company DNA. It is the append-only record of
WHEN a job's scorecard was frozen and at what version, and
`orchestration/versioning.resolve_for_application` reads it to answer "what was
this job built on when I applied" for every candidate already assessed.
Dropping it would delete that answer for every existing application, so it is
RENAMED IN PLACE with its rows intact and only its two Company DNA columns
removed.

WHY A RENAME RATHER THAN LEAVING THE NAME ALONE
-------------------------------------------------
A table named after a feature that no longer exists is a table the next reader
has to reconstruct a deleted concept to understand. `ALTER TABLE ... RENAME`
preserves every row, and the constraints, indexes and policy are renamed
alongside so nothing in the schema still carries the old name.

ORDER MATTERS HERE
--------------------
The bindings' `company_dna_id` is a foreign key onto `company_dna`, so the
column is dropped BEFORE the table. Dropping the table first would either
cascade into the bindings or refuse, and one of those two loses rows.

DOWNGRADE
-----------
Reverses the rename and re-adds the columns as NULLABLE, because the values
they used to hold are gone and a NOT NULL column with no data cannot be
recreated honestly. It does NOT recreate `company_dna` or its data: a
downgrade that invented an empty table would let a rolled-back image read
zero rows and conclude every client had answered nothing.
"""
from alembic import op

revision = "0088_remove_company_dna"
down_revision = "0087_sender_rejected_status"
branch_labels = None
depends_on = None

TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"
BYPASS = "current_setting('app.bypass_rls', true) = 'on'"

_OLD = "job_company_dna_bindings"
_NEW = "job_scorecard_bindings"
_OLD_POLICY = "job_company_dna_bindings_tenant_isolation"
_NEW_POLICY = "job_scorecard_bindings_tenant_isolation"

#: The capabilities the retired router gated on, written out rather than
#: imported: a migration must keep meaning what it meant on the day it ran.
_GRANTS: tuple[tuple[str, str], ...] = (
    ("client", "manage_company_dna"),
    ("recruitment_manager", "manage_company_dna"),
    ("hr_manager", "manage_company_dna"),
    ("client", "view_company_dna"),
    ("recruitment_manager", "view_company_dna"),
    ("hr_manager", "view_company_dna"),
    ("recruiter", "view_company_dna"),
    ("hiring_manager", "view_company_dna"),
)


def upgrade() -> None:
    # ── The binding loses its Company DNA columns, then its name ────────────
    op.execute(f"ALTER TABLE {_OLD} DROP COLUMN IF EXISTS company_dna_id")
    op.execute(f"ALTER TABLE {_OLD} DROP COLUMN IF EXISTS company_dna_version")

    op.execute(f"DROP POLICY IF EXISTS {_OLD_POLICY} ON {_OLD}")
    op.execute(f"ALTER TABLE {_OLD} RENAME TO {_NEW}")
    op.execute(
        "ALTER INDEX IF EXISTS ix_job_company_dna_bindings_job "
        "RENAME TO ix_job_scorecard_bindings_job"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_job_company_dna_bindings_tenant "
        "RENAME TO ix_job_scorecard_bindings_tenant"
    )
    op.execute(
        f"ALTER TABLE {_NEW} RENAME CONSTRAINT uq_job_company_dna_binding_sequence "
        "TO uq_job_scorecard_binding_sequence"
    )
    op.execute(
        f"ALTER TABLE {_NEW} RENAME CONSTRAINT ck_job_company_dna_binding_sequence "
        "TO ck_job_scorecard_binding_sequence"
    )
    # The policy is recreated rather than renamed, because it was dropped above
    # so the rename could not leave a policy naming a table that no longer
    # exists under that name.
    op.execute(
        f"CREATE POLICY {_NEW_POLICY} ON {_NEW} "
        f"USING ((tenant_id = {TENANT}) OR ({BYPASS})) "
        f"WITH CHECK ((tenant_id = {TENANT}) OR ({BYPASS}))"
    )

    # ── The evaluation stops recording a version of a thing that is gone ────
    op.execute("ALTER TABLE evaluations DROP COLUMN IF EXISTS company_dna_version")

    # ── The artifact itself ─────────────────────────────────────────────────
    op.execute(
        "DROP TRIGGER IF EXISTS trg_company_dna_version_is_immutable ON company_dna"
    )
    op.execute("DROP TABLE IF EXISTS company_dna")
    op.execute("DROP FUNCTION IF EXISTS company_dna_version_is_immutable()")

    # ── The grants ──────────────────────────────────────────────────────────
    #
    # Deleted for EVERY tenant, not only the global template rows 0060 wrote.
    # A tenant override granting a capability nothing checks any more is a row
    # that answers a question nobody asks, and it would still appear on a
    # permissions screen.
    for role, capability in _GRANTS:
        op.execute(
            "DELETE FROM role_permissions "
            f"WHERE role = '{role}' AND capability = '{capability}'"
        )
    # `jsonb_exists` rather than the `?` operator: `?` is a bind-parameter
    # marker in some drivers and this string travels through SQLAlchemy's
    # `text()`, so the function form is the one that cannot be misread.
    op.execute(
        "UPDATE users SET permissions_json = "
        "  (permissions_json - 'manage_company_dna' - 'view_company_dna') "
        "WHERE permissions_json IS NOT NULL "
        "  AND (jsonb_exists(permissions_json, 'manage_company_dna') "
        "       OR jsonb_exists(permissions_json, 'view_company_dna'))"
    )


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {_NEW_POLICY} ON {_NEW}")
    op.execute(f"ALTER TABLE {_NEW} RENAME TO {_OLD}")
    op.execute(
        "ALTER INDEX IF EXISTS ix_job_scorecard_bindings_job "
        "RENAME TO ix_job_company_dna_bindings_job"
    )
    op.execute(
        "ALTER INDEX IF EXISTS ix_job_scorecard_bindings_tenant "
        "RENAME TO ix_job_company_dna_bindings_tenant"
    )
    op.execute(
        f"ALTER TABLE {_OLD} RENAME CONSTRAINT uq_job_scorecard_binding_sequence "
        "TO uq_job_company_dna_binding_sequence"
    )
    op.execute(
        f"ALTER TABLE {_OLD} RENAME CONSTRAINT ck_job_scorecard_binding_sequence "
        "TO ck_job_company_dna_binding_sequence"
    )
    op.execute(
        f"CREATE POLICY {_OLD_POLICY} ON {_OLD} "
        f"USING ((tenant_id = {TENANT}) OR ({BYPASS})) "
        f"WITH CHECK ((tenant_id = {TENANT}) OR ({BYPASS}))"
    )
    # NULLABLE, and without the foreign key: `company_dna` is not recreated, so
    # a REFERENCES clause would point at nothing and a NOT NULL column would
    # need values that no longer exist.
    op.execute(f"ALTER TABLE {_OLD} ADD COLUMN IF NOT EXISTS company_dna_id uuid")
    op.execute(
        f"ALTER TABLE {_OLD} ADD COLUMN IF NOT EXISTS company_dna_version integer"
    )
    op.execute(
        "ALTER TABLE evaluations ADD COLUMN IF NOT EXISTS company_dna_version integer"
    )
