"""Regressions for permission changes that did not reach the people they were
made for (reported 2026-09-12).

Three defects, one complaint. An administrator granted a Recruiter
`edit_company_profile`, the permission screen reported "Allowed · granted to
this person", and the Recruiter's Company Profile page kept saying
"You have read-only access". Separately, that Recruiter had been signed in and
working for weeks while the staff table still reported their invitation as
"Pending".

  1. `PUT /admin/permissions` wrote the role matrix and never invalidated the
     120-second cache in front of it, so the new matrix did not apply until
     the TTL happened to lapse.
  2. `POST /admin/tenants` called the invalidation that belonged to (1), on a
     request body that has neither of the two attributes it reads — so every
     customer creation raised AttributeError and rolled back.
  3. Nothing but the token endpoint ever wrote `staff_invites.accepted_at`, so
     an invitee who signed in directly showed Active and Pending at once.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import admin as admin_api
from app.core.config import get_settings
from app.models.enums import Role, UserStatus
from app.models.invite import StaffInvite, invite_expiry, hash_invite_token
from app.models.tenant import Tenant
from app.models.user import User
from app.schemas.admin import TenantCreateIn
from app.services import staff_invites


async def _db_or_skip():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 — any connect failure means "no DB here"
        await engine.dispose()
        pytest.skip("no database reachable")
    return engine


# ── 1. The role matrix invalidates the cache it is read through ──────────────

def test_update_permissions_invalidates_the_role_permission_cache() -> None:
    """`rbac._permission_rows` caches each (tenant, role) row set for 120s.

    Without an invalidation here, an administrator toggles a capability,
    reloads, and sees the PREVIOUS matrix for up to two minutes — which is
    indistinguishable, from the chair, from the grant not having worked.
    """
    import inspect

    source = inspect.getsource(admin_api.update_permissions)
    assert "invalidate_role_permissions" in source, (
        "update_permissions must bust the cache its own writes are read through"
    )


def test_update_permissions_clears_every_tenant_for_a_global_edit() -> None:
    """A global-template edit reaches every tenant's cache ENTRY.

    Each entry holds that tenant's rows AND the global rows together
    (`rbac._permission_rows` selects both), so clearing one tenant's key would
    leave every other tenant reading the superseded template.
    """
    import inspect

    source = inspect.getsource(admin_api.update_permissions)
    assert "if body.tenant_id is None" in source
    assert "invalidate_role_permissions(None)" in source


# ── 2. Creating a customer does not crash ────────────────────────────────────

def test_tenant_create_payload_has_no_permission_fields() -> None:
    """The shape that made the crash possible, pinned.

    `create_tenant` read `body.tenant_id` and `body.entries` off THIS model.
    Neither exists, so the handler raised AttributeError on every call and no
    customer could be onboarded at all.
    """
    fields = set(TenantCreateIn.model_fields)
    assert "tenant_id" not in fields
    assert "entries" not in fields


def test_create_tenant_does_not_read_permission_fields_off_its_body() -> None:
    import inspect

    source = inspect.getsource(admin_api.create_tenant)
    assert "body.tenant_id" not in source
    assert "body.entries" not in source


async def test_create_tenant_seeds_a_usable_customer() -> None:
    """End to end: the handler runs, and the company's own owner comes out
    holding `edit_company_profile` rather than a rolled-back transaction."""
    from app.services import capabilities as caps
    from app.services import rbac

    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    name = f"Perm-Prop-{uuid.uuid4().hex[:10]}"
    created_tenants: list[uuid.UUID] = []
    try:
        body = TenantCreateIn(
            name=name,
            client_email=f"owner-{uuid.uuid4().hex[:8]}@example.com",
            industry="Technology",
            culture=" ".join(["culture"] * 120),
        )
        async with factory() as session:
            from app.core.db import superadmin_scope

            async with superadmin_scope(session):
                owner = (
                    await session.execute(
                        select(User).where(User.role == Role.super_admin)
                    )
                ).scalars().first()
                if owner is None:
                    pytest.skip("no owner account seeded in this database")
                from app.api.deps import CurrentUser
                from app.core.security import AUDIENCE_OWNER

                actor = CurrentUser(
                    user_id=owner.id, role=Role.super_admin,
                    tenant_id=None, audience=AUDIENCE_OWNER,
                )
                out = await admin_api.create_tenant(body, user=actor, session=session)
                await session.commit()
        created_tenants.append(out.tenant.id)

        async with factory() as session:
            from app.core.db import superadmin_scope

            async with superadmin_scope(session):
                allowed = await rbac.has_capability(
                    session, out.tenant.id, Role.client, caps.EDIT_COMPANY_PROFILE
                )
        assert allowed, "a freshly created customer's owner must be able to edit its profile"
    finally:
        async with factory() as session:
            from app.core.db import superadmin_scope

            async with superadmin_scope(session):
                for tid in created_tenants:
                    await session.execute(
                        User.__table__.delete().where(User.tenant_id == tid)
                    )
                    await session.execute(
                        Tenant.__table__.delete().where(Tenant.id == tid)
                    )
                await session.commit()
        await engine.dispose()


# ── 3. Signing in as the invited user accepts the invitation ─────────────────

def _pending_invite(tenant_id: uuid.UUID, user_id: uuid.UUID) -> StaffInvite:
    return StaffInvite(
        tenant_id=tenant_id, user_id=user_id,
        email=f"{user_id}@pickready.test", role=Role.recruiter.value,
        token_hash=hash_invite_token(uuid.uuid4().hex),
        expires_at=invite_expiry(),
    )


async def _seed(factory, invite_factory=_pending_invite):
    tid, uid = uuid.uuid4(), uuid.uuid4()
    async with factory() as session:
        from app.core.db import superadmin_scope

        async with superadmin_scope(session):
            session.add(Tenant(id=tid, name=f"Inv-{tid}", domain=f"{tid}.inv.test"))
            session.add(User(
                id=uid, tenant_id=tid, role=Role.recruiter,
                email=f"{uid}@pickready.test", status=UserStatus.invited,
                full_name="Invited Recruiter",
            ))
            await session.flush()
            invite = invite_factory(tid, uid)
            session.add(invite)
            await session.commit()
    return tid, uid


async def _cleanup(factory, tid: uuid.UUID) -> None:
    async with factory() as session:
        from app.core.db import superadmin_scope

        async with superadmin_scope(session):
            await session.execute(
                StaffInvite.__table__.delete().where(StaffInvite.tenant_id == tid)
            )
            await session.execute(User.__table__.delete().where(User.tenant_id == tid))
            await session.execute(Tenant.__table__.delete().where(Tenant.id == tid))
            await session.commit()


async def test_signing_in_accepts_the_outstanding_invitation() -> None:
    """The reported symptom: Active in one column, "Pending" in the next."""
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tid, uid = await _seed(factory)
    try:
        async with factory() as session:
            from app.core.db import superadmin_scope

            async with superadmin_scope(session):
                accepted = await staff_invites.accept_pending_invite(session, uid)
                await session.commit()
        assert accepted is not None
        async with factory() as session:
            from app.core.db import superadmin_scope

            async with superadmin_scope(session):
                row = (await session.execute(
                    select(StaffInvite).where(StaffInvite.user_id == uid)
                )).scalars().one()
                assert row.accepted_at is not None
    finally:
        await _cleanup(factory, tid)
        await engine.dispose()


async def test_acceptance_timestamp_is_not_restamped_by_later_logins() -> None:
    """`accepted_at` records the FIRST acceptance. Re-stamping it on every
    subsequent sign-in would quietly turn an audit fact into a last-seen
    clock."""
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    first = datetime.now(timezone.utc) - timedelta(days=3)

    def already_accepted(tenant_id, user_id):
        invite = _pending_invite(tenant_id, user_id)
        invite.accepted_at = first
        return invite

    tid, uid = await _seed(factory, already_accepted)
    try:
        async with factory() as session:
            from app.core.db import superadmin_scope

            async with superadmin_scope(session):
                assert await staff_invites.accept_pending_invite(session, uid) is None
                await session.commit()
        async with factory() as session:
            from app.core.db import superadmin_scope

            async with superadmin_scope(session):
                row = (await session.execute(
                    select(StaffInvite).where(StaffInvite.user_id == uid)
                )).scalars().one()
                assert abs((row.accepted_at - first).total_seconds()) < 1
    finally:
        await _cleanup(factory, tid)
        await engine.dispose()


async def test_a_revoked_invitation_is_not_resurrected_by_signing_in() -> None:
    """Withdrawing an invitation must not be undone by the invitee logging in."""
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    def revoked(tenant_id, user_id):
        invite = _pending_invite(tenant_id, user_id)
        invite.revoked_at = datetime.now(timezone.utc)
        return invite

    tid, uid = await _seed(factory, revoked)
    try:
        async with factory() as session:
            from app.core.db import superadmin_scope

            async with superadmin_scope(session):
                assert await staff_invites.accept_pending_invite(session, uid) is None
                await session.commit()
        async with factory() as session:
            from app.core.db import superadmin_scope

            async with superadmin_scope(session):
                row = (await session.execute(
                    select(StaffInvite).where(StaffInvite.user_id == uid)
                )).scalars().one()
                assert row.accepted_at is None
    finally:
        await _cleanup(factory, tid)
        await engine.dispose()


async def test_an_expired_invitation_still_has_to_be_resent() -> None:
    """Matches `_resolve_invite`'s 410: expiry is not waived by a later login."""
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    def expired(tenant_id, user_id):
        invite = _pending_invite(tenant_id, user_id)
        invite.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        return invite

    tid, uid = await _seed(factory, expired)
    try:
        async with factory() as session:
            from app.core.db import superadmin_scope

            async with superadmin_scope(session):
                assert await staff_invites.accept_pending_invite(session, uid) is None
                await session.commit()
    finally:
        await _cleanup(factory, tid)
        await engine.dispose()


