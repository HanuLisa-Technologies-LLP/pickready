"""The one apply path, over HTTP, as a signed-in candidate (WP6-B).

`POST /portal/jobs/{id}/apply` is used by the portal's New Jobs board AND the
public `/apply/{job}` page. It takes a resume, the six mandatory validation
fields and where the applicant clicked, and nothing else. Everything the old
forms also posted (age, gender, a 40-answer aspects blob, a name and city) is
ignored by the route, and this suite proves "ignored" by reading the database
back rather than by trusting a 201.

REAL SESSION, NO OVERRIDES, SECOND CONNECTION. The candidate is signed in
through `tests/candidate_session.py`, so the request passes the same
dependencies a browser's does; every state assertion is read on a fresh
connection after the response, because a write that answered 201 and rolled
back at commit is invisible to an assertion on the body.
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.workers import dispatch as dispatch_mod
from tests.application_fixtures import VALIDATION_PAYLOAD
from tests.candidate_session import close_candidate_session, create_candidate_session
from tests.portal_fixtures import (
    drop_employer,
    engine_and_factory,
    fake_asset,
    resume_file,
    row,
    scalar,
    seed_employer,
)


@pytest.fixture
async def world(monkeypatch):
    """A signed-in candidate who already consented to the databank on My
    Profile, and an employer with two published jobs."""
    from app.api import portal as portal_mod

    async def fake_store(_resume):
        return fake_asset("unified")

    monkeypatch.setattr(portal_mod, "store_resume", fake_store)
    engine, factory = engine_and_factory()
    employer = await seed_employer(factory)
    candidate = await create_candidate_session(factory, full_name="Unified Applicant")
    await scalar(
        factory,
        "UPDATE candidates SET consent_databank = true, city = 'Pune', "
        "profile_form_json = CAST(:form AS jsonb) WHERE id = :c RETURNING id",
        c=str(candidate.candidate_id),
        form=json.dumps({"current_city": "Pune", "declaration_accepted": True}),
    )
    try:
        yield factory, employer, candidate
    finally:
        await drop_employer(factory, employer)
        await close_candidate_session(factory, candidate)
        await engine.dispose()


def _apply(candidate, job_id, **data):
    form = {"validation": VALIDATION_PAYLOAD, "reuse_previous": "false", **data}
    with TestClient(app, cookies=candidate.cookies()) as http:
        return http.post(
            f"/api/v1/portal/jobs/{job_id}/apply",
            data=form,
            files={"resume": resume_file()},
            headers=candidate.headers(activity=True),
        )


async def test_the_retired_fields_are_ignored_and_nothing_is_rewritten(world) -> None:
    """Age, gender, name, city and the aspects blob reach nothing, and the
    databank consent given on My Profile survives an application whose old
    aspects payload said "no" (the silent revocation this route used to do)."""
    factory, employer, candidate = world
    response = _apply(
        candidate,
        employer.job_ids[0],
        age="31",
        gender="female",
        full_name="Somebody Else",
        residing_city="Chennai",
        aspects=json.dumps({"40": False, "declaration_accepted": False}),
        application_source="direct",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {"link_id", "job_id", "profile_id", "resume_reused"}

    person = await row(
        factory,
        "SELECT age, gender, full_name, city, consent_databank FROM candidates "
        "WHERE id = :c",
        c=str(candidate.candidate_id),
    )
    assert person["age"] is None and person["gender"] is None
    assert person["full_name"] == "Unified Applicant"
    assert person["city"] == "Pune"
    assert person["consent_databank"] is True, (
        "applying rewrote the databank consent given on My Profile"
    )
    snapshot = await scalar(
        factory,
        "SELECT aspects_json FROM profiles WHERE id = :p",
        p=body["profile_id"],
    )
    assert snapshot == {"current_city": "Pune", "declaration_accepted": True}, (
        "the application must snapshot My Profile and nothing the request posted"
    )


async def test_applying_issues_no_assessment_and_dispatches_after_commit(world) -> None:
    """Applying is not being invited, even to a job that is ready to assess.

    No `assessment_conversations` row (it IS the invitation), no questions,
    no `generate_candidate_questions`, and the two dispatches that do happen
    are the parse and the confirmation, recorded by the commit hook.
    """
    factory, employer, candidate = world
    await scalar(
        factory,
        "UPDATE jobs SET assessment_status = 'ready_for_candidates' WHERE id = :j "
        "RETURNING id",
        j=str(employer.job_ids[0]),
    )
    dispatch_mod.clear_recorded()
    response = _apply(candidate, employer.job_ids[0], application_source="direct")
    assert response.status_code == 201, response.text
    link_id = response.json()["link_id"]

    assert await scalar(
        factory,
        "SELECT count(*) FROM assessment_conversations WHERE job_candidate_link_id = :l",
        l=link_id,
    ) == 0
    assert await scalar(
        factory,
        "SELECT count(*) FROM candidate_questions WHERE job_candidate_link_id = :l",
        l=link_id,
    ) == 0
    names = dispatch_mod.recorded_names()
    assert "pickready.generate_candidate_questions" not in names
    assert sorted(names) == [
        "pickready.parse_resume",
        "pickready.send_application_confirmation",
    ]
    link = await row(
        factory,
        "SELECT status, source_type, application_source FROM job_candidate_links "
        "WHERE id = :l",
        l=link_id,
    )
    assert link["status"] == "applied"
    assert link["source_type"] == "applied"
    assert link["application_source"] == "direct"


async def test_a_refused_application_dispatches_nothing(world) -> None:
    factory, employer, candidate = world
    dispatch_mod.clear_recorded()
    incomplete = json.dumps({"current_ctc": "10,00,000"})
    response = _apply(candidate, employer.job_ids[0], validation=incomplete)
    assert response.status_code == 422
    assert dispatch_mod.recorded_names() == []
    assert await scalar(
        factory,
        "SELECT count(*) FROM job_candidate_links WHERE candidate_id = :c",
        c=str(candidate.candidate_id),
    ) == 0


async def test_an_unknown_source_is_recorded_as_direct(world) -> None:
    """A crafted value never reaches the column the CHECK guards."""
    factory, employer, candidate = world
    response = _apply(
        candidate, employer.job_ids[0], application_source="sourced"
    )
    assert response.status_code == 201, response.text
    link = await row(
        factory,
        "SELECT source_type, application_source FROM job_candidate_links "
        "WHERE id = :l",
        l=response.json()["link_id"],
    )
    # "sourced" is a pipeline STAGE (a recruiter's upload), never something an
    # applicant can claim about themselves.
    assert link["application_source"] == "direct"
    assert link["source_type"] == "applied"


async def test_a_public_link_applicant_is_an_applicant(world) -> None:
    """`external_link` is where they clicked; `applied` is what they are.

    This is the half of the fix that needs the CHECK constraint swap in the
    phase 6 migration (`ck_jcl_application_source` gains `external_link`).
    It used to be recorded as `application_source = sourced`, which the
    model's listener turned into `source_type = sourced`: a person who read
    the job and applied, labelled as a recruiter's upload who never had.
    """
    factory, employer, candidate = world
    response = _apply(
        candidate, employer.job_ids[1], application_source="external_link"
    )
    assert response.status_code == 201, response.text
    link = await row(
        factory,
        "SELECT source_type, application_source FROM job_candidate_links "
        "WHERE id = :l",
        l=response.json()["link_id"],
    )
    assert link["application_source"] == "external_link"
    assert link["source_type"] == "applied"
