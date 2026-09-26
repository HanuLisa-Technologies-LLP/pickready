"""The grade locks with the skills at the first candidate start (D5).

It decides the question budget every candidate on the job receives, so once a
`job_skill_snapshots` row exists `PATCH /jobs/{id}` refuses a grade CHANGE
with `GRADE_LOCKED_DETAIL`, and nothing is written. Asked of the snapshot
TABLE, never of a timestamp. Resending the current grade is not a change, and
the job's other metadata stays editable after the lock.

Through the real router and a session on the RLS application role, read back
from a SECOND connection.

MUTATION CHECK, recorded: removing the `assessment_contract.is_locked` check
from `patch_job` makes `test_a_grade_change_after_the_lock_is_a_409_and_writes_nothing`
fail with a 200 and a moved grade.
"""
from __future__ import annotations

import pytest

from app.api import jobs as jobs_api
from tests import job_setup_api_fixtures as http
from tests import skills_fixtures as fx


@pytest.fixture
async def world():
    worlds: list[fx.World] = []

    async def _make() -> fx.World:
        w = await fx.seed()
        worlds.append(w)
        return w

    yield _make
    for w in worlds:
        await http.drop(w)


async def _grade(w: fx.World) -> dict:
    return (
        await http.read(
            "SELECT assessment_grade, title FROM jobs WHERE id = :j", j=w.job
        )
    )[0]


async def test_the_grade_moves_freely_before_the_lock(world) -> None:
    w = await world()
    async with http.api(w) as api:
        response = await api.http.patch(f"{http.JOBS}/{w.job}", json={"grade": "leadership"})
    assert response.status_code == 200, response.text
    assert (await _grade(w))["assessment_grade"] == "leadership"


async def test_a_grade_change_after_the_lock_is_a_409_and_writes_nothing(world) -> None:
    w = await world()
    await http.lock(w)
    async with http.api(w) as api:
        response = await api.http.patch(
            f"{http.JOBS}/{w.job}", json={"grade": "cxo", "title": "Renamed"}
        )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == jobs_api.GRADE_LOCKED_DETAIL
    stored = await _grade(w)
    assert stored["assessment_grade"] == "managerial"
    assert stored["title"] == "Senior Data Engineer", "the refused request wrote nothing"


async def test_the_same_grade_and_other_metadata_stay_editable_after_the_lock(world) -> None:
    w = await world()
    await http.lock(w)
    async with http.api(w) as api:
        response = await api.http.patch(
            f"{http.JOBS}/{w.job}", json={"grade": "managerial", "title": "Staff Data Engineer"}
        )
    assert response.status_code == 200, response.text
    stored = await _grade(w)
    assert stored == {"assessment_grade": "managerial", "title": "Staff Data Engineer"}


async def test_the_jd_and_level_are_refused_loudly_by_the_metadata_patch(world) -> None:
    """`PATCH /jobs/{id}/jd` is the ONE JD edit path; `level` is gone."""
    w = await world()
    async with http.api(w) as api:
        jd = await api.http.patch(f"{http.JOBS}/{w.job}", json={"jd_markdown": "## Role\n\nNew"})
        level = await api.http.patch(f"{http.JOBS}/{w.job}", json={"level": "Senior"})
        put = await api.http.put(f"{http.JOBS}/{w.job}/jd", json={"jd": {}})
    assert jd.status_code == 422 and level.status_code == 422
    assert put.status_code in (404, 405)
