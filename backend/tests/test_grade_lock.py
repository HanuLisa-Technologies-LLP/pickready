"""The JD, the title, the experience band and the grade freeze at the first
genuine application (CONTRACT v10; the grade alone locked at the first START
until 2026-09-28).

Once a `job_skill_snapshots` row exists, `PATCH /jobs/{id}/jd` refuses every
edit and `PATCH /jobs/{id}` refuses a CHANGE to a frozen field, each with the
ONE dated frozen sentence (`assessment_contract.FROZEN_DETAIL`), and nothing is
written. Asked of the snapshot TABLE, never of a timestamp. Resending a
current value is not a change, and the company narrative, the proctoring
policy and the department stay editable.

Through the real router and a session on the RLS application role, read back
from a SECOND connection.

MUTATION CHECKS, recorded: removing the `_require_not_frozen` call from
`patch_job` makes `test_a_frozen_field_change_is_a_409_and_writes_nothing`
fail with a 200 and a moved value; removing it from `save_jd_markdown` makes
`test_the_jd_document_is_frozen_too` fail the same way.
"""
from __future__ import annotations

import pytest

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


async def _job(w: fx.World) -> dict:
    return (
        await http.read(
            "SELECT assessment_grade, title, experience_min_years, experience_max_years, "
            "jd_markdown, about_company, department, proctoring_warning_policy "
            "FROM jobs WHERE id = :j",
            j=w.job,
        )
    )[0]


async def test_the_frozen_fields_move_freely_before_the_freeze(world) -> None:
    w = await world()
    async with http.api(w) as api:
        response = await api.http.patch(
            f"{http.JOBS}/{w.job}",
            json={"grade": "leadership", "title": "Lead Data Engineer",
                  "experience_min_years": 6, "experience_max_years": 10},
        )
    assert response.status_code == 200, response.text
    stored = await _job(w)
    assert (stored["assessment_grade"], stored["title"]) == ("leadership", "Lead Data Engineer")
    assert (stored["experience_min_years"], stored["experience_max_years"]) == (6, 10)


@pytest.mark.parametrize(
    "change",
    [
        {"grade": "cxo"},
        {"title": "Renamed"},
        {"experience_min_years": 6},
        {"experience_max_years": 10},
        {"grade": "cxo", "about_company": "Also sent, and not written."},
    ],
    ids=["grade", "title", "band_low", "band_high", "mixed"],
)
async def test_a_frozen_field_change_is_a_409_and_writes_nothing(world, change) -> None:
    w = await world()
    await http.lock(w)
    before = await _job(w)
    async with http.api(w) as api:
        response = await api.http.patch(f"{http.JOBS}/{w.job}", json=change)
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == await fx.frozen_sentence(w)
    assert await _job(w) == before, "the refused request wrote nothing"


async def test_the_jd_document_is_frozen_too(world) -> None:
    w = await world()
    await http.lock(w)
    before = await _job(w)
    async with http.api(w) as api:
        response = await api.http.patch(
            f"{http.JOBS}/{w.job}/jd", json={"jd_markdown": "## Role\n\n" + "New words " * 20}
        )
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == await fx.frozen_sentence(w)
    assert (await _job(w))["jd_markdown"] == before["jd_markdown"]
    assert http.recorded("pickready.draft_job_skills") == []


async def test_unfrozen_fields_and_unchanged_values_stay_editable_after_the_freeze(world) -> None:
    w = await world()
    await http.lock(w)
    async with http.api(w) as api:
        response = await api.http.patch(
            f"{http.JOBS}/{w.job}",
            json={
                "grade": "managerial",
                "title": "Senior Data Engineer",
                "experience_min_years": 5,
                "about_company": "We run payment rails for lenders across India.",
                "department": "Data",
                "proctoring_warning_policy": "terminate",
            },
        )
    assert response.status_code == 200, response.text
    stored = await _job(w)
    assert stored["about_company"] == "We run payment rails for lenders across India."
    assert stored["department"] == "Data"
    assert stored["proctoring_warning_policy"] == "terminate"
    assert (stored["assessment_grade"], stored["title"]) == ("managerial", "Senior Data Engineer")


async def test_the_jd_and_level_are_refused_loudly_by_the_metadata_patch(world) -> None:
    """`PATCH /jobs/{id}/jd` is the ONE JD edit path; `level` is gone."""
    w = await world()
    async with http.api(w) as api:
        jd = await api.http.patch(f"{http.JOBS}/{w.job}", json={"jd_markdown": "## Role\n\nNew"})
        level = await api.http.patch(f"{http.JOBS}/{w.job}", json={"level": "Senior"})
        put = await api.http.put(f"{http.JOBS}/{w.job}/jd", json={"jd": {}})
    assert jd.status_code == 422 and level.status_code == 422
    assert put.status_code in (404, 405)
