"""Candidate portal — open application + resume reuse (PRD v1.0, FR-3.5/6.2/9.2).

One live integration test class proving the open-application model:
- ANY authenticated candidate can apply to ANY published (ratified) job, with
  no prior-contact gate (the old outreach gate is gone).
- `reuse_previous=true` carries the last resume forward without a re-upload.
- A fresh upload still mints a NEW Profile per application.
- The 40-aspect JSON is captured on the Profile.
- Bad resume files are still rejected (413/422).

The suite SKIPS cleanly when no database is reachable and runs for real inside
the backend container (same convention as test_resume_upload.py)."""
from __future__ import annotations

import io
import json
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text
from starlette.datastructures import Headers, UploadFile


def _upload(
    data: bytes = b"%PDF-1.4 minimal resume bytes",
    filename: str = "cv.pdf",
    content_type: str = "application/pdf",
) -> UploadFile:
    return UploadFile(
        file=io.BytesIO(data),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


_ASPECTS = json.dumps({str(n): f"answer {n}" for n in range(5, 40)} | {"40": True})


async def _factory_or_skip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 — no DB reachable
        await engine.dispose()
        pytest.skip("no database reachable — skipping portal integration test")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class _Fixture:
    """A candidate with NO prior contact from the tenant, plus 3 published jobs."""

    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.email = f"portal-{uuid.uuid4().hex[:8]}@candidates.pickready.test"
        self.cand_id: uuid.UUID | None = None
        self.jobs: list[uuid.UUID] = []


async def _seed(factory, fx: _Fixture) -> None:
    from app.core.db import superadmin_scope
    from app.models import Candidate, Job, JobStatus, Role, Tenant, User
    from app.models.enums import UserStatus

    now = datetime.now(timezone.utc)
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                s.add(Tenant(id=fx.tenant_id, name="OpenApply",
                             domain=f"{fx.tenant_id}.open.test"))
                s.add(User(id=fx.user_id, email=fx.email, role=Role.candidate,
                           tenant_id=None, full_name="Open Applicant",
                           status=UserStatus.active))
                await s.flush()
                cand = Candidate(id=uuid.uuid4(), email=fx.email, user_id=fx.user_id,
                                 full_name="Open Applicant", consent_databank=False)
                s.add(cand)
                fx.jobs = [uuid.uuid4() for _ in range(3)]
                for jid in fx.jobs:
                    s.add(Job(id=jid, tenant_id=fx.tenant_id, title="Role", jd_json={},
                              status=JobStatus.ratified, ratified_at=now))
                await s.flush()
                fx.cand_id = cand.id


async def _cleanup(factory, fx: _Fixture) -> None:
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(text("DELETE FROM tenants WHERE id = :t"),
                                {"t": str(fx.tenant_id)})
                if fx.cand_id:
                    await s.execute(text("DELETE FROM candidates WHERE id = :c"),
                                    {"c": str(fx.cand_id)})
                await s.execute(text("DELETE FROM users WHERE id = :u"),
                                {"u": str(fx.user_id)})


def _user(fx: _Fixture):
    from app.api.deps import CurrentUser
    from app.core.security import AUDIENCE_CANDIDATE
    from app.models import Role

    return CurrentUser(user_id=fx.user_id, tenant_id=None, role=Role.candidate,
                       audience=AUDIENCE_CANDIDATE)


async def test_open_apply_with_no_prior_contact_succeeds(monkeypatch) -> None:
    """The core PRD v1.0 reversal: no outreach needed to apply."""
    from app.api import candidates as cand_mod
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope
    from app.models import JobCandidateLink, Profile

    async def fake_store(_resume):
        return "https://res.cloudinary.com/x/raw/upload/fresh.pdf"

    monkeypatch.setattr(cand_mod, "store_resume", fake_store)
    monkeypatch.setattr(portal_mod.celery_app, "send_task", lambda *a, **k: None)

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        user = _user(fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    out = await portal_mod.apply_to_job(
                        fx.jobs[0], _ASPECTS, _upload(), False, user, s
                    )
        assert out.job_id == fx.jobs[0]
        assert out.resume_reused is False
        assert out.aspects_received == 36  # aspects 5..40

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    link = (await s.execute(
                        select(JobCandidateLink).where(JobCandidateLink.id == out.link_id)
                    )).scalar_one()
                    profile = (await s.execute(
                        select(Profile).where(Profile.id == out.profile_id)
                    )).scalar_one()
        assert link.candidate_id == fx.cand_id
        # aspects captured + completion stamped
        assert profile.aspects_json.get("40") is True
        assert profile.aspects_completed_at is not None
        assert profile.resume_url == "https://res.cloudinary.com/x/raw/upload/fresh.pdf"
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_reuse_previous_copies_last_resume(monkeypatch) -> None:
    from app.api import candidates as cand_mod
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope
    from app.models import Profile

    uploads: list[str] = []

    async def fake_store(_resume):
        url = f"https://res.cloudinary.com/x/raw/upload/{uuid.uuid4().hex}.pdf"
        uploads.append(url)
        return url

    monkeypatch.setattr(cand_mod, "store_resume", fake_store)
    monkeypatch.setattr(portal_mod.celery_app, "send_task", lambda *a, **k: None)

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        user = _user(fx)

        # First application: fresh upload → stores a resume.
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    first = await portal_mod.apply_to_job(
                        fx.jobs[0], _ASPECTS, _upload(), False, user, s
                    )
        first_url = uploads[0]

        # Second application to a DIFFERENT job: reuse the previous resume, no file.
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    second = await portal_mod.apply_to_job(
                        fx.jobs[1], _ASPECTS, None, True, user, s
                    )
        assert second.resume_reused is True
        assert len(uploads) == 1, "reuse must NOT re-upload"
        assert second.profile_id != first.profile_id

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    profile = (await s.execute(
                        select(Profile).where(Profile.id == second.profile_id)
                    )).scalar_one()
        assert profile.resume_url == first_url  # carried over onto the new Profile
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_fresh_upload_creates_a_new_profile(monkeypatch) -> None:
    from app.api import candidates as cand_mod
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope
    from app.models import Profile

    async def fake_store(_resume):
        return f"https://res.cloudinary.com/x/raw/upload/{uuid.uuid4().hex}.pdf"

    monkeypatch.setattr(cand_mod, "store_resume", fake_store)
    monkeypatch.setattr(portal_mod.celery_app, "send_task", lambda *a, **k: None)

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        user = _user(fx)
        profile_ids: list[uuid.UUID] = []
        for jid in fx.jobs[:2]:
            async with factory() as s:
                async with s.begin():
                    async with superadmin_scope(s):
                        out = await portal_mod.apply_to_job(
                            jid, _ASPECTS, _upload(), False, user, s
                        )
                        profile_ids.append(out.profile_id)
        assert profile_ids[0] != profile_ids[1]

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    rows = (await s.execute(
                        select(Profile).where(Profile.candidate_id == fx.cand_id)
                    )).scalars().all()
        assert len(rows) >= 2
        assert all(r.resume_url for r in rows)
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_reuse_without_any_previous_resume_is_422(monkeypatch) -> None:
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope

    monkeypatch.setattr(portal_mod.celery_app, "send_task", lambda *a, **k: None)

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        user = _user(fx)
        with pytest.raises(portal_mod.HTTPException) as exc:
            async with factory() as s:
                async with s.begin():
                    async with superadmin_scope(s):
                        await portal_mod.apply_to_job(
                            fx.jobs[0], _ASPECTS, None, True, user, s
                        )
        assert exc.value.status_code == 422
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_apply_rejects_bad_resume_file(monkeypatch) -> None:
    """Validation still bites on a real upload (no store_resume stub here)."""
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope

    monkeypatch.setattr(portal_mod.celery_app, "send_task", lambda *a, **k: None)

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        user = _user(fx)
        bad = _upload(data=b"hello", filename="notes.txt", content_type="text/plain")
        with pytest.raises(portal_mod.HTTPException) as exc:
            async with factory() as s:
                async with s.begin():
                    async with superadmin_scope(s):
                        await portal_mod.apply_to_job(
                            fx.jobs[0], _ASPECTS, bad, False, user, s
                        )
        assert exc.value.status_code == 422
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()
