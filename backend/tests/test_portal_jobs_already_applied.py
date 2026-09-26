"""The New Jobs board says "already applied" before anybody fills a form in.

`PortalJobOut.already_applied` and `application_id` exist so the board can link
straight to the application on Applied Jobs instead of opening an apply form
that could only answer 409. A recruiter's `sourced` entry is NOT an
application (Gate 5): reporting it as one would dead-end somebody acting on
the recruiter's own invitation, so it reads as not applied, and the apply path
converts it. The free-text `level` is gone from the payload; the grade is the
one answer to "what level is this role".

Over HTTP as a real signed-in candidate, no dependency overrides.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.candidate_session import close_candidate_session, create_candidate_session
from tests.portal_fixtures import drop_employer, engine_and_factory, scalar, seed_employer


@pytest.fixture
async def board():
    engine, factory = engine_and_factory()
    employer = await seed_employer(factory, jobs=3)
    candidate = await create_candidate_session(factory)
    applied_link = uuid.uuid4()
    sourced_link = uuid.uuid4()
    for link_id, job_id, status, source_type in (
        (applied_link, employer.job_ids[0], "applied", "applied"),
        (sourced_link, employer.job_ids[1], "sourced", "databank"),
    ):
        await scalar(
            factory,
            "INSERT INTO job_candidate_links (id, tenant_id, job_id, candidate_id, "
            "source, source_type, status, application_source) VALUES (:l, :t, :j, "
            ":c, 'fresh', :st, :s, 'direct') RETURNING id",
            l=str(link_id),
            t=str(employer.tenant_id),
            j=str(job_id),
            c=str(candidate.candidate_id),
            st=source_type,
            s=status,
        )
    try:
        yield employer, candidate, applied_link
    finally:
        await drop_employer(factory, employer)
        await close_candidate_session(factory, candidate)
        await engine.dispose()


def _get(candidate, path: str):
    with TestClient(app, cookies=candidate.cookies()) as http:
        return http.get(path, headers=candidate.headers())


async def test_the_board_marks_applications_and_only_applications(board) -> None:
    employer, candidate, applied_link = board
    response = _get(candidate, "/api/v1/portal/jobs?all_jobs=true")
    assert response.status_code == 200, response.text
    jobs = {job["id"]: job for job in response.json()["jobs"]}

    applied = jobs[str(employer.job_ids[0])]
    assert applied["already_applied"] is True
    assert applied["application_id"] == str(applied_link)

    sourced = jobs[str(employer.job_ids[1])]
    assert sourced["already_applied"] is False, (
        "a recruiter's databank entry was reported as the candidate's application"
    )
    assert sourced["application_id"] is None

    untouched = jobs[str(employer.job_ids[2])]
    assert untouched["already_applied"] is False
    assert untouched["application_id"] is None

    for job in jobs.values():
        assert "level" not in job, "the free-text level still reaches the board"


async def test_the_single_job_read_says_the_same(board) -> None:
    employer, candidate, applied_link = board
    applied = _get(candidate, f"/api/v1/portal/jobs/{employer.job_ids[0]}")
    assert applied.status_code == 200, applied.text
    assert applied.json()["already_applied"] is True
    assert applied.json()["application_id"] == str(applied_link)

    sourced = _get(candidate, f"/api/v1/portal/jobs/{employer.job_ids[1]}")
    assert sourced.status_code == 200, sourced.text
    assert sourced.json()["already_applied"] is False
