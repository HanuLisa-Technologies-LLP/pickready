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
from app.schemas.jobs import (
    JDGenerateIn,
    JDIn,
    JDUpdateIn,
    JobCreateIn,
    JobDetailOut,
    JobOut,
    PublicJobOut,
)
from pydantic import ValidationError


def _user(role: Role = Role.recruiter) -> CurrentUser:
    return CurrentUser(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), role=role, audience="org"
    )


class _Result:
    """A stand-in for a SQLAlchemy Result covering the two access shapes the
    job endpoints use.

    The two are kept separate because one fake session serves two different
    queries: `scalar_one_or_none()` answers the tenant-name lookup, while
    `scalars().first()` answers the ORM company-profile lookup. Returning the
    same value from both would hand a bare string to code expecting a Company.
    """

    def __init__(self, *, scalar=None, entity=None) -> None:
        self._scalar = scalar
        self._entity = entity

    def scalars(self):
        return self

    def first(self):
        return self._entity

    def scalar_one_or_none(self):
        return self._scalar


class _FakeSession:
    """Minimal async session: assigns ids on add/flush, records nothing else.

    `execute` returns an empty result, which is what a tenant with no
    `companies` row looks like — so a job created here seeds its narrative
    sections from nothing and they stay NULL (see api/jobs._company_sections).
    """

    def __init__(self) -> None:
        self.added: list = []

    def add(self, obj) -> None:
        self.added.append(obj)
        self._stamp(obj)

    async def flush(self) -> None:
        for obj in self.added:
            self._stamp(obj)

    async def execute(self, *a, **k):
        return _Result()

    async def get(self, model, ident):
        return None

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
    return JobCreateIn(
        title="Backend Engineer", grade="non_managerial",
        jd=JDIn(role="Own APIs", skills=["Python"]),
    )


def test_blank_optional_jd_numbers_are_not_422_errors() -> None:
    body = JobCreateIn.model_validate(
        {
            "title": "Backend Engineer",
            "grade": "managerial",
            "jd": {"reportees": "", "experience_years": ""},
        }
    )
    # `reportees` was removed on 2026-07-28. A stale client still sending it is
    # IGNORED, not rejected, so a half-deployed frontend cannot 422 a job.
    assert not hasattr(body.jd, "reportees")
    assert body.jd.experience_years is None


# ── grade: required on create, four literals only ────────────────────────────

def test_experience_years_accepts_legacy_numeric_ranges() -> None:
    body = JobCreateIn.model_validate(
        {
            "title": "Backend Engineer",
            "grade": "managerial",
            "jd": {"experience_years": " 3 - 5 "},
        }
    )
    assert body.jd.experience_years == "3-5"


def test_experience_years_rejects_prose() -> None:
    with pytest.raises(ValidationError):
        JobCreateIn.model_validate(
            {
                "title": "Backend Engineer",
                "grade": "managerial",
                "jd": {"experience_years": "several"},
            }
        )


@pytest.mark.parametrize(
    "grade", ["non_managerial", "managerial", "leadership", "cxo"]
)
def test_grade_accepts_the_four_contract_values(grade) -> None:
    body = JobCreateIn.model_validate({"title": "X", "grade": grade, "jd": {}})
    assert body.grade == grade


def test_grade_is_required_on_job_create() -> None:
    with pytest.raises(ValidationError):
        JobCreateIn.model_validate({"title": "X", "jd": {}})


@pytest.mark.parametrize("bad", ["NON_MANAGERIAL", "senior", "", None, 3, "cxo "])
def test_invalid_grade_is_rejected(bad) -> None:
    with pytest.raises(ValidationError):
        JobCreateIn.model_validate({"title": "X", "grade": bad, "jd": {}})


