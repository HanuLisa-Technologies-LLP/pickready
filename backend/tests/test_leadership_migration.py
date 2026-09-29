"""Migration 0135's Drishti carry-over, run against seeded rows.

Pilot held no Drishti profile on 2026-09-29, so this is the only place the
carry-over ever runs against a row. What it pins: a function name becomes the
tenant's department (matched on the normalised name, created when absent), the
profile becomes version 1 of that department's Functional Head stream with its
sections folded into the two fields, its compiled lines are carried as
department priorities that the live resolution then reads, the Drishti row
itself is untouched, and a second run changes nothing.
"""
from __future__ import annotations

import importlib.util
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from app.core.db import superadmin_scope
from app.services.leadership import context as leadership_context
from tests import leadership_fixtures as lf
from tests import skills_fixtures as fx

MIGRATION = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0135_leadership_intelligence.py"


def _migration():
    spec = importlib.util.spec_from_file_location("m0135", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def world():
    worlds: list[fx.World] = []

    async def _make(**kwargs) -> fx.World:
        w = await fx.seed(**kwargs)
        worlds.append(w)
        return w

    yield _make
    for w in worlds:
        await fx.drop(w)


async def _carry() -> None:
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                for statement in _migration().CARRY_DRISHTI_SQL:
                    await session.execute(text(statement))


async def _one(sql: str, **params):
    async with fx.sessions()() as session:
        async with superadmin_scope(session):
            return (await session.execute(text(sql), params)).mappings().all()


async def test_a_drishti_profile_becomes_the_departments_first_version(world) -> None:
    w = await world()
    compiled = {"version": 1, "function": "Engineering", "context_lines": [
        f"Strategic purpose of the function: {lf.FH_LINE}"
    ]}
    drishti_id = uuid.uuid4()
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "INSERT INTO drishti_profiles (id, tenant_id, function_name, "
                        "strategic_purpose, people_philosophy, compiled_json, updated_by) "
                        "VALUES (:i, :t, '  Engineering ', :sp, :pp, CAST(:c AS jsonb), :u)"
                    ),
                    {"i": drishti_id, "t": w.tenant, "sp": lf.FH_LINE, "pp": lf.IDEAL_LINE,
                     "c": json.dumps(compiled), "u": w.client},
                )

    await _carry()
    await _carry()  # idempotent

    departments = await _one(
        "SELECT id, name FROM company_departments WHERE tenant_id = :t", t=w.tenant
    )
    assert [row["name"] for row in departments] == ["Engineering"]
    profiles = await _one(
        "SELECT * FROM leadership_profiles WHERE tenant_id = :t", t=w.tenant
    )
    assert len(profiles) == 1
    profile = profiles[0]
    assert profile["author_role"] == "functional_head"
    assert profile["version"] == 1
    assert profile["department_id"] == departments[0]["id"]
    assert profile["author_user_id"] == w.client
    assert profile["department_requirements"] == lf.FH_LINE
    assert profile["ideal_employee_expectations"] == lf.IDEAL_LINE
    assert profile["compiled_json"]["migrated_from"] == "drishti_profiles"
    assert (await _one("SELECT count(*) AS n FROM drishti_profiles WHERE id = :i", i=drishti_id))[0]["n"] == 1

    await lf.set_job_department(w, departments[0]["id"], "Engineering")
    live = await fx.run_as(
        w, lambda s, j: leadership_context.resolve_for_job(s, j), commit=False
    )
    assert [line.source for line in live.lines] == ["leadership_functional_head"]
    assert lf.FH_LINE in live.lines[0].text
