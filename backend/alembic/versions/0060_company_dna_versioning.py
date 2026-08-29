"""Company DNA versioning: immutability, the job binding, and two grants.

Revision ID: 0060_company_dna_versioning
Revises: 0059_hiring_intelligence

WHAT THIS ADDS, AND WHY EACH PIECE IS HERE
-------------------------------------------
Migration 0059 created `company_dna` with a version number, an `is_current`
flag and a status. That is enough to STORE versions and not enough to make them
IMMUTABLE, and nothing referenced a version from a job. spec-doc6 §4.2 requires
both. Three things, then:

1. `job_company_dna_bindings` -- which Company DNA version a job's scorecard was
   frozen against, append-only, one row per freeze. See
   `app/models/company_dna.py` for why this is a table rather than a column on
   `jobs`.

2. A BEFORE UPDATE trigger on `company_dna` that refuses to rewrite a version
   once it has left draft. The application refuses it too, at the API layer, and
   both are needed for the reason the "culture" competency ban is enforced three
   times over: an application check is code somebody can route around, and the
   thing being protected here is the answer to "what criteria was this candidate
   actually graded under", which every report already written depends on.

   The trigger permits EXACTLY the transition the supersede path needs: a
   complete version becoming superseded and losing `is_current`. It refuses any
   change to `answers_json`, `artifact_json`, `transcript_json`, `version`,
   `tenant_id` or `conducted_by` on a row that is not a draft, and it refuses
   the status moving backwards to draft.

3. Two global `role_permissions` rows per role for the Company DNA
   capabilities, so authorization is DATA rather than a role-name branch in the
   router (CLAUDE.md rule 3).

   `manage_company_dna` goes to the client's Super Admin (`client`), the
   Recruitment Manager and the legacy HR Manager. spec-doc6 D3 puts authorship
   with the HR Manager and gives the Super Admin the same actions under RBAC
   §7.5 override authority. `view_company_dna` additionally goes to the
   Recruiter and the Hiring Manager, who need the compiled artifact to
   understand why weights landed where they did and who must never reach the
   raw session.

   These two capability names are NOT yet in `services/capabilities.ALL_CAPABILITIES`:
   that module is owned elsewhere this wave. The engine reads ROWS, so the
   grants below are already authoritative; what the missing constants cost is
   that the names do not appear in `/auth/me`'s capability list and cannot be
   pinned in a per-user overlay (`rbac.sanitize_overrides` drops unknown names).
   The Company DNA screens therefore read their permissions from
   `GET /clients/{id}/company-dna/status`, which resolves them server-side.

ADDITIVE, AND THEREFORE SAFE UNDER A ROLLING DEPLOY
-----------------------------------------------------
A rolling deploy runs the previous image and this one against this schema at the
same time. Checked statement by statement rather than asserted:

  * ONE `CREATE TABLE`, of a table no deployed code reads or writes. Adding a
    table cannot break a reader that does not know it exists.
  * ONE `CREATE FUNCTION` plus ONE `CREATE TRIGGER` on `company_dna`. NOTHING IN
    THE PREVIOUS IMAGE WRITES `company_dna` AT ALL: before this release the
    table had no API route, no service writer and no task. So the trigger
    cannot refuse a statement the old image issues, because the old image
    issues none. It is also a BEFORE UPDATE trigger only, so inserts and reads
    are untouched.
  * `INSERT` of ten `role_permissions` rows, `ON CONFLICT DO NOTHING`. The old
    image resolves capabilities by iterating its own `ALL_CAPABILITIES` list and
    looking each one up, so two rows naming capabilities it has never heard of
    are read into a dictionary and never consulted. `resolve_permission` tests
    for KEY PRESENCE in that dictionary, so an unknown key cannot flip an
    existing answer.

  * No column is added to an existing table, so there is no NOT NULL and no
    default to argue about. No column is dropped, renamed or narrowed. No
    existing row is rewritten. No index on an existing table is dropped.

The one caveat worth stating rather than hiding: the new grants take effect for
the OLD image too, because the rows are global. They grant capabilities the old
image never checks, so nothing changes for it.

DOWNGRADE
---------
Drops the trigger, the function, the table and the ten grant rows, in dependency
order. It does not touch `company_dna` data: a downgrade must not destroy a
client's philosophy because the code that reads it was rolled back.
"""
from alembic import op

revision = "0060_company_dna_versioning"
down_revision = "0059_hiring_intelligence"
branch_labels = None
depends_on = None

TENANT = "nullif(current_setting('app.tenant_id', true), '')::uuid"
BYPASS = "current_setting('app.bypass_rls', true) = 'on'"

_BINDINGS = "job_company_dna_bindings"
_BINDINGS_POLICY = "job_company_dna_bindings_tenant_isolation"

#: The capability names the Company DNA router gates on. Written out here
#: rather than imported from `services/capabilities`, because a migration must
#: keep meaning what it meant on the day it ran even if a constant is later
#: renamed.
MANAGE = "manage_company_dna"
VIEW = "view_company_dna"

