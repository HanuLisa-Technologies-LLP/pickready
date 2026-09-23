"""Give the least privileged application role a login, and rotate its password.

WHY THIS EXISTS: THE 2026-09-11 OUTAGE
---------------------------------------
`infra/modules/rds` has said from the first line of its header that "THE
APPLICATION DOES NOT USE THE MASTER CREDENTIAL. `DATABASE_URL` is a separate
secret holding a least-privileged application role; the master exists to create
that role and to run migrations." That role was never created. `DATABASE_URL`
was hand-composed with the RDS MASTER username and a copy of its password.

`manage_master_user_password = true` hands the master password to Secrets
Manager, which ROTATES it on a schedule. Seven days after the pilot instance was
created AWS rotated it, the copy in `DATABASE_URL` went stale, and every
database connection in the product failed at once: the API, the health probe,
and therefore sign-in. The symptom was misleading in a way worth recording,
because it will be the next reader's first clue too. asyncpg's default `prefer`
SSL mode retries a failed connection WITHOUT TLS, so the traceback said

    no pg_hba.conf entry for host "10.0.11.22", user "readypick_admin",
    database "readypick", no encryption

which reads as a networking or TLS problem. The database's own log carried the
truth one line earlier: `password authentication failed`, on a connection that
had matched the `hostssl` rule perfectly well.

WHAT THIS SCRIPT DOES, AND THE ORDER IT DOES IT IN
---------------------------------------------------
1. Gives `pickready_app` a freshly generated password (and, on the first run,
   LOGIN and NOINHERIT).
2. On the first run only: moves object ownership OFF the rotating master onto a
   dedicated NOLOGIN owner role, and makes `pickready_app` a member of it so
   `alembic/env.py` can `SET ROLE` up for DDL.
3. Re-applies the canonical grants the migrations maintain, as the owner. Belt
   and braces: they should already be complete, and a run that finds them
   complete changes nothing.
4. PROVES the new credential works by opening a second connection with it.
5. Only then writes the composed DSN to Secrets Manager.

Step 4 before step 5 is the whole safety property. Writing the secret first and
discovering the role cannot log in would leave the product holding a credential
that has never worked, which is the outage this script exists to end, caused by
the fix for it.

WHY OWNERSHIP MOVES TO A THIRD ROLE, WHICH LOOKS LIKE ONE ROLE TOO MANY
------------------------------------------------------------------------
The obvious design is to make `pickready_app` a member of the master and have
migrations `SET ROLE` to that. PostgreSQL 16 refuses to build it:

    ERROR:  permission denied to grant role "readypick_admin"
    DETAIL:  Only roles with the ADMIN option on role "readypick_admin" may
             grant this role.

A role does not have ADMIN OPTION on itself, and the RDS master is not a
superuser, so the master cannot grant itself to anything. It CAN grant a role
it created, which is what makes the third role the shortest path rather than an
extra one: the master creates `readypick_owner`, hands it every object it owns
(`REASSIGN OWNED`), and grants it to `pickready_app`. After that the master is
never needed again and AWS may rotate its password forever.

The sequence has one more non-obvious step. PG16 gives the creator of a role
ADMIN OPTION but NOT inheritance, and `REASSIGN OWNED ... TO x` requires the
privileges OF x, so the master takes that grant explicitly first. Every step of
this was run against `pgvector/pgvector:pg16` with a non-superuser CREATEROLE
master before it was written here; the end state was checked in both
directions, DML succeeding and `ALTER TABLE` refused until `SET ROLE`.

THE PASSWORD NEVER LEAVES THE VPC AND IS NEVER PRINTED
-------------------------------------------------------
It is generated here, inside the task, and goes straight into the secret. It is
not an argument, so it is not in a `RunTask` API call, not in CloudTrail, not in
a shell history and not in this process's own logs. That is also why the target
secret is named by ARN in the environment rather than composed from a prefix:
the task role's grant is one enumerated ARN (`service_secret_writers` in
`infra/modules/secrets`), so the blast radius of this script is one secret.

RE-RUNNING IT IS A ROTATION
----------------------------
Every run mints a new password. The second and later runs connect AS
`pickready_app` and escalate through the membership granted by the first, so
rotating the application credential needs the master exactly once, ever. See
`scripts/rotate-app-db-credential.sh`.

The services keep the old password until they restart, which is correct and is
the operator's next step rather than this script's: a running task holding a
pooled connection is unaffected until it opens a new one.
"""
from __future__ import annotations

import asyncio
import os
import secrets as secrets_module
import sys
from urllib.parse import quote, urlsplit, urlunsplit

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.core.db import SQL_IDENTIFIER_RE

#: The environment variable naming the secret this writes. No default and no
#: prefix arithmetic: an operator who has not said which secret to write has not
#: decided, and guessing would overwrite a DSN in some other environment.
DSN_SECRET_ENV = "APP_DSN_SECRET_ID"

