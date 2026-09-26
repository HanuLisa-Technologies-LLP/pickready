"""A PRISM Report is INSERT-only, in the code and in the database (PLAN-p5 WP5-D).

Three places hold it, and each is asserted where it lives:

  1. THE WRITER. `assessment_pipeline.persistence` issues an INSERT and never
     an UPDATE or a DELETE (by AST, so a comment explaining the rule cannot
     read as a violation). A second write for the same application RAISES
     `ReportAlreadyWritten` rather than rewriting a delivered report.
  2. THE TRIGGER. Migration 0130's BEFORE UPDATE trigger refuses an UPDATE on
     `functional_skills_reports` and on `report_dimensions`, whoever sends it.
  3. THE GRANT. UPDATE is revoked from `pickready_app` on both tables.

And the evaluation beside it is ONE live row: the partial unique index
refuses a second live row, so the writer's upsert is the only way a rescore
lands.

Read back from a SECOND connection, on rows a real scoring run committed.
"""
from __future__ import annotations

import ast
import inspect
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.core.db import superadmin_scope
from app.models import Job, JobCandidateLink
from app.services import agent_loop
from app.services import functional_assessment as fa
from app.services.assessment_formats import evaluation as format_evaluation
from app.services.assessment_pipeline import persistence
from tests.test_assessment_contract import _drop, _factory, _seed
from tests.test_miti_live_rows import _EVIDENCE_VALUE, _INSUFFICIENT, _Script, _issue
from tests.test_miti_report_rows import _application, _run

IMMUTABLE = ("functional_skills_reports", "report_dimensions")


def test_the_writer_issues_no_update_and_no_delete() -> None:
    tree = ast.parse(inspect.getsource(persistence))
    called = {
        node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", "")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    }
    assert not {"update", "delete"} & called, sorted({"update", "delete"} & called)
    literals = [
        node.value.upper()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    for statement in ("UPDATE FUNCTIONAL_SKILLS_REPORTS", "UPDATE REPORT_DIMENSIONS", "DELETE FROM"):
        assert not any(statement in literal for literal in literals), statement


def test_the_orchestrator_has_no_second_writer() -> None:
    source = inspect.getsource(fa)
    assert "FunctionalSkillsReport(" not in source
    assert "ReportDimension(" not in source


@pytest.fixture
async def written(monkeypatch):
    engine, factory = await _factory()
    w = await _seed(factory)
    try:
        await _issue(factory, w)
        await _application(factory, w)

        async def _evaluation(session, **kwargs):
            return agent_loop.LoopResult(value=dict(_EVIDENCE_VALUE), degraded=False)

        monkeypatch.setattr(format_evaluation, "evaluate", _evaluation)
        result, _miti = await _run(
            factory, w, monkeypatch,
            judge=_Script('{"score": 82, "band": "75_89"}'),
            evaluators=_Script(_INSUFFICIENT),
        )
        assert result.report_id is not None
        yield factory, w, result.report_id
    finally:
        await _drop(factory, w)
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("table", IMMUTABLE)
async def test_the_database_refuses_an_update(written, table) -> None:
    """Both locks, separately. As the application role the REVOKE answers
    first; as the table's OWNER (which keeps UPDATE, and is what a migration or
    an operator's session runs as) the trigger answers."""
    factory, _w, report_id = written
    column = "id" if table == "functional_skills_reports" else "report_id"
    statement = text(f"UPDATE {table} SET tenant_id = tenant_id WHERE {column} = :r")
    async with factory() as session:
        async with superadmin_scope(session):
            with pytest.raises(DBAPIError, match="permission denied"):
                await session.execute(statement, {"r": str(report_id)})
            await session.rollback()
    async with factory() as session:
        # The owner, with RLS bypassed so the row is visible to the UPDATE and
        # the refusal is the trigger's rather than an empty match.
        await session.execute(text("SELECT set_config('app.bypass_rls', 'on', true)"))
        with pytest.raises(DBAPIError, match="immutable"):
            await session.execute(statement, {"r": str(report_id)})
        await session.rollback()


@pytest.mark.asyncio
async def test_update_is_revoked_from_the_application_role(written) -> None:
    factory, _w, _report_id = written
    async with factory() as session:
        for table in IMMUTABLE:
            granted = (
                await session.execute(
                    text("SELECT has_table_privilege('pickready_app', :t, 'UPDATE')"),
                    {"t": table},
                )
            ).scalar_one()
            assert granted is False, table
            inserting = (
                await session.execute(
                    text("SELECT has_table_privilege('pickready_app', :t, 'INSERT')"),
                    {"t": table},
                )
            ).scalar_one()
            assert inserting is True, table


@pytest.mark.asyncio
async def test_a_second_write_raises_rather_than_rewriting(written, monkeypatch) -> None:
    """Under the lock the scoring task returns on an existing report; if the
    insert is ever reached anyway, it refuses instead of taking an UPDATE
    branch that would rewrite a delivered report."""
    factory, w, _report_id = written
    async with factory() as session:
        async with superadmin_scope(session):
            assert await persistence.report_exists(session, w.links[0])
            job = await session.get(Job, w.job)
            link = await session.get(JobCandidateLink, w.links[0])
            inputs = type("Inputs", (), {"job": job, "link": link})()
            with pytest.raises(persistence.ReportAlreadyWritten):
                await persistence.write_report(
                    session,
                    inputs,
                    fields={"grade": "managerial", "overall_summary": "x"},
                    dimensions=[],
                    provenance=fa.ProvenanceRecorder(),
                )
            await session.rollback()


@pytest.mark.asyncio
async def test_a_second_live_evaluation_row_is_refused(written) -> None:
    factory, w, _report_id = written
    async with factory() as session:
        async with superadmin_scope(session):
            with pytest.raises(IntegrityError, match="uq_evaluations_live_link"):
                await session.execute(
                    text(
                        "INSERT INTO evaluations (id, tenant_id, job_id, link_id, "
                        "scorecard_version) VALUES (:id, :t, :j, :l, 1)"
                    ),
                    {"id": str(uuid.uuid4()), "t": str(w.tenant), "j": str(w.job), "l": str(w.links[0])},
                )
            await session.rollback()
