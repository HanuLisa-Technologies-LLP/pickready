"""The dashboard refresh has failed on every run since the credential split.

Revision ID: 0099_dashboard_refresh_grant
Revises: 0098_jobs_embedding_hnsw_index

WHAT WAS BROKEN, AND FOR HOW LONG
----------------------------------
`pickready.refresh_dashboard_views` is scheduled every five minutes. Since the
database credential was split on 2026-09-11 it has raised, every single time:

    asyncpg.exceptions.InsufficientPrivilegeError:
        must be owner of materialized view dashboard_job_metrics

So `dashboard_job_metrics` has not refreshed once in that week, and every
surface reading it has been serving a snapshot frozen at the moment of the
rotation. Nothing in the product reported it. The task retries three times and
re-raises correctly, which moves the Lambda error metric and fires
`readypick-pilot-task-worker-error-rate`, and that alarm notifies an SNS
subscription still in `PendingConfirmation`. It was found by listing alarms.

TWO CORRECT CHANGES INTERACTING BADLY
--------------------------------------
Moving the application off the rotating RDS master onto `pickready_app`, a
least-privileged NOINHERIT role, was right and stands: the 2026-09-11 outage
happened because `DATABASE_URL` held a copy of a password something else
rotated. `REFRESH MATERIALIZED VIEW` requires OWNERSHIP, and `workers/tasks.py`
issues it directly. Nothing in the credential work was wrong; this one
statement needed a companion nobody looked for, because the failure it produces
is a background task in a log rather than a request anybody makes.

WHY NOT THE ONE-LINE FIX
-------------------------
`SET ROLE` to the object owner would work with no migration at all:
`app.scripts.provision_app_db_role` makes `pickready_app` a member of the owner
precisely so `alembic/env.py` can escalate for DDL, and NOINHERIT only means it
has to ask. It is refused because it hands a scheduled background task the FULL
rights of the role that owns every object in the schema in order to buy one
statement, and because `POSTGRES_MIGRATION_ROLE` is deliberately set on the
migrate container and on nothing that serves a request, with
`tests/test_app_db_credential.py` sweeping every environment's Terraform to
keep it that way.

This grants the one capability instead, which is the same argument this
repository already makes everywhere it talks about IAM: enumerate the need,
never hand over a prefix.

PROVEN BEFORE IT WAS WRITTEN
-----------------------------
On a throwaway database with this shape (an owner role owning the view, a
NOINHERIT member app role), measured rather than assumed:

  * the app role refreshing DIRECTLY reproduced the exact production error;
  * calling the SECURITY DEFINER function SUCCEEDED;
  * it succeeded inside an explicit `BEGIN ... COMMIT` too, so the surrounding
    transaction is not an obstacle and the AUTOCOMMIT connection is belt and
    braces rather than a requirement;
  * the app role still could not remove the view, alter the source table,
    remove the table, or take ownership of anything;
  * and `SET ROLE` did succeed, which is what makes the paragraph above a real
    choice rather than a rationalisation: the shortcut was available and would
    have granted every one of the four operations just refused.

THE BYPASS LIVES INSIDE THE FUNCTION, AND THAT IS THE POINT
------------------------------------------------------------
`jobs` and `job_candidate_links` both carry FORCE ROW LEVEL SECURITY, so being
the owner does NOT exempt the refresh from their policies. A refresh on a
connection belonging to no tenant therefore rebuilds the view from zero rows,
raises nothing, and leaves every dashboard rendering blank behind a clean 200.
That has happened on this product before and is recorded in the task's own
docstring.

Putting the two `set_config` calls inside the function means a caller cannot
get it wrong. The alternative, leaving them to the caller, is a correctness
requirement stored in somebody's memory, and the next caller of this function
is exactly the person who will not have read this file.

SESSION-LEVEL (`false`), NOT transaction-local, matching the behaviour the task
has today and for the reason `core/db.superadmin_scope` gives: passed `true`
outside an explicit transaction the setting is discarded immediately, and a
`REFRESH` that runs its own internal transactions must not lose the flag
underneath itself. The sentinel tenant is pinned beside it so the policies'
`::uuid` cast is always well defined.

`search_path` is PINNED in the definition. A SECURITY DEFINER function with a
mutable search_path is a privilege-escalation primitive rather than a fix: it
lets any caller who can create a schema decide which `set_config` or which
table name the body resolves to, and the body then runs as the owner of every
object in the database.
"""
from alembic import op

revision = "0099_dashboard_refresh_grant"
down_revision = "0098_jobs_embedding_hnsw_index"
branch_labels = None
depends_on = None


#: Named once so the upgrade, the downgrade and the grant cannot drift.
FUNCTION = "refresh_dashboard_job_metrics"


def upgrade() -> None:
    # CREATE OR REPLACE rather than CREATE, so an environment hand-repaired
    # before this migration ran does not fail the deploy over already being
    # correct. Same reasoning as 0098's IF NOT EXISTS.
    #
    # The function is owned by whoever runs this migration, which `env.py` has
    # already `SET ROLE`d to the object owner. That is the whole mechanism:
    # SECURITY DEFINER means the body executes with THAT role's privileges,
    # which is exactly the ownership `REFRESH` demands.
    op.execute(
        f"""
        CREATE OR REPLACE FUNCTION {FUNCTION}() RETURNS void
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $func$
        BEGIN
            PERFORM set_config('app.bypass_rls', 'on', false);
            PERFORM set_config(
                'app.tenant_id', '00000000-0000-0000-0000-000000000000', false
            );
            REFRESH MATERIALIZED VIEW CONCURRENTLY public.dashboard_job_metrics;
        END;
        $func$
        """
    )
    # PUBLIC gets EXECUTE on a new function by default, which would make this
    # callable by every role in the database. Revoked first, then granted to
    # the one role that needs it, in that order: the reverse leaves a window in
    # which the function is world-callable.
    op.execute(f"REVOKE ALL ON FUNCTION {FUNCTION}() FROM PUBLIC")
    op.execute(f"GRANT EXECUTE ON FUNCTION {FUNCTION}() TO pickready_app")


def downgrade() -> None:
    op.execute(f"DROP FUNCTION IF EXISTS {FUNCTION}()")
