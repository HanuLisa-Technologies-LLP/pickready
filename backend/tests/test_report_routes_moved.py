"""The PRISM Report's routes moved router without moving URL (PLAN-p5 WP5-F).

`api/assessments.py` held the report, the PDF, the transcript and the three
immutability handlers and nothing else; they now live in
`api/assessment_reports.py`, mounted under the SAME `/api/v2/assessments`
prefix. A report link already sitting in somebody's inbox is a URL, so the
move is only safe if the URL set is byte-identical, which is what this pins
against the mounted application rather than against the router module.
"""
from __future__ import annotations

import pathlib

import pytest

PREFIX = "/api/v2/assessments"

#: (method, path) for every route the report surface answers. The citations
#: read is the one NEW route; every other entry existed before the move.
EXPECTED = {
    ("GET", f"{PREFIX}/reports/links/{{link_id}}"),
    ("GET", f"{PREFIX}/reports/links/{{link_id}}/pdf"),
    ("GET", f"{PREFIX}/reports/links/{{link_id}}/citations"),
    ("PATCH", f"{PREFIX}/reports/links/{{link_id}}"),
    ("PUT", f"{PREFIX}/reports/links/{{link_id}}"),
    ("DELETE", f"{PREFIX}/reports/links/{{link_id}}"),
    ("GET", f"{PREFIX}/transcripts/links/{{link_id}}"),
}


def _mounted() -> dict[tuple[str, str], object]:
    from app.main import app

    found: dict[tuple[str, str], object] = {}
    for route in app.routes:
        path = getattr(route, "path", "")
        if not (
            path.startswith(f"{PREFIX}/reports") or path.startswith(f"{PREFIX}/transcripts")
        ):
            continue
        for method in getattr(route, "methods", None) or ():
            if method in {"HEAD", "OPTIONS"}:
                continue
            found[(method, path)] = route.endpoint
    return found


def test_the_url_set_is_exactly_the_one_links_already_carry() -> None:
    assert set(_mounted()) == EXPECTED


def test_every_report_route_is_served_by_the_one_router() -> None:
    """One owner: a second module answering one of these paths would be a
    second serializer free to state a different grade."""
    for key, endpoint in _mounted().items():
        assert endpoint.__module__ == "app.api.assessment_reports", key


def test_the_old_module_is_gone() -> None:
    backend = pathlib.Path(__file__).resolve().parents[1]
    assert not (backend / "app" / "api" / "assessments.py").exists()
    with pytest.raises(ModuleNotFoundError):
        __import__("app.api.assessments")


def test_the_three_immutability_handlers_still_answer_403() -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    client = TestClient(app)
    path = f"{PREFIX}/reports/links/00000000-0000-0000-0000-000000000000"
    for method in ("patch", "put", "delete"):
        response = getattr(client, method)(path)
        assert response.status_code == 403, (method, response.status_code)
        assert "immutable" in response.json()["detail"]
