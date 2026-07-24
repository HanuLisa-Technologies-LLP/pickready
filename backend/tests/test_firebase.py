"""Firebase-session endpoint hardening (claude.md rule 2: Firebase is
identity-only, DB roles/permissions stay authoritative).

Two layers:
- DB-free unit tests on the provider gate + owner-email predicate.
- Live integration against the app DB (skips cleanly when unreachable). We can't
  mint a real Firebase token in tests, so `firebase_auth.verify_id_token` is
  monkeypatched to return a chosen FirebaseIdentity; the endpoint logic (owner
  invariant, staff linking, multi-context chooser, provider gate, edge cases)
  is what is exercised.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.auth import firebase_session, select_context
from app.api.deps import ACCESS_COOKIE
from app.core.config import get_settings
from app.models.candidate import Candidate
from app.models.enums import Role, UserStatus
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.auth import FirebaseSessionIn, SelectContextIn
from app.services import firebase_auth
from app.services.firebase_auth import FirebaseIdentity, assert_provider_allowed
from fastapi import HTTPException, Response


# ── DB-free: provider gate (Google = candidates only) ────────────────────────

def _identity(provider: str = "password", email: str | None = "x@y.test",
              phone: str | None = None, uid: str | None = None,
              name: str | None = "Test User", email_verified: bool = True) -> FirebaseIdentity:
    return FirebaseIdentity(
        uid=uid or f"fbuid-{uuid.uuid4().hex}", email=email, phone=phone,
        name=name, provider=provider, email_verified=email_verified,
    )


def test_google_allowed_for_candidate() -> None:
    assert_provider_allowed(_identity(provider="google.com"), "candidate")  # no raise


@pytest.mark.parametrize("role", ["hr_manager", "recruiter", "hiring_manager", "client"])
def test_google_rejected_for_staff_roles(role: str) -> None:
    with pytest.raises(HTTPException) as exc:
        assert_provider_allowed(_identity(provider="google.com"), role)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("provider", ["password", "phone"])
@pytest.mark.parametrize("role", ["candidate", "hr_manager", "recruiter", "client"])
def test_password_and_phone_allowed_for_all_roles(provider: str, role: str) -> None:
    assert_provider_allowed(_identity(provider=provider), role)  # no raise


def test_unknown_provider_rejected() -> None:
    with pytest.raises(HTTPException) as exc:
        assert_provider_allowed(_identity(provider="apple.com"), "candidate")
    assert exc.value.status_code == 403


# ── Live integration (skips if the database is unreachable) ──────────────────

async def _db_or_skip():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 — any connect failure means "no DB here"
        await engine.dispose()
        pytest.skip("no database reachable — skipping firebase integration test")
    return engine


def _set_verify(monkeypatch, identity: FirebaseIdentity) -> None:
    """Patch the module attribute the endpoint looks up at call time."""
    monkeypatch.setattr(firebase_auth, "verify_id_token", lambda _tok: identity)


def _cookie_names(response: Response) -> list[str]:
    return [v.decode().split("=", 1)[0] for (k, v) in response.raw_headers
            if k == b"set-cookie"]


async def _call(session, identity, monkeypatch) -> tuple[Response, object]:
    _set_verify(monkeypatch, identity)
    response = Response()
    out = await firebase_session(FirebaseSessionIn(id_token="t" * 40), response, session)
    return response, out


async def _cleanup_users(factory, user_ids: list[uuid.UUID], tenant_ids: list[uuid.UUID] | None = None) -> None:
    async with factory() as session:
        for uid in user_ids:
            await session.execute(
                Candidate.__table__.delete().where(Candidate.user_id == uid)
            )
            await session.execute(User.__table__.delete().where(User.id == uid))
        for tid in (tenant_ids or []):
            await session.execute(Tenant.__table__.delete().where(Tenant.id == tid))
        await session.commit()


async def _get_or_create_owner(factory) -> uuid.UUID:
    owner_email = get_settings().owner_email
    async with factory() as session:
        owner = (await session.execute(
            select(User).where(User.role == Role.super_admin)
        )).scalars().first()
        if owner is None:
            owner = User(role=Role.super_admin, email=owner_email, tenant_id=None,
                         status=UserStatus.active, full_name="Owner")
            session.add(owner)
            await session.commit()
        return owner.id


# ── 1. OWNER INVARIANT ───────────────────────────────────────────────────────

async def test_owner_google_login_yields_super_admin_with_wildcard() -> None:
    """A Firebase login as the owner email resolves to the seeded super_admin
    (never a candidate) and gets capabilities ["*"] — even via Google, which is
    otherwise candidates-only."""
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    owner_id = await _get_or_create_owner(factory)
    monkeypatch = pytest.MonkeyPatch()
    try:
        ident = _identity(provider="google.com", email=get_settings().owner_email.upper())
        async with factory() as session:
            response, out = await _call(session, ident, monkeypatch)
        assert out.user is not None
        assert out.user.role == Role.super_admin
        assert out.user.id == owner_id
        assert out.capabilities == ["*"]
        assert ACCESS_COOKIE in _cookie_names(response)
        # No impostor super_admin was created.
        async with factory() as session:
            admins = (await session.execute(
                select(User).where(User.role == Role.super_admin)
            )).scalars().all()
            assert len(admins) == 1
    finally:
        monkeypatch.undo()
        await engine.dispose()


async def test_random_email_never_becomes_super_admin() -> None:
    """An unknown identity is created as a candidate, never elevated to owner."""
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch = pytest.MonkeyPatch()
    email = f"random-{uuid.uuid4().hex}@pickready.test"
    created: list[uuid.UUID] = []
    try:
        ident = _identity(provider="google.com", email=email)
        async with factory() as session:
            _, out = await _call(session, ident, monkeypatch)
        assert out.user is not None
        assert out.user.role == Role.candidate
        created.append(out.user.id)
        assert out.capabilities == []  # candidates carry no org capabilities
    finally:
        monkeypatch.undo()
        await _cleanup_users(factory, created)
        await engine.dispose()


# ── 2. STAFF LINKING BY EMAIL ────────────────────────────────────────────────

async def test_staff_first_login_links_uid_and_preserves_role() -> None:
    """A pre-seeded staff user (no firebase_uid) is found by email and LINKED,
    not duplicated; the role is preserved and org cookies are issued."""
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch = pytest.MonkeyPatch()
    email = f"staff-{uuid.uuid4().hex}@pickready.test"
    tid = uuid.uuid4()
    created: list[uuid.UUID] = []
    try:
        async with factory() as session:
            session.add(Tenant(id=tid, name="FB-Staff-Test", domain=f"{tid}.fb.test"))
            staff = User(role=Role.hr_manager, email=email, tenant_id=tid,
                         status=UserStatus.invited)
            session.add(staff)
            await session.commit()
            created.append(staff.id)

        ident = _identity(provider="password", email=email)
        async with factory() as session:
            response, out = await _call(session, ident, monkeypatch)
        assert out.user is not None
        assert out.user.role == Role.hr_manager
        assert out.user.id == created[0]
        assert ACCESS_COOKIE in _cookie_names(response)

        async with factory() as session:
            rows = (await session.execute(
                select(User).where(User.email == email)
            )).scalars().all()
            assert len(rows) == 1  # linked in place, no duplicate candidate
            assert rows[0].firebase_uid == ident.uid
            assert rows[0].status == UserStatus.active  # invited -> active
            assert ident.provider in rows[0].auth_providers
    finally:
        monkeypatch.undo()
        await _cleanup_users(factory, created, [tid])
        await engine.dispose()


# ── 3. MULTI-CONTEXT CHOOSER ─────────────────────────────────────────────────

async def test_multi_context_returns_chooser_then_select_issues_cookies() -> None:
    """One identity matching two users returns contexts + context_token and NO
    cookies; /auth/select-context then exchanges the token for cookies."""
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch = pytest.MonkeyPatch()
    email = f"multi-{uuid.uuid4().hex}@pickready.test"
    ta, tb = uuid.uuid4(), uuid.uuid4()
    created: list[uuid.UUID] = []
    try:
        async with factory() as session:
            session.add(Tenant(id=ta, name="FB-Multi-A", domain=f"{ta}.fb.test"))
            session.add(Tenant(id=tb, name="FB-Multi-B", domain=f"{tb}.fb.test"))
            u1 = User(role=Role.recruiter, email=email, tenant_id=ta, status=UserStatus.active)
            u2 = User(role=Role.hr_manager, email=email, tenant_id=tb, status=UserStatus.active)
            session.add_all([u1, u2])
            await session.commit()
            created.extend([u1.id, u2.id])
            pick = u1.id

        ident = _identity(provider="password", email=email)
        async with factory() as session:
            response, out = await _call(session, ident, monkeypatch)
        assert out.user is None
        assert out.context_token is not None
        assert len(out.contexts) == 2
        assert ACCESS_COOKIE not in _cookie_names(response)  # no cookies yet

        # Exchange the proof token for a real session as one of the two users.
        async with factory() as session:
            sel_response = Response()
            sel = await select_context(
                SelectContextIn(context_token=out.context_token, user_id=pick),
                sel_response, session,
            )
        assert sel.user is not None
        assert sel.user.id == pick
        assert ACCESS_COOKIE in _cookie_names(sel_response)
    finally:
        monkeypatch.undo()
        await _cleanup_users(factory, created, [ta, tb])
        await engine.dispose()


# ── 4. PROVIDER GATE + EDGE CASES ────────────────────────────────────────────

async def test_phone_only_candidate_signup_allowed() -> None:
    """A phone-provider identity with no email creates a candidate with a phone
    and a NULL email (email is optional for phone signups)."""
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch = pytest.MonkeyPatch()
    phone = f"91{uuid.uuid4().int % 10**9:09d}"
    created: list[uuid.UUID] = []
    try:
        ident = _identity(provider="phone", email=None, phone=phone,
                          name=None, email_verified=False)
        async with factory() as session:
            response, out = await _call(session, ident, monkeypatch)
        assert out.user is not None
        assert out.user.role == Role.candidate
        assert out.user.email is None
        created.append(out.user.id)
        assert ACCESS_COOKIE in _cookie_names(response)
        async with factory() as session:
            cand = (await session.execute(
                select(Candidate).where(Candidate.user_id == created[0])
            )).scalars().first()
            assert cand is not None
            assert cand.phone == phone
    finally:
        monkeypatch.undo()
        await _cleanup_users(factory, created)
        await engine.dispose()


async def test_no_email_and_no_phone_is_422() -> None:
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch = pytest.MonkeyPatch()
    try:
        ident = _identity(provider="password", email=None, phone=None, name=None)
        async with factory() as session:
            with pytest.raises(HTTPException) as exc:
                await _call(session, ident, monkeypatch)
        assert exc.value.status_code == 422
    finally:
        monkeypatch.undo()
        await engine.dispose()


async def test_disabled_user_gets_403() -> None:
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch = pytest.MonkeyPatch()
    email = f"disabled-{uuid.uuid4().hex}@pickready.test"
    created: list[uuid.UUID] = []
    try:
        async with factory() as session:
            u = User(role=Role.candidate, email=email, tenant_id=None,
                     status=UserStatus.disabled)
            session.add(u)
            await session.commit()
            created.append(u.id)

        ident = _identity(provider="password", email=email)
        async with factory() as session:
            with pytest.raises(HTTPException) as exc:
                await _call(session, ident, monkeypatch)
        assert exc.value.status_code == 403
        # The disabled row must NOT have been duplicated by a fresh candidate.
        async with factory() as session:
            rows = (await session.execute(
                select(User).where(User.email == email)
            )).scalars().all()
            assert len(rows) == 1
    finally:
        monkeypatch.undo()
        await _cleanup_users(factory, created)
        await engine.dispose()


async def test_google_staff_login_is_403() -> None:
    """Staff exist but signed in with Google (candidates-only) -> 403, no link."""
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch = pytest.MonkeyPatch()
    email = f"gstaff-{uuid.uuid4().hex}@pickready.test"
    tid = uuid.uuid4()
    created: list[uuid.UUID] = []
    try:
        async with factory() as session:
            session.add(Tenant(id=tid, name="FB-GStaff", domain=f"{tid}.fb.test"))
            u = User(role=Role.recruiter, email=email, tenant_id=tid, status=UserStatus.active)
            session.add(u)
            await session.commit()
            created.append(u.id)

        ident = _identity(provider="google.com", email=email)
        async with factory() as session:
            with pytest.raises(HTTPException) as exc:
                await _call(session, ident, monkeypatch)
        assert exc.value.status_code == 403
    finally:
        monkeypatch.undo()
        await _cleanup_users(factory, created, [tid])
        await engine.dispose()


async def test_email_verified_is_stamped() -> None:
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch = pytest.MonkeyPatch()
    email = f"verif-{uuid.uuid4().hex}@pickready.test"
    created: list[uuid.UUID] = []
    try:
        ident = _identity(provider="password", email=email, email_verified=True)
        async with factory() as session:
            _, out = await _call(session, ident, monkeypatch)
        created.append(out.user.id)
        assert out.user.email_verified is True
    finally:
        monkeypatch.undo()
        await _cleanup_users(factory, created)
        await engine.dispose()