def test_jd_update_grade_is_optional_but_still_validated() -> None:
    assert JDUpdateIn.model_validate({"jd": {}}).grade is None
    assert JDUpdateIn.model_validate({"jd": {}, "grade": "cxo"}).grade == "cxo"
    with pytest.raises(ValidationError):
        JDUpdateIn.model_validate({"jd": {}, "grade": "principal"})


def _job(**kwargs) -> Job:
    base = dict(
        id=uuid.uuid4(), tenant_id=uuid.uuid4(), title="X", jd_json={},
        status=JobStatus.ratified, ratified_at=datetime.now(timezone.utc),
        created_by=uuid.uuid4(), created_at=datetime.now(timezone.utc),
    )
    base.update(kwargs)
    return Job(**base)


def test_grade_is_present_and_never_null_on_every_job_response() -> None:
    graded = _job(assessment_grade="leadership", compensation_json=None)
    legacy = _job(assessment_grade=None, compensation_json=None)

    for model in (JobOut, JobDetailOut):
        assert model.model_validate(graded).model_dump()["grade"] == "leadership"
        assert model.model_validate(legacy).model_dump()["grade"] == "non_managerial"

    public = PublicJobOut.model_validate(graded).model_dump()
    assert public["grade"] == "leadership"
    assert PublicJobOut.model_validate(legacy).model_dump()["grade"] == "non_managerial"


def test_jd_and_compensation_mirrors_always_agree_with_canonical_columns() -> None:
    jd = {"role": "Own APIs", "skills": ["Python"], "responsibilities": ["Ship"]}
    comp = {"min": 100, "max": 200}
    job = _job(jd_json=jd, compensation_json=comp, assessment_grade="managerial")

    detail = JobDetailOut.model_validate(job).model_dump()
    assert detail["jd"] == detail["jd_json"] == jd
    assert detail["compensation"] == detail["compensation_json"] == comp

    public = PublicJobOut.model_validate(job).model_dump()
    assert public["jd"] == public["jd_json"] == jd

    # Empty/None canonical values mirror faithfully too (never invented).
    bare = JobDetailOut.model_validate(_job(jd_json={}, compensation_json=None)).model_dump()
    assert bare["jd"] == {} and bare["compensation"] is None


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
    # The form's required grade is stored on the canonical column and echoed back.
    assert session.added[0].assessment_grade == "non_managerial"
    assert out.grade == "non_managerial"


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
        # Two different queries land here: the tenant-name lookup (read via
        # scalar_one_or_none) and the company-profile lookup (read via
        # scalars().first()). There is no companies row, so the narrative
        # sections resolve to None.
        return _Result(scalar=self._company)


