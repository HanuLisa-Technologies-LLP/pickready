"""AI Matching's run and status routes, over a real database and real tenancy.

THE DEFECT THIS PINS
--------------------
`GET /matching/tasks/{task_id}` had no tenant check at all. The run-status
record lives in Redis keyed by the task id alone (`workers/status`), so a user
of ANY tenant holding `trigger_matching` could read any other tenant's run by
its id, and nothing recorded which job a task belonged to. The replacement,
`GET /matching/jobs/{job_id}/tasks/{task_id}`, resolves the job through the RLS
session AND requires the `matching_triggered` audit row the run route wrote for
that job, in that tenant, carrying that task id.

Every state assertion reads from a SECOND connection after the response,
because an answer of 202 over a transaction that rolled back is exactly the
evidence that lied in this repository before.
"""
from __future__ import annotations

from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api import matching as matching_api
from app.workers import dispatch
from tests.ranking_world import (
    Tenant,
    client_for,
    committed,
    execute,
    run,
    tenant_world,
)


@pytest.fixture
def tenant_a() -> Iterator[Tenant]:
    yield from tenant_world()


@pytest.fixture
def tenant_b() -> Iterator[Tenant]:
    yield from tenant_world()


@pytest.fixture
def client_a(tenant_a: Tenant) -> Iterator[TestClient]:
    yield from client_for(tenant_a)


def _start(client: TestClient, tenant: Tenant) -> str:
    response = client.post(f"/api/v1/matching/jobs/{tenant.job}/run")
    assert response.status_code == 202, response.text
    return response.json()["task_id"]


def test_the_run_is_audited_with_its_task_id_and_dispatched_after_commit(
    client_a, tenant_a
) -> None:
    task_id = _start(client_a, tenant_a)
    rows = committed(
        "SELECT metadata_json->>'task_id' AS task_id, target_id FROM audit_log "
        "WHERE tenant_id = :tid AND action = 'matching_triggered'",
        {"tid": str(tenant_a.id)},
    )
    assert rows == [{"task_id": task_id, "target_id": str(tenant_a.job)}]
    assert "pickready.run_matching" in dispatch.recorded_names()


def test_the_tenant_that_started_a_run_can_poll_it(client_a, tenant_a) -> None:
    task_id = _start(client_a, tenant_a)
    response = client_a.get(f"/api/v1/matching/jobs/{tenant_a.job}/tasks/{task_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["task_id"] == task_id
    assert body["degraded"] is False


def test_another_tenant_cannot_read_the_run_by_its_id(client_a, tenant_a, tenant_b) -> None:
    task_id = _start(client_a, tenant_a)
    for http in client_for(tenant_b):
        # Tenant A's job is invisible to B: 404, never 403.
        foreign_job = http.get(f"/api/v1/matching/jobs/{tenant_a.job}/tasks/{task_id}")
        assert foreign_job.status_code == 404
        # B's own job did not start that task: still 404.
        own_job = http.get(f"/api/v1/matching/jobs/{tenant_b.job}/tasks/{task_id}")
        assert own_job.status_code == 404
        assert own_job.json()["detail"] == matching_api.DETAIL_TASK_NOT_FOUND


def test_an_unknown_task_id_is_not_found(client_a, tenant_a) -> None:
    response = client_a.get(f"/api/v1/matching/jobs/{tenant_a.job}/tasks/never-started")
    assert response.status_code == 404


def test_the_unscoped_status_route_and_the_results_route_are_gone(client_a, tenant_a) -> None:
    task_id = _start(client_a, tenant_a)
    assert client_a.get(f"/api/v1/matching/tasks/{task_id}").status_code == 404
    assert client_a.get(f"/api/v1/matching/jobs/{tenant_a.job}/results").status_code == 404


def test_unsaved_skills_refuse_the_run_and_start_nothing(client_a, tenant_a) -> None:
    run(execute(
        "UPDATE jobs SET framework_approved_at = NULL WHERE id = :id",
        {"id": str(tenant_a.job)},
    ))
    response = client_a.post(f"/api/v1/matching/jobs/{tenant_a.job}/run")
    assert response.status_code == 409
    assert response.json()["detail"] == matching_api.DETAIL_SKILLS_NOT_SAVED
    assert "pickready.run_matching" not in dispatch.recorded_names()
    assert committed(
        "SELECT 1 FROM audit_log WHERE tenant_id = :tid AND action = 'matching_triggered'",
        {"tid": str(tenant_a.id)},
    ) == []


def test_a_closed_job_refuses_the_run(client_a, tenant_a) -> None:
    run(execute(
        "UPDATE jobs SET closed_at = now() WHERE id = :id", {"id": str(tenant_a.job)}
    ))
    response = client_a.post(f"/api/v1/matching/jobs/{tenant_a.job}/run")
    assert response.status_code == 409
    assert response.json()["detail"] == matching_api.DETAIL_CLOSED
    assert "pickready.run_matching" not in dispatch.recorded_names()
