"""Keep database enum vocabularies readable by their Python model enums.

The production pipeline incident was not a PostgreSQL native-enum problem:
``pipeline_status.status`` and ``job_candidate_links.status`` are VARCHAR
columns. Migration 0018 widened the latter's CHECK vocabulary, while the
``PipelineStatus`` Python enum stayed behind. SQLAlchemy then raised while
materialising a row and every dashboard read for that tenant returned 500.

This test covers the schema that actually exists:

* PostgreSQL native enum labels, if any are introduced later;
* string-valued CHECK ... IN (...) vocabularies on mapped Enum columns; and
* every distinct value already stored in every SQLAlchemy Enum-mapped column.

It deliberately runs against the migrated real PostgreSQL service in CI.

WHAT THE FIRST VERSION DID NOT COVER, AND WHAT IT COST
-------------------------------------------------------
It walked columns whose SQLAlchemy type is ``Enum``. ``resume_storage_provider``
is a plain ``String(20)`` with a ``CHECK ... IN``, so it was invisible here --
and it had drifted exactly the way migration 0018 drifted. The AWS move changed
``resume_storage.STORAGE_PROVIDER`` from ``"gcs"`` to ``"s3"`` and no migration
widened the vocabulary, so every resume upload was refused by PostgreSQL.
``0060_resume_provider_s3`` fixes it and
``test_every_storage_provider_the_code_writes_is_accepted`` keeps it fixed:
the expected set is READ OUT OF the writing module, so changing the constant
again without a migration fails here rather than in production.

The other addition needs no database at all, on purpose. The migration chain
itself was broken -- ``0058_single_embedding_space`` named 0057's FILENAME where
its REVISION ID belongs, so ``alembic upgrade head`` raised ``KeyError`` before
executing a single statement and no fresh database could be created. Every test
in this module needs a migrated database, so that defect hid this one. A chain
check that needs no database is a check that still runs in the environment where
the chain is broken.
"""
from __future__ import annotations

import pathlib
import re
import uuid

import pytest
from sqlalchemy import Enum as SAEnum
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.models import Base  # imports every model into Base.metadata
from app.models.enums import PipelineStatus
from app.services import resume_storage


_SQL_LITERAL = re.compile(r"'((?:''|[^'])*)'")


def _python_values(column) -> set[str]:
    enum_class = column.type.enum_class
    return {str(member.value) for member in enum_class}


def _check_values(definition: str) -> set[str]:
    return {value.replace("''", "'") for value in _SQL_LITERAL.findall(definition)}


def _engine():
    """The engine these tests run against, and NO reachability skip.

    Every other database test in this suite guards itself with
    `_factory_or_skip()`. This module deliberately does not. Something has to
    fail loudly when the database is absent, or the environment gap that hid
    two production defects behind seventy-nine SKIPPED lines simply reopens.
    `tests/conftest.py` defaults DATABASE_URL to the containerised stack, so the
    only way to reach this failure is to not have started it.
    """
    return create_async_engine(get_settings().database_url)


def _unreachable(exc: Exception) -> AssertionError:
    """Turn a connection error into the sentence that fixes it."""
    return AssertionError(
        "The test database is not reachable at "
        f"{get_settings().database_url.split('@')[-1]}. This is not an enum "
        "parity failure; nothing was checked. Start the stack with: "
        "docker compose -f docker-compose.test.yml up -d "
        "&& (cd backend && alembic upgrade head). "
        f"Underlying error: {type(exc).__name__}: {exc}"
    )



