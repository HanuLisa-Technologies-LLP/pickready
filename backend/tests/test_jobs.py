"""Jobs endpoint tests for the flat staff model (PRD v1.0 §4).

Covers the direct-publish create flow, the public application-link builder,
the public (unauthenticated) published-job read, and the AI JD-generation
endpoint (including its defensive 503 when the service isn't wired up). The DB
and Celery boundaries are stubbed — these tests never touch Postgres or a
broker.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.api import jobs as jobs_api
from app.api.deps import CurrentUser
from app.models.enums import JobStatus, Role
from app.models.job import Job
from app.schemas.jobs import JDGenerateIn, JobCreateIn, JDIn


def _user(role: Role = Role.recruiter) -> CurrentUser:
    return CurrentUser(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), role=role, audience="org"
    )


class _FakeSession:
    """Minimal async session: assigns ids on add/flush, records nothing else."""

    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)
        self._stamp(obj)

    async def flush(self) -> None:
        for obj in self.added:
            self._stamp(obj)

    @staticmethod
    def _stamp(obj) -> None:
        # Mimic what the DB fills in: PK default + server_default(created_at).
        if getattr(obj, "id", None) is None:
            obj.id = uuid.uuid4()
        if hasattr(obj, "created_at") and getattr(obj, "created_at", None) is None:
            obj.created_at = datetime.now(timezone.utc)


# ── public application-link builder ──────────────────────────────────────────

def test_public_job_url_uses_frontend_base(monkeypatch) -> None:
    monkeypatch.setattr(
        jobs_api, "get_settings",
        lambda: SimpleNamespace(frontend_url="https://picready.com"),
    )
    jid = uuid.uuid4()
    assert jobs_api.public_job_url(jid) == f"https://picready.com/apply/{jid}"


def test_public_job_url_strips_trailing_slash(monkeypatch) -> None:
    monkeypatch.setattr(
        jobs_api, "get_settings",
        lambda: SimpleNamespace(frontend_url="http://localhost:3000/"),
    )
    jid = uuid.uuid4()
    assert jobs_api.public_job_url(jid) == f"http://localhost:3000/apply/{jid}"


def test_with_public_url_only_when_published(monkeypatch) -> None:
    monkeypatch.setattr(
        jobs_api, "get_settings",
        lambda: SimpleNamespace(frontend_url="https://picready.com"),
    )
    draft = Job(id=uuid.uuid4(), tenant_id=uuid.uuid4(), title="X",
                jd_json={}, status=JobStatus.draft, ratified_at=None,
                created_by=uuid.uuid4(), created_at=datetime.now(timezone.utc))
    published = Job(id=uuid.uuid4(), tenant_id=uuid.uuid4(), title="Y",
                    jd_json={}, status=JobStatus.ratified,
                    ratified_at=datetime.now(timezone.utc),
                    created_by=uuid.uuid4(), created_at=datetime.now(timezone.utc))
    assert jobs_api._with_public_url(draft).public_url is None
    assert jobs_api._with_public_url(published).public_url is not None


# ── create → published directly ──────────────────────────────────────────────

def _stub_create_deps(monkeypatch) -> dict:
    calls: dict = {}

    async def _fake_audit(session, **kwargs):
        calls["audit"] = kwargs

    async def _fake_publish(session, job):
        job.status = JobStatus.ratified
        job.ratified_at = datetime.now(timezone.utc)

    monkeypatch.setattr(jobs_api, "audit", _fake_audit)
    monkeypatch.setattr(jobs_api.fsm, "apply_direct_publish", _fake_publish)
    monkeypatch.setattr(
        jobs_api.celery_app, "send_task",
        lambda *a, **k: calls.setdefault("matching", (a, k)),
    )
    monkeypatch.setattr(
        jobs_api, "get_settings",
        lambda: SimpleNamespace(frontend_url="https://picready.com"),
    )
    return calls


def _job_create_body() -> JobCreateIn:
    return JobCreateIn(title="Backend Engineer", jd=JDIn(role="Own APIs", skills=["Python"]))


@pytest.mark.asyncio
async def test_create_job_publishes_immediately(monkeypatch) -> None:
    calls = _stub_create_deps(monkeypatch)
    session = _FakeSession()
    out = await jobs_api.create_job(_job_create_body(), user=_user(), session=session)

    assert out.status == JobStatus.ratified
    assert out.ratified_at is not None
    assert out.public_url == f"https://picready.com/apply/{out.id}"
    # Matching is enqueued on publish (FR-4.2), and the publish was audited.
    assert "matching" in calls
    assert calls["audit"]["metadata"]["published"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [Role.hr_manager, Role.recruiter, Role.hiring_manager])
async def test_all_three_staff_roles_can_create(monkeypatch, role) -> None:
    # require_capability is the gate (checked elsewhere); the handler itself is
    # role-agnostic — every staff role drives the identical flat path.
    _stub_create_deps(monkeypatch)
    session = _FakeSession()
    out = await jobs_api.create_job(_job_create_body(), user=_user(role), session=session)
    assert out.status == JobStatus.ratified
    assert out.public_url is not None


# ── public (unauthenticated) published-job read ──────────────────────────────

class _PublicSession:
    def __init__(self, job, company_name="Acme Corp") -> None:
        self._job = job
        self._company = company_name

    async def get(self, model, ident):
        return self._job

    async def execute(self, *a, **k):
        return SimpleNamespace(scalar_one_or_none=lambda: self._company)


@pytest.mark.asyncio
async def test_public_read_returns_only_published(monkeypatch) -> None:
    published = Job(id=uuid.uuid4(), tenant_id=uuid.uuid4(), title="Data Eng",
                    department="Eng", level="Senior",
                    jd_json={"role": "pipelines"}, status=JobStatus.ratified,
                    ratified_at=datetime.now(timezone.utc),
                    created_by=uuid.uuid4(), created_at=datetime.now(timezone.utc))
    out = await jobs_api.get_public_job(published.id, session=_PublicSession(published))
    assert out.title == "Data Eng"
    assert out.company_name == "Acme Corp"
    assert out.jd_json == {"role": "pipelines"}
    # No internal fields leak on the public schema.
    assert not hasattr(out, "created_by")
    assert not hasattr(out, "compensation_json")


@pytest.mark.asyncio
async def test_public_read_404_for_unpublished() -> None:
    draft = Job(id=uuid.uuid4(), tenant_id=uuid.uuid4(), title="Draft",
                jd_json={}, status=JobStatus.draft, ratified_at=None,
                created_by=uuid.uuid4(), created_at=datetime.now(timezone.utc))
    with pytest.raises(Exception) as ei:
        await jobs_api.get_public_job(draft.id, session=_PublicSession(draft))
    assert getattr(ei.value, "status_code", None) == 404


@pytest.mark.asyncio
async def test_public_read_404_for_unknown() -> None:
    with pytest.raises(Exception) as ei:
        await jobs_api.get_public_job(uuid.uuid4(), session=_PublicSession(None))
    assert getattr(ei.value, "status_code", None) == 404


# ── AI JD generation (FR-3.3 Path A) ─────────────────────────────────────────

def _brief() -> JDGenerateIn:
    return JDGenerateIn(title="Backend Engineer", skills=["Python"], experience="5y")


def _patch_jd_module(monkeypatch, module) -> None:
    """Patch what `from app.services import jd_generation` resolves to.

    `from pkg import sub` binds the package ATTRIBUTE when the submodule is
    already imported, so patching sys.modules alone isn't enough — patch the
    attribute on the app.services package object."""
    import app.services as services_pkg

    monkeypatch.setattr(services_pkg, "jd_generation", module, raising=False)


@pytest.mark.asyncio
async def test_generate_jd_calls_service(monkeypatch) -> None:
    async def _fake_audit(session, **kwargs):
        return None

    monkeypatch.setattr(jobs_api, "audit", _fake_audit)

    async def _gen(brief: dict) -> dict:
        assert brief["title"] == "Backend Engineer"
        return {"role": "Own the backend", "skills": ["Python"], "experience_years": 5}

    _patch_jd_module(monkeypatch, SimpleNamespace(generate_job_description=_gen))

    out = await jobs_api.generate_jd(_brief(), user=_user(), session=_FakeSession())
    assert out["role"] == "Own the backend"


@pytest.mark.asyncio
async def test_generate_jd_503_when_function_absent(monkeypatch) -> None:
    # Service module present but without the expected function → clean 503,
    # never a 500. (The bare-ImportError branch is the same contract for the
    # case where the module isn't wired up at all.)
    _patch_jd_module(monkeypatch, SimpleNamespace())
    with pytest.raises(Exception) as ei:
        await jobs_api.generate_jd(_brief(), user=_user(), session=_FakeSession())
    assert getattr(ei.value, "status_code", None) == 503
