"""The retired Role Intake has no route and feeds no skills draft."""
from __future__ import annotations

import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.assessment_reports import router
from app.api.job_setup import router as job_setup_router


def test_retired_intake_routes_return_404_while_document_route_remains() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v2/assessments")
    # The document routes moved to `api/job_setup` under the same prefix.
    app.include_router(job_setup_router, prefix="/api/v2/assessments")
    job_id = uuid.uuid4()
    base = f"/api/v2/assessments/jobs/{job_id}"
    with TestClient(app) as client:
        assert client.get(f"{base}/swot").status_code == 404
        assert client.post(f"{base}/swot/respond", json={"answer": "old"}).status_code == 404
    # The published paths rather than `app.routes`: from FastAPI 0.141 an
    # included router is one `_IncludedRouter` entry carrying `path = None`,
    # so `getattr(route, "path", "")` quietly matched nothing and this
    # assertion failed while the route was registered and working.
    assert (
        "/api/v2/assessments/jobs/{job_id}/swot-analysis"
        in app.openapi()["paths"]
    )


def test_the_skills_draft_reads_the_saved_document_never_the_historic_intake() -> None:
    """Sutra drafts from the Job SWOT DOCUMENT the team saved (Vivekium release).

    `job_swot_intakes` survives as a transcript and nothing on the live path
    reads it. The draft reaches the SWOT only through `swot_analysis.get`, and
    neither Sutra module nor the skills service names the intake model at all.
    """
    import inspect

    from app.services import skills
    from app.services.hiring import sutra

    for module in (skills, sutra):
        source = inspect.getsource(module)
        assert "JobSwotIntake" not in source, module.__name__
        assert "job_swot_intakes" not in source, module.__name__
    assert "swot_analysis.get(" in inspect.getsource(skills.draft)
