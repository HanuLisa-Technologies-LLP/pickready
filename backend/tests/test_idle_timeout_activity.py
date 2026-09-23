"""The thirty-minute idle deadline moves for a person, never for a timer.

THE DEFECT
----------
`auth_sessions.validate` ran `EXPIRE` on every authenticated request, and
`rotate` did the same on every refresh. A tab left open on a polling page
therefore kept its session alive for ever: the poll renewed the deadline, and
when the fifteen-minute access token lapsed the poll's 401 was repaired by a
refresh that renewed it again. "Thirty minutes idle" meant "thirty minutes with
the tab closed".

THE RULE
--------
Only a request carrying `X-User-Activity: 1` renews, and the browser sends it
only within a few seconds of a real pointer, key or touch event. Everything
here runs against REAL Redis through the REAL HTTP stack with NO dependency
overrides (`tests/candidate_session.py`), and reads the deadline straight off
the session key with `TTL`, because the question is what the server stored,
not what a response said.

Mutation check: restoring the unconditional `_TOUCH` in `validate` fails
`test_a_request_without_the_activity_header_does_not_renew`; dropping the
ARGV[8] test in `_ROTATE` fails `test_a_refresh_without_the_header_does_not_renew`.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import authenticate_socket_token
from app.core import cache
from app.core.config import get_settings
from app.core.security import AUDIENCE_CANDIDATE, AUDIENCE_ORG
from app.main import app
from app.services import auth_sessions
from tests.candidate_session import close_candidate_session, create_candidate_session

#: Low enough that a renewal to IDLE_SECONDS is unmistakable, high enough that
#: a slow request cannot run it out.
LOWERED_TTL = 120


@pytest.fixture
async def signed_in():
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    candidate = await create_candidate_session(factory)
    try:
        yield candidate
    finally:
        await close_candidate_session(factory, candidate)
        await engine.dispose()


async def _ttl(sid: str) -> int:
    client = cache._redis()  # noqa: SLF001 - the same client the store uses
    return await client.ttl(auth_sessions._key(sid))  # noqa: SLF001


async def _lower(sid: str) -> None:
    client = cache._redis()  # noqa: SLF001
    assert await client.expire(auth_sessions._key(sid), LOWERED_TTL)  # noqa: SLF001


def _get_me(candidate, *, activity: bool) -> int:
    with TestClient(app, cookies=candidate.cookies()) as http:
        return http.get(
            "/api/v1/auth/me", headers=candidate.headers(activity=activity)
        ).status_code


def _refresh(candidate, *, activity: bool) -> int:
    with TestClient(app, cookies=candidate.cookies()) as http:
        return http.post(
            "/api/v1/auth/refresh", headers=candidate.headers(activity=activity)
        ).status_code


async def test_a_request_without_the_activity_header_does_not_renew(signed_in) -> None:
    await _lower(signed_in.sid)
    assert _get_me(signed_in, activity=False) == 200
    assert await _ttl(signed_in.sid) <= LOWERED_TTL


async def test_a_request_with_the_activity_header_renews(signed_in) -> None:
    await _lower(signed_in.sid)
    assert _get_me(signed_in, activity=True) == 200
    assert await _ttl(signed_in.sid) > LOWERED_TTL
    assert await _ttl(signed_in.sid) <= auth_sessions.IDLE_SECONDS


async def test_a_refresh_without_the_header_does_not_renew(signed_in) -> None:
    """The poll's 401 is repaired by a refresh; the repair is not activity."""
    await _lower(signed_in.sid)
    assert _refresh(signed_in, activity=False) == 200
    assert await _ttl(signed_in.sid) <= LOWERED_TTL


async def test_a_refresh_with_the_header_renews(signed_in) -> None:
    await _lower(signed_in.sid)
    assert _refresh(signed_in, activity=True) == 200
    assert await _ttl(signed_in.sid) > LOWERED_TTL


async def test_an_expired_session_is_refused_whatever_the_header_says(signed_in) -> None:
    """The header renews a live session; it cannot resurrect a dead one."""
    client = cache._redis()  # noqa: SLF001
    await client.delete(auth_sessions._key(signed_in.sid))  # noqa: SLF001
    assert _get_me(signed_in, activity=True) == 401


async def test_the_socket_check_refuses_a_revoked_session_and_never_renews(
    signed_in,
) -> None:
    """The conversation socket used to decode the JWT and never ask the session
    store, so a signed-out tab kept streaming. The shared authenticator asks,
    and asks without touching the deadline."""
    await _lower(signed_in.sid)
    user = await authenticate_socket_token(signed_in.access, AUDIENCE_CANDIDATE)
    assert user is not None and user.user_id == signed_in.user_id
    assert await _ttl(signed_in.sid) <= LOWERED_TTL

    # A token for the wrong audience is refused before the store is asked.
    assert await authenticate_socket_token(signed_in.access, AUDIENCE_ORG) is None
    assert await authenticate_socket_token(None, AUDIENCE_CANDIDATE) is None

    await auth_sessions.revoke(signed_in.sid, signed_in.user_id)
    assert await authenticate_socket_token(signed_in.access, AUDIENCE_CANDIDATE) is None


async def test_the_socket_check_refuses_when_the_store_cannot_answer(
    signed_in, monkeypatch
) -> None:
    """Fail closed, the posture every REST route takes: a store outage refuses
    the socket rather than letting an unverifiable session stream."""
    monkeypatch.setattr(cache, "_redis", lambda: None)
    assert await authenticate_socket_token(signed_in.access, AUDIENCE_CANDIDATE) is None


async def test_validate_needs_an_explicit_decision() -> None:
    """No default for `touch`: a new caller must say whether it is activity."""
    with pytest.raises(TypeError):
        await auth_sessions.validate("sid", "user")  # type: ignore[call-arg]
