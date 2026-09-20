"""Session lifecycle with a shared in-memory stand-in for Redis.

The store is shared across requests and tabs; route tests exercise real signed
JWTs and cookies without requiring a running Redis server for a focused run.
"""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import Response
from starlette.requests import Request

from app.api import auth
from app.api.deps import ACCESS_COOKIE, REFRESH_COOKIE, _authenticated_user
from app.core.security import AUDIENCE_ORG
from app.core.security import decode_token
from app.models.enums import Role, UserStatus
from app.schemas.auth import FirebaseSessionIn
from app.services import auth_sessions


class Store:
    def __init__(self):
        self.now = 1_800_000_000
        self.hashes = {}
        self.sets = {}
        self.deadlines = {}

    def _alive(self, key):
        if self.deadlines.get(key, float("inf")) <= self.now:
            self.hashes.pop(key, None)
            self.sets.pop(key, None)
        return key in self.hashes or key in self.sets

    def pipeline(self, transaction=True):
        return Pipeline(self)

    async def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update(mapping)

    async def expire(self, key, ttl):
        self.deadlines[key] = self.now + int(ttl)

    async def sadd(self, key, value):
        self.sets.setdefault(key, set()).add(value)

    async def srem(self, key, value):
        self.sets.setdefault(key, set()).discard(value)

    async def smembers(self, key):
        self._alive(key)
        return self.sets.get(key, set()).copy()

    async def delete(self, key):
        self.hashes.pop(key, None)
        self.sets.pop(key, None)

    async def eval(self, script, count, session_key, index_key, *args):
        self._alive(session_key)
        record = self.hashes.get(session_key)
        if not record or record["user"] != args[0]:
            return None if script == auth_sessions._ROTATE else 0
        if script == auth_sessions._TOUCH:
            await self.expire(session_key, args[1])
            await self.expire(index_key, args[1])
            return 1
        _, old_jti, new_jti, new_token, previous_until, now, ttl = args
        if record["jti"] == old_jti:
            record.update(jti=new_jti, token=new_token,
                          previous_jti=old_jti, previous_until=str(previous_until))
        elif record["previous_jti"] != old_jti or int(record["previous_until"]) < now:
            return None
        await self.expire(session_key, ttl)
        await self.expire(index_key, ttl)
        return record["token"]


class Pipeline:
    def __init__(self, store):
        self.store = store
        self.operations = []

    def __getattr__(self, name):
        def enqueue(*args, **kwargs):
            self.operations.append((name, args, kwargs))
            return self
        return enqueue

    async def execute(self):
        for name, args, kwargs in self.operations:
            await getattr(self.store, name)(*args, **kwargs)


def request_with_cookies(**cookies):
    value = "; ".join(f"{key}={token}" for key, token in cookies.items())
    return Request({"type": "http", "headers": [(b"cookie", value.encode())]})


def cookie(response, name):
    for raw in response.headers.getlist("set-cookie"):
        if raw.startswith(name + "="):
            return raw.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"missing {name}")


@pytest.fixture
def store(monkeypatch):
    state = Store()
    monkeypatch.setattr(auth_sessions, "_client", lambda: state)
    monkeypatch.setattr(auth_sessions.time, "time", lambda: state.now)
    return state


@pytest.fixture
def user():
    return SimpleNamespace(
        id=uuid.uuid4(), role=Role.recruiter, tenant_id=uuid.uuid4(),
        status=UserStatus.active,
    )


@pytest.mark.asyncio
async def test_idle_timeout_is_server_side_and_activity_slides_it(store, user):
    response = Response()
    await auth._issue_session(response, user, AUDIENCE_ORG)
    access = cookie(response, ACCESS_COOKIE)
    request = request_with_cookies(**{ACCESS_COOKIE: access})
    assert (await _authenticated_user(request, access, AUDIENCE_ORG)).user_id == user.id
    sid = decode_token(access, AUDIENCE_ORG)["sid"]
    store.now += 29 * 60
    assert await auth_sessions.validate(sid, user.id)
    store.now += 30 * 60 + 1
    assert not await auth_sessions.validate(sid, user.id)


@pytest.mark.asyncio
async def test_two_tabs_refresh_one_browser_session_without_stranding_either(store, user):
    response = Response()
    await auth._issue_session(response, user, AUDIENCE_ORG)
    old_refresh = cookie(response, REFRESH_COOKIE)
    session = SimpleNamespace(get=lambda _model, _id: _async_user(user))
    first = Response()
    second = Response()
    request = request_with_cookies(**{REFRESH_COOKIE: old_refresh})
    assert await auth.refresh(request, first, session) == {"refreshed": True}
    assert await auth.refresh(request, second, session) == {"refreshed": True}
    assert cookie(first, REFRESH_COOKIE) == cookie(second, REFRESH_COOKIE)
    store.now += auth_sessions.ROTATION_GRACE_SECONDS + 1
    denied = await auth.refresh(request, Response(), session)
    assert denied.status_code == 401


async def _async_user(user):
    return user


@pytest.mark.asyncio
async def test_logout_and_password_change_revoke_server_records(store, user, monkeypatch):
    first = Response()
    second = Response()
    await auth._issue_session(first, user, AUDIENCE_ORG)
    await auth._issue_session(second, user, AUDIENCE_ORG)
    first_access = cookie(first, ACCESS_COOKIE)
    second_access = cookie(second, ACCESS_COOKIE)
    await auth.logout(request_with_cookies(**{ACCESS_COOKIE: first_access}), Response())
    with pytest.raises(Exception, match="Session expired or revoked"):
        await _authenticated_user(
            request_with_cookies(**{ACCESS_COOKIE: first_access}), first_access, AUDIENCE_ORG,
        )
    assert (await _authenticated_user(
        request_with_cookies(**{ACCESS_COOKIE: second_access}), second_access, AUDIENCE_ORG,
    )).user_id == user.id
    monkeypatch.setattr(auth.firebase_auth, "verify_id_token", lambda _: SimpleNamespace(uid="firebase-user"))
    result = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: [user.id]))
    session = SimpleNamespace(execute=lambda _: _async_user(result))
    assert await auth.password_changed(
        FirebaseSessionIn(id_token="x" * 40), Response(), session,
    ) == {"sessions_revoked": True}
    with pytest.raises(Exception, match="Session expired or revoked"):
        await _authenticated_user(
            request_with_cookies(**{ACCESS_COOKIE: second_access}), second_access, AUDIENCE_ORG,
        )
