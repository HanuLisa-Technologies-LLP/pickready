"""Candidate data-retention consent (Consent & Privacy spec, 2026-09-05).

The rule under test, in both directions: the client portal may DOWNLOAD a
candidate's assessment only on an explicit Yes. An explicit No and a
never-asked NULL both read as View-only, the safe direction. On-screen
viewing is never gated.

Pure-helper tests run everywhere; the integration tests seed a real tenant,
candidate, link and report and SKIP cleanly when no database is reachable
(same convention as tests/test_portal.py).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from app.services import retention_consent


# ── The pure permission rule ─────────────────────────────────────────────────


def test_null_consent_is_view_only() -> None:
    """Never asked reads exactly as "this job only": absence is not consent."""
    candidate = SimpleNamespace(
        retain_assessment_consent=None, retain_video_consent=None
    )
    assert retention_consent.assessment_download_allowed(candidate) is False
    assert retention_consent.video_download_allowed(candidate) is False


def test_explicit_false_is_view_only() -> None:
    candidate = SimpleNamespace(
        retain_assessment_consent=False, retain_video_consent=False
    )
    assert retention_consent.assessment_download_allowed(candidate) is False
    assert retention_consent.video_download_allowed(candidate) is False


def test_explicit_true_enables_download() -> None:
    candidate = SimpleNamespace(
        retain_assessment_consent=True, retain_video_consent=True
    )
    assert retention_consent.assessment_download_allowed(candidate) is True
    assert retention_consent.video_download_allowed(candidate) is True


def test_the_two_consents_are_independent() -> None:
    """Consenting to one retention must not leak into the other."""
    candidate = SimpleNamespace(
        retain_assessment_consent=True, retain_video_consent=False
    )
    assert retention_consent.assessment_download_allowed(candidate) is True
    assert retention_consent.video_download_allowed(candidate) is False


def test_a_missing_candidate_is_view_only() -> None:
    """A report can outlive its candidate row; a record that cannot answer
    "did they consent?" must answer no."""
    assert retention_consent.assessment_download_allowed(None) is False
    assert retention_consent.video_download_allowed(None) is False


def test_truthy_non_boolean_values_do_not_count_as_consent() -> None:
    """Only a stored, explicit True is a Yes."""
    candidate = SimpleNamespace(
        retain_assessment_consent="yes", retain_video_consent=1
    )
    assert retention_consent.assessment_download_allowed(candidate) is False
    assert retention_consent.video_download_allowed(candidate) is False


# ── Integration: the portal API and the PDF route ────────────────────────────


async def _factory_or_skip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 -- no DB reachable
        await engine.dispose()
        pytest.skip("no database reachable, skipping retention consent integration test")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class _Fixture:
    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.candidate_user_id = uuid.uuid4()
        self.staff_user_id = uuid.uuid4()
        self.email = f"retention-{uuid.uuid4().hex[:8]}@candidates.pickready.test"
        self.cand_id = uuid.uuid4()
        self.job_id = uuid.uuid4()
        self.link_id = uuid.uuid4()
        self.report_id = uuid.uuid4()


async def _seed(factory, fx: _Fixture) -> None:
    from app.core.db import superadmin_scope
    from app.models import Candidate, Job, JobStatus, Role, Tenant, User
    from app.models.assessment import FunctionalSkillsReport
    from app.models.candidate import JobCandidateLink
    from app.models.enums import LinkSource, UserStatus

    now = datetime.now(timezone.utc)
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                s.add(Tenant(id=fx.tenant_id, name="RetentionCo",
                             domain=f"{fx.tenant_id}.retention.test"))
                s.add(User(id=fx.candidate_user_id, email=fx.email,
                           role=Role.candidate, tenant_id=None,
                           full_name="Retention Candidate",
                           status=UserStatus.active))
                s.add(User(id=fx.staff_user_id,
                           email=f"staff-{uuid.uuid4().hex[:8]}@retention.test",
                           role=Role.client, tenant_id=fx.tenant_id,
                           full_name="Retention Staff",
                           status=UserStatus.active))
                await s.flush()
                s.add(Candidate(id=fx.cand_id, email=fx.email,
                                user_id=fx.candidate_user_id,
                                full_name="Retention Candidate",
                                consent_databank=False))
                s.add(Job(id=fx.job_id, tenant_id=fx.tenant_id, title="Role",
                          jd_json={}, status=JobStatus.ratified,
                          ratified_at=now))
                await s.flush()
                s.add(JobCandidateLink(id=fx.link_id, tenant_id=fx.tenant_id,
                                       job_id=fx.job_id,
                                       candidate_id=fx.cand_id,
                                       source=LinkSource.fresh))
                await s.flush()
                s.add(FunctionalSkillsReport(
                    id=fx.report_id, tenant_id=fx.tenant_id, job_id=fx.job_id,
                    job_candidate_link_id=fx.link_id, grade="non_managerial",
                    overall_summary=(
                        "A steady record of shipped work with clear ownership "
                        "and honest gaps named where evidence was thin."
                    ),
                    validation_json={}, synthesized_at=now,
                ))


async def _cleanup(factory, fx: _Fixture) -> None:
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(text("DELETE FROM tenants WHERE id = :t"),
                                {"t": str(fx.tenant_id)})
                await s.execute(text("DELETE FROM candidates WHERE id = :c"),
                                {"c": str(fx.cand_id)})
                await s.execute(
                    text("DELETE FROM users WHERE id IN (:a, :b)"),
                    {"a": str(fx.candidate_user_id), "b": str(fx.staff_user_id)},
                )


def _candidate_user(fx: _Fixture):
    from app.api.deps import CurrentUser
    from app.core.security import AUDIENCE_CANDIDATE
    from app.models import Role

    return CurrentUser(user_id=fx.candidate_user_id, tenant_id=None,
                       role=Role.candidate, audience=AUDIENCE_CANDIDATE)


def _staff_user(fx: _Fixture):
    from app.api.deps import CurrentUser
    from app.core.security import AUDIENCE_ORG
    from app.models import Role

    return CurrentUser(user_id=fx.staff_user_id, tenant_id=fx.tenant_id,
                       role=Role.client, audience=AUDIENCE_ORG)


async def test_portal_api_sets_consents_and_stamps_when() -> None:
    """PUT records the choice and WHEN it was made; a one-field save leaves
    the other choice, and its timestamp, exactly as they were."""
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope
    from app.models import Candidate

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        user = _candidate_user(fx)

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    before = await portal_mod.get_retention_consents(
                        user=user, session=s
                    )
        assert before.retain_assessment is None
        assert before.retain_video is None
        assert before.retain_assessment_updated_at is None

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    out = await portal_mod.set_retention_consents(
                        portal_mod.RetentionConsentsIn(retain_assessment=True),
                        user=user, session=s,
                    )
        assert out.retain_assessment is True
        assert out.retain_assessment_updated_at is not None
        # The unsent field is untouched: still never-asked, still unstamped.
        assert out.retain_video is None
        assert out.retain_video_updated_at is None

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    out = await portal_mod.set_retention_consents(
                        portal_mod.RetentionConsentsIn(retain_video=False),
                        user=user, session=s,
                    )
        assert out.retain_video is False
        assert out.retain_video_updated_at is not None
        assert out.retain_assessment is True

        # The stored row agrees with what the API reported.
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    candidate = await s.get(Candidate, fx.cand_id)
                    assert candidate.retain_assessment_consent is True
                    assert candidate.retain_assessment_consented_at is not None
                    assert candidate.retain_video_consent is False
                    assert candidate.retain_video_consented_at is not None
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_explicit_null_is_refused_once_asked() -> None:
    """Once the question is on the wire, the honest answers are Yes and No."""
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        user = _candidate_user(fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    with pytest.raises(HTTPException) as excinfo:
                        await portal_mod.set_retention_consents(
                            portal_mod.RetentionConsentsIn(retain_assessment=None),
                            user=user, session=s,
                        )
        assert excinfo.value.status_code == 422
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_pdf_download_is_refused_without_consent() -> None:
    """NULL (never asked) blocks the PDF route with 403 and a plain message;
    the on-screen report stays readable and says the download is off."""
    from app.api import assessments as assessments_mod
    from app.core.db import superadmin_scope

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        staff = _staff_user(fx)

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    # Viewing is not gated: the report serialises, flagged.
                    report_out = await assessments_mod.get_report(
                        link_id=fx.link_id, user=staff, session=s
                    )
                    assert report_out.report_download_allowed is False

                    with pytest.raises(HTTPException) as excinfo:
                        await assessments_mod.download_report_pdf(
                            link_id=fx.link_id, user=staff, session=s
                        )
        assert excinfo.value.status_code == 403
        assert excinfo.value.detail == (
            "The candidate consented to view-only access for this assessment."
        )
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_explicit_false_also_blocks_the_pdf() -> None:
    from app.api import assessments as assessments_mod
    from app.core.db import superadmin_scope
    from app.models import Candidate

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    candidate = await s.get(Candidate, fx.cand_id)
                    candidate.retain_assessment_consent = False
                    candidate.retain_assessment_consented_at = datetime.now(
                        timezone.utc
                    )

        staff = _staff_user(fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    with pytest.raises(HTTPException) as excinfo:
                        await assessments_mod.download_report_pdf(
                            link_id=fx.link_id, user=staff, session=s
                        )
        assert excinfo.value.status_code == 403
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_explicit_true_serves_the_pdf_and_flags_the_serializer() -> None:
    """An explicit Yes opens the download: the route answers a real PDF and
    the on-screen payload advertises the enabled control."""
    from app.api import assessments as assessments_mod
    from app.core.db import superadmin_scope
    from app.models import Candidate

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    candidate = await s.get(Candidate, fx.cand_id)
                    candidate.retain_assessment_consent = True
                    candidate.retain_assessment_consented_at = datetime.now(
                        timezone.utc
                    )

        staff = _staff_user(fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    report_out = await assessments_mod.get_report(
                        link_id=fx.link_id, user=staff, session=s
                    )
                    assert report_out.report_download_allowed is True

                    response = await assessments_mod.download_report_pdf(
                        link_id=fx.link_id, user=staff, session=s
                    )
        assert response.media_type == "application/pdf"
        assert response.body.startswith(b"%PDF")
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()
