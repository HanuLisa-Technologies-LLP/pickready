"""The retired Role Intake has no route or live matrix input."""
from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.assessments import router
from app.models.job import Job
from app.models.job_setup import JobSwotAnalysis, JobSwotIntake
from app.services.hiring import scorecard


def test_retired_intake_routes_return_404_while_document_route_remains() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v2/assessments")
    job_id = uuid.uuid4()
    base = f"/api/v2/assessments/jobs/{job_id}"
    with TestClient(app) as client:
        assert client.get(f"{base}/swot").status_code == 404
        assert client.post(f"{base}/swot/respond", json={"answer": "old"}).status_code == 404
    assert any(
        getattr(route, "path", "") == "/api/v2/assessments/jobs/{job_id}/swot-analysis"
        for route in app.routes
    )


class _Result:
    def __init__(self, row: JobSwotAnalysis | None) -> None:
        self.row = row

    def scalar_one_or_none(self) -> JobSwotAnalysis | None:
        return self.row


class _Session:
    def __init__(self, analysis: JobSwotAnalysis | None) -> None:
        self.analysis = analysis
        self.queries: list[type] = []

    async def execute(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        self.queries.append(entity)
        assert entity is not JobSwotIntake
        return _Result(self.analysis)


@pytest.mark.asyncio
async def test_matrix_reads_saved_document_without_historic_intake() -> None:
    job = Job(id=uuid.uuid4(), tenant_id=uuid.uuid4(), title="Engineer")
    document = JobSwotAnalysis(
        job_id=job.id,
        tenant_id=job.tenant_id,
        strengths="Owns production migrations.",
        weaknesses="Debugs a fragile scheduler.",
        opportunities="Can build a platform team.",
        threats="A missed deadline affects customers.",
        version=3,
    )
    session = _Session(document)

    captured, version = await scorecard._layer3(session, job)

    assert captured["strengths"] == ["Owns production migrations."]
    assert captured["weaknesses"] == ["Debugs a fragile scheduler."]
    assert version == 3
    assert session.queries == [JobSwotAnalysis]


@pytest.mark.asyncio
async def test_historic_intake_alone_does_not_seed_new_matrix() -> None:
    job = Job(id=uuid.uuid4(), tenant_id=uuid.uuid4(), title="Engineer")
    session = _Session(None)

    with pytest.raises(scorecard.ScorecardInputMissing, match="Job SWOT Analysis"):
        await scorecard._layer3(session, job)
    assert session.queries == [JobSwotAnalysis]