async def test_a_user_with_no_invitation_is_simply_left_alone() -> None:
    """Every candidate sign-in reaches this call; it must be a quiet no-op."""
    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            from app.core.db import superadmin_scope

            async with superadmin_scope(session):
                assert await staff_invites.accept_pending_invite(
                    session, uuid.uuid4()
                ) is None
    finally:
        await engine.dispose()


async def test_firebase_sign_in_wires_acceptance_into_the_login_path() -> None:
    """The WIRING, not just the helper.

    This is the actual reported bug: the invitee never clicks the emailed
    link, signs in with the invited address, and `firebase_session` matches
    them by email and flips `users.status` to active. Before this change that
    was the whole story and `staff_invites.accepted_at` stayed NULL, so the
    staff table reported "Pending" beside an account that was signed in.
    """
    from fastapi import Response

    from app.api.auth import firebase_session
    from app.schemas.auth import FirebaseSessionIn
    from app.services import firebase_auth
    from app.services.firebase_auth import FirebaseIdentity

    engine = await _db_or_skip()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tid, uid = await _seed(factory)
    monkeypatch = pytest.MonkeyPatch()
    try:
        async with factory() as session:
            from app.core.db import superadmin_scope

            async with superadmin_scope(session):
                invited = await session.get(User, uid)
                assert invited.status == UserStatus.invited
                email = invited.email

        identity = FirebaseIdentity(
            uid=f"fbuid-{uuid.uuid4().hex}", email=email, phone=None,
            name="Invited Recruiter", provider="password", email_verified=True,
        )
        monkeypatch.setattr(firebase_auth, "verify_id_token", lambda _t: identity)
        async with factory() as session:
            from app.core.db import superadmin_scope

            async with superadmin_scope(session):
                await firebase_session(
                    FirebaseSessionIn(id_token="t" * 40), Response(), session
                )

        async with factory() as session:
            from app.core.db import superadmin_scope

            async with superadmin_scope(session):
                user = await session.get(User, uid)
                row = (await session.execute(
                    select(StaffInvite).where(StaffInvite.user_id == uid)
                )).scalars().one()
        assert user.status == UserStatus.active, "signing in activates the account"
        assert row.accepted_at is not None, (
            "...and the invitation stops reporting Pending, which is the bug"
        )
    finally:
        monkeypatch.undo()
        await _cleanup(factory, tid)
        await engine.dispose()
