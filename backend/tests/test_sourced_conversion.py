"""A recruiter's sourced entry converts to the applicant's application (Gate 5).

THE CASE THIS PROVES. A recruiter uploads a resume: an UNLINKED candidate
record with the person's address and a `sourced` link on the job. The person
already had an account of their own (so sign-in linking had nothing to link),
or signed up with a different letter case, and applies. Before the merge rule
the application landed as a SECOND link beside the sourced one, so the
recruiter saw one person twice and the databank entry never moved.

`candidate_identity.rehome_sourced_link` re-points that ONE link onto the
applicant, and only for a Firebase-verified address: the address is the only
thing connecting the two records, and an unverified address proves nothing.
Over HTTP as a real signed-in candidate, read back on a second connection.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
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


async def _sourced_record(factory, employer, *, email: str) -> tuple[uuid.UUID, uuid.UUID]:
    """An unlinked candidate the recruiter uploaded, and its sourced link."""
    candidate_id = uuid.uuid4()
    link_id = uuid.uuid4()
    await scalar(
        factory,
        "INSERT INTO candidates (id, email, full_name, consent_databank, created_at) "
        "VALUES (:c, :e, 'Uploaded Resume', false, now() - interval '30 days') "
        "RETURNING id",
        c=str(candidate_id),
        e=email,
    )
    await scalar(
        factory,
        "INSERT INTO job_candidate_links (id, tenant_id, job_id, candidate_id, "
        "source, source_type, status, application_source) VALUES (:l, :t, :j, "
        ":c, 'databank', 'databank', 'sourced', 'direct') RETURNING id",
        l=str(link_id),
        t=str(employer.tenant_id),
        j=str(employer.job_ids[0]),
        c=str(candidate_id),
    )
    employer.extra_candidates.append(candidate_id)
    return candidate_id, link_id


def _apply(candidate, job_id):
    with TestClient(app, cookies=candidate.cookies()) as http:
        return http.post(
            f"/api/v1/portal/jobs/{job_id}/apply",
            data={"validation": VALIDATION_PAYLOAD, "application_source": "direct"},
            files={"resume": resume_file()},
            headers=candidate.headers(activity=True),
        )


@pytest.fixture
async def setting(monkeypatch):
    from app.api import portal as portal_mod

    async def fake_store(_resume):
        return fake_asset("rehome")

    monkeypatch.setattr(portal_mod, "store_resume", fake_store)
    engine, factory = engine_and_factory()
    employer = await seed_employer(factory)
    sessions = []
    try:
        yield factory, employer, sessions
    finally:
        await drop_employer(factory, employer)
        for session in sessions:
            await close_candidate_session(factory, session)
        await engine.dispose()


async def test_a_verified_applicant_takes_over_the_sourced_link(setting) -> None:
    factory, employer, sessions = setting
    applicant = await create_candidate_session(factory, email_verified=True)
    sessions.append(applicant)
    # Uploaded AFTER the applicant signed up, in a different letter case: the
    # sign-in link could not have found it, and the match is case-insensitive.
    old_record, link_id = await _sourced_record(
        factory, employer, email=applicant.email.upper()
    )

    response = _apply(applicant, employer.job_ids[0])
    assert response.status_code == 201, response.text
    assert response.json()["link_id"] == str(link_id), (
        "the application minted a second link instead of converting the "
        "recruiter's entry"
    )

    link = await row(
        factory,
        "SELECT candidate_id, status, source_type FROM job_candidate_links "
        "WHERE id = :l",
        l=str(link_id),
    )
    assert link["candidate_id"] == applicant.candidate_id
    assert link["status"] == "applied"
    # Provenance survives the conversion: the recruiter still sees where the
    # candidate first came from.
    assert link["source_type"] == "databank"
    assert await scalar(
        factory,
        "SELECT count(*) FROM job_candidate_links WHERE job_id = :j",
        j=str(employer.job_ids[0]),
    ) == 1
    assert await scalar(
        factory,
        "SELECT count(*) FROM pipeline_status WHERE job_candidate_link_id = :l "
        "AND status = 'applied'",
        l=str(link_id),
    ) == 1, "the conversion did not go through the FSM"
    audit = await row(
        factory,
        "SELECT metadata_json AS metadata FROM audit_log WHERE action = 'candidate_link_rehomed' "
        "AND target_id = :l",
        l=str(link_id),
    )
    assert audit is not None
    assert audit["metadata"]["from"] == str(old_record)
    assert audit["metadata"]["to"] == str(applicant.candidate_id)
    # The old record is history, not deleted.
    assert await scalar(
        factory, "SELECT count(*) FROM candidates WHERE id = :c", c=str(old_record)
    ) == 1


async def test_an_unverified_applicant_leaves_the_sourced_link_alone(setting) -> None:
    """The takeover guard: an address nobody proved claims nothing."""
    factory, employer, sessions = setting
    applicant = await create_candidate_session(factory, email_verified=False)
    sessions.append(applicant)
    old_record, link_id = await _sourced_record(
        factory, employer, email=applicant.email
    )

    response = _apply(applicant, employer.job_ids[0])
    assert response.status_code == 201, response.text
    assert response.json()["link_id"] != str(link_id)

    untouched = await row(
        factory,
        "SELECT candidate_id, status FROM job_candidate_links WHERE id = :l",
        l=str(link_id),
    )
    assert untouched["candidate_id"] == old_record
    assert untouched["status"] == "sourced"
    assert await scalar(
        factory,
        "SELECT count(*) FROM audit_log WHERE action = 'candidate_link_rehomed' "
        "AND target_id = :l",
        l=str(link_id),
    ) == 0


async def test_a_sourced_link_on_the_applicants_own_record_converts_in_place(
    setting,
) -> None:
    """The ordinary Gate 5 case: the entry is already on their record."""
    factory, employer, sessions = setting
    applicant = await create_candidate_session(factory, email_verified=False)
    sessions.append(applicant)
    link_id = uuid.uuid4()
    await scalar(
        factory,
        "INSERT INTO job_candidate_links (id, tenant_id, job_id, candidate_id, "
        "source, source_type, status, application_source) VALUES (:l, :t, :j, "
        ":c, 'databank', 'databank', 'sourced', 'direct') RETURNING id",
        l=str(link_id),
        t=str(employer.tenant_id),
        j=str(employer.job_ids[0]),
        c=str(applicant.candidate_id),
    )

    response = _apply(applicant, employer.job_ids[0])
    assert response.status_code == 201, response.text
    assert response.json()["link_id"] == str(link_id)
    assert await scalar(
        factory, "SELECT status FROM job_candidate_links WHERE id = :l", l=str(link_id)
    ) == "applied"

    # And a second application is now a real duplicate.
    again = _apply(applicant, employer.job_ids[0])
    assert again.status_code == 409
