"""My Profile — the unified candidate profile (client decision, 2026-07-27).

The 40 validation aspects are answered ONCE on the candidate's own profile and
snapshotted onto every application, instead of being re-asked inside each job's
assessment conversation. The candidate also gains a designated MAIN resume that
can be replaced at any time without rewriting past applications.

Same convention as test_portal.py: skips cleanly with no database, runs for
real inside the backend container.
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text
from starlette.datastructures import Headers, UploadFile

from app.services.resume_storage import ResumeAsset


def _upload(filename: str = "cv.pdf") -> UploadFile:
    return UploadFile(
        file=io.BytesIO(b"%PDF-1.4 minimal resume bytes"),
        filename=filename,
        headers=Headers({"content-type": "application/pdf"}),
    )


def _asset(url: str) -> ResumeAsset:
    return ResumeAsset(
        public_id=f"pickready/resumes/{uuid.uuid4().hex}", secure_url=url,
        original_filename="cv.pdf", mime_type="application/pdf", size_bytes=24,
        uploaded_at=datetime.now(timezone.utc), sha256=uuid.uuid4().hex * 2,
        metadata={"resource_type": "raw"},
    )


FORM_ANSWERS = {
    "current_city": "Bengaluru",
    "total_experience": "6 Years 2 Months",
    "current_ctc": "18 LPA",
    "expected_ctc": "26 LPA",
    "notice_period": "Maximum of 30 Days",
    "shift_preference": ["Day Shift", "Not A Real Option"],
    "declaration_accepted": True,
    "declaration_full_name": "Open Applicant",
    "education": {"graduation": {"course": "B.E. CSE", "year_of_passing": "2018"}},
    "not_a_field": "must be dropped",
}


async def _factory_or_skip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 — no DB reachable
        await engine.dispose()
        pytest.skip("no database reachable — skipping candidate profile test")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class _Fixture:
    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.email = f"profile-{uuid.uuid4().hex[:8]}@candidates.pickready.test"
        self.cand_id: uuid.UUID | None = None
        self.jobs: list[uuid.UUID] = []


async def _seed(factory, fx: _Fixture, titles: list[str] | None = None) -> None:
    from app.core.db import superadmin_scope
    from app.models import Candidate, Job, JobStatus, Role, Tenant, User
    from app.models.enums import UserStatus

    now = datetime.now(timezone.utc)
    titles = titles or ["Role", "Role", "Role"]
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                s.add(Tenant(id=fx.tenant_id, name="ProfileCo",
                             domain=f"{fx.tenant_id}.profile.test"))
                s.add(User(id=fx.user_id, email=fx.email, role=Role.candidate,
                           tenant_id=None, full_name="Open Applicant",
                           status=UserStatus.active))
                await s.flush()
                cand = Candidate(id=uuid.uuid4(), email=fx.email, user_id=fx.user_id,
                                 full_name="Open Applicant", consent_databank=False)
                s.add(cand)
                fx.jobs = [uuid.uuid4() for _ in titles]
                for jid, title in zip(fx.jobs, titles):
                    s.add(Job(id=jid, tenant_id=fx.tenant_id, title=title, jd_json={},
                              status=JobStatus.ratified, ratified_at=now))
                await s.flush()
                fx.cand_id = cand.id


async def _cleanup(factory, fx: _Fixture) -> None:
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                # Break the candidate -> profile FK before the profiles go, so
                # the delete order cannot deadlock on the circular reference.
                await s.execute(
                    text("UPDATE candidates SET main_profile_id = NULL WHERE id = :c"),
                    {"c": str(fx.cand_id)},
                )
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


# ── The form itself ─────────────────────────────────────────────────────────

def test_form_definition_is_self_consistent() -> None:
    """The definition served to the UI must match what the cleaner accepts."""
    from app.services import candidate_profile_form as form

    definition = form.form_definition()
    served = {
        field["key"]
        for section in definition["sections"]
        for field in section["fields"]
    }
    assert served == set(form.ALLOWED_KEYS)
    # Every required field is actually reachable in the served definition.
    assert set(form.REQUIRED_FIELD_KEYS) <= served
    # An empty profile is incomplete and names what it still needs.
    assert form.is_complete({}) is False
    assert set(form.missing_required({})) == set(form.REQUIRED_FIELD_KEYS)


def test_clean_answers_rejects_everything_not_in_the_definition() -> None:
    from app.services import candidate_profile_form as form

    cleaned = form.clean_answers(FORM_ANSWERS)
    assert "not_a_field" not in cleaned
    # An option the form never offered is dropped, not stored.
    assert cleaned["shift_preference"] == ["Day Shift"]
    # A radio value outside the option list is dropped entirely.
    assert "notice_period" in cleaned
    assert form.clean_answers({"notice_period": "Whenever I feel like it"}) == {}
    # Education keeps only known rows and columns.
    assert set(cleaned["education"]) == {"graduation"}
    assert form.clean_answers({"education": {"hogwarts": {"course": "Potions"}}}) == {}
    # Non-dict input is survivable, never a 500.
    assert form.clean_answers("nonsense") == {}
    assert form.clean_answers(None) == {}


# ── Integration ─────────────────────────────────────────────────────────────

async def test_profile_form_round_trips_and_drops_unknown_input() -> None:
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope
    from app.models import Candidate

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        user = _user(fx)

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    empty = await portal_mod.get_profile_form(user=user, session=s)
        # An untouched profile reports what is still required rather than 404ing.
        assert empty.complete is False
        assert "current_city" in empty.missing
        assert empty.definition["sections"], "the form definition must be served"

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    saved = await portal_mod.save_profile_form(
                        portal_mod.ProfileFormIn(answers=dict(FORM_ANSWERS)),
                        user=user, session=s,
                    )
        assert saved.complete is True
        assert saved.missing == []
        assert "not_a_field" not in saved.answers
        assert saved.answers["shift_preference"] == ["Day Shift"]

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    cand = (await s.execute(
                        select(Candidate).where(Candidate.id == fx.cand_id)
                    )).scalar_one()
        # Denormalised columns the ATS reads are kept in step.
        assert cand.city == "Bengaluru"
        assert cand.profile_form_updated_at is not None
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_apply_snapshots_the_profile_form_onto_the_application(monkeypatch) -> None:
    """A candidate never retypes the 40 answers: applying copies them across."""
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope
    from app.models import Candidate, Profile

    async def fake_store(_resume):
        return _asset("https://res.cloudinary.com/x/raw/upload/snapshot.pdf")

    monkeypatch.setattr(portal_mod, "store_resume", fake_store)
    monkeypatch.setattr(portal_mod.celery_app, "send_task", lambda *a, **k: None)

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        user = _user(fx)

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await portal_mod.save_profile_form(
                        portal_mod.ProfileFormIn(answers=dict(FORM_ANSWERS)),
                        user=user, session=s,
                    )
                    out = await portal_mod.apply_to_job(
                        fx.jobs[0], "{}", _upload(), False, user, s
                    )

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    profile = (await s.execute(
                        select(Profile).where(Profile.id == out.profile_id)
                    )).scalar_one()
                    cand = (await s.execute(
                        select(Candidate).where(Candidate.id == fx.cand_id)
                    )).scalar_one()

        assert profile.aspects_json["current_city"] == "Bengaluru"
        assert profile.aspects_json["notice_period"] == "Maximum of 30 Days"
        assert profile.aspects_completed_at is not None
        # The declaration carries the Databank consent now that aspect 40 is gone.
        assert cand.consent_databank is True
        # A first upload also becomes the main resume, so My Profile is not
        # empty immediately after applying.
        assert cand.main_profile_id == profile.id
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_main_resume_replaces_without_rewriting_past_applications(monkeypatch) -> None:
    """Replacing the main resume must not mutate an already-submitted one."""
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope
    from app.models import Candidate, Profile

    urls = iter([
        "https://res.cloudinary.com/x/raw/upload/first.pdf",
        "https://res.cloudinary.com/x/raw/upload/second.pdf",
    ])

    async def fake_store(_resume):
        return _asset(next(urls))

    monkeypatch.setattr(portal_mod, "store_resume", fake_store)
    monkeypatch.setattr(portal_mod.celery_app, "send_task", lambda *a, **k: None)

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        user = _user(fx)

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    applied = await portal_mod.apply_to_job(
                        fx.jobs[0], "{}", _upload(), False, user, s
                    )
                    replaced = await portal_mod.replace_main_resume(
                        _upload(), user=user, session=s
                    )
        assert replaced.has_resume is True

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    cand = (await s.execute(
                        select(Candidate).where(Candidate.id == fx.cand_id)
                    )).scalar_one()
                    submitted = (await s.execute(
                        select(Profile).where(Profile.id == applied.profile_id)
                    )).scalar_one()
                    main = (await s.execute(
                        select(Profile).where(Profile.id == cand.main_profile_id)
                    )).scalar_one()

        # The application keeps the resume it was actually submitted with.
        assert submitted.resume_url.endswith("first.pdf")
        assert main.resume_url.endswith("second.pdf")
        assert main.id != submitted.id

        # A later application reusing the main resume gets the NEW file.
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    again = await portal_mod.apply_to_job(
                        fx.jobs[1], "{}", None, True, user, s
                    )
                    reused = (await s.execute(
                        select(Profile).where(Profile.id == again.profile_id)
                    )).scalar_one()
        assert again.resume_reused is True
        assert reused.resume_url.endswith("second.pdf")
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_job_board_search_bypasses_relevance_filtering() -> None:
    """Search must find a role by name whether or not the profile says it fits."""
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(
            factory, fx,
            titles=["Kubernetes Platform Engineer", "Payroll Specialist", "Role"],
        )
        user = _user(fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    hits = await portal_mod.portal_jobs(
                        search="payroll", user=user, session=s
                    )
                    every = await portal_mod.portal_jobs(
                        all_jobs=True, user=user, session=s
                    )

        found = {j.title for j in hits.jobs}
        assert "Payroll Specialist" in found
        assert "Kubernetes Platform Engineer" not in found
        # all_jobs is the unfiltered board and must be a superset.
        assert {j.title for j in every.jobs} >= found
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()
