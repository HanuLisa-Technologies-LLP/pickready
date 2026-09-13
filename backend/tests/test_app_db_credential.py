"""The application's database credential is its own, and only one thing escalates.

THE OUTAGE THIS FILE EXISTS FOR (2026-09-11)
---------------------------------------------
`DATABASE_URL` held a hand-composed copy of the RDS MASTER credential.
`manage_master_user_password = true` hands that password to Secrets Manager to
ROTATE on a schedule, and seven days after the pilot instance was created AWS
rotated it. The copy went stale and every database connection in the product
failed at once: the API, the health probe, and therefore sign-in. No deploy, no
code change, no warning.

`infra/modules/rds` had documented the correct design in its header from the
first day -- the application uses a least-privileged role, the master exists to
create that role and to run migrations -- and the role had never been created.

Two properties keep it fixed, and both are asserted here rather than described:

  1. The migration job, and nothing that serves a request, can escalate to the
     object owner. `POSTGRES_MIGRATION_ROLE` is read in exactly one place and
     set on exactly one container.
  2. Composing the rotated DSN carries the database's identity across verbatim.
     A rebuild-from-parts would be a second place that knows what the database
     is called, and it is the one that would be forgotten.

A THIRD THING THIS FILE PINS, AND IT IS THE SUBTLEST
-----------------------------------------------------
asyncpg's default `prefer` SSL mode retries a refused connection WITHOUT TLS.
That is why the outage's traceback said `no pg_hba.conf entry ... no
encryption`, which reads as a networking fault, while the database's own log
said `password authentication failed` one line earlier. A deployed DSN carries
`ssl=require` so the fallback cannot happen and the error names its own cause.
"""
from __future__ import annotations

import ast
import pathlib
import re
from urllib.parse import unquote, urlsplit

import pytest

from app.scripts import provision_app_db_role as provision

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
ENV_PY = BACKEND / "alembic" / "env.py"
ENVIRONMENTS = sorted((REPO / "infra" / "environments").glob("*/main.tf"))
SECRETS_MODULE = REPO / "infra" / "modules" / "secrets"


# ── Composing the rotated DSN ────────────────────────────────────────────────


def test_the_rotated_dsn_keeps_the_database_it_was_pointing_at() -> None:
    """Host, port, database and every query parameter carry across untouched."""
    before = (
        "postgresql+asyncpg://readypick_admin:old%2Apass@"
        "readypick-pilot.example.rds.amazonaws.com:5432/readypick?ssl=require"
    )
    after = provision._compose_dsn(before, "pickready_app", "new-password")

    old, new = urlsplit(before), urlsplit(after)
    assert (new.scheme, new.hostname, new.port, new.path, new.query) == (
        old.scheme,
        old.hostname,
        old.port,
        old.path,
        old.query,
    )
    assert new.username == "pickready_app"
    assert new.password == "new-password"


def test_the_ssl_parameter_survives_rotation() -> None:
    """The one query parameter whose loss is invisible until it matters.

    Without `ssl=require` asyncpg falls back to an unencrypted connection when
    the TLS attempt fails, so a rotation that dropped it would move the product
    onto plaintext silently, and RDS would answer with a pg_hba error that
    names encryption rather than the credential.
    """
    rotated = provision._compose_dsn(
        "postgresql+asyncpg://owner:pw@db.internal:5432/readypick?ssl=require",
        "pickready_app",
        "x",
    )
    assert "ssl=require" in rotated


def test_a_password_needing_encoding_is_encoded() -> None:
    dsn = provision._compose_dsn(
        "postgresql+asyncpg://owner:pw@db.internal:5432/readypick",
        "pickready_app",
        "a/b@c:d?e",
    )
    assert "a/b@c:d?e" not in dsn
    # `urlsplit` does not decode, which is the point: the character that would
    # have ended the userinfo early is encoded in the string the driver reads,
    # and the host is still the host rather than whatever followed the `@`.
    assert unquote(urlsplit(dsn).password) == "a/b@c:d?e"
    assert urlsplit(dsn).hostname == "db.internal"


