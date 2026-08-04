"""Guard every RLS policy's `app.tenant_id` cast against the empty string.

Revision ID: 0034_guard_rls_guc_cast
Revises: 0033_purge_noncanonical_caps

WHAT WENT WRONG
---------------
Every tenant-isolation policy in this schema casts the tenant GUC directly:

    tenant_id = (current_setting('app.tenant_id', true))::uuid

`current_setting(..., missing_ok => true)` returns NULL when the setting has
never been touched, and `NULL::uuid` is harmless. The trouble is that the
setting does not stay untouched.

`core/db.py` sets it with `set_config('app.tenant_id', :tid, true)` -- the third
argument being is_local, so the value is scoped to the transaction. A LOCAL
setting does NOT revert to *unset* at COMMIT. It reverts to the placeholder
GUC's RESET VALUE, and for a custom `app.*` GUC that was never given a boot
value, the reset value is the EMPTY STRING. The pooled connection then carries
`''` into whatever runs next, and

    ''::uuid  ->  ERROR: invalid input syntax for type uuid: ""

Reproduced against production, on one connection:

    fresh connection                       -> current_setting(...) is NULL
    begin; set_config(..., true); commit;  -> current_setting(...) is ''

So the failure is POOL-STATE DEPENDENT. The identical query is quiet on a fresh
connection and a 500 on a recycled one, which is why it reads as intermittent
and why no amount of reviewing `if tenant_id is None` branches finds it.

WHY `app.bypass_rls = 'on'` DOES NOT SAVE THE TRUSTED PATHS
-----------------------------------------------------------
Each policy is `<cast> OR current_setting('app.bypass_rls', true) = 'on'`, which
reads as though a trusted caller can opt out. It cannot. `current_setting` is
STABLE, so the planner CONSTANT-FOLDS the cast while planning the scan and
raises before a single row is examined and before the OR is ever evaluated.
Verified directly: with the GUC at `''` AND bypass_rls at `'on'`, the scan still
raises.

For the same reason the `tenant_id IS NULL OR ...` prefix on `candidates` and
`role_permissions`, and `source_tenant_id IS NULL OR ...` on `profiles`, buys
nothing on a SELECT. It looks like it protects the shared/databank rows; it does
not, because planning fails first. (A single-row INSERT is unaffected: WITH CHECK
is evaluated per row at runtime and short-circuits.)

The one path that works today does so because `superadmin_scope` pins a sentinel
uuid rather than relying on the bypass flag. That sentinel is load-bearing and
is currently the only thing holding up the owner, BD and candidate portals.

THE FIX
-------
    tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid

`nullif` collapses `''` back to NULL, so the crashing state becomes the
already-correct unset state: `tenant_id = NULL` is NULL, the row is filtered, and
an unscoped session reads nothing instead of exploding. Isolation is UNCHANGED --
this migration widens no one's visibility. A session with a real tenant GUC
behaves exactly as before; only the poisoned-connection case changes, and it
changes from "500" to "no rows".

This is deliberately fixed at the POLICY rather than at the call sites. The call
sites are not individually wrong: the trap is that a correct-looking
`set_config(..., true)` poisons the connection for the NEXT caller, and that the
documented escape hatch does not work. Hardening the 26 policies removes the
trap for every current and future caller at once.

`bd_leads` is deliberately untouched: its policy is bypass-flag only, with no
cast, so it was never exposed.

SEPARATELY, AND NOT FIXED HERE
------------------------------
`workers/tasks.refresh_dashboard_views` runs on a connection with NEITHER the
GUC nor the bypass flag set, so `REFRESH MATERIALIZED VIEW dashboard_job_metrics`
saw zero base rows and rebuilt the view EMPTY -- measured at 0 view rows against
35 jobs. That is the silent-filter twin of this bug and `nullif` does not cure
it: filtering to nothing is the correct behaviour for an unscoped session. It is
fixed in the same commit, in Python, by setting the escape hatch explicitly on
that connection.

Idempotent: ALTER POLICY is declarative, so re-running restates the same
expression. Downgrade restores the unguarded casts exactly, crash and all.
"""
from alembic import op

# revision identifiers, used by Alembic.
revision = "0034_guard_rls_guc_cast"
down_revision = "0033_purge_noncanonical_caps"
branch_labels = None
depends_on = None

GUARDED = "nullif(current_setting('app.tenant_id', true), '')::uuid"
UNGUARDED = "(current_setting('app.tenant_id', true))::uuid"
BYPASS = "current_setting('app.bypass_rls', true) = 'on'"

