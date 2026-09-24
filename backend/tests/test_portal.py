"""Candidate portal: open application + resume reuse (PRD v1.0, FR-3.5/6.2/9.2).

One live integration test class proving the open-application model:
- ANY authenticated candidate can apply to ANY published (ratified) job, with
  no prior-contact gate (the old outreach gate is gone).
- `reuse_previous=true` carries the last resume forward without a re-upload.
- A fresh upload still mints a NEW Profile per application.
- The candidate's My Profile answers are snapshotted onto that Profile.
- Bad resume files are still rejected (413/422).
- Applying creates no assessment and dispatches only after the commit.

The suite SKIPS cleanly when no database is reachable and runs for real inside
the backend container (same convention as test_resume_upload.py)."""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text
from starlette.datastructures import Headers, UploadFile
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


def _asset(url: str) -> ResumeAsset:
    return ResumeAsset(
        public_id=f"pickready/resumes/{uuid.uuid4().hex}", secure_url=url,
        original_filename="cv.pdf", mime_type="application/pdf", size_bytes=24,
        uploaded_at=datetime.now(timezone.utc), sha256=uuid.uuid4().hex * 2,
        metadata={"resource_type": "raw"},
    )


#: What the seeded candidate has on My Profile. The application snapshots it.
_PROFILE_FORM = {"current_city": "Pune", "declaration_full_name": "Open Applicant"}



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
                                 full_name="Open Applicant", consent_databank=False,
                                 profile_form_json=dict(_PROFILE_FORM))
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
        return _asset("https://res.cloudinary.com/x/raw/upload/fresh.pdf")

    monkeypatch.setattr(cand_mod, "store_resume", fake_store)
    monkeypatch.setattr(portal_mod, "store_resume", fake_store)

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        user = _user(fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    out = await portal_mod.apply_to_job(
                        fx.jobs[0], resume=_upload(), reuse_previous=False,
                        application_source="direct",
                        user=user, session=s, validation=_VALIDATION,
                    )
        assert out.job_id == fx.jobs[0]
        assert out.resume_reused is False
        # The answer names the application and nothing about an assessment:
        # applying is not being invited.
        assert set(out.model_dump()) == {"link_id", "job_id", "profile_id", "resume_reused"}

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
        # My Profile snapshotted onto the application + completion stamped
        assert profile.aspects_json == _PROFILE_FORM
        assert profile.aspects_completed_at is not None
        # A portal-board application is direct, and the person is an applicant.
        assert link.application_source == "direct"
        assert link.source_type == "applied"
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
        return _asset(url)

    monkeypatch.setattr(cand_mod, "store_resume", fake_store)
    monkeypatch.setattr(portal_mod, "store_resume", fake_store)

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
                        fx.jobs[0], resume=_upload(), reuse_previous=False,
                        application_source="direct",
                        user=user, session=s, validation=_VALIDATION,
                    )
        first_url = uploads[0]

        # Second application to a DIFFERENT job: reuse the previous resume, no file.
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    second = await portal_mod.apply_to_job(
                        fx.jobs[1], resume=None, reuse_previous=True,
                        application_source="direct",
                        user=user, session=s, validation=_VALIDATION,
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
        return _asset(f"https://res.cloudinary.com/x/raw/upload/{uuid.uuid4().hex}.pdf")

    monkeypatch.setattr(cand_mod, "store_resume", fake_store)
    monkeypatch.setattr(portal_mod, "store_resume", fake_store)

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
                            jid, resume=_upload(), reuse_previous=False,
                            application_source="direct",
                            user=user, session=s, validation=_VALIDATION,
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
                            fx.jobs[0], resume=None, reuse_previous=True,
                            application_source="direct",
                            user=user, session=s, validation=_VALIDATION,
                        )
        assert exc.value.status_code == 422
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_apply_rejects_bad_resume_file(monkeypatch) -> None:
    """Validation still bites on a real upload (no store_resume stub here)."""
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope

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
                            fx.jobs[0], resume=bad, reuse_previous=False,
                            application_source="direct",
                            user=user, session=s, validation=_VALIDATION,
                        )
        assert exc.value.status_code == 422
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_apply_context_reports_stored_resume_and_duplicate(monkeypatch) -> None:
    """The apply page asks for this BEFORE showing the form: is there a
    resume to reuse, and has this candidate already applied? (FR-6.2/9.2.)"""
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope

    async def fake_store(_resume):
        return _asset("https://res.cloudinary.test/ctx.pdf")

    monkeypatch.setattr(portal_mod, "store_resume", fake_store)

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        user = _user(fx)

        # Nothing on file yet, and not applied.
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    before = await portal_mod.apply_context(
                        fx.jobs[0], user=user, session=s
                    )
        assert before.already_applied is False
        assert before.application_id is None
        assert before.resume.has_resume is False

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    applied = await portal_mod.apply_to_job(
                        fx.jobs[0], resume=_upload(), reuse_previous=False,
                        application_source="direct",
                        user=user, session=s, validation=_VALIDATION,
                    )

        # Now: applied to job 0, and a reusable resume exists for job 1.
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    same = await portal_mod.apply_context(
                        fx.jobs[0], user=user, session=s
                    )
                    other = await portal_mod.apply_context(
                        fx.jobs[1], user=user, session=s
                    )
                    stored = await portal_mod.my_stored_resume(user=user, session=s)
        assert same.already_applied is True
        assert same.applied_at is not None
        # The public page's "View your application" link opens this card.
        assert same.application_id == applied.link_id
        assert other.already_applied is False
        assert other.application_id is None
        assert other.resume.has_resume is True
        assert other.resume.filename == "cv.pdf"
        assert stored.has_resume is True
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


