"""Applied Jobs lists applications, and a sourced entry is not one (Gate 5).

`GET /portal/applications` returned EVERY link on the candidate, including a
recruiter's `sourced` databank entry on a job they never applied to, shown
with a stage label that claimed an application. It now lists applications
only; the sourced entry appears once they apply and the apply path converts
it. Over HTTP as a real signed-in candidate, no dependency overrides.
"""
from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.main import app
from tests.candidate_session import close_candidate_session, create_candidate_session
from tests.portal_fixtures import drop_employer, engine_and_factory, scalar, seed_employer


async def test_a_sourced_link_is_not_listed_as_an_application() -> None:
    engine, factory = engine_and_factory()
    employer = await seed_employer(factory)
    candidate = await create_candidate_session(factory)
    applied_link, sourced_link = uuid.uuid4(), uuid.uuid4()
    try:
        for link_id, job_id, status in (
            (applied_link, employer.job_ids[0], "applied"),
            (sourced_link, employer.job_ids[1], "sourced"),
        ):
            await scalar(
                factory,
                "INSERT INTO job_candidate_links (id, tenant_id, job_id, "
                "candidate_id, source, source_type, status, application_source) "
                "VALUES (:l, :t, :j, :c, 'fresh', 'applied', :s, 'direct') "
                "RETURNING id",
                l=str(link_id),
                t=str(employer.tenant_id),
                j=str(job_id),
                c=str(candidate.candidate_id),
                s=status,
            )

        with TestClient(app, cookies=candidate.cookies()) as http:
            response = http.get("/api/v1/portal/applications", headers=candidate.headers())
        assert response.status_code == 200, response.text
        listed = [item["link_id"] for item in response.json()["applications"]]
        assert listed == [str(applied_link)], (
            f"Applied Jobs listed something the candidate never applied to: {listed}"
        )
    finally:
        await drop_employer(factory, employer)
        await close_candidate_session(factory, candidate)
        await engine.dispose()