def test_a_dsn_with_no_host_is_refused_rather_than_half_composed() -> None:
    with pytest.raises(provision.ProvisioningRefused):
        provision._compose_dsn("postgresql+asyncpg:///readypick", "pickready_app", "x")


def test_the_generated_password_is_url_safe_and_long() -> None:
    """URL-safe by construction, because a DSN is a URL and a stray percent in
    one has already cost this product a migration run (see env.py's `%%`)."""
    for _ in range(20):
        password = provision._new_password()
        assert len(password) >= 40
        assert re.fullmatch(r"[A-Za-z0-9_-]+", password), password


# ── The grants ───────────────────────────────────────────────────────────────


def test_the_audit_log_revoke_comes_after_the_blanket_grant() -> None:
    """Order is the whole rule. `GRANT ... ON ALL TABLES` includes audit_log,
    so a revoke placed before it is a revoke that is immediately undone."""
    statements = provision._CANONICAL_GRANTS
    blanket = next(
        index
        for index, statement in enumerate(statements)
        if "ON ALL TABLES IN SCHEMA public" in statement
    )
    revoke = next(
        index
        for index, statement in enumerate(statements)
        if statement.startswith("REVOKE UPDATE, DELETE ON audit_log")
    )
    assert revoke > blanket, (
        "the audit_log revoke runs before the blanket grant re-adds what it "
        "takes away, so the append-only property is not enforced at all"
    )


def test_the_owner_role_must_be_named_before_anything_is_provisioned(
    monkeypatch,
) -> None:
    """No default, and no guess.

    The owner role is the one whose membership lets migrations run DDL. A
    default would pick a role for an environment nobody configured, and the
    plausible default is the RDS master -- which is exactly the role this whole
    change exists to stop depending on.
    """
    from app.core import config as config_module

    monkeypatch.setattr(
        config_module.get_settings(), "postgres_migration_role", "", raising=False
    )
    with pytest.raises(provision.ProvisioningRefused):
        provision._owner_role()


def test_the_owner_is_read_from_the_catalogue_not_from_configuration() -> None:
    """Which role owns the schema is a fact about the database.

    Trusting configuration for it would let a mistyped value reassign objects to
    a role that owns nothing, which succeeds and leaves migrations unable to run.
    """
    assert "pg_class" in provision._OWNER_PROBE
    assert "relowner" in provision._OWNER_PROBE


def test_the_role_name_must_be_a_plain_identifier(monkeypatch) -> None:
    """A role name is interpolated into DDL. It is validated, never quoted and
    hoped, and the refusal names the setting."""
    from app.core import config as config_module

    monkeypatch.setattr(
        config_module.get_settings(),
        "postgres_rls_app_role",
        'app"; DROP',
        raising=False,
    )
    with pytest.raises(provision.ProvisioningRefused):
        provision._app_role()


# ── The escalation lives in one place ────────────────────────────────────────


def test_migrations_set_role_before_anything_else_runs() -> None:
    """Read out of the source: `env.py` cannot be imported without an Alembic
    run context, the same reason `test_alembic_dsn_escaping` reads it this way.

    The ORDER is the assertion. A `SET ROLE` issued after `run_migrations()`
    would be a no-op with a comment explaining why it was important.
    """
    body = ast.parse(ENV_PY.read_text(encoding="utf-8"))
    function = next(
        node
        for node in ast.walk(body)
        if isinstance(node, ast.FunctionDef) and node.name == "_do_run_migrations"
    )

    def first_line_containing(fragment: str) -> int:
        return min(
            node.lineno
            for node in ast.walk(function)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and fragment in node.value
        )

    set_role_line = first_line_containing("SET ROLE")
    bypass_line = first_line_containing("app.bypass_rls")
    run_line = min(
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run_migrations"
    )
    assert set_role_line < bypass_line < run_line


#: The two modules under `app/` that may name the owner role, each for a stated
#: reason. `provision_app_db_role` is the script that CREATES that role, so it
#: necessarily names it; it runs as a one-off command on the migrate task
#: definition, the same container the variable is set on, and is reachable from
#: no route. `config` declares the setting. `alembic/env.py` is the third
#: reader and is outside `app/` entirely.
MIGRATION_ROLE_READERS = {"app/core/config.py", "app/scripts/provision_app_db_role.py"}


