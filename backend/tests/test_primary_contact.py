"""Setting a customer's primary contact, and the one identity move it performs.

WHY THE ROUTE EXISTS (owner decision, 2026-09-11)
--------------------------------------------------
The Provider Portal is read-only over a customer's own data, enforced by
ABSENCE: `api/provider.py` has no route that writes a contact, a team member or
a compliance document. That rule protects the customer's data. The primary
contact turned out not to be data in that sense, it is the DOOR into the
tenant, and onboarding collected the address exactly once. A typo, an expired
invitation, or a tenant seeded with no address at all left a customer
permanently unreachable, and nothing anywhere in the product could repair it.
The carve-out is one route, and `test_provider_portal` pins its width so a
second one cannot arrive quietly.

WHAT THIS FILE PROVES, AND WHY IT USES A REAL DATABASE
--------------------------------------------------------
The interesting behaviour is not "the handler returned 200", it is which of
four things happened to a `users` row and to the invitations attached to it.
Three of the four are invisible to a response-shape assertion:

  * a bound account whose email CHANGES must lose its `firebase_uid`, or the
    OLD person stays signed in under the NEW address -- auth matches on uid OR
    email (`api/auth.firebase_session`), so leaving the uid in place is a
    silent account hand-over nobody asked for;
  * a fresh invitation must REVOKE the outstanding one, or a token mailed to a
    mistyped address stays live for its whole seven days;
  * a bound account whose email does NOT change must send nothing at all,
    because an invitation to somebody already signed in is an invitation to a
    page that will tell them they are already signed in.

Skips cleanly when no database is reachable, the same shape as `test_rls` and
`test_support_rls`.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import provider as provider_api
from app.api.deps import CurrentUser
from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.models.enums import Role, UserStatus
from app.schemas.provider import PrimaryContactSetIn
from app.workers import dispatch as dispatch_module

pytestmark = pytest.mark.asyncio


async def _factory_or_skip():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 -- any connect failure means "no DB here"
        await engine.dispose()
        pytest.skip("no database reachable -- skipping primary contact tests")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _tenant_and_actor(session) -> tuple[uuid.UUID, CurrentUser]:
    """A customer, and the ReadyPick staff account acting on it."""
    tenant_id, actor_id = uuid.uuid4(), uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
            "VALUES (:id, :name, :domain, 'pending')"
        ),
        {
            "id": tenant_id,
            "name": f"Contact Co {tenant_id.hex[:8]}",
            "domain": f"{tenant_id.hex[:8]}.example.com",
        },
    )
    await session.execute(
        text(
            "INSERT INTO users (id, tenant_id, role, email, full_name, status) "
            "VALUES (:id, NULL, 'super_admin', :email, 'Owner', 'active')"
        ),
        {"id": actor_id, "email": f"owner-{actor_id.hex[:8]}@readypick.ai"},
    )
    return tenant_id, CurrentUser(
        user_id=actor_id, tenant_id=None, role=Role.super_admin, audience="owner"
    )


async def _contact_row(session, tenant_id: uuid.UUID) -> dict | None:
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, email, full_name, status, firebase_uid FROM users "
                    "WHERE tenant_id = :t AND role = 'client' ORDER BY created_at"
                ),
                {"t": tenant_id},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def _live_invites(session, user_id: uuid.UUID) -> int:
    return (
        await session.execute(
            text(
                "SELECT count(*) FROM staff_invites WHERE user_id = :u "
                "AND accepted_at IS NULL AND revoked_at IS NULL"
            ),
            {"u": user_id},
        )
    ).scalar_one()


def _emails_dispatched() -> list:
    return [
        call
        for call in dispatch_module.recorded()
        if call.name == "pickready.send_email"
    ]


async def test_a_customer_with_no_contact_gets_one_and_an_invitation() -> None:
    """The case the product could not reach at all before this route."""
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    tenant_id, actor = await _tenant_and_actor(session)
                    result = await provider_api.set_primary_contact(
                        tenant_id,
                        PrimaryContactSetIn(
                            email="first@customer.example.com",
                            full_name="First Contact",
                        ),
                        user=actor,
                        session=session,
                    )
                    assert result.invite_sent is True
                    assert result.rebound is False

                    contact = await _contact_row(session, tenant_id)
                    assert contact is not None
                    assert contact["email"] == "first@customer.example.com"
                    assert contact["status"] == UserStatus.invited.value
                    assert await _live_invites(session, contact["id"]) == 1
                    assert len(_emails_dispatched()) == 1
                await session.rollback()
    finally:
        await engine.dispose()


async def test_changing_a_bound_account_rebinds_rather_than_editing_a_field() -> None:
    """The dangerous one, and the reason the route reports `rebound` at all.

    Auth resolves an identity by `firebase_uid` OR by email. An account that
    kept its uid while its email moved would leave the ORIGINAL person signed
    in under the NEW address, which no operator asked for and no screen shows.
    """
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    tenant_id, actor = await _tenant_and_actor(session)
                    await provider_api.set_primary_contact(
                        tenant_id,
                        PrimaryContactSetIn(email="before@customer.example.com"),
                        user=actor,
                        session=session,
                    )
                    contact = await _contact_row(session, tenant_id)
                    # The invitee having signed in: Firebase bound the uid and
                    # `_finalize_single` flipped invited -> active.
                    await session.execute(
                        text(
                            "UPDATE users SET firebase_uid = :uid, status = 'active' "
                            "WHERE id = :id"
                        ),
                        {"uid": f"fb-{uuid.uuid4().hex}", "id": contact["id"]},
                    )

                    result = await provider_api.set_primary_contact(
                        tenant_id,
                        PrimaryContactSetIn(email="after@customer.example.com"),
                        user=actor,
                        session=session,
                    )
                    assert result.rebound is True
                    assert result.invite_sent is True

                    moved = await _contact_row(session, tenant_id)
                    assert moved["id"] == contact["id"], "a second row, not a move"
                    assert moved["email"] == "after@customer.example.com"
                    assert moved["firebase_uid"] is None
                    assert moved["status"] == UserStatus.invited.value
                await session.rollback()
    finally:
        await engine.dispose()


async def test_a_bound_account_keeping_its_email_is_not_invited_again() -> None:
    """Name and phone only. An invitation to somebody already signed in is an
    invitation to a page that tells them they are already signed in."""
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    tenant_id, actor = await _tenant_and_actor(session)
                    await provider_api.set_primary_contact(
                        tenant_id,
                        PrimaryContactSetIn(email="steady@customer.example.com"),
                        user=actor,
                        session=session,
                    )
                    contact = await _contact_row(session, tenant_id)
                    await session.execute(
                        text(
                            "UPDATE users SET firebase_uid = :uid, status = 'active' "
                            "WHERE id = :id"
                        ),
                        {"uid": f"fb-{uuid.uuid4().hex}", "id": contact["id"]},
                    )
                    dispatch_module.clear_recorded()

                    result = await provider_api.set_primary_contact(
                        tenant_id,
                        PrimaryContactSetIn(
                            email="steady@customer.example.com",
                            full_name="Renamed Person",
                        ),
                        user=actor,
                        session=session,
                    )
                    assert result.invite_sent is False
                    assert result.rebound is False
                    assert _emails_dispatched() == []

                    kept = await _contact_row(session, tenant_id)
                    assert kept["firebase_uid"] is not None
                    assert kept["status"] == "active"
                    assert kept["full_name"] == "Renamed Person"
                await session.rollback()
    finally:
        await engine.dispose()


async def test_a_corrected_address_kills_the_invitation_sent_to_the_wrong_one() -> None:
    """A token mailed to a mistyped address must not stay live for a week.

    At most one pending invite per user, which is what `StaffInvite` already
    documents; this asserts the route holds to it rather than stacking.
    """
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    tenant_id, actor = await _tenant_and_actor(session)
                    await provider_api.set_primary_contact(
                        tenant_id,
                        PrimaryContactSetIn(email="typo@customer.example.com"),
                        user=actor,
                        session=session,
                    )
                    contact = await _contact_row(session, tenant_id)
                    assert await _live_invites(session, contact["id"]) == 1

                    await provider_api.set_primary_contact(
                        tenant_id,
                        PrimaryContactSetIn(email="correct@customer.example.com"),
                        user=actor,
                        session=session,
                    )
                    assert await _live_invites(session, contact["id"]) == 1
                    revoked = (
                        await session.execute(
                            text(
                                "SELECT count(*) FROM staff_invites "
                                "WHERE user_id = :u AND revoked_at IS NOT NULL"
                            ),
                            {"u": contact["id"]},
                        )
                    ).scalar_one()
                    assert revoked == 1
                await session.rollback()
    finally:
        await engine.dispose()


async def test_an_address_another_member_already_holds_is_refused() -> None:
    """Inside ONE tenant an address must resolve to one person, or the login
    lookup is ambiguous. Across tenants it is fine and stays allowed: the same
    person may hold accounts in several workspaces."""
    from fastapi import HTTPException

    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    tenant_id, actor = await _tenant_and_actor(session)
                    await provider_api.set_primary_contact(
                        tenant_id,
                        PrimaryContactSetIn(email="owner@customer.example.com"),
                        user=actor,
                        session=session,
                    )
                    await session.execute(
                        text(
                            "INSERT INTO users (id, tenant_id, role, email, status) "
                            "VALUES (:id, :t, 'recruiter', :email, 'active')"
                        ),
                        {
                            "id": uuid.uuid4(),
                            "t": tenant_id,
                            "email": "recruiter@customer.example.com",
                        },
                    )

                    with pytest.raises(HTTPException) as raised:
                        await provider_api.set_primary_contact(
                            tenant_id,
                            PrimaryContactSetIn(
                                email="recruiter@customer.example.com"
                            ),
                            user=actor,
                            session=session,
                        )
                    assert raised.value.status_code == 409
                await session.rollback()
    finally:
        await engine.dispose()