#: (table, policy) for the plain `tenant_id = <cast> OR bypass` shape.
#: Hardcoded rather than discovered from pg_policies at runtime: a historical
#: migration must apply to the schema as it stood, not to whatever a later
#: release happens to have added.
STANDARD: tuple[tuple[str, str], ...] = (
    ("assessment_conversations", "assessment_conversations_tenant_isolation"),
    ("assessment_messages", "assessment_messages_tenant_isolation"),
    ("billing_transactions", "billing_transactions_tenant_isolation"),
    ("candidate_questions", "candidate_questions_tenant_isolation"),
    ("candidate_team_reviews", "candidate_team_reviews_tenant_isolation"),
    ("companies", "companies_tenant_isolation"),
    ("compliance_documents", "compliance_documents_tenant_isolation"),
    ("credit_ledger", "credit_ledger_tenant_isolation"),
    ("email_log", "email_log_tenant_isolation"),
    ("email_templates", "email_templates_tenant_isolation"),
    ("functional_skills_reports", "functional_skills_reports_tenant_isolation"),
    ("hiring_managers", "hiring_managers_tenant_isolation"),
    ("interviews", "interviews_tenant_isolation"),
    ("job_approvals", "job_approvals_tenant_isolation"),
    ("job_candidate_links", "job_candidate_links_tenant_isolation"),
    ("job_competencies", "job_competencies_tenant_isolation"),
    ("jobs", "jobs_tenant_isolation"),
    ("old_profile_reviews", "old_profile_reviews_tenant_isolation"),
    ("pipeline_status", "pipeline_status_tenant_isolation"),
    ("report_dimensions", "report_dimensions_tenant_isolation"),
    ("staff_invites", "staff_invites_tenant_isolation"),
    ("technical_questions", "technical_questions_tenant_isolation"),
    ("verification_requests", "verification_requests_tenant_isolation"),
)

#: `tenant_id IS NULL OR ...` -- the shared/global rows these tables carry.
NULL_TOLERANT: tuple[tuple[str, str], ...] = (
    ("candidates", "candidates_databank"),
    ("role_permissions", "role_permissions_tenant_or_global"),
)

#: profiles keys on source_tenant_id and adds a consent-based USING clause that
#: WITH CHECK deliberately does not carry: a databank consent flag governs who
#: may READ a profile, never who may write one into another tenant.
PROFILES_USING = (
    "(source_tenant_id IS NULL)"
    " OR (source_tenant_id = {cast})"
    " OR ({bypass})"
    " OR (EXISTS (SELECT 1 FROM candidates c"
    " WHERE c.id = profiles.candidate_id AND c.consent_databank = true))"
)
PROFILES_CHECK = (
    "(source_tenant_id IS NULL)"
    " OR (source_tenant_id = {cast})"
    " OR ({bypass})"
)


def _apply(cast: str) -> None:
    for table, policy in STANDARD:
        expr = f"(tenant_id = {cast}) OR ({BYPASS})"
        op.execute(
            f"ALTER POLICY {policy} ON {table} "
            f"USING ({expr}) WITH CHECK ({expr})"
        )

    for table, policy in NULL_TOLERANT:
        expr = f"(tenant_id IS NULL) OR (tenant_id = {cast}) OR ({BYPASS})"
        op.execute(
            f"ALTER POLICY {policy} ON {table} "
            f"USING ({expr}) WITH CHECK ({expr})"
        )

    op.execute(
        "ALTER POLICY profiles_databank ON profiles "
        f"USING ({PROFILES_USING.format(cast=cast, bypass=BYPASS)}) "
        f"WITH CHECK ({PROFILES_CHECK.format(cast=cast, bypass=BYPASS)})"
    )


def _bound_lock_wait() -> None:
    """Fail fast instead of blocking the whole application.

    ALTER POLICY takes ACCESS EXCLUSIVE on the table, so it queues behind any
    open transaction touching it -- and, worse, every subsequent reader then
    queues behind the waiting ALTER. Observed while rehearsing this migration:
    the backend holds a transaction open ACROSS an LLM call (it reads
    `llm_provider_keys`, calls the provider, then UPDATEs `jobs`), which with a
    15s-per-attempt budget keeps row locks on `jobs` for many seconds at a time.
    An unbounded ALTER POLICY landing in that window converts a slow deploy into
    an application-wide stall.

    With lock_timeout the statement gives up after 5s and the migration fails
    loudly, which the pipeline surfaces as a red deploy that can simply be
    re-run. A hung migration holding ACCESS EXCLUSIVE is the far worse outcome:
    it is an outage that looks like a slow build.
    """
    op.execute("SET lock_timeout = '5s'")


def upgrade() -> None:
    _bound_lock_wait()
    _apply(GUARDED)


def downgrade() -> None:
    _bound_lock_wait()
    _apply(UNGUARDED)