@pytest.fixture
async def migrated_database():
    """Turn a refused connection or an unmigrated schema into the sentence
    that fixes it.

    Requested BY NAME rather than autouse, because
    `test_the_migration_chain_resolves_end_to_end` deliberately needs no
    database: the environment where the chain is broken is exactly the
    environment where nothing else here can run, so gating it on a live
    connection would put the check behind the failure it exists to catch.

    Without this a stopped stack produces five ConnectionRefusedError
    tracebacks, which read as five enum-parity failures and are not: nothing was
    compared. The distinction matters because this module exists to report drift
    and "the environment was absent" is the one answer it must never be confused
    with.
    """
    engine = _engine()
    try:
        async with engine.connect() as connection:
            try:
                stamped = (
                    await connection.execute(
                        text("SELECT version_num FROM alembic_version")
                    )
                ).scalar_one_or_none()
            except Exception as exc:  # noqa: BLE001 - re-raised as a precise message
                raise AssertionError(
                    "The test database exists but has never been migrated: "
                    "there is no alembic_version table. This is not an enum "
                    "parity failure; nothing was compared. Run "
                    "`./scripts/test.sh`, which migrates it, or export "
                    "DATABASE_URL and run `alembic upgrade head` yourself. "
                    "A bare `alembic upgrade head` uses the config default of "
                    "localhost:5432, which is NOT the test stack."
                ) from exc
            assert stamped, (
                "alembic_version is empty, so the schema is whatever a partial "
                "run left. Re-run ./scripts/test.sh."
            )
    except AssertionError:
        raise
    except Exception as exc:  # noqa: BLE001 - re-raised as a precise message
        raise _unreachable(exc) from exc
    finally:
        await engine.dispose()
    yield

@pytest.mark.asyncio
async def test_every_database_enum_value_has_a_python_member(migrated_database) -> None:
    engine = _engine()
    failures: list[str] = []
    checked: list[str] = []
    try:
        async with engine.begin() as connection:
            # The vocabulary audit is intentionally cross-tenant. Use the same
            # explicit policy escape hatch as structural/admin code, never an
            # implicit owner/BYPASSRLS privilege.
            await connection.execute(
                text(
                    "SELECT set_config('app.tenant_id', "
                    "'00000000-0000-0000-0000-000000000000', true)"
                )
            )
            await connection.execute(
                text("SELECT set_config('app.bypass_rls', 'on', true)")
            )

            for table in Base.metadata.tables.values():
                for column in table.columns:
                    if not isinstance(column.type, SAEnum) or column.type.enum_class is None:
                        continue

                    key = f"{table.name}.{column.name}"
                    expected = _python_values(column)
                    checked.append(key)

                    db_type = (
                        await connection.execute(
                            text(
                                """
                                SELECT t.typtype, t.typname
                                FROM pg_attribute a
                                JOIN pg_class c ON c.oid = a.attrelid
                                JOIN pg_namespace n ON n.oid = c.relnamespace
                                JOIN pg_type t ON t.oid = a.atttypid
                                WHERE n.nspname = 'public'
                                  AND c.relname = :table_name
                                  AND a.attname = :column_name
                                  AND NOT a.attisdropped
                                """
                            ),
                            {"table_name": table.name, "column_name": column.name},
                        )
                    ).one()

                    if db_type.typtype == "e":
                        native_values = set(
                            (
                                await connection.execute(
                                    text(
                                        """
                                        SELECT e.enumlabel
                                        FROM pg_enum e
                                        JOIN pg_type t ON t.oid = e.enumtypid
                                        WHERE t.typname = :type_name
                                        ORDER BY e.enumsortorder
                                        """
                                    ),
                                    {"type_name": db_type.typname},
                                )
                            ).scalars()
                        )
                        if native_values != expected:
                            failures.append(
                                f"{key}: native enum has {sorted(native_values)}, "
                                f"Python has {sorted(expected)}"
                            )

                    constraints = (
                        await connection.execute(
                            text(
                                """
                                SELECT pg_get_constraintdef(con.oid) AS definition
                                FROM pg_constraint con
                                JOIN pg_class c ON c.oid = con.conrelid
                                JOIN pg_namespace n ON n.oid = c.relnamespace
                                WHERE n.nspname = 'public'
                                  AND c.relname = :table_name
                                  AND con.contype = 'c'
                                  AND :column_name = ANY (
                                      SELECT a.attname
                                      FROM unnest(con.conkey) AS key(attnum)
                                      JOIN pg_attribute a
                                        ON a.attrelid = con.conrelid
                                       AND a.attnum = key.attnum
                                  )
                                """
                            ),
                            {"table_name": table.name, "column_name": column.name},
                        )
                    ).scalars()
                    for definition in constraints:
                        if " IN (" not in definition.upper():
                            continue
                        allowed = _check_values(definition)
                        if allowed != expected:
                            failures.append(
                                f"{key}: CHECK has {sorted(allowed)}, "
                                f"Python has {sorted(expected)}"
                            )

                    preparer = connection.dialect.identifier_preparer
                    quoted_table = preparer.quote(table.name)
                    quoted_column = preparer.quote(column.name)
                    stored_values = set(
                        (
                            await connection.execute(
                                text(
                                    f"SELECT DISTINCT {quoted_column}::text "
                                    f"FROM {quoted_table} "
                                    f"WHERE {quoted_column} IS NOT NULL"
                                )
                            )
                        ).scalars()
                    )
                    unknown = stored_values - expected
                    if unknown:
                        failures.append(
                            f"{key}: stored values missing from Python: {sorted(unknown)}"
                        )
    finally:
        await engine.dispose()

    assert "pipeline_status.status" in checked
    assert "job_candidate_links.status" not in checked  # plain VARCHAR by design
    assert not failures, "\n".join(failures)


