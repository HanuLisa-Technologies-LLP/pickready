"""The candidate-portal Projects endpoints, against a live database.

Follows the `test_portal.py` pattern: handlers are called directly with a
superadmin-scoped session, the object store and the broker are faked at the
module attribute (never `sys.modules`), and every test seeds and cleans its
own world. Skips when no database is reachable.
"""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from starlette.datastructures import Headers, UploadFile


def _upload(
    data: bytes = b"import flask\n",
    filename: str = "app.py",
    content_type: str = "text/x-python",
) -> UploadFile:
    return UploadFile(
        file=io.BytesIO(data),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


async def _factory_or_skip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 -- no DB reachable
        await engine.dispose()
        pytest.skip("no database reachable -- skipping project API test")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class _Fixture:
    def __init__(self) -> None:
        self.user_id = uuid.uuid4()
        self.email = f"projects-{uuid.uuid4().hex[:8]}@candidates.pickready.test"
        self.cand_id: uuid.UUID | None = None


async def _seed(factory, fx: _Fixture) -> None:
    from app.core.db import superadmin_scope
    from app.models import Candidate, Role, User
    from app.models.enums import UserStatus

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                s.add(
                    User(
                        id=fx.user_id, email=fx.email, role=Role.candidate,
                        tenant_id=None, full_name="Project Candidate",
                        status=UserStatus.active,
                    )
                )
                await s.flush()
                cand = Candidate(
                    id=uuid.uuid4(), email=fx.email, user_id=fx.user_id,
                    full_name="Project Candidate", consent_databank=False,
                )
                s.add(cand)
                await s.flush()
                fx.cand_id = cand.id


async def _cleanup(factory, fx: _Fixture) -> None:
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                if fx.cand_id:
                    await s.execute(
                        text("DELETE FROM candidates WHERE id = :c"),
                        {"c": str(fx.cand_id)},
                    )
                await s.execute(
                    text("DELETE FROM users WHERE id = :u"), {"u": str(fx.user_id)}
                )


def _user(fx: _Fixture):
    from app.api.deps import CurrentUser
    from app.core.security import AUDIENCE_CANDIDATE
    from app.models import Role

    return CurrentUser(
        user_id=fx.user_id, tenant_id=None, role=Role.candidate,
        audience=AUDIENCE_CANDIDATE,
    )


def _fake_staging(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.api import portal as portal_mod
    from app.services.projects import intake as project_intake

    async def fake_stage(project_id: str, validated):
        return [
            {"key": f"project-intake/{project_id}/{i:03d}-test", "filename": name}
            for i, (name, _ctype, _data) in enumerate(validated)
        ]

    monkeypatch.setattr(project_intake, "stage_intake", fake_stage)
    monkeypatch.setattr(portal_mod, "dispatch", lambda *a, **k: None)


async def test_add_list_and_delete_a_project(monkeypatch) -> None:
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope
    from app.models.project import CandidateProject
    from app.services import object_storage

    _fake_staging(monkeypatch)
    monkeypatch.setattr(object_storage, "delete", lambda key: None)

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        user = _user(fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    created = await portal_mod.add_project(
                        name="Garage System",
                        description="A Django garage management system with "
                        "vehicle history and inventory tracking.",
                        repository_url="https://github.com/example/garage",
                        files=[_upload()],
                        user=user,
                        session=s,
                    )
        assert created.submission_kind == "mixed"
        assert created.status == "submitted"
        assert created.files[0].supported is True

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    listing = await portal_mod.list_my_projects(user=user, session=s)
        assert len(listing.projects) == 1
        assert listing.limits.description_max_words == 100
        assert "not stored" in listing.retention_notice

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await portal_mod.delete_my_project(
                        created.id, user=user, session=s
                    )
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    remaining = (
                        await s.execute(
                            select(CandidateProject).where(
                                CandidateProject.candidate_id == fx.cand_id
                            )
                        )
                    ).scalars().all()
        assert remaining == []
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_the_description_word_limit_is_enforced_at_the_endpoint(
    monkeypatch,
) -> None:
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope

    _fake_staging(monkeypatch)
    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        user = _user(fx)
        long_description = " ".join(f"word{i}" for i in range(101))
        with pytest.raises(HTTPException) as excinfo:
            async with factory() as s:
                async with s.begin():
                    async with superadmin_scope(s):
                        await portal_mod.add_project(
                            name="Too Wordy",
                            description=long_description,
                            repository_url="https://github.com/example/x",
                            files=[],
                            user=user,
                            session=s,
                        )
        assert excinfo.value.status_code == 422
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_a_submission_with_neither_files_nor_repository_is_refused(
    monkeypatch,
) -> None:
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope

    _fake_staging(monkeypatch)
    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        user = _user(fx)
        with pytest.raises(HTTPException) as excinfo:
            async with factory() as s:
                async with s.begin():
                    async with superadmin_scope(s):
                        await portal_mod.add_project(
                            name="Empty",
                            description="Nothing attached.",
                            repository_url=None,
                            files=[],
                            user=user,
                            session=s,
                        )
        assert excinfo.value.status_code == 422
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_projects_are_optional_and_absent_reads_as_empty(monkeypatch) -> None:
    """A candidate with no projects gets an empty list and full limits, never
    an error and never a penalty marker."""
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope

    _fake_staging(monkeypatch)
    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        user = _user(fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    listing = await portal_mod.list_my_projects(user=user, session=s)
        assert listing.projects == []
        assert listing.limits.max_projects >= 1
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_a_crafted_repository_url_is_refused_at_submission(monkeypatch) -> None:
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope

    _fake_staging(monkeypatch)
    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        user = _user(fx)
        with pytest.raises(HTTPException) as excinfo:
            async with factory() as s:
                async with s.begin():
                    async with superadmin_scope(s):
                        await portal_mod.add_project(
                            name="Sneaky",
                            description="Repository with embedded credentials.",
                            repository_url="https://user:token@github.com/a/b",
                            files=[],
                            user=user,
                            session=s,
                        )
        assert excinfo.value.status_code == 422
        assert "public" in str(excinfo.value.detail).lower()
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_the_project_ceiling_is_enforced(monkeypatch) -> None:
    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope
    from app.services.projects import limits as project_limits_mod

    _fake_staging(monkeypatch)
    small = project_limits_mod.from_settings().__class__(
        **{
            **project_limits_mod.from_settings().__dict__,
            "max_projects_per_candidate": 1,
        }
    )
    monkeypatch.setattr(project_limits_mod, "from_settings", lambda: small)

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        user = _user(fx)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await portal_mod.add_project(
                        name="First",
                        description="The first project.",
                        repository_url="https://github.com/example/one",
                        files=[],
                        user=user,
                        session=s,
                    )
        with pytest.raises(HTTPException) as excinfo:
            async with factory() as s:
                async with s.begin():
                    async with superadmin_scope(s):
                        await portal_mod.add_project(
                            name="Second",
                            description="One too many.",
                            repository_url="https://github.com/example/two",
                            files=[],
                            user=user,
                            session=s,
                        )
        assert excinfo.value.status_code == 422
        assert "maximum" in str(excinfo.value.detail)
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()
