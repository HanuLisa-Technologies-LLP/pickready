"""Resume storage + candidate self-upload guarantees.

Two layers, matching the repo's convention (see test_seed / test_rls):
- DB-free unit tests on `store_resume` / `read_validated_resume` with GCS
  fully mocked (no real network) — always run.
- One live integration test proving a candidate `apply` creates a FRESH Profile
  every time (claude.md rule 6). It SKIPS cleanly when no database is reachable
  and runs for real inside the backend container.
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone

import pytest
from starlette.datastructures import Headers, UploadFile

from app.api import candidates as cand_mod
from app.services import resume_storage
from app.services.resume_storage import ResumeAsset
from tests.application_fixtures import VALIDATION_PAYLOAD as _VALIDATION


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


class _FakeSettings:
    def __init__(self, gcs_bucket: str) -> None:
        self.gcs_bucket = gcs_bucket


def _asset() -> ResumeAsset:
    return ResumeAsset(
        public_id="resumes/test",
        secure_url="gs://test-private/resumes/test",
        original_filename="cv.pdf",
        mime_type="application/pdf",
        size_bytes=24,
        uploaded_at=datetime.now(timezone.utc),
        sha256="a" * 64,
        metadata={"resource_type": "raw"},
    )


# ── store_resume: URL on success, None when unconfigured ─────────────────────

async def test_store_resume_returns_metadata_on_success(monkeypatch) -> None:
    monkeypatch.setattr(resume_storage, "get_settings",
                        lambda: _FakeSettings("test-private"))
    monkeypatch.setattr(
        resume_storage,
        "_upload_or_get_existing",
        lambda data, sha, filename, mime: {
            "object_name": f"resumes/{sha}",
            "size": len(data),
            "generation": "1",
            "created_at": datetime.now(timezone.utc),
        },
    )

    asset = await cand_mod.store_resume(_upload())
    assert asset.secure_url.startswith("gs://test-private/resumes/")
    assert asset.original_filename == "cv.pdf"


async def test_store_resume_rejects_unconfigured_storage(monkeypatch) -> None:
    monkeypatch.setattr(resume_storage, "get_settings", lambda: _FakeSettings(""))
    with pytest.raises(cand_mod.HTTPException) as exc:
        await cand_mod.store_resume(_upload())
    assert exc.value.status_code == 503


async def test_store_resume_reports_gcs_failure(monkeypatch) -> None:
    monkeypatch.setattr(resume_storage, "get_settings",
                        lambda: _FakeSettings("test-private"))

    def boom(*args, **kwargs):
        raise cand_mod.HTTPException(status_code=503, detail="gcs down")

    monkeypatch.setattr(resume_storage, "_upload_or_get_existing", boom)
    # Storage failure must not propagate — the upload flow degrades to no URL.
    with pytest.raises(cand_mod.HTTPException) as exc:
        await cand_mod.store_resume(_upload())
    assert exc.value.status_code == 503


# ── Validation: type / size / empty ──────────────────────────────────────────

async def test_upload_rejects_oversized_file() -> None:
    big = b"x" * (cand_mod.MAX_RESUME_BYTES + 1)
    with pytest.raises(cand_mod.HTTPException) as exc:
        await cand_mod.read_validated_resume(_upload(data=big, filename="huge.pdf"))
    assert exc.value.status_code == 413


async def test_upload_rejects_wrong_type() -> None:
    with pytest.raises(cand_mod.HTTPException) as exc:
        await cand_mod.read_validated_resume(
            _upload(data=b"hello", filename="notes.txt", content_type="text/plain")
        )
    assert exc.value.status_code == 422


async def test_upload_rejects_empty_file() -> None:
    with pytest.raises(cand_mod.HTTPException) as exc:
        await cand_mod.read_validated_resume(_upload(data=b"", filename="cv.pdf"))
    assert exc.value.status_code == 422


async def test_upload_accepts_docx_by_extension() -> None:
    # Some browsers send application/octet-stream for .docx — extension wins.
    data, filename, mime_type = await cand_mod.read_validated_resume(
        _upload(data=b"PK\x03\x04 docx", filename="resume.docx",
                content_type="application/octet-stream")
    )
    assert data == b"PK\x03\x04 docx"
    assert filename == "resume.docx"
    assert mime_type.endswith("wordprocessingml.document")


# ── seed identity derivation (DB-free) ───────────────────────────────────────

def test_seed_identity_is_deterministic_and_non_deliverable() -> None:
    from app.scripts.seed_resumes import derive_identity

    ident = derive_identity("Resume_07_Vikramaditya_Verma.docx")
    assert ident["full_name"] == "Vikramaditya Verma"
    assert ident["email"] == "vikramaditya.verma07@candidates.pickready.test"
    assert "example.com" not in ident["email"]
    assert ident["public_id"] == "resume_07_vikramaditya_verma"
    # Stable across calls.
    assert derive_identity("Resume_07_Vikramaditya_Verma.docx") == ident


# ── Live: apply creates a NEW profile every time (rule 6) ────────────────────

async def _factory_or_skip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 — no DB reachable
        await engine.dispose()
        pytest.skip("no database reachable — skipping apply integration test")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def test_apply_creates_a_fresh_profile_each_time(monkeypatch) -> None:
    from datetime import datetime, timezone

    from sqlalchemy import select

    from app.api import portal as portal_mod
    from app.api.deps import CurrentUser
    from app.core.db import superadmin_scope
    from app.core.security import AUDIENCE_CANDIDATE
    from app.models import (
        Candidate, Job, JobCandidateLink, JobStatus, Profile, Role, Tenant, User,
    )
    from app.models.enums import LinkSource, UserStatus

    engine, factory = await _factory_or_skip()

    # Never touch Cloudinary or Celery from the test.
    async def fake_store(_resume):
        return _asset()

    monkeypatch.setattr(cand_mod, "store_resume", fake_store)
    monkeypatch.setattr(portal_mod, "store_resume", fake_store)
    monkeypatch.setattr(portal_mod.celery_app, "send_task", lambda *a, **k: None)

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    email = f"apply-{uuid.uuid4().hex[:8]}@candidates.pickready.test"
    now = datetime.now(timezone.utc)
    try:
        # ── Fixture: a candidate the tenant has already contacted + 3 jobs ──
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    s.add(Tenant(id=tenant_id, name="ApplyTest", domain=f"{tenant_id}.apply.test"))
                    s.add(User(id=user_id, email=email, role=Role.candidate,
                               tenant_id=None, full_name="Apply Tester",
                               status=UserStatus.active))
                    await s.flush()
                    s.add(Candidate(id=uuid.uuid4(), email=email, user_id=user_id,
                                    full_name="Apply Tester", consent_databank=True))
                    jobs = [uuid.uuid4() for _ in range(3)]
                    for jid in jobs:
                        s.add(Job(id=jid, tenant_id=tenant_id, title="Role", jd_json={},
                                  status=JobStatus.ratified, ratified_at=now))
                    await s.flush()
                    cand = (await s.execute(
                        select(Candidate).where(Candidate.email == email)
                    )).scalar_one()
                    # Pre-existing link => tenant has "contacted" this candidate.
                    s.add(JobCandidateLink(tenant_id=tenant_id, job_id=jobs[0],
                                           candidate_id=cand.id, source=LinkSource.fresh))
        cand_id = cand.id
        user = CurrentUser(user_id=user_id, tenant_id=None, role=Role.candidate,
                           audience=AUDIENCE_CANDIDATE)

        # ── Apply to two DIFFERENT jobs; each must mint a new Profile ──
        import json as _json

        aspects = _json.dumps({str(n): f"a{n}" for n in range(5, 41)})
        profile_ids: list[uuid.UUID] = []
        for jid in jobs[1:]:
            async with factory() as s:
                async with s.begin():
                    async with superadmin_scope(s):
                        # New signature: (job_id, aspects, resume, reuse_previous, user, session)
                        out = await portal_mod.apply_to_job(
                            jid, aspects, _upload(), False,
                            user=user, session=s, validation=_VALIDATION,
                        )
                        link = (await s.execute(
                            select(JobCandidateLink).where(
                                JobCandidateLink.id == out.link_id)
                        )).scalar_one()
                        profile_ids.append(link.profile_id)

        assert len(profile_ids) == 2
        assert profile_ids[0] != profile_ids[1], "each application must create a NEW profile"

        # And both are real, distinct Profile rows for this candidate.
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    rows = (await s.execute(
                        select(Profile).where(Profile.candidate_id == cand_id)
                    )).scalars().all()
        assert {str(p) for p in profile_ids} <= {str(r.id) for r in rows}
        assert len(rows) >= 2
        # Fresh resume stored on each (never reused/blank).
        assert all(r.resume_url for r in rows)
    finally:
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    from sqlalchemy import text
                    await s.execute(text("DELETE FROM tenants WHERE id = :t"),
                                    {"t": str(tenant_id)})
                    await s.execute(text("DELETE FROM candidates WHERE id = :c"),
                                    {"c": str(cand_id)})
                    await s.execute(text("DELETE FROM users WHERE id = :u"),
                                    {"u": str(user_id)})
        await engine.dispose()