@pytest.mark.asyncio
async def test_public_read_returns_only_published(monkeypatch) -> None:
    published = Job(id=uuid.uuid4(), tenant_id=uuid.uuid4(), title="Data Eng",
                    department="Eng", level="Senior",
                    jd_json={"role": "pipelines"}, status=JobStatus.ratified,
                    ratified_at=datetime.now(timezone.utc),
                    # Inside the 30-day active window: the public link is only
                    # live during it (spec Rule 2). A job with no
                    # posting_start_date deliberately reads as expired — an
                    # unknown window must fail closed — so the fixture has to
                    # carry one for this to be a published-job test rather than
                    # an expired-job test.
                    posting_start_date=datetime.now(timezone.utc),
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
    """The reworked Create Job brief (2026-07-28).

    No `company_context` (the field is gone), an experience BAND instead of a
    free-text level, and the old "requirements brief" folded into `skills`.
    `key_requirements` is still sent here on purpose: it is the deprecated
    alias, and it must keep working.
    """
    return JDGenerateIn(
        title="Backend Engineer",
        key_requirements=["Build APIs"],
        skills=["Python"],
        experience_min_years=3,
        experience_max_years=6,
    )


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
        # `key_requirements` is merged INTO skills, and company_context is gone.
        assert brief["skills"] == ["Python", "Build APIs"]
        assert "company_context" not in brief
        assert brief["experience_min_years"] == 3
        assert brief["experience_max_years"] == 6
        return {
            "jd_markdown": "## Role\n\nOwn the backend\n\n## Skills\n\n- Python\n",
            "jd": {"role": "Own the backend", "skills": ["Python"],
                   "experience_years": 3},
            "generated_by_ai": True,
        }

    _patch_jd_module(monkeypatch, SimpleNamespace(generate_jd_document=_gen))

    out = await jobs_api.generate_jd(_brief(), user=_user(), session=_FakeSession())
    assert out.jd_markdown.startswith("## Role")
    assert out.jd["role"] == "Own the backend"
    # Legacy top-level mirrors: a client that has not been rebuilt still reads
    # `role` and `skills` straight off the response.
    dumped = out.model_dump()
    assert dumped["role"] == "Own the backend"
    assert dumped["skills"] == ["Python"]


@pytest.mark.asyncio
async def test_generate_jd_503_when_function_absent(monkeypatch) -> None:
    # Service module present but without the expected function → clean 503,
    # never a 500. (The bare-ImportError branch is the same contract for the
    # case where the module isn't wired up at all.)
    _patch_jd_module(monkeypatch, SimpleNamespace())
    with pytest.raises(Exception) as ei:
        await jobs_api.generate_jd(_brief(), user=_user(), session=_FakeSession())
    assert getattr(ei.value, "status_code", None) == 503


# ═════════════════════════════════════════════════════════════════════════════
# 2026-07-28 spec: the unified JD document, explicit publish, the experience
# band, the reporting-to dropdown, and Upload Candidate Data Bank.
# ═════════════════════════════════════════════════════════════════════════════

from app.models.candidate import SOURCE_TYPE_DATABANK, JobCandidateLink  # noqa: E402
from app.schemas.jobs import JDMarkdownIn, JobPatchIn  # noqa: E402


# ── experience band: min must not exceed max ─────────────────────────────────

def test_experience_band_rejects_min_above_max() -> None:
    with pytest.raises(ValidationError):
        JobCreateIn.model_validate(
            {
                "title": "Backend Engineer",
                "grade": "managerial",
                "jd": {},
                "experience_min_years": 8,
                "experience_max_years": 3,
            }
        )


def test_experience_band_accepts_equal_bounds_and_blanks() -> None:
    equal = JobCreateIn.model_validate(
        {
            "title": "X", "grade": "cxo", "jd": {},
            "experience_min_years": 5, "experience_max_years": 5,
        }
    )
    assert equal.experience_min_years == equal.experience_max_years == 5

    # A blank string from an untouched form field is an absence, not a zero.
    blank = JobCreateIn.model_validate(
        {
            "title": "X", "grade": "cxo", "jd": {},
            "experience_min_years": "", "experience_max_years": "",
        }
    )
    assert blank.experience_min_years is None


def test_experience_band_is_enforced_on_generate_and_patch() -> None:
    with pytest.raises(ValidationError):
        JDGenerateIn.model_validate(
            {
                "title": "X", "skills": ["Go"],
                "experience_min_years": 9, "experience_max_years": 2,
            }
        )
    with pytest.raises(ValidationError):
        JobPatchIn.model_validate(
            {"experience_min_years": 9, "experience_max_years": 2}
        )


def test_generate_brief_requires_both_experience_bounds() -> None:
    with pytest.raises(ValidationError):
        JDGenerateIn.model_validate({"title": "X", "skills": ["Go"]})


def test_generate_brief_drops_company_context_and_merges_legacy_requirements() -> None:
    body = JDGenerateIn.model_validate(
        {
            "title": "X",
            "skills": ["Python"],
            "key_requirements": ["Build APIs", "Python"],
            "company_context": "ignored, the field no longer exists",
            "experience_min_years": 2,
            "experience_max_years": 6,
        }
    )
    assert not hasattr(body, "company_context")
    # Deprecated alias folded in, de-duplicated, recruiter's own box first.
    assert body.merged_skills() == ["Python", "Build APIs"]


def test_reportees_is_gone_from_the_jd_schema() -> None:
    jd = JDIn.model_validate({"role": "Own APIs", "reportees": 4})
    assert not hasattr(jd, "reportees")
    # Ignored rather than 422: a client mid-deploy still sending it must not
    # have its job creation rejected.
    assert jd.role == "Own APIs"


# ── the reporting-to dropdown ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_reporting_to_options_are_stable_and_end_with_others() -> None:
    out = await jobs_api.reporting_to_options(_user())
    assert out.options[0] == "Team Lead"
    assert out.options[-1] == "Others"
    assert out.other_value == "Others"
    # Every entry unique, so the dropdown cannot show the same role twice.
    assert len(set(out.options)) == len(out.options)
    for expected in ("Engineering Manager", "Director", "VP", "CTO", "CEO", "Founder"):
        assert expected in out.options


# ── the unified document on create / read ────────────────────────────────────

@pytest.mark.asyncio
async def test_create_with_publish_false_leaves_job_unpublished(monkeypatch) -> None:
    calls = _stub_create_deps(monkeypatch)
    session = _FakeSession()
    body = JobCreateIn.model_validate(
        {
            "title": "Backend Engineer", "grade": "non_managerial", "jd": {},
            "jd_markdown": "## Description\n\nBuild things.\n",
            "publish": False,
        }
    )
    out = await jobs_api.create_job(body, user=_user(), session=session)

    assert out.ratified_at is None
    assert out.public_url is None and out.public_application_url is None
    assert calls["audit"]["metadata"]["published"] is False


@pytest.mark.asyncio
async def test_create_derives_jd_json_from_the_document(monkeypatch) -> None:
    _stub_create_deps(monkeypatch)
    session = _FakeSession()
    body = JobCreateIn.model_validate(
        {
            "title": "Backend Engineer", "grade": "non_managerial",
            # Deliberately contradictory: the document must win, because it is
            # what the candidate actually reads.
            "jd": {"skills": ["StaleSkill"]},
            "jd_markdown": "## Skills\n\n- Python\n- Go\n\n## Role\n\nOwn APIs.\n",
            "publish": False,
        }
    )
    await jobs_api.create_job(body, user=_user(), session=session)
    job = session.added[0]
    assert job.jd_json["skills"] == ["Python", "Go"]
    assert job.jd_json["role"] == "Own APIs."


@pytest.mark.asyncio
async def test_create_without_a_document_still_gets_one(monkeypatch) -> None:
    # The pre-2026-07-28 per-section contract still works, and the job ends up
    # with a canonical document rendered from those sections.
    _stub_create_deps(monkeypatch)
    session = _FakeSession()
    out = await jobs_api.create_job(_job_create_body(), user=_user(), session=session)
    assert "## Description" in (out.jd_markdown or "")
    assert "Own APIs" in (out.jd_markdown or "")


def test_jd_markdown_for_renders_legacy_jobs_rather_than_blanking_them() -> None:
    legacy = _job(
        jd_json={"role": "Own APIs", "skills": ["Python"]},
        jd_markdown=None,
        compensation_json=None,
    )
    document = jobs_api.jd_markdown_for(legacy)
    assert "## Role" in document and "Own APIs" in document

    stored = _job(jd_json={}, jd_markdown="## Role\n\nHand written.\n",
                  compensation_json=None)
    # A stored document always wins over a rendering of the sections.
    assert jobs_api.jd_markdown_for(stored).strip().endswith("Hand written.")


# ── publish: explicit, once, and never with an empty JD ──────────────────────

class _PublishSession(_FakeSession):
    """A fake session whose `get` returns one job, for the publish endpoint."""

    def __init__(self, job) -> None:
        super().__init__()
        self.job = job

    async def get(self, model, ident):
        return self.job


def _stub_publish_deps(monkeypatch) -> dict:
    calls: dict = {"tasks": []}

    async def _fake_audit(session, **kwargs):
        calls["audit"] = kwargs

    async def _fake_publish(session, job):
        job.status = JobStatus.ratified
        job.ratified_at = datetime.now(timezone.utc)

    async def _can_see(session, user):
        return True

    monkeypatch.setattr(jobs_api, "audit", _fake_audit)
    monkeypatch.setattr(jobs_api.fsm, "apply_direct_publish", _fake_publish)
    monkeypatch.setattr(jobs_api, "_can_see_pre_ratified", _can_see)
    monkeypatch.setattr(
        jobs_api.celery_app, "send_task",
        lambda *a, **k: calls["tasks"].append(a),
    )
    monkeypatch.setattr(
        jobs_api, "get_settings",
        lambda: SimpleNamespace(frontend_url="https://picready.com"),
    )
    return calls


def _draft_job(user: CurrentUser, **kwargs) -> Job:
    base = dict(
        id=uuid.uuid4(), tenant_id=user.tenant_id, title="Backend Engineer",
        jd_json={}, jd_markdown="## Description\n\nBuild the platform.\n",
        status=JobStatus.draft, ratified_at=None, created_by=user.user_id,
        created_at=datetime.now(timezone.utc),
    )
    base.update(kwargs)
    return Job(**base)


@pytest.mark.asyncio
async def test_publish_returns_the_public_application_link(monkeypatch) -> None:
    calls = _stub_publish_deps(monkeypatch)
    user = _user()
    job = _draft_job(user)
    out = await jobs_api.publish_job(job.id, user=user, session=_PublishSession(job))

    assert out.ratified_at is not None
    expected = f"https://picready.com/apply/{job.id}"
    # Both names carry the same absolute link, for the copy-link popup.
    assert out.public_application_url == expected
    assert out.public_url == expected
    assert calls["audit"]["action"] == "job_published"


@pytest.mark.asyncio
async def test_publish_blocked_when_the_jd_is_empty(monkeypatch) -> None:
    _stub_publish_deps(monkeypatch)
    user = _user()
    job = _draft_job(user, jd_markdown=None, jd_json={})
    with pytest.raises(Exception) as ei:
        await jobs_api.publish_job(job.id, user=user, session=_PublishSession(job))
    assert getattr(ei.value, "status_code", None) == 409
    assert "job description" in str(getattr(ei.value, "detail", "")).lower()


@pytest.mark.asyncio
async def test_publish_blocked_when_only_headings_were_written(monkeypatch) -> None:
    # A document of empty headings is not a job description. Publishing it
    # would send candidates to a page with nothing on it.
    _stub_publish_deps(monkeypatch)
    user = _user()
    job = _draft_job(user, jd_markdown="## Description\n\n## Role\n\n## Skills\n")
    with pytest.raises(Exception) as ei:
        await jobs_api.publish_job(job.id, user=user, session=_PublishSession(job))
    assert getattr(ei.value, "status_code", None) == 409


@pytest.mark.asyncio
async def test_publishing_twice_is_refused(monkeypatch) -> None:
    # Re-stamping would restart the fixed 30-day posting window.
    _stub_publish_deps(monkeypatch)
    user = _user()
    job = _draft_job(user, status=JobStatus.ratified,
                     ratified_at=datetime.now(timezone.utc))
    with pytest.raises(Exception) as ei:
        await jobs_api.publish_job(job.id, user=user, session=_PublishSession(job))
    assert getattr(ei.value, "status_code", None) == 409


# ── PATCH /jobs/{id}/jd: the explicit Edit button ────────────────────────────

def test_jd_markdown_in_rejects_blank_text() -> None:
    with pytest.raises(ValidationError):
        JDMarkdownIn.model_validate({"jd_markdown": "   \n  "})
    assert JDMarkdownIn.model_validate({"jd_markdown": "## Role\n\nX"}).jd_markdown


@pytest.mark.asyncio
async def test_save_jd_markdown_works_after_publish_and_rederives_sections(
    monkeypatch,
) -> None:
    calls = _stub_publish_deps(monkeypatch)
    user = _user()
    job = _draft_job(user, status=JobStatus.ratified,
                     ratified_at=datetime.now(timezone.utc))
    body = JDMarkdownIn(
        jd_markdown="## Role\n\nOwn the platform — end to end.\n\n## Skills\n\n- Rust\n"
    )
    out = await jobs_api.save_jd_markdown(
        job.id, body, user=user, session=_PublishSession(job)
    )
    # Editing a LIVE job is allowed: a typo in a published JD must be fixable.
    assert calls["audit"]["metadata"]["published"] is True
    assert job.jd_json["skills"] == ["Rust"]
    # The em dash never survives to a candidate.
    assert "—" not in job.jd_markdown
    assert "—" not in (out.jd_markdown or "")


# ── Upload Candidate Data Bank ───────────────────────────────────────────────

class _StubUpload:
    """Minimal UploadFile stand-in: filename plus a rewindable body."""

    def __init__(self, filename: str, data: bytes = b"%PDF-1.4 body") -> None:
        self.filename = filename
        self.content_type = "application/pdf"
        self._data = data

    async def read(self, size: int = -1) -> bytes:
        return self._data

    async def seek(self, offset: int) -> None:
        return None


def _stub_databank_deps(monkeypatch, failing: set | None = None) -> dict:
    """Stub storage, identity extraction, audit and Celery for bulk upload."""
    from fastapi import HTTPException as _HTTPException

    from app.services import resume_parsing, resume_storage

    failing = failing or set()
    calls: dict = {"tasks": []}

    async def _fake_audit(session, **kwargs):
        calls["audit"] = kwargs

    async def _read_validated(upload):
        if upload.filename in failing:
            raise _HTTPException(
                status_code=422,
                detail={"message": "The selected file is not a valid PDF."},
            )
        return await upload.read(), upload.filename, "application/pdf"

    async def _store(upload):
        return SimpleNamespace(
            public_id=f"pickready/resumes/{upload.filename}",
            secure_url=f"https://cdn.example/{upload.filename}",
            original_filename=upload.filename,
            mime_type="application/pdf",
            size_bytes=12,
            uploaded_at=datetime.now(timezone.utc),
            sha256=upload.filename,
            metadata={},
        )

    def _identity(data, filename):
        # "anon-*.pdf" carries no email, which forces the placeholder path.
        if filename.startswith("anon"):
            return {"full_name": None, "email": None, "phone": None}
        stem = filename.split(".")[0]
        return {"full_name": stem.title(), "email": f"{stem}@example.com",
                "phone": "+919000000000"}

    monkeypatch.setattr(jobs_api, "audit", _fake_audit)
    monkeypatch.setattr(resume_storage, "read_validated_resume", _read_validated)
    monkeypatch.setattr(resume_storage, "store_resume", _store)
    monkeypatch.setattr(resume_storage, "apply_resume_asset", lambda p, a: None)
    monkeypatch.setattr(resume_parsing, "extract_contact_identity", _identity)
    monkeypatch.setattr(
        jobs_api.celery_app, "send_task",
        lambda *a, **k: calls["tasks"].append(a),
    )
    monkeypatch.setattr(
        jobs_api, "get_settings",
        lambda: SimpleNamespace(frontend_url="https://picready.com"),
    )

    async def _can_see(session, user):
        return True

    monkeypatch.setattr(jobs_api, "_can_see_pre_ratified", _can_see)
    return calls


class _DatabankSession(_PublishSession):
    """Fake session where no candidate or link pre-exists."""

    async def execute(self, *a, **k):
        return _Result()


@pytest.mark.asyncio
async def test_databank_accepts_twenty_five_files(monkeypatch) -> None:
    calls = _stub_databank_deps(monkeypatch)
    user = _user()
    job = _draft_job(user, status=JobStatus.ratified,
                     ratified_at=datetime.now(timezone.utc))
    session = _DatabankSession(job)
    files = [_StubUpload(f"cand{i}.pdf") for i in range(25)]

    out = await jobs_api.upload_databank_candidates(
        job.id, files=files, user=user, session=session
    )
    assert out.received == 25 and out.created == 25 and out.failed == 0
    assert all(r.ok for r in out.results)

    links = [o for o in session.added if isinstance(o, JobCandidateLink)]
    assert len(links) == 25
    # Every stored link is tagged as a databank procurement.
    assert all(link.source_type == SOURCE_TYPE_DATABANK for link in links)

    # One parse task per file, and exactly ONE matching run for the batch.
    parse_tasks = [t for t in calls["tasks"] if t[0] == "pickready.parse_resume"]
    match_tasks = [t for t in calls["tasks"] if t[0] == "pickready.run_matching"]
    assert len(parse_tasks) == 25
    assert len(match_tasks) == 1


@pytest.mark.asyncio
async def test_databank_rejects_a_twenty_sixth_file(monkeypatch) -> None:
    calls = _stub_databank_deps(monkeypatch)
    user = _user()
    job = _draft_job(user, status=JobStatus.ratified,
                     ratified_at=datetime.now(timezone.utc))
    files = [_StubUpload(f"cand{i}.pdf") for i in range(26)]

    with pytest.raises(Exception) as ei:
        await jobs_api.upload_databank_candidates(
            job.id, files=files, user=user, session=_DatabankSession(job)
        )
    assert getattr(ei.value, "status_code", None) == 400
    assert "25" in str(getattr(ei.value, "detail", ""))
    # Refused whole: nothing was stored and nothing was enqueued.
    assert calls["tasks"] == []


@pytest.mark.asyncio
async def test_databank_partial_failure_keeps_the_good_files(monkeypatch) -> None:
    calls = _stub_databank_deps(monkeypatch, failing={"broken.pdf"})
    user = _user()
    job = _draft_job(user, status=JobStatus.ratified,
                     ratified_at=datetime.now(timezone.utc))
    files = [_StubUpload("alice.pdf"), _StubUpload("broken.pdf"), _StubUpload("bob.pdf")]

    out = await jobs_api.upload_databank_candidates(
        job.id, files=files, user=user, session=_DatabankSession(job)
    )
    assert out.received == 3 and out.created == 2 and out.failed == 1
    bad = [r for r in out.results if not r.ok]
    assert len(bad) == 1 and bad[0].filename == "broken.pdf"
    assert bad[0].error and "PDF" in bad[0].error
    # The other two still went through, one parse task each.
    assert len([t for t in calls["tasks"] if t[0] == "pickready.parse_resume"]) == 2


@pytest.mark.asyncio
async def test_databank_generates_a_placeholder_identity_without_an_email(
    monkeypatch,
) -> None:
    _stub_databank_deps(monkeypatch)
    user = _user()
    job = _draft_job(user, status=JobStatus.ratified,
                     ratified_at=datetime.now(timezone.utc))
    session = _DatabankSession(job)

    out = await jobs_api.upload_databank_candidates(
        job.id, files=[_StubUpload("anon1.pdf")], user=user, session=session
    )
    result = out.results[0]
    assert result.ok is True
    # Flagged, not silently filed under a real-looking address.
    assert result.identified is False
    assert result.email.endswith("@placeholder.invalid")


def test_placeholder_email_is_stable_for_the_same_file() -> None:
    jid = uuid.uuid4()
    first = jobs_api._placeholder_email(jid, "abc123")
    assert first == jobs_api._placeholder_email(jid, "abc123")
    # Different content, different candidate.
    assert first != jobs_api._placeholder_email(jid, "def456")

