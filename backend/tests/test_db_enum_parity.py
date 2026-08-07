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
"""
from __future__ import annotations

import re

import pytest
from sqlalchemy import Enum as SAEnum
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import get_settings
from app.models import Base  # imports every model into Base.metadata
from app.models.enums import PipelineStatus


_SQL_LITERAL = re.compile(r"'((?:''|[^'])*)'")


def _python_values(column) -> set[str]:
    enum_class = column.type.enum_class
    return {str(member.value) for member in enum_class}


def _check_values(definition: str) -> set[str]:
    return {value.replace("''", "'") for value in _SQL_LITERAL.findall(definition)}


@pytest.mark.asyncio
async def test_every_database_enum_value_has_a_python_member() -> None:
    engine = create_async_engine(get_settings().database_url)
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
async def test_pipeline_check_vocabulary_matches_pipeline_status() -> None:
    """Guard the exact migration-0018 mismatch before any affected row exists."""
    engine = create_async_engine(get_settings().database_url)
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
