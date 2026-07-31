"""Self-service profile CRUD — GET/PATCH /api/v1/portal/me.

The contract:
  GET   /portal/me            -> {id, full_name, email, phone, role}
  PATCH /portal/me  {full_name?, phone?}  -> the same shape

`email` is read-only (claude.md rule 2 — Firebase owns credentials and account
recovery) and both the `users` row and the caller's `candidates` row are kept
in step so the HR Review Screen shows the corrected name.

Schema tests run anywhere; the write test needs a database and SKIPS cleanly
without one (same convention as test_portal.py).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.schemas.portal import MeUpdateIn


# ── Request validation ───────────────────────────────────────────────────────

def test_full_name_is_stripped() -> None:
    assert MeUpdateIn(full_name="  Ada Lovelace  ").full_name == "Ada Lovelace"


def test_blank_full_name_is_rejected() -> None:
    with pytest.raises(ValueError):
        MeUpdateIn(full_name="   ")


def test_phone_separators_are_normalized() -> None:
    assert MeUpdateIn(phone="+91 98765-43210").phone == "+919876543210"
    assert MeUpdateIn(phone="(080) 4123 4567").phone == "08041234567"


def test_phone_can_be_cleared() -> None:
    body = MeUpdateIn(phone=None)
    assert body.phone is None
    # An explicitly-sent null is distinguishable from an omitted field, which
    # is what lets the handler clear the column rather than ignore it.
    assert "phone" in body.model_fields_set
    assert "phone" not in MeUpdateIn(full_name="Ada").model_fields_set


@pytest.mark.parametrize("bad", ["abc", "12345", "+" + "9" * 16, "12-34"])
def test_invalid_phone_is_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        MeUpdateIn(phone=bad)


def test_email_change_is_rejected_with_a_clear_message() -> None:
    with pytest.raises(ValueError, match="Firebase owns credentials"):
        MeUpdateIn(email="someone-else@example.com")


def test_omitted_fields_are_left_untouched() -> None:
    body = MeUpdateIn(full_name="Ada")
    assert body.model_fields_set == {"full_name"}


# ── The write path ───────────────────────────────────────────────────────────

async def _factory_or_skip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 — no DB reachable
        await engine.dispose()
        pytest.skip("no database reachable — skipping profile CRUD integration test")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def test_patch_me_updates_the_user_and_the_candidate_row() -> None:
    """A corrected name must reach the `candidates` row too — that is the row
    the HR Review Screen renders."""
    from app.api import portal as portal_mod
    from app.api.deps import CurrentUser
    from app.core.db import superadmin_scope
    from app.core.security import AUDIENCE_CANDIDATE
    from app.models import Candidate, Role, User
    from app.models.enums import UserStatus

    engine, factory = await _factory_or_skip()
    user_id = uuid.uuid4()
    cand_id = uuid.uuid4()
    email = f"crud-{uuid.uuid4().hex[:8]}@candidates.pickready.test"
    try:
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    s.add(User(id=user_id, email=email, role=Role.candidate,
                               tenant_id=None, full_name="Old Name",
                               phone="9000000001", status=UserStatus.active))
                    await s.flush()
                    s.add(Candidate(id=cand_id, email=email, user_id=user_id,
                                    full_name="Old Name", phone="9000000001",
                                    consent_databank=False))

        current = CurrentUser(user_id=user_id, tenant_id=None, role=Role.candidate,
                              audience=AUDIENCE_CANDIDATE)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    out = await portal_mod.update_me(
                        MeUpdateIn(full_name="New Name", phone="+919876500000"),
                        user=current, session=s,
                    )
        assert out.full_name == "New Name"
        assert out.phone == "+919876500000"
        assert out.email == email  # unchanged, read-only
        assert out.role == "candidate"

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    row = await s.get(User, user_id)
                    cand = await s.get(Candidate, cand_id)
                    assert row is not None and cand is not None
                    assert row.full_name == "New Name"
                    assert cand.full_name == "New Name"   # review screen reads this
                    assert cand.phone == "+919876500000"
                    audited = (
                        await s.execute(
                            text(
                                "SELECT count(*) FROM audit_log WHERE "
                                "action = 'profile_updated' AND target_id = :t"
                            ),
                            {"t": str(user_id)},
                        )
                    ).scalar_one()
                    assert audited >= 1
    finally:
        # audit_log rows are deliberately left behind: the application role has
        # no DELETE grant on that table (it is append-only by migration).
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await s.execute(text("DELETE FROM candidates WHERE id = :c"),
                                    {"c": str(cand_id)})
                    await s.execute(text("DELETE FROM users WHERE id = :u"),
                                    {"u": str(user_id)})
        await engine.dispose()


async def test_patch_me_clears_the_phone_when_sent_as_null() -> None:
    from app.api import portal as portal_mod
    from app.api.deps import CurrentUser
    from app.core.db import superadmin_scope
    from app.core.security import AUDIENCE_CANDIDATE
    from app.models import Role, User
    from app.models.enums import UserStatus

    engine, factory = await _factory_or_skip()
    user_id = uuid.uuid4()
    email = f"crud-{uuid.uuid4().hex[:8]}@candidates.pickready.test"
    try:
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    s.add(User(id=user_id, email=email, role=Role.candidate,
                               tenant_id=None, full_name="Keep This Name",
                               phone="9000000002", status=UserStatus.active))

        current = CurrentUser(user_id=user_id, tenant_id=None, role=Role.candidate,
                              audience=AUDIENCE_CANDIDATE)
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    out = await portal_mod.update_me(
                        MeUpdateIn(phone=None), user=current, session=s
                    )
        assert out.phone is None
        assert out.full_name == "Keep This Name"  # omitted field untouched
    finally:
        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    await s.execute(text("DELETE FROM users WHERE id = :u"),
                                    {"u": str(user_id)})
        await engine.dispose()
