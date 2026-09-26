"""Migration 0130 makes `evaluations` one LIVE row per application, deleting none.

The writer upserts onto `uq_evaluations_live_link` (`superseded_at IS NULL`),
and `test_miti_not_assessed.py` proves a rescore lands on that one row. This
file proves the migration's half on real rows, inside a transaction that is
rolled back so the migrated test database is left as it was:

  * an application with two evaluation rows keeps BOTH after the upgrade (a
    review disposition may point at either), the older stamped superseded and
    the newer live;
  * the downgrade drops what 0130 added and re-grants UPDATE, and refuses
    while a report row states a skill with no score.
"""
from __future__ import annotations

import importlib.util
import pathlib
import uuid

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect as sa_inspect
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from tests.test_report_insert_only import written  # noqa: F401  (the fixture)

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "0130_report_immutability.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location("migration_0130", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_vocabularies_match_the_code() -> None:
    from app.services.assessment_pipeline import persistence
    from app.services.miti import grades
    from app.services.miti import aggregation
    from app.services.siddhi import remarks

    module = _migration()
    assert set(module.DIMENSION_STATUSES) == set(grades.ANSWER_STATUSES)
    assert set(module.REMARK_SOURCES) == set(remarks.REMARK_SOURCES)
    assert set(module.OVERALL_STATUSES) == {aggregation.OVERALL_GRADED, aggregation.OVERALL_NOT_ASSESSED}
    assert set(module.EVALUATION_STATUSES) == {
        persistence.EVALUATION_COMPLETE, persistence.EVALUATION_NOT_ASSESSED,
    }


async def _in_rolled_back_transaction(work):
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            try:
                return await conn.run_sync(work)
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_upgrade_supersedes_older_duplicates_and_deletes_none(written) -> None:  # noqa: F811
    _factory, w, _report_id = written
    module = _migration()
    older = uuid.uuid4()

    def _round_trip(sync_conn):
        sync_conn.exec_driver_sql("SELECT set_config('app.bypass_rls', 'on', true)")
        with Operations.context(MigrationContext.configure(sync_conn)):
            module.downgrade()
            assert "superseded_at" not in {
                c["name"] for c in sa_inspect(sync_conn).get_columns("evaluations")
            }
            sync_conn.execute(
                text(
                    "INSERT INTO evaluations (id, tenant_id, job_id, link_id, scorecard_version, "
                    "created_at) VALUES (:id, :t, :j, :l, 1, now() - interval '1 day')"
                ),
                {"id": str(older), "t": str(w.tenant), "j": str(w.job), "l": str(w.links[0])},
            )
            module.upgrade()
        return sync_conn.execute(
            text(
                "SELECT id, superseded_at IS NOT NULL AS superseded FROM evaluations "
                "WHERE link_id = :l"
            ),
            {"l": str(w.links[0])},
        ).all()

    rows = await _in_rolled_back_transaction(_round_trip)
    assert len(rows) == 2, "no evaluation row is deleted"
    superseded = {row.id: row.superseded for row in rows}
    assert superseded[older] is True
    assert list(superseded.values()).count(False) == 1, "exactly one live row"


@pytest.mark.asyncio
async def test_the_downgrade_refuses_while_a_skill_carries_no_score(written) -> None:  # noqa: F811
    _factory, w, report_id = written
    module = _migration()

    def _attempt(sync_conn):
        sync_conn.exec_driver_sql("SELECT set_config('app.bypass_rls', 'on', true)")
        sync_conn.exec_driver_sql(
            "ALTER TABLE report_dimensions DISABLE TRIGGER trg_report_dimensions_immutable"
        )
        sync_conn.execute(
            text(
                "UPDATE report_dimensions SET score = NULL, assessment_status = 'not_assessed' "
                "WHERE id = (SELECT id FROM report_dimensions WHERE report_id = :r LIMIT 1)"
            ),
            {"r": str(report_id)},
        )
        with Operations.context(MigrationContext.configure(sync_conn)):
            with pytest.raises(RuntimeError, match="0130 downgrade refused"):
                module.downgrade()

    await _in_rolled_back_transaction(_attempt)
