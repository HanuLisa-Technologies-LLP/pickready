"""The employer verification link expires. It used to live for ever.

WHAT WAS WRONG, AND WHY IT READ AS FINE
---------------------------------------
`_pending_request_by_token` refused any request whose status was not `pending`,
and the module docstring called the token "single-use". Single-use is a real
property and it is not the same property as expiry: a link that nobody ever
used stayed `pending` for ever, and therefore stayed VALID for ever. It lives
in a third party's mailbox, in every mailbox the message was ever forwarded to,
and in whatever archive that mail system keeps. This is the same argument
`INBOUND_WEBHOOK_SECRET` already makes about thread tokens, applied to a
credential that can write an employment verification about a named person.

WHY AN EXPIRY IS SAFE HERE AND WOULD NOT BE EVERYWHERE
-------------------------------------------------------
An expiry with no way out converts a stale-credential risk into a dead end for
a candidate who did nothing wrong. It is acceptable here because the way out
already exists and is documented as such: `POST /verification/requests/{id}/
override` is, in its own docstring, "the only way a fresh candidate moves
forward without an employer response", and it takes a reason and writes an
audit row.

Skips cleanly with no database, runs for real inside the backend container.
"""
from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text


async def _factory_or_skip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001
        await engine.dispose()
        pytest.skip("no database reachable, skipping verification expiry test")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class _Fixture:
    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.candidate_id = uuid.uuid4()
        self.profile_id = uuid.uuid4()
        self.fresh_token = secrets.token_urlsafe(32)
        self.stale_token = secrets.token_urlsafe(32)
        self.used_token = secrets.token_urlsafe(32)


async def _seed(factory, fx: _Fixture) -> None:
    """Three links: one fresh, one older than the TTL, one already submitted.

    The used one is the control. An expiry check written as "refuse anything
    old" with no regard for status would still refuse it, and the test would
    pass while the two refusals had become indistinguishable to the person
    reading them.
    """
    from app.core.config import get_settings
    from app.core.db import superadmin_scope

    ttl = get_settings().verification_link_ttl_days
    now = datetime.now(timezone.utc)
    rows = (
        (fx.fresh_token, now, "pending"),
        # One minute past the boundary rather than a month, so the test is
        # about the boundary rather than about a value nothing could disagree
        # on.
        (fx.stale_token, now - timedelta(days=ttl, minutes=1), "pending"),
        (fx.used_token, now, "submitted"),
    )
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(
                    text(
                        "INSERT INTO tenants (id, name, domain, spf_dkim_status)"
                        " VALUES (:t, :n, :d, 'pending')"
                    ),
                    {"t": str(fx.tenant_id), "n": f"vexp-{fx.tenant_id}",
                     "d": f"{fx.tenant_id}.vexp.test"},
                )
                await s.execute(
                    text(
                        "INSERT INTO candidates (id, tenant_id, full_name, "
                        "email, consent_databank) VALUES "
                        "(:c, :t, 'Checked Person', :e, false)"
                    ),
                    {"c": str(fx.candidate_id), "t": str(fx.tenant_id),
                     "e": f"{fx.candidate_id}@vexp.test"},
                )
                await s.execute(
                    text(
                        "INSERT INTO profiles (id, candidate_id, "
                        "source_tenant_id) VALUES (:p, :c, :t)"
                    ),
                    {"p": str(fx.profile_id), "c": str(fx.candidate_id),
                     "t": str(fx.tenant_id)},
                )
                for seq, (token, created, status) in enumerate(rows, start=1):
                    await s.execute(
                        text(
                            "INSERT INTO verification_requests (id, tenant_id, "
                            "profile_id, employer_seq, employer_email, token, "
                            "status, created_at) VALUES (:i, :t, :p, :seq, :e, "
                            ":tok, :st, :created)"
                        ),
                        {"i": str(uuid.uuid4()), "t": str(fx.tenant_id),
                         "p": str(fx.profile_id), "seq": seq,
                         "e": f"hr{seq}@employer.test", "tok": token,
                         "st": status, "created": created},
                    )


async def _cleanup(factory, fx: _Fixture) -> None:
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(text("DELETE FROM candidates WHERE id = :c"),
                                {"c": str(fx.candidate_id)})
                await s.execute(text("DELETE FROM tenants WHERE id = :t"),
                                {"t": str(fx.tenant_id)})


def test_the_expiry_is_derived_from_the_send_date() -> None:
    """Derived, never stored, like `posting_status` and `profile_age`.

    A stored expiry is a second copy of something the row already determines,
    and the two can disagree. This also proves the TTL is read from settings
    rather than written as a literal at the one place that refuses.
    """
    from types import SimpleNamespace

    from app.api.verification import link_expires_at
    from app.core.config import get_settings

    sent = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
    ttl = get_settings().verification_link_ttl_days
    assert ttl == 3, "the brief asks for a three day window"
    assert link_expires_at(SimpleNamespace(created_at=sent)) == sent + timedelta(
        days=ttl
    )


async def test_a_stale_link_is_refused_and_says_so_in_its_own_words() -> None:
    """Three outcomes, three distinguishable answers.

    The fresh link works. The stale one is 410 EXPIRED. The submitted one is
    410 ALREADY USED, and keeping those two sentences apart is the point: they
    are different facts and the recruiter's next move differs. An expired link
    means nobody answered and somebody has to decide whether to override; a
    used one means an employer answered and the response is on file.
    """
    from fastapi import HTTPException

    from app.api import verification as ver
    from app.core.db import superadmin_scope

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)

        async with factory() as s:
            async with superadmin_scope(s):
                # The fresh one resolves, and carries its own deadline so the
                # page and the refusal cannot disagree about when it dies.
                form = await ver.get_employer_form(fx.fresh_token, session=s)
                assert form.expires_at > datetime.now(timezone.utc)

                with pytest.raises(HTTPException) as stale:
                    await ver.get_employer_form(fx.stale_token, session=s)
                assert stale.value.status_code == 410
                assert "expired" in stale.value.detail.lower()

                with pytest.raises(HTTPException) as used:
                    await ver.get_employer_form(fx.used_token, session=s)
                assert used.value.status_code == 410
                assert "already been used" in used.value.detail.lower()

                # The two refusals are not the same sentence.
                assert stale.value.detail != used.value.detail
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


def test_both_form_routes_go_through_the_one_chokepoint() -> None:
    """The expiry is enforced once, where both routes already resolve a token.

    A check added to the GET alone would be a check the POST does not make, and
    the POST is the one that writes. Asserted over the SOURCE rather than by
    calling them, because the failure being prevented is a future route that
    looks up a token its own way.
    """
    import inspect

    from app.api import verification as ver

    for route in (ver.get_employer_form, ver.submit_employer_form):
        body = inspect.getsource(route)
        assert "_pending_request_by_token" in body, (
            f"{route.__name__} resolves a token without the chokepoint that "
            "enforces single use and expiry"
        )
    assert "link_expires_at" in inspect.getsource(ver._pending_request_by_token)