#: The table whose owner is, by definition, the role that ran the migrations.
#: Asking the catalogue rather than trusting configuration: the owner is a fact
#: about the database, and a mismatched guess would grant membership in a role
#: that owns nothing.
_OWNER_PROBE = (
    "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE relname = 'users'"
    " AND relkind = 'r'"
)

#: Exactly what `0014_job_grade_and_grants` and every migration since maintain.
#: Restated here so a role that was created by hand, or a table that predates
#: the default privileges, cannot leave a gap that only shows up as a 500 on
#: one endpoint.
_CANONICAL_GRANTS = (
    "GRANT USAGE ON SCHEMA public TO {role}",
    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {role}",
    "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public"
    " GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public"
    " GRANT USAGE, SELECT ON SEQUENCES TO {role}",
    # LAST, and the order matters: the blanket grant above would re-grant what
    # this takes away. An audit row is a record of something that happened, so
    # the application may add to the log and may not rewrite it.
    "REVOKE UPDATE, DELETE ON audit_log FROM {role}",
)


class ProvisioningRefused(RuntimeError):
    """A precondition was not met. Nothing was written."""


def _app_role() -> str:
    role = get_settings().postgres_rls_app_role.strip()
    if not role or not SQL_IDENTIFIER_RE.match(role):
        raise ProvisioningRefused(
            f"POSTGRES_RLS_APP_ROLE is not a plain SQL identifier: {role!r}"
        )
    return role


def _new_password() -> str:
    """A long URL-safe password.

    URL-safe alphabet on purpose. The DSN is a URL, so any other character has
    to be percent-encoded, and a percent in a DSN has already cost this product
    one broken migration run (see the `%%` doubling in `alembic/env.py`). The
    value is still percent-encoded on the way in, so this is belt and braces
    rather than the only protection.
    """
    return secrets_module.token_urlsafe(36)


def _compose_dsn(current: str, role: str, password: str) -> str:
    """The current DSN with its credentials replaced, everything else kept.

    Host, port, database name and every query parameter are carried across
    verbatim. Rebuilding a DSN from parts would be a second place that knows
    what the database is called, and it is the place that would be forgotten
    the day a parameter is added to the other one.
    """
    parts = urlsplit(current)
    if not parts.hostname:
        raise ProvisioningRefused("DATABASE_URL has no host to carry across")
    host = parts.hostname
    if parts.port:
        host = f"{host}:{parts.port}"
    netloc = f"{quote(role, safe='')}:{quote(password, safe='')}@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


async def _owner_of_the_schema(conn) -> str:
    owner = (await conn.execute(text(_OWNER_PROBE))).scalar()
    if not owner or not SQL_IDENTIFIER_RE.match(owner):
        raise ProvisioningRefused(
            "could not read the owner of the `users` table; has the migration "
            "job ever run against this database?"
        )
    return owner


async def _rendered(conn, template: str, *values: str) -> str:
    """Let Postgres do the quoting, with `format`'s %I and %L.

    A role name and a password are interpolated into DDL that cannot take bind
    parameters. Asking the server to build the statement means the escaping
    rules are the server's own rather than this file's guess at them.
    """
    # Every bind is CAST, deliberately, and spelled `CAST(x AS text)` rather
    # than `x::text`. `format` is variadic "any", so Postgres cannot infer a
    # parameter's type and answers `could not determine data type of parameter
    # $2`, which reads like a driver bug and is just an untyped placeholder.
    # The `::` spelling collides with `text()`'s own `:name` binds and produces
    # a syntax error at the second colon.
    binds = ", ".join(f"CAST(:v{index} AS text)" for index in range(len(values)))
    statement = text(
        f"SELECT format(CAST(:tmpl AS text), {binds})"
        if binds
        else "SELECT format(CAST(:tmpl AS text))"
    )
    params = {"tmpl": template}
    params.update({f"v{index}": value for index, value in enumerate(values)})
    return (await conn.execute(statement, params)).scalar()


def _owner_role() -> str:
    """The dedicated NOLOGIN role that owns every object.

    Shared with `alembic/env.py`, which SET ROLEs to it for DDL, so the two
    cannot disagree about which role owns the schema.
    """
    role = get_settings().postgres_migration_role.strip()
    if not role or not SQL_IDENTIFIER_RE.match(role):
        raise ProvisioningRefused(
            "POSTGRES_MIGRATION_ROLE must name the object-owner role for this "
            f"environment; got {role!r}"
        )
    return role


