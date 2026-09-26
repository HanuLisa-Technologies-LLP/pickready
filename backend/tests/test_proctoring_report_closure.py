"""The proctoring report honours job closure (audit P1 3.22, master prompt #22).

`GET /proctoring/links/{id}/report` served the report of a CLOSED job: the
route had no `job_assessment_retention.require_readable` call, so the one
artifact of an assessment that says how the candidate behaved stayed readable
after the PRISM Report, the transcript and the recording had all gone to 410.

The route now runs the gate AFTER the tenant check and BEFORE the report is
loaded. These tests call the real handler over the real tables, with the real
grant engine and the real retention states:

- a live job: 200 with the report;
- a closed job inside its thirty days: 410, and the report is never loaded
  (a refusal that read the row first has already read what it refused);
- a closed job whose window has passed: 410;
- another tenant's link: 404 before closure is even considered.

Mutation-checked: deleting the `require_readable` call makes the closed-job
tests fail with a 200.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api import proctoring as proctoring_api
from app.api.deps import CurrentUser
from app.models.enums import Role
from app.models.proctoring import POLICY_CONTINUE_AND_NOTE
from app.services import job_assessment_retention
from app.services.proctoring import ingestion
from app.services.proctoring import report as proctoring_report

from tests.test_proctoring_pipeline import (
    CONFIG,
    MS,
    _batch,
    _cleanup,
    _factory_or_skip,
    _Fx,
    _load,
    _seed,
)


def _recruiter(fx: _Fx, *, tenant_id: uuid.UUID | None = None) -> CurrentUser:
    return CurrentUser(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id or fx.tenant_id,
        role=Role.client,
        audience="org",
    )


async def _with_report(factory, fx: _Fx) -> None:
    """A session that ended and whose report was written."""
    from app.core.db import superadmin_scope

    await _seed(factory, fx)
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                ps = await _load(s, fx)
                await ingestion.ingest(
                    s, ps, POLICY_CONTINUE_AND_NOTE,
                    _batch(("FACE_ABSENT_EXTENDED", CONFIG.face_absent_extended_seconds * MS)),
                    now=datetime.now(timezone.utc), enqueue=fx.enqueue,
                )
                await proctoring_report.generate(s, ps)


async def _close(factory, fx: _Fx, *, closed_at: datetime) -> None:
    from app.core.db import superadmin_scope
    from app.models import Job

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                job = await s.get(Job, fx.job_id)
                job.closed_at = closed_at
                job.assessment_purge_due_at = job_assessment_retention.purge_due_at(closed_at)


async def _get(factory, fx: _Fx, user: CurrentUser):
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                return await proctoring_api.get_proctoring_report(
                    fx.link_id, user=user, session=s
                )


@pytest.mark.asyncio
async def test_a_live_job_serves_the_report() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _with_report(factory, fx)
        report = await _get(factory, fx, _recruiter(fx))
        assert report.outcome
        assert report.findings.camera
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_closed_job_is_410_and_the_report_is_never_loaded(monkeypatch) -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    loaded: list[uuid.UUID] = []
    real_loader = proctoring_report.load_report_out

    async def _watching_loader(session, link_id):
        loaded.append(link_id)
        return await real_loader(session, link_id)

    try:
        await _with_report(factory, fx)
        await _close(factory, fx, closed_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        monkeypatch.setattr(proctoring_report, "load_report_out", _watching_loader)
        with pytest.raises(HTTPException) as caught:
            await _get(factory, fx, _recruiter(fx))
        assert caught.value.status_code == 410
        assert caught.value.detail == job_assessment_retention.WITHHELD_MESSAGE
        assert loaded == [], "the report was read before the closure refused it"
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_job_past_its_retention_window_is_410() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _with_report(factory, fx)
        long_ago = datetime.now(timezone.utc) - timedelta(
            days=job_assessment_retention.RETENTION_DAYS + 1
        )
        await _close(factory, fx, closed_at=long_ago)
        with pytest.raises(HTTPException) as caught:
            await _get(factory, fx, _recruiter(fx))
        assert caught.value.status_code == 410
        assert caught.value.detail == job_assessment_retention.PURGED_MESSAGE
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_another_tenants_link_is_404_whatever_the_closure() -> None:
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _with_report(factory, fx)
        await _close(factory, fx, closed_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        with pytest.raises(HTTPException) as caught:
            await _get(factory, fx, _recruiter(fx, tenant_id=uuid.uuid4()))
        assert caught.value.status_code == 404
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()