@pytest.mark.asyncio
async def test_pipeline_check_vocabulary_matches_pipeline_status(migrated_database) -> None:
    """Guard the exact migration-0018 mismatch before any affected row exists."""
    engine = _engine()
    try:
        async with engine.connect() as connection:
            definition = (
                await connection.execute(
                    text(
                        """
                        SELECT pg_get_constraintdef(oid)
                        FROM pg_constraint
                        WHERE conname = 'ck_jcl_status'
                          AND conrelid = 'job_candidate_links'::regclass
                        """
                    )
                )
            ).scalar_one()
    finally:
        await engine.dispose()

    assert _check_values(definition) == {status.value for status in PipelineStatus}


# ── String CHECK vocabularies the models do not describe ─────────────────────
#
# A `CHECK ... IN` on a plain VARCHAR is an enum the ORM cannot see. The pairs
# below name the column and the module whose constants decide what gets written
# into it, so the assertion is "the database accepts what this code produces"
# rather than "the database matches a list somebody also typed into the test".
# A second copy of the vocabulary in a test file drifts exactly like the first
# copy did.


def _writable_storage_providers() -> set[str]:
    """Every value `services.resume_storage` can put in the column.

    Read from the module rather than restated, so a future provider move that
    changes the constant and forgets the migration fails HERE.
    """
    return {
        resume_storage.STORAGE_PROVIDER,
        resume_storage.LEGACY_STORAGE_PROVIDER,
    }


@pytest.mark.asyncio
async def test_every_storage_provider_the_code_writes_is_accepted(migrated_database) -> None:
    """The exact defect 0060 fixes, pinned against the code that caused it.

    Before 0060 the CHECK said `('cloudinary', 'gcs')` while the uploader wrote
    `'s3'`, so `POST /jobs/{id}/apply` answered 500 for every candidate on every
    tenant. The failure is a PostgreSQL CheckViolationError on INSERT, which no
    amount of application-side testing without a database can produce.
    """
    engine = _engine()
    try:
        async with engine.connect() as connection:
            definition = (
                await connection.execute(
                    text(
                        """
                        SELECT pg_get_constraintdef(oid)
                        FROM pg_constraint
                        WHERE conname = 'ck_profiles_resume_storage_provider'
                          AND conrelid = 'profiles'::regclass
                        """
                    )
                )
            ).scalar_one()
    finally:
        await engine.dispose()

    allowed = _check_values(definition)
    writable = _writable_storage_providers()
    missing = writable - allowed
    assert not missing, (
        "profiles.resume_storage_provider: services.resume_storage writes "
        f"{sorted(missing)}, which the CHECK constraint refuses. Every resume "
        "upload fails with a CheckViolationError until a migration widens it."
    )


