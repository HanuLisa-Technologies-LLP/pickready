"""Adding back a name you removed, over a real database.

THE BUG THIS PINS, MEASURED IN PILOT ON 2026-09-20
--------------------------------------------------
`remove_competency` is a SOFT delete (`is_active = False`, because a generated
candidate question may reference the row). `uq_job_competency_name` is on
(job_id, category, name) with no predicate. So a removed name still holds its
slot, invisibly, and inserting it again raised `UniqueViolationError`:

    POST /api/v2/assessments/jobs/<id>/framework/bulk  ->  500

A hiring manager cleared the six generated Must-have items and pasted their own
five. Two 500s, forty-five seconds apart, and the only thing the screen said
was "API error 500". The paste box is the product's normal way into this form.

THE BULK ROUTE HAD NO TEST OF ANY KIND, which is how it shipped. These run the
real routes against the real table with the real constraint, and read the
result back from a SECOND connection, because a 201 is not evidence that a row
committed.

The second defect pinned here is the one that made the screen misleading rather
than merely broken: with every item removed, `_framework_repair_pending` could
not tell "a human emptied this" from "the generator never landed", so it said
"We are still preparing the evaluation criteria for this role" and re-enqueued
Sutra, which would have restored the items the reviewer had just deleted.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import CurrentUser, get_current_user, get_tenant_db
from app.core.config import get_settings
from app.core.db import superadmin_scope, tenant_scope
from app.core.security import AUDIENCE_ORG
from app.main import app
from app.models.enums import Role
from app.services import ppi

BASE = "/api/v2/assessments/jobs"


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _sessions():
    return async_sessionmaker(
        create_async_engine(get_settings().database_url, poolclass=NullPool),
        expire_on_commit=False,
    )


async def _reachable() -> bool:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text("SELECT 1 FROM job_competencies LIMIT 0"))
        return True
    except Exception:  # noqa: BLE001 - no database here
        return False
    finally:
        await engine.dispose()


class World:
    def __init__(self) -> None:
        self.tenant = uuid.uuid4()
        self.user = uuid.uuid4()
        self.job = uuid.uuid4()


@pytest.fixture
def world() -> Iterator[World]:
    if not _run(_reachable()):
        pytest.skip("no database reachable")

    w = World()
    sessions = _sessions()

    async def _seed() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text(
                            "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                            "VALUES (:id, :name, :domain, 'pending')"
                        ),
                        {
                            "id": str(w.tenant),
                            "name": f"Matrix-{w.tenant.hex[:6]}",
                            "domain": f"{w.tenant.hex[:10]}.matrix.test",
                        },
                    )
                    await session.execute(
                        sa.text(
                            "INSERT INTO users (id, tenant_id, email, full_name, "
                            " role, status) VALUES (:id, :tid, :email, "
                            " 'Anita Rao', 'client', 'active')"
                        ),
                        {
                            "id": str(w.user),
                            "tid": str(w.tenant),
                            "email": f"{w.user.hex[:10]}@matrix.test",
                        },
                    )
                    await session.execute(
                        sa.text(
                            "INSERT INTO jobs (id, tenant_id, title, jd_json, "
                            " jd_markdown, status, lifecycle_state) "
                            "VALUES (:id, :tid, 'Platform Engineer', "
                            " CAST('{}' AS jsonb), '## The role', 'draft', 'DRAFT')"
                        ),
                        {"id": str(w.job), "tid": str(w.tenant)},
                    )

    async def _teardown() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text("DELETE FROM tenants WHERE id = :id"),
                        {"id": str(w.tenant)},
                    )

    _run(_seed())
    try:
        yield w
    finally:
        _run(_teardown())


@pytest.fixture
def client(world: World) -> Iterator[TestClient]:
    sessions = _sessions()

    async def _current_user() -> CurrentUser:
        return CurrentUser(
            user_id=world.user,
            tenant_id=world.tenant,
            role=Role.client,
            audience=AUDIENCE_ORG,
        )

    async def _tenant_db():
        async with sessions() as session:
            async with session.begin():
                async with tenant_scope(session, world.tenant):
                    yield session

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = _current_user
    app.dependency_overrides[get_tenant_db] = _tenant_db
    try:
        with TestClient(app) as http:
            yield http
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def _committed(query: str, params: dict) -> list[dict]:
    """Rows as a SECOND connection sees them, after the request is over."""

    async def _read():
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    result = await session.execute(sa.text(query), params)
                    return [dict(row) for row in result.mappings()]

    return _run(_read())


def _stored(job: uuid.UUID) -> list[dict]:
    return _committed(
        "SELECT name, category, is_active, ordinal, required_level "
        "FROM job_competencies WHERE job_id = :jid ORDER BY category, ordinal",
        {"jid": str(job)},
    )


def _add_bulk(
    client: TestClient, job: uuid.UUID, names: list[str], *, level: str = "Matching"
):
    return client.post(
        f"{BASE}/{job}/framework/bulk",
        json={"category": "must_have", "names": names, "required_level": level},
    )


# -- The 500 -----------------------------------------------------------------


def test_pasting_a_list_of_new_names_stores_them(
    client: TestClient, world: World
) -> None:
    response = _add_bulk(client, world.job, ["Python", "SQL", "DSA"])
    assert response.status_code == 201, response.text
    assert [row["name"] for row in response.json()] == ["Python", "SQL", "DSA"]

    stored = _stored(world.job)
    assert [(row["name"], row["is_active"]) for row in stored] == [
        ("Python", True),
        ("SQL", True),
        ("DSA", True),
    ]


def test_a_pasted_name_that_was_deleted_comes_back_instead_of_500ing(
    client: TestClient, world: World
) -> None:
    """The exact pilot sequence: add, delete, paste the same name again."""
    first = _add_bulk(client, world.job, ["Python", "SQL"])
    assert first.status_code == 201, first.text
    removed = first.json()[0]["id"]

    assert client.delete(f"{BASE}/{world.job}/framework/{removed}").status_code == 204
    # Soft: the row is still there holding the unique slot, invisibly.
    assert [(row["name"], row["is_active"]) for row in _stored(world.job)] == [
        ("Python", False),
        ("SQL", True),
    ]

    again = _add_bulk(client, world.job, ["Python", "Agentic AI"])
    assert again.status_code == 201, again.text

    stored = {row["name"]: row for row in _stored(world.job)}
    assert stored["Python"]["is_active"] is True
    assert stored["Agentic AI"]["is_active"] is True
    # Revived, not duplicated: one row per name, which is what the constraint
    # was protecting all along.
    assert len(_stored(world.job)) == 3


def test_a_single_add_of_a_deleted_name_comes_back_too(
    client: TestClient, world: World
) -> None:
    created = client.post(
        f"{BASE}/{world.job}/framework",
        json={
            "category": "must_have",
            "name": "Kafka",
            "description": None,
            "required_level": "Matching",
        },
    )
    assert created.status_code == 201, created.text
    assert (
        client.delete(
            f"{BASE}/{world.job}/framework/{created.json()['id']}"
        ).status_code
        == 204
    )

    again = client.post(
        f"{BASE}/{world.job}/framework",
        json={
            "category": "must_have",
            "name": "Kafka",
            "description": None,
            "required_level": "Highly Matching",
        },
    )
    assert again.status_code == 201, again.text
    stored = _stored(world.job)
    assert len(stored) == 1
    assert stored[0]["is_active"] is True
    # Re-adding it at a different level is the level it comes back at.
    assert stored[0]["required_level"] == ppi.required_level_score("Highly Matching")


def test_a_paste_containing_a_name_already_present_keeps_the_rest(
    client: TestClient, world: World
) -> None:
    """Twenty-eight new skills must not be thrown away by two duplicates."""
    assert (
        _add_bulk(client, world.job, ["Python"], level="Highly Matching").status_code
        == 201
    )

    response = _add_bulk(client, world.job, ["Python", "SQL", "DSA"], level="Matching")
    assert response.status_code == 201, response.text
    assert [row["name"] for row in response.json()] == ["Python", "SQL", "DSA"]

    stored = {row["name"]: row for row in _stored(world.job)}
    assert set(stored) == {"Python", "SQL", "DSA"}
    # The existing entry keeps the level the reviewer chose for it: a paste
    # must not quietly restate a criterion that was already set.
    assert stored["Python"]["required_level"] == ppi.required_level_score(
        "Highly Matching"
    )
    assert stored["SQL"]["required_level"] == ppi.required_level_score("Matching")


def test_renaming_onto_a_deleted_name_is_refused_with_the_reason(
    client: TestClient, world: World
) -> None:
    created = _add_bulk(client, world.job, ["Python", "SQL"])
    rows = created.json()
    assert (
        client.delete(f"{BASE}/{world.job}/framework/{rows[0]['id']}").status_code
        == 204
    )

    clash = client.put(
        f"{BASE}/{world.job}/framework/{rows[1]['id']}",
        json={
            "category": "must_have",
            "name": "Python",
            "description": None,
            "required_level": "Matching",
        },
    )
    assert clash.status_code == 409, clash.text
    assert "Python" in clash.json()["detail"]


# -- The misleading message --------------------------------------------------


def test_a_matrix_the_reviewer_emptied_does_not_claim_to_be_generating(
    client: TestClient, world: World
) -> None:
    created = _add_bulk(client, world.job, ["Python", "SQL"])
    for row in created.json():
        assert (
            client.delete(f"{BASE}/{world.job}/framework/{row['id']}").status_code
            == 204
        )

    framework = client.get(f"{BASE}/{world.job}/framework")
    assert framework.status_code == 200, framework.text
    body = framework.json()
    assert body["competencies"] == []
    reason = body["blocking_reason"] or ""
    assert "still preparing" not in reason
    # What they are told instead is the blocker they can actually clear.
    assert "Must-have" in reason

    setup = client.get(f"{BASE}/{world.job}/setup")
    assert setup.status_code == 200, setup.text
    assert setup.json()["framework_pending"] is False
