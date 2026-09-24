"""A REAL candidate session for tests: real rows, real Redis record, real cookies.

WHY THIS EXISTS
---------------
The BGV candidate routes were unreachable for every real candidate for weeks:
each one declared a STAFF principal (`get_current_user`) beside a CANDIDATE
session (`get_candidate_db`), and no token satisfies both. Every test passed,
because every test overrode BOTH dependencies and so never asked the question a
browser asks. A dependency override is a claim about who is calling; this
module replaces the claim with the thing itself.

What a caller gets is minted by the production path and nothing else:

* the `users` row and the candidate record, resolved through
  `candidate_identity.link_on_sign_in`, the same call every candidate sign-in
  makes;
* the session record, written by `auth._issue_session`, which is what
  `/auth/firebase/session` runs after a proven identity, so the access and
  refresh cookies carry a real `sid` backed by a real Redis hash.

So a request made with `cookies()` passes `get_current_candidate` and
`get_candidate_db` exactly as a browser's does, and fails them exactly as a
browser's would. Use it with NO dependency overrides; that is the point.

Requires the suite's declared infrastructure (Postgres and Redis). It does not
skip: a helper that quietly skipped would turn every test built on it back into
the untested claim it replaces.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api import auth
from app.api.deps import (
    ACCESS_COOKIE,
    ACTIVITY_HEADER,
    ACTIVITY_HEADER_VALUE,
    REFRESH_COOKIE,
    SESSION_HINT_COOKIE,
)
from app.core.db import superadmin_scope
from app.core.security import AUDIENCE_CANDIDATE, decode_token
from app.models.candidate import Candidate
from app.models.enums import Role, UserStatus
from app.models.user import User
from app.services import auth_sessions, candidate_identity
from app.services.firebase_auth import FirebaseIdentity


@dataclass(frozen=True)
class CandidateSession:
    """One signed-in candidate, as the server knows them."""

    user_id: uuid.UUID
    candidate_id: uuid.UUID
    email: str
    sid: str
    access: str
    refresh: str

    def cookies(self) -> dict[str, str]:
        """The three cookies a browser holds after signing in."""
        return {
            ACCESS_COOKIE: self.access,
            REFRESH_COOKIE: self.refresh,
            SESSION_HINT_COOKIE: "1",
        }

    def headers(self, *, activity: bool = False) -> dict[str, str]:
        """Request headers; `activity=True` marks the request as a person's
        interaction, which is the only thing that renews the idle deadline."""
        return {ACTIVITY_HEADER: ACTIVITY_HEADER_VALUE} if activity else {}


def _cookie(response: Response, name: str) -> str:
    for raw in response.headers.getlist("set-cookie"):
        if raw.startswith(name + "="):
            return raw.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"_issue_session set no {name} cookie")


async def open_session(user: User) -> tuple[str, str, str]:
    """(sid, access, refresh) for `user`, through the production minting path."""
    response = Response()
    await auth._issue_session(response, user, AUDIENCE_CANDIDATE)  # noqa: SLF001
    access = _cookie(response, ACCESS_COOKIE)
    refresh = _cookie(response, REFRESH_COOKIE)
    sid = decode_token(access, audience=AUDIENCE_CANDIDATE)["sid"]
    return sid, access, refresh


async def create_candidate_session(
    factory: async_sessionmaker[AsyncSession],
    *,
    email: str | None = None,
    email_verified: bool = True,
    full_name: str = "Session Candidate",
) -> CandidateSession:
    """A candidate user, their one candidate record and a live session.

    The record is resolved by `link_on_sign_in`, so an email that matches an
    unlinked record created beforehand is linked to it when `email_verified`,
    exactly as a real first sign-in would.
    """
    address = email or f"session-{uuid.uuid4().hex}@vivekium.test"
    identity = FirebaseIdentity(
        uid=f"fbuid-{uuid.uuid4().hex}",
        email=address,
        name=full_name,
        provider="password",
        email_verified=email_verified,
    )
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                user = User(
                    role=Role.candidate,
                    email=address,
                    full_name=full_name,
                    tenant_id=None,
                    status=UserStatus.active,
                    firebase_uid=identity.uid,
                    auth_providers=[identity.provider],
                    # What `auth._finalize_single` stamps for a verified
                    # identity; the apply path's rehome reads it.
                    email_verified_at=(
                        datetime.now(timezone.utc) if email_verified else None
                    ),
                )
                session.add(user)
                await session.flush()
                candidate = await candidate_identity.link_on_sign_in(
                    session, user, identity
                )
                user_id, candidate_id = user.id, candidate.id
    sid, access, refresh = await open_session(user)
    return CandidateSession(
        user_id=user_id,
        candidate_id=candidate_id,
        email=address,
        sid=sid,
        access=access,
        refresh=refresh,
    )


async def close_candidate_session(
    factory: async_sessionmaker[AsyncSession], candidate: CandidateSession
) -> None:
    """Revoke the session and delete the rows this helper created.

    Deletes only the candidate record the session owns and its user; anything a
    test hung off them is the test's to remove first.
    """
    await auth_sessions.revoke(candidate.sid, candidate.user_id)
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    delete(Candidate).where(Candidate.id == candidate.candidate_id)
                )
                await session.execute(delete(User).where(User.id == candidate.user_id))


async def candidate_ids_for_user(
    factory: async_sessionmaker[AsyncSession], user_id: uuid.UUID
) -> list[uuid.UUID]:
    """Every candidate record linked to `user_id`, read on a fresh session."""
    async with factory() as session:
        async with session.begin():
            async with superadmin_scope(session):
                return list(
                    (
                        await session.execute(
                            select(Candidate.id).where(Candidate.user_id == user_id)
                        )
                    ).scalars()
                )