async def _move_ownership_off_the_master(conn, owner: str, current_owner: str) -> None:
    """First run only: hand every object to `owner` and leave the master behind.

    Runs as the master, which is the only role that can do any of it. Skipped
    entirely on a rotation run, where the objects already belong to `owner`.
    """
    await conn.execute(
        text(await _rendered(conn, "CREATE ROLE %I NOLOGIN", owner))
    )
    # PG16 gives the creator ADMIN OPTION but not inheritance, and REASSIGN
    # OWNED requires the privileges OF the target role. Taking the grant
    # explicitly is what makes the next statement legal; admin option is what
    # makes taking it legal.
    await conn.execute(
        text(await _rendered(conn, "GRANT %I TO %I WITH INHERIT TRUE", owner, current_owner))
    )
    await conn.execute(
        text(await _rendered(conn, "REASSIGN OWNED BY %I TO %I", current_owner, owner))
    )


async def provision() -> str:
    """Do the work and return the composed DSN. Writes nothing to a log."""
    settings = get_settings()
    role = _app_role()
    owner = _owner_role()
    password = _new_password()

    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with engine.begin() as conn:
            # The role is migration 0001's to create, and this refuses rather
            # than creating a second one. Two creators of one role drift, and
            # the one that drifts is whichever ran last on a database nobody
            # was watching.
            exists = (
                await conn.execute(
                    text("SELECT 1 FROM pg_roles WHERE rolname = :name"),
                    {"name": role},
                )
            ).scalar()
            if not exists:
                raise ProvisioningRefused(
                    f"role {role!r} does not exist. Migration 0001 creates it; "
                    "run `alembic upgrade head` against this database first."
                )

            current_owner = await _owner_of_the_schema(conn)
            bootstrapping = current_owner != owner

            # THE PASSWORD FIRST, and on its own, because the two runs have
            # different rights here. A bootstrap run is the master, which holds
            # CREATEROLE and may set any attribute. A rotation run IS the app
            # role, which may change its OWN password and nothing else -- so
            # LOGIN and NOINHERIT are set once, where they can be.
            if bootstrapping:
                await conn.execute(
                    text(
                        await _rendered(
                            conn,
                            "ALTER ROLE %I WITH LOGIN NOINHERIT PASSWORD %L",
                            role,
                            password,
                        )
                    )
                )
                await _move_ownership_off_the_master(conn, owner, current_owner)
                await conn.execute(
                    text(await _rendered(conn, "GRANT %I TO %I", owner, role))
                )
            else:
                await conn.execute(
                    text(
                        await _rendered(
                            conn, "ALTER ROLE %I WITH PASSWORD %L", role, password
                        )
                    )
                )

            # As the OWNER, because granting on a table is the owner's act. A
            # rotation run reaches it through the membership granted above,
            # which is the same door `alembic/env.py` uses and the only one.
            await conn.execute(text(await _rendered(conn, "SET ROLE %I", owner)))
            for statement in _CANONICAL_GRANTS:
                await conn.execute(text(statement.format(role=f'"{role}"')))
    finally:
        await engine.dispose()

    dsn = _compose_dsn(settings.database_url, role, password)

    # PROVE IT, on its own connection, before anything is written anywhere.
    probe = create_async_engine(dsn, pool_pre_ping=False)
    try:
        async with probe.connect() as conn:
            who = (await conn.execute(text("SELECT current_user"))).scalar()
            if who != role:
                raise ProvisioningRefused(
                    f"connected with the new credential and got {who!r}, not {role!r}"
                )
            # A real read through the real policies, not `SELECT 1`. The grants
            # are the half of this that a bare liveness check cannot see.
            await conn.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
            await conn.execute(
                text(
                    "SELECT set_config('app.tenant_id',"
                    " '00000000-0000-0000-0000-000000000000', true)"
                )
            )
            await conn.execute(text("SELECT count(*) FROM users"))
    finally:
        await probe.dispose()

    return dsn


def _write_secret(secret_id: str, dsn: str) -> str:
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "secretsmanager",
        config=Config(
            connect_timeout=5,
            read_timeout=10,
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )
    return client.put_secret_value(SecretId=secret_id, SecretString=dsn)["VersionId"]


async def main() -> int:
    secret_id = os.environ.get(DSN_SECRET_ENV, "").strip()
    if not secret_id:
        print(
            f"{DSN_SECRET_ENV} is not set. This script writes the composed DSN "
            "to Secrets Manager and deliberately has no way to print it, so "
            "there is nothing useful it can do without one.",
            file=sys.stderr,
        )
        return 2

    dsn = await provision()
    version = _write_secret(secret_id, dsn)
    # The ROLE and the VERSION. Never the DSN, which carries the password.
    print(f"app_db_role.rotated role={_app_role()} secret_version={version}")
    print(
        "The services keep their existing connections until they restart. "
        "Roll them with scripts/deploy-services.sh, and recycle the Lambdas "
        "with scripts/update-lambda-code.sh."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
