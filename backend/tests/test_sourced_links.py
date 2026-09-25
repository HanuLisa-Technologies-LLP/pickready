"""A resume the recruitment team puts on a job enters at `sourced`, with its
history row, through the real routes (audit Part 1 #5, PLAN-p2 test 9).

The single recruiter upload recorded the person as `applied` with no history;
the databank bulk upload set `sourced` by hand and wrote no `pipeline_status`
row. Both now go through `hiring_pipeline.start_sourced`, and both dispatch the
parse only after the request commits (`dispatch_after_commit`). Every
stored-state assertion reads from a SECOND connection after the response.

Mutation checks recorded in the Phase 2 WP-B report: replacing
`start_sourced` with a bare `session.add` in the single upload fails
`test_the_single_upload_enters_at_sourced_with_one_history_row`; in the bulk
upload, `test_the_databank_upload_enters_at_sourced_with_one_history_row`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from app.core.db import superadmin_scope
from app.services import hiring_pipeline, resume_storage
from app.workers import dispatch
from tests import job_setup_api_fixtures as fxapi
from tests import skills_fixtures as fx

PDF = b"%PDF-1.4\n% a resume\n"


def _stored(data, sha, filename, mime):
    return {
        "object_name": f"resumes/{sha}",
        "size": len(data),
        "etag": "d41d8cd98f00b204e9800998ecf8427e",
        "created_at": datetime.now(timezone.utc),
    }


@pytest.fixture
async def world(monkeypatch):
    monkeypatch.setattr(resume_storage, "_upload_or_get_existing", _stored)
    monkeypatch.setattr(
        resume_storage, "_object_uri", lambda name: f"s3://test-private/{name}"
    )
    w = await fx.seed(saved=True)
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "UPDATE jobs SET ratified_at = now(), status = 'ratified', "
                        "lifecycle_state = 'PUBLISHED' WHERE id = :j"
                    ),
                    {"j": w.job},
                )
    try:
        yield w
    finally:
        async with fx.sessions()() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        text(
                            "DELETE FROM candidates WHERE id IN (SELECT candidate_id "
                            "FROM job_candidate_links WHERE job_id = :j)"
                        ),
                        {"j": w.job},
                    )
        await fx.drop(w)


async def _links(job_id: uuid.UUID) -> list[dict]:
    async with fx.sessions()() as session:
        async with superadmin_scope(session):
            links = (
                await session.execute(
                    text(
                        "SELECT id, status, source_type, current_stage, profile_id "
                        "FROM job_candidate_links WHERE job_id = :j"
                    ),
                    {"j": job_id},
                )
            ).mappings().all()
            out = []
            for link in links:
                history = (
                    await session.execute(
                        text(
                            "SELECT status, set_by, remarks FROM pipeline_status "
                            "WHERE job_candidate_link_id = :l"
                        ),
                        {"l": link["id"]},
                    )
                ).all()
                out.append({**dict(link), "history": [tuple(h) for h in history]})
    return out


async def test_the_single_upload_enters_at_sourced_with_one_history_row(world) -> None:
    async with fxapi.api(world) as api:
        response = await api.http.post(
            f"/api/v1/candidates/jobs/{world.job}/upload-resume",
            data={"email": f"{uuid.uuid4().hex[:10]}@found.test", "full_name": "Found Person"},
            files={"file": ("cv.pdf", PDF, "application/pdf")},
        )
        names = dispatch.recorded_names()
    assert response.status_code == 201, response.text
    [link] = await _links(world.job)
    assert (link["status"], link["source_type"]) == ("sourced", "sourced")
    assert link["current_stage"] == hiring_pipeline.STAGE_LABELS[hiring_pipeline.SOURCED]
    assert len(link["history"]) == 1
    status, set_by, _remarks = link["history"][0]
    assert (status, set_by) == ("sourced", world.client)
    assert names == ["pickready.parse_resume"], "dispatched after the commit, and only the parse"


async def test_the_databank_upload_enters_at_sourced_with_one_history_row(world) -> None:
    async with fxapi.api(world) as api:
        response = await api.http.post(
            f"/api/v1/jobs/{world.job}/candidates/databank",
            files=[
                ("files", ("one.pdf", PDF + b"one", "application/pdf")),
                ("files", ("two.pdf", PDF + b"two", "application/pdf")),
            ],
        )
        names = dispatch.recorded_names()
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["created"] == 2, body
    links = await _links(world.job)
    assert len(links) == 2
    for link in links:
        assert (link["status"], link["source_type"]) == ("sourced", "databank")
        assert [(h[0], h[1]) for h in link["history"]] == [("sourced", world.client)]
    assert names == ["pickready.parse_resume", "pickready.parse_resume"], (
        "one parse per file, after the commit, and no job-wide AI Matching run"
    )


async def test_one_bad_file_costs_that_file_and_not_the_batch(world, monkeypatch) -> None:
    from app.api import jobs as jobs_api

    real = jobs_api._store_one_databank_resume
    calls = {"n": 0}

    async def flaky(session, user, job, upload):
        calls["n"] += 1
        result = await real(session, user, job, upload)
        if calls["n"] == 1:
            raise RuntimeError("the first file broke after writing its rows")
        return result

    monkeypatch.setattr(jobs_api, "_store_one_databank_resume", flaky)
    async with fxapi.api(world) as api:
        response = await api.http.post(
            f"/api/v1/jobs/{world.job}/candidates/databank",
            files=[
                ("files", ("one.pdf", PDF + b"one", "application/pdf")),
                ("files", ("two.pdf", PDF + b"two", "application/pdf")),
            ],
        )
    assert response.status_code == 201, response.text
    assert response.json()["created"] == 1
    links = await _links(world.job)
    assert len(links) == 1, "the broken file's rows rolled back with its savepoint"


def test_a_sourced_link_has_one_way_out_and_it_is_applying() -> None:
    assert hiring_pipeline.allowed_transitions(hiring_pipeline.SOURCED) >= {
        hiring_pipeline.APPLIED
    }
    assert not hiring_pipeline.can_transition(
        hiring_pipeline.SOURCED, hiring_pipeline.ASSESSMENT_INVITED
    )
    assert not hiring_pipeline.can_transition(
        hiring_pipeline.SOURCED, hiring_pipeline.SHORTLISTED
    )


async def test_start_sourced_refuses_a_link_that_already_exists(world) -> None:
    from app.models import JobCandidateLink

    async with fx.sessions()() as session:
        await session.begin()
        async with superadmin_scope(session):
            cand = uuid.uuid4()
            await session.execute(
                text("INSERT INTO candidates (id, full_name, email) VALUES (:c, 'X', :e)"),
                {"c": cand, "e": f"{cand}@x.test"},
            )
            link = JobCandidateLink(
                tenant_id=world.tenant, job_id=world.job, candidate_id=cand, source="fresh"
            )
            session.add(link)
            await session.flush()
            with pytest.raises(ValueError, match="already exists"):
                await hiring_pipeline.start_sourced(session, link)
        await session.rollback()