def test_only_the_migration_path_names_the_migration_role() -> None:
    """The escalation is reachable from the migration path and nowhere else.

    A route, a service or a worker that read this setting could `SET ROLE` to
    the object owner, which is the whole thing the split prevents. Swept over
    the tree rather than checked at a call site, and pinned as an exact set so
    a third reader is a test change somebody has to justify rather than an
    import somebody adds.
    """
    readers = {
        path.relative_to(BACKEND).as_posix()
        for path in (BACKEND / "app").rglob("*.py")
        if "postgres_migration_role" in path.read_text(encoding="utf-8")
    }
    assert readers == MIGRATION_ROLE_READERS, (
        "the set of modules naming POSTGRES_MIGRATION_ROLE changed.\n"
        f"  added:   {sorted(readers - MIGRATION_ROLE_READERS)}\n"
        f"  removed: {sorted(MIGRATION_ROLE_READERS - readers)}"
    )


def test_no_route_service_or_worker_names_the_migration_role() -> None:
    """The same rule stated where it is sharpest, so the exemption above cannot
    be widened into the surfaces that actually serve traffic."""
    offenders = [
        path.relative_to(BACKEND).as_posix()
        for directory in ("api", "services", "workers")
        for path in (BACKEND / "app" / directory).rglob("*.py")
        if "postgres_migration_role" in path.read_text(encoding="utf-8")
    ]
    assert offenders == [], (
        f"{offenders} can escalate to the object owner. Only the migration "
        "path may, and it is not reachable from a request."
    )


# ── The deployment shape ─────────────────────────────────────────────────────


def _terraform_service_block(text: str, service: str) -> str:
    """The `<service> = {` block from a services map, brace-matched."""
    start = text.index(f"\n    {service} = {{")
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"unbalanced braces around the {service} service block")


@pytest.mark.parametrize("main_tf", ENVIRONMENTS, ids=lambda p: p.parent.name)
def test_only_the_migration_job_can_escalate_or_rewrite_the_dsn(main_tf) -> None:
    """Both powers are set on the `migrate` container and on no other.

    Swept over the whole file rather than checked at one service, because the
    failure being prevented is somebody copying a services block and taking the
    environment map with it.
    """
    text = main_tf.read_text(encoding="utf-8")
    for variable in ("POSTGRES_MIGRATION_ROLE", "APP_DSN_SECRET_ID"):
        occurrences = text.count(variable)
        if occurrences == 0:
            continue  # an environment that has not adopted the split yet
        migrate_block = _terraform_service_block(text, "migrate")
        assert migrate_block.count(variable) == occurrences, (
            f"{variable} appears {occurrences} times in {main_tf.parent.name} "
            f"but only {migrate_block.count(variable)} inside the migrate "
            "service. Any other container carrying it can become the object "
            "owner or rewrite the application's credential."
        )


def test_the_only_secret_anything_may_write_is_the_dsn_and_only_migrate_may() -> None:
    """The write grant is new and narrow, and it must stay both."""
    variables = (SECRETS_MODULE / "variables.tf").read_text(encoding="utf-8")
    block = variables[variables.index('variable "service_secret_writers"') :]
    block = block[: block.index("\nvariable ")]
    default = block[block.index("default") :]
    assert re.search(r'"migrate"\s*=\s*\["DATABASE_URL"\]', default), default
    named = set(re.findall(r'"([A-Z_]{3,})"', default))
    assert named == {"DATABASE_URL"}, (
        f"service_secret_writers now grants writes to {sorted(named)}. "
        "PutSecretValue on anything but the DSN needs its own argument."
    )


def test_the_write_grant_is_put_secret_value_and_nothing_else() -> None:
    """Not DeleteSecret, not UpdateSecret, not RestoreSecret. Replacing a value
    is the operation; removing the container is not something a rotation job
    should be able to do."""
    main = (SECRETS_MODULE / "main.tf").read_text(encoding="utf-8")
    actions = re.findall(r'"secretsmanager:(\w+)"', main)
    assert set(actions) <= {
        "GetSecretValue",
        "DescribeSecret",
        "PutSecretValue",
    }, actions