@pytest.mark.asyncio
async def test_a_row_carrying_the_current_provider_can_actually_be_stored(migrated_database) -> None:
    """Reading a constraint definition is not the same as writing a row.

    The vocabulary check above parses `pg_get_constraintdef` text. This one asks
    PostgreSQL, through the same INSERT the uploader performs, and rolls back.
    They can disagree -- a constraint can be NOT VALID, or a trigger can refuse
    what the CHECK allows -- and the question the product cares about is whether
    the row lands, which is precisely what the ten skipped upload tests stopped
    asking.
    """
    engine = _engine()
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(
                    text("SELECT set_config('app.bypass_rls', 'on', true)")
                )
                for provider in sorted(_writable_storage_providers()):
                    candidate_id = uuid.uuid4()
                    await connection.execute(
                        text(
                            "INSERT INTO candidates (id, full_name, email) "
                            "VALUES (:id, :name, :email)"
                        ),
                        {
                            "id": candidate_id,
                            "name": "Enum Parity Probe",
                            "email": f"parity-{candidate_id}@example.invalid",
                        },
                    )
                    await connection.execute(
                        text(
                            "INSERT INTO profiles "
                            "(id, candidate_id, resume_storage_provider) "
                            "VALUES (:id, :candidate_id, :provider)"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "candidate_id": candidate_id,
                            "provider": provider,
                        },
                    )
            finally:
                # Nothing this test writes may survive it. The suite shares one
                # database and a probe row left behind is a row the next test
                # counts.
                await transaction.rollback()
    finally:
        await engine.dispose()


# ── The chain that has to be walkable before any of the above can run ────────


def test_the_migration_chain_resolves_end_to_end() -> None:
    """`alembic upgrade head` must reach head from nothing, with no database.

    `0058_single_embedding_space` set `down_revision` to 0057's FILENAME
    (`0057_report_needs_human_review`) rather than its REVISION ID
    (`0057_report_review`). Alembic builds its revision map before it opens a
    connection, so `upgrade head` died with

        KeyError: '0057_report_needs_human_review'

    having executed nothing. No fresh database could be created: not in CI, not
    on a new engineer's machine, and not by `scripts/run-migration.sh` against
    RDS. It survived review because the two strings differ by four words in the
    middle of a file nobody diffs after it merges, and because every test that
    would have caught it needs the database the broken chain prevents.

    This check touches no database on purpose. The environment where the chain
    is broken is exactly the environment where nothing else here can run.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = pathlib.Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    script = ScriptDirectory.from_config(config)

    heads = script.get_heads()
    assert len(heads) == 1, (
        "The migration history has forked into "
        f"{sorted(heads)}. A rolling deploy applies one of them and the other "
        "silently never runs."
    )

    # Walking base -> head is what actually resolves every `down_revision`; a
    # name that is not a revision id raises here exactly as it does in alembic.
    walked = [revision.revision for revision in script.walk_revisions("base", heads[0])]
    assert walked, "No revisions were resolved."

    known = {revision.revision for revision in script.walk_revisions()}
    for revision in script.walk_revisions():
        down = revision.down_revision
        for parent in (down,) if isinstance(down, str) or down is None else down:
            if parent is None:
                continue
            assert parent in known, (
                f"{revision.revision} names down_revision '{parent}', which is "
                "not a revision id in this directory. If it looks like a "
                "filename, that is the defect: alembic keys on the `revision` "
                "string inside the file, never on the file's name."
            )