# ── Job description on the portal job read (client review, page 1) ───────────

_SEEDED_JD = {
    "description": "Own the ingestion pipeline end to end.",
    "role": "Data Engineer",
    "responsibilities": ["Build ETL", "Own data quality"],
    "accountabilities": ["Pipeline uptime"],
    "reporting_to": "Head of Data",
    "education": "B.E. / B.Tech",
    "skills": ["Python", "SQL"],
}


async def test_portal_job_read_carries_the_job_description() -> None:
    """The candidate apply dialog reads the JD from the SAME response that
    lists/serves the job — no second round-trip to /jobs/public/{id}."""
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope
    from app.models import Job

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    job = await s.get(Job, fx.jobs[0])
                    job.jd_json = dict(_SEEDED_JD)
                    job.assessment_grade = "managerial"

        user = _user(fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    detail = await portal_mod.portal_job(
                        fx.jobs[0], user=user, session=s
                    )
                    listing = await portal_mod.portal_jobs(user=user, session=s)

        assert detail.jd_json == _SEEDED_JD
        # The `jd` mirror is computed from the canonical column, so the two can
        # never drift apart.
        assert detail.jd == detail.jd_json
        assert detail.grade == "managerial"

        listed = {j.id: j for j in listing.jobs}
        assert listed[fx.jobs[0]].jd == _SEEDED_JD
        assert listed[fx.jobs[0]].jd == listed[fx.jobs[0]].jd_json
        assert listed[fx.jobs[0]].grade == "managerial"
        # A job with no stored grade still reads as a concrete grade, never null.
        assert listed[fx.jobs[1]].grade == "non_managerial"

        # No internal ATS field leaks into the candidate-facing payload.
        emitted = detail.model_dump()
        for forbidden in (
            "compensation", "compensation_json", "created_by", "match_score",
            "approval_levels_config", "requirement_period",
        ):
            assert forbidden not in emitted
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_applying_to_a_ready_job_creates_no_assessment(monkeypatch) -> None:
    """Applying is not being invited, even to a job that is ready to assess.

    This used to create an `assessment_conversations` row and dispatch
    `generate_candidate_questions` the moment somebody applied to a job whose
    setup was approved. That row IS the invitation, so every applicant held a
    half-made invitation the assessment page then refused (403, no
    `invitation_sent_at`), and the matrix lock counted it as an issued
    contract before a recruiter had invited anybody. The invitation is the
    recruiter's act (`select-candidates`) and nothing else's.

    Read back on a SECOND connection after the commit, and the dispatches are
    read from the record the commit hook wrote, so a dispatch that fired
    before the commit, or a row that rolled back, cannot pass this.
    """
    from sqlalchemy import update as sa_update

    from app.api import candidates as cand_mod
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope
    from app.models import Job
    from app.workers import dispatch as dispatch_mod

    async def fake_store(_resume):
        return _asset("https://res.cloudinary.com/x/raw/upload/ready.pdf")

    monkeypatch.setattr(cand_mod, "store_resume", fake_store)
    monkeypatch.setattr(portal_mod, "store_resume", fake_store)

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await s.execute(
                        sa_update(Job)
                        .where(Job.id == fx.jobs[0])
                        .values(assessment_status="ready_for_candidates")
                    )
        user = _user(fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    out = await portal_mod.apply_to_job(
                        fx.jobs[0], resume=_upload(), reuse_previous=False,
                        application_source="direct",
                        user=user, session=s, validation=_VALIDATION,
                    )
                    # Nothing leaves the process while the transaction is open.
                    assert dispatch_mod.recorded_names() == []

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    conversations = (await s.execute(
                        text("SELECT count(*) FROM assessment_conversations "
                             "WHERE job_candidate_link_id = :l"),
                        {"l": str(out.link_id)},
                    )).scalar_one()
                    questions = (await s.execute(
                        text("SELECT count(*) FROM candidate_questions "
                             "WHERE job_candidate_link_id = :l"),
                        {"l": str(out.link_id)},
                    )).scalar_one()
        assert conversations == 0, "applying created an assessment invitation"
        assert questions == 0, "applying issued assessment questions"
        names = dispatch_mod.recorded_names()
        assert "pickready.generate_candidate_questions" not in names
        assert names == [
            "pickready.parse_resume",
            "pickready.send_application_confirmation",
        ]
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_a_refused_application_dispatches_nothing(monkeypatch) -> None:
    """A refused application rolls back, and nothing reaches the dispatcher:
    every dispatch on the apply path is registered for AFTER the commit, so a
    request that never commits sends nothing about a row that was never
    stored."""
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope
    from app.workers import dispatch as dispatch_mod

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
                            fx.jobs[0], resume=None, reuse_previous=True,
                            application_source="direct",
                            user=user, session=s, validation=_VALIDATION,
                        )
        assert exc.value.status_code == 422
        assert dispatch_mod.recorded_names() == []
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()