#: (role, capability) pairs, exactly as spec-doc6 D3 states them.
#:
#: `interview_manager` is absent because this codebase has no such role; the
#: closest thing is the Hiring Manager at the bottom of the hierarchy. D3 gives
#: the Interview Manager no access, so its absence is the correct outcome
#: either way, and adding a grant for a role that does not exist would be a row
#: nothing can ever match.
_GRANTS: tuple[tuple[str, str], ...] = (
    # Authorship. The client's Super Admin holds it under RBAC §7.5 override
    # authority, recorded as an override in the audit trail by the router.
    ("client", MANAGE),
    ("recruitment_manager", MANAGE),
    ("hr_manager", MANAGE),
    # Reading the COMPILED artifact. Every authoring role reads it too, and the
    # grant is separate so a tenant can widen reading without widening writing.
    ("client", VIEW),
    ("recruitment_manager", VIEW),
    ("hr_manager", VIEW),
    ("recruiter", VIEW),
    ("hiring_manager", VIEW),
)

_IMMUTABILITY_FUNCTION = """
CREATE OR REPLACE FUNCTION company_dna_version_is_immutable()
RETURNS trigger AS $$
BEGIN
    -- A draft is the working copy and may change freely.
    IF OLD.status = 'draft' THEN
        RETURN NEW;
    END IF;

    -- Past draft, the CONTENT is frozen. Every clause below names one column
    -- whose change would make an already-written evaluation unreadable: the
    -- criteria it was run against would no longer be the criteria on the row.
    IF NEW.answers_json IS DISTINCT FROM OLD.answers_json
       OR NEW.artifact_json IS DISTINCT FROM OLD.artifact_json
       OR NEW.transcript_json IS DISTINCT FROM OLD.transcript_json
       OR NEW.version IS DISTINCT FROM OLD.version
       OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
       OR NEW.conducted_by IS DISTINCT FROM OLD.conducted_by
       OR NEW.completed_at IS DISTINCT FROM OLD.completed_at
    THEN
        RAISE EXCEPTION
            'company_dna version % is % and cannot be rewritten; publish a new version instead',
            OLD.version, OLD.status
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- The one permitted transition: a complete version is superseded by a
    -- newer one and stops being current. Anything else, including a return to
    -- draft, is refused.
    IF NEW.status IS DISTINCT FROM OLD.status
       AND NOT (OLD.status = 'complete' AND NEW.status = 'superseded')
    THEN
        RAISE EXCEPTION
            'company_dna version % cannot move from % to %',
            OLD.version, OLD.status, NEW.status
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- `is_current` may only be cleared, never set back on a superseded row.
    IF NEW.is_current AND NOT OLD.is_current THEN
        RAISE EXCEPTION
            'company_dna version % is % and cannot become current again',
            OLD.version, OLD.status
            USING ERRCODE = 'restrict_violation';
    END IF;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    # ── Which Layer 2 version a job's scorecard was frozen against ──────────
    op.execute(
        f"""
        CREATE TABLE {_BINDINGS} (
            id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id uuid NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            job_id uuid NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            company_dna_id uuid NOT NULL
                REFERENCES company_dna(id) ON DELETE CASCADE,
            company_dna_version integer NOT NULL,
            freeze_sequence integer NOT NULL DEFAULT 1,
            scorecard_version integer NOT NULL DEFAULT 1,
            frozen_by uuid REFERENCES users(id) ON DELETE SET NULL,
            correlation_id varchar(64),
            frozen_at timestamptz,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_job_company_dna_binding_sequence
                UNIQUE (job_id, freeze_sequence),
            CONSTRAINT ck_job_company_dna_binding_sequence
                CHECK (freeze_sequence >= 1)
        )
        """
    )
    op.execute(
        f"CREATE INDEX ix_job_company_dna_bindings_job ON {_BINDINGS} "
        "(job_id, freeze_sequence DESC)"
    )
    op.execute(
        f"CREATE INDEX ix_job_company_dna_bindings_tenant ON {_BINDINGS} (tenant_id)"
    )
    op.execute(f"ALTER TABLE {_BINDINGS} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_BINDINGS} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {_BINDINGS_POLICY} ON {_BINDINGS} "
        f"USING ((tenant_id = {TENANT}) OR ({BYPASS})) "
        f"WITH CHECK ((tenant_id = {TENANT}) OR ({BYPASS}))"
    )

    # ── A completed version cannot be rewritten ─────────────────────────────
    op.execute(_IMMUTABILITY_FUNCTION)
    op.execute(
        "CREATE TRIGGER trg_company_dna_version_is_immutable "
        "BEFORE UPDATE ON company_dna "
        "FOR EACH ROW EXECUTE FUNCTION company_dna_version_is_immutable()"
    )

    # ── The two grants, as data ─────────────────────────────────────────────
    for role, capability in _GRANTS:
        op.execute(
            "INSERT INTO role_permissions (id, tenant_id, role, capability, allowed) "
            f"VALUES (gen_random_uuid(), NULL, '{role}', '{capability}', true) "
            "ON CONFLICT (tenant_id, role, capability) DO NOTHING"
        )


def downgrade() -> None:
    for role, capability in _GRANTS:
        op.execute(
            "DELETE FROM role_permissions WHERE tenant_id IS NULL "
            f"AND role = '{role}' AND capability = '{capability}'"
        )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_company_dna_version_is_immutable ON company_dna"
    )
    op.execute("DROP FUNCTION IF EXISTS company_dna_version_is_immutable()")
    op.execute(f"DROP POLICY IF EXISTS {_BINDINGS_POLICY} ON {_BINDINGS}")
    op.execute(f"DROP TABLE IF EXISTS {_BINDINGS}")
