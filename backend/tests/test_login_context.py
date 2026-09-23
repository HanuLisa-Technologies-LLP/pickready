"""The workspace chooser: who a proven identity may sign in as, and how once.

`services/login_context` was extracted from the retired code-login service on
2026-09-24. The pure resolution and context-token cases below are the ones
that used to live in the code-login tests and are ported unchanged, because
the behaviour is unchanged. Two behaviours DID change, and each is pinned here
in the direction that would catch the old code:

* A `client` whose phone was never verified used to be answered
  `pending_channels` and no session at the chooser, a state no screen handled.
  Mutation check: restore the pending gate and
  `test_a_client_with_no_verified_phone_is_signed_in_not_stranded` fails.
* The single-use flag used to fall back to per-process memory when Redis
  failed, which made a token replayable across API tasks. Mutation check:
  restore a memory fallback and `test_redis_unavailable_refuses_rather_than_replaying`
  fails.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import cache, security
from app.core.config import get_settings
from app.models.enums import Role, UserStatus
from app.models.tenant import Tenant
from app.models.user import User
from app.services import login_context
from app.services.login_context import (
    PORTAL_CANDIDATE,
    PORTAL_ORG,
    PORTAL_OWNER,
    ContextStoreUnavailable,
    ContextTokenConsumed,
    ContextTokenInvalid,
    ContextUserMismatch,
    UserNotFound,
    decode_context_token,
    eligible_login_users,
    make_context_token,
    portal_for_role,
    resolve_login,
)

OWNER_EMAIL = "owner@vivekium.test"


def make_user(
    role: Role = Role.recruiter,
    email: str = "user@example.com",
    status: UserStatus = UserStatus.active,
    tenant_id: uuid.UUID | None = None,
) -> User:
    u = User(role=role, email=email, status=status, tenant_id=tenant_id)
    u.id = uuid.uuid4()  # normally DB-assigned; needed for identity checks
    return u


# ── Pure resolution (ported) ─────────────────────────────────────────────────


def test_portal_mapping() -> None:
    assert portal_for_role(Role.super_admin) == PORTAL_OWNER
    assert portal_for_role(Role.candidate) == PORTAL_CANDIDATE
    for role in (Role.client, Role.hr_manager, Role.recruiter, Role.hiring_manager):
        assert portal_for_role(role) == PORTAL_ORG


def test_single_user_resolves_to_session_shape() -> None:
    user = make_user()
    resolution = resolve_login([user], owner_email=OWNER_EMAIL)
    assert resolution.user is user
    assert resolution.is_multi is False
    assert resolution.contexts == []


def test_multiple_users_resolve_to_contexts_shape() -> None:
    a = make_user(role=Role.recruiter, tenant_id=uuid.uuid4())
    b = make_user(role=Role.hr_manager, tenant_id=uuid.uuid4())
    resolution = resolve_login([a, b], owner_email=OWNER_EMAIL)
    assert resolution.user is None
    assert resolution.is_multi is True
    assert resolution.contexts == [a, b]


def test_disabled_users_are_filtered_out() -> None:
    active = make_user()
    disabled = make_user(status=UserStatus.disabled)
    assert resolve_login([active, disabled], owner_email=OWNER_EMAIL).user is active


def test_nobody_eligible_raises_user_not_found() -> None:
    with pytest.raises(UserNotFound):
        resolve_login([make_user(status=UserStatus.disabled)], owner_email=OWNER_EMAIL)
    with pytest.raises(UserNotFound):
        resolve_login([], owner_email=OWNER_EMAIL)


def test_invited_users_are_eligible() -> None:
    invited = make_user(status=UserStatus.invited)
    assert eligible_login_users([invited], owner_email=OWNER_EMAIL) == [invited]


def test_a_fake_owner_is_treated_as_nonexistent() -> None:
    impostor = make_user(role=Role.super_admin, email="evil@example.com")
    assert eligible_login_users([impostor], owner_email=OWNER_EMAIL) == []
    org = make_user(role=Role.client, tenant_id=uuid.uuid4())
    assert resolve_login([impostor, org], owner_email=OWNER_EMAIL).user is org


def test_the_real_owner_is_eligible_case_insensitively() -> None:
    owner = make_user(role=Role.super_admin, email="Owner@Vivekium.TEST")
    assert eligible_login_users([owner], owner_email=OWNER_EMAIL) == [owner]


# ── Context tokens (ported) ──────────────────────────────────────────────────


def test_context_token_roundtrip() -> None:
    ids = [uuid.uuid4(), uuid.uuid4()]
    payload = decode_context_token(make_context_token("who@example.com", ids))
    assert payload["type"] == "context"
    assert payload["identifier"] == "who@example.com"
    assert payload["user_ids"] == [str(i) for i in ids]
    assert payload["jti"]


def test_context_token_jti_is_unique_per_token() -> None:
    a = decode_context_token(make_context_token("x@y.com", [uuid.uuid4()]))
    b = decode_context_token(make_context_token("x@y.com", [uuid.uuid4()]))
    assert a["jti"] != b["jti"]


def test_an_expired_or_tampered_context_token_is_rejected() -> None:
    stale = datetime.now(timezone.utc) - timedelta(minutes=30)
    with pytest.raises(ContextTokenInvalid):
        decode_context_token(make_context_token("x@y.com", [uuid.uuid4()], now=stale))
    token = make_context_token("x@y.com", [uuid.uuid4()])
    with pytest.raises(ContextTokenInvalid):
        decode_context_token(token[:-4] + "AAAA")


def test_an_access_token_is_not_a_context_token() -> None:
    access = security.create_access_token(uuid.uuid4(), "client", uuid.uuid4())
    with pytest.raises(ContextTokenInvalid):
        decode_context_token(access)


def test_the_retired_dual_channel_gate_is_gone() -> None:
    assert not hasattr(login_context, "pending_channels_for")
    assert "pending_channels" not in login_context.SelectionResult.__dataclass_fields__


# ── select_context against the real tables ───────────────────────────────────


async def _db_or_skip():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except OSError:
        await engine.dispose()
        pytest.skip("no database reachable at DATABASE_URL")
    return engine


async def _redis_or_skip() -> None:
    client = cache._redis()  # noqa: SLF001
    if client is None:
        pytest.skip("no Redis client could be built for REDIS_URL")
    try:
        await client.ping()
    except (RedisError, OSError):
        pytest.skip("no Redis reachable at REDIS_URL")


@pytest.fixture
async def world():
    """Two workspaces for one email: an invited client with NO verified phone
    in tenant A, and a recruiter in tenant B. Removed afterwards."""
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    email = f"chooser-{uuid.uuid4().hex}@vivekium.test"
    ta, tb = uuid.uuid4(), uuid.uuid4()
    async with factory() as session:
        session.add(Tenant(id=ta, name="Chooser-A", domain=f"{ta}.chooser.test"))
        session.add(Tenant(id=tb, name="Chooser-B", domain=f"{tb}.chooser.test"))
        client = User(
            role=Role.client, email=email, tenant_id=ta,
            status=UserStatus.invited, phone_verified_at=None,
        )
        recruiter = User(
            role=Role.recruiter, email=email, tenant_id=tb, status=UserStatus.active
        )
        session.add_all([client, recruiter])
        await session.commit()
    token = make_context_token(email, [client.id, recruiter.id])
    try:
        yield factory, client.id, recruiter.id, token
    finally:
        async with factory() as session:
            for uid in (client.id, recruiter.id):
                await session.execute(User.__table__.delete().where(User.id == uid))
            for tid in (ta, tb):
                await session.execute(Tenant.__table__.delete().where(Tenant.id == tid))
            await session.commit()
        await engine.dispose()


async def test_a_client_with_no_verified_phone_is_signed_in_not_stranded(world) -> None:
    await _redis_or_skip()
    factory, client_id, _recruiter_id, token = world
    async with factory() as session:
        result = await login_context.select_context(
            session, context_token=token, user_id=client_id
        )
        await session.commit()
    assert result.user.id == client_id

    # Read back from a SECOND session: the activation was committed.
    async with factory() as session:
        stored = (
            await session.execute(select(User).where(User.id == client_id))
        ).scalar_one()
    assert stored.status == UserStatus.active
    assert stored.phone_verified_at is None, "selection proves nothing about a phone"


async def test_a_context_token_is_single_use_across_processes(world) -> None:
    """The flag lives in Redis, so a second API task cannot replay the token.
    A fresh client between the two calls stands in for the second process."""
    await _redis_or_skip()
    factory, _client_id, recruiter_id, token = world
    async with factory() as session:
        await login_context.select_context(
            session, context_token=token, user_id=recruiter_id
        )
        await session.commit()

    cache._CLIENT.reset_for_tests()  # noqa: SLF001 - a different process's client
    async with factory() as session:
        with pytest.raises(ContextTokenConsumed):
            await login_context.select_context(
                session, context_token=token, user_id=recruiter_id
            )


async def test_a_mismatched_pick_does_not_burn_the_token(world) -> None:
    await _redis_or_skip()
    factory, _client_id, recruiter_id, token = world
    async with factory() as session:
        with pytest.raises(ContextUserMismatch):
            await login_context.select_context(
                session, context_token=token, user_id=uuid.uuid4()
            )
    async with factory() as session:
        result = await login_context.select_context(
            session, context_token=token, user_id=recruiter_id
        )
    assert result.user.id == recruiter_id


async def test_redis_unavailable_refuses_rather_than_replaying(world, monkeypatch) -> None:
    factory, client_id, _recruiter_id, token = world
    monkeypatch.setattr(cache, "_redis", lambda: None)
    async with factory() as session:
        with pytest.raises(ContextStoreUnavailable):
            await login_context.select_context(
                session, context_token=token, user_id=client_id
            )
        await session.commit()
    async with factory() as session:
        stored = (
            await session.execute(select(User).where(User.id == client_id))
        ).scalar_one()
    assert stored.status == UserStatus.invited, "nothing is activated on a refusal"


async def test_the_route_answers_503_and_sets_no_cookie(world, monkeypatch) -> None:
    from fastapi import HTTPException, Response

    from app.api.auth import select_context as select_context_route
    from app.schemas.auth import SelectContextIn

    factory, client_id, _recruiter_id, token = world
    monkeypatch.setattr(cache, "_redis", lambda: None)
    response = Response()
    async with factory() as session:
        with pytest.raises(HTTPException) as excinfo:
            await select_context_route(
                SelectContextIn(context_token=token, user_id=client_id),
                response, session,
            )
    assert excinfo.value.status_code == 503
    assert not [k for k, _v in response.raw_headers if k == b"set-cookie"]


async def test_a_redis_error_mid_call_also_refuses(world, monkeypatch) -> None:
    from redis.exceptions import ConnectionError as RedisConnectionError

    class _Broken:
        async def set(self, *_args, **_kwargs):
            raise RedisConnectionError("down")

    factory, _client_id, recruiter_id, token = world
    monkeypatch.setattr(cache, "_redis", lambda: _Broken())
    async with factory() as session:
        with pytest.raises(ContextStoreUnavailable):
            await login_context.select_context(
                session, context_token=token, user_id=recruiter_id
            )
