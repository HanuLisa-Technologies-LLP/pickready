"""Delete My Profile: the door the erasure machinery never had.

`VIVEKIUM_SPRINT_FEATURES.md` feature 7, and India's DPDP Act 2023.

WHY THIS TEST IS WORTH ITS DATABASE
-----------------------------------
`services/erasure` was complete and correct for its whole existence and was
reachable by nobody: `pickready.cascade_erasure` had no caller, and no route on
any portal could ask for an erasure. A unit test of the erasure function would
have passed throughout, which is exactly the class of green this repository
already has a rule about.

The three failures pinned here are the ones a route test that only asserted a
200 would miss:

  * A LOWERCASE CONFIRMATION ERASING THE ACCOUNT. `phrase_matches` folding case
    would accept an autocorrect with no intent behind it.
  * A REFUSED CONFIRMATION ERASING SOMETHING ANYWAY. The refusal has to happen
    before the first statement, and the way to prove it is to read the rows
    back after a 422.
  * THE GHOST ACCOUNT. `candidates.user_id` is ON DELETE SET NULL, so deleting
    the candidate leaves a `users` row holding a working Firebase identity. The
    person signs in successfully and gets 404 on every route forever: erased in
    substance, still an account holder. Asserted by reading `users` back.

Same convention as test_candidate_profile.py: skips cleanly with no database,
runs for real inside the backend container.
"""
from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import text

from app.services import account_deletion

EM_DASH = chr(8212)

#: The brief forbids both of these anywhere in system copy and requires
#: "employer clients registered on the platform" in their place.
FORBIDDEN_TERMS = ("direct employer", "manpower agency")

VECTOR = "[" + ",".join(["0.02"] * 1024) + "]"


async def _factory_or_skip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 -- no DB reachable
        await engine.dispose()
        pytest.skip("no database reachable, skipping account deletion test")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _redis_or_skip() -> None:
    """The erasure RAISES when it cannot reach Redis, by design: an erasure
    that could not purge the cache has not erased anything from it. So a run
    without Redis is not a failure of this feature and must not read as one."""
    import redis.asyncio as redis_asyncio

    from app.core.config import get_settings

    client = redis_asyncio.from_url(
        get_settings().redis_url,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    try:
        await client.ping()
    except Exception:  # noqa: BLE001
        pytest.skip("no redis reachable, skipping account deletion test")
    finally:
        await client.aclose()


class _Fixture:
    """One candidate with a sign-in account, and a STAFF user as the control.

    The staff user is the whole reason the role guard is testable: an erasure
    that deleted every `users` row it could reach would pass every assertion
    about the candidate's own account.
    """

    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.user_id = uuid.uuid4()
        self.staff_user_id = uuid.uuid4()
        self.candidate_id = uuid.uuid4()
        self.profile_id = uuid.uuid4()
        self.email = f"erase-{uuid.uuid4().hex[:8]}@candidates.pickready.test"
        self.staff_email = f"staff-{uuid.uuid4().hex[:8]}@staff.pickready.test"


async def _seed(factory, fx: _Fixture) -> None:
    from app.core.db import superadmin_scope
    from app.models import Candidate, Role, Tenant, User
    from app.models.enums import UserStatus

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                s.add(Tenant(id=fx.tenant_id, name="EraseCo",
                             domain=f"{fx.tenant_id}.erase.test"))
                s.add(User(id=fx.user_id, email=fx.email, role=Role.candidate,
                           tenant_id=None, full_name="Erasable Person",
                           status=UserStatus.active))
                s.add(User(id=fx.staff_user_id, email=fx.staff_email,
                           role=Role.recruiter, tenant_id=fx.tenant_id,
                           full_name="Staying Staffer",
                           status=UserStatus.active))
                await s.flush()
                s.add(Candidate(id=fx.candidate_id, email=fx.email,
                                user_id=fx.user_id, full_name="Erasable Person",
                                consent_databank=False))
                await s.flush()
                await s.execute(
                    text(
                        "INSERT INTO profiles (id, candidate_id, "
                        "source_tenant_id, resume_text, embedding) VALUES "
                        "(:p, :c, :t, 'Kafka and Postgres', CAST(:v AS vector))"
                    ),
                    {"p": str(fx.profile_id), "c": str(fx.candidate_id),
                     "t": str(fx.tenant_id), "v": VECTOR},
                )


async def _cleanup(factory, fx: _Fixture) -> None:
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(
                    text("UPDATE candidates SET main_profile_id = NULL "
                         "WHERE id = :c"),
                    {"c": str(fx.candidate_id)},
                )
                await s.execute(text("DELETE FROM candidates WHERE id = :c"),
                                {"c": str(fx.candidate_id)})
                await s.execute(text("DELETE FROM tenants WHERE id = :t"),
                                {"t": str(fx.tenant_id)})
                await s.execute(
                    text("DELETE FROM users WHERE id IN (:u, :s)"),
                    {"u": str(fx.user_id), "s": str(fx.staff_user_id)},
                )
    # audit_log is deliberately NOT cleaned: the application role holds no
    # DELETE grant on it, which is the append-only property the erasure receipt
    # depends on.


def _user(fx: _Fixture):
    from app.api.deps import CurrentUser
    from app.core.security import AUDIENCE_CANDIDATE
    from app.models import Role

    return CurrentUser(user_id=fx.user_id, tenant_id=None, role=Role.candidate,
                       audience=AUDIENCE_CANDIDATE)


async def _counts(factory, fx: _Fixture) -> dict[str, int]:
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with superadmin_scope(s):
            out: dict[str, int] = {}
            for key, sql, params in (
                ("candidates",
                 "SELECT count(*) FROM candidates WHERE id = :i",
                 {"i": str(fx.candidate_id)}),
                ("profiles",
                 "SELECT count(*) FROM profiles WHERE candidate_id = :i",
                 {"i": str(fx.candidate_id)}),
                ("candidate_user",
                 "SELECT count(*) FROM users WHERE id = :i",
                 {"i": str(fx.user_id)}),
                ("staff_user",
                 "SELECT count(*) FROM users WHERE id = :i",
                 {"i": str(fx.staff_user_id)}),
            ):
                out[key] = int(
                    (await s.execute(text(sql), params)).scalar_one()
                )
            return out


# -- The phrase, which is the whole authorisation ---------------------------


def test_the_confirmation_phrase_is_exact_and_case_sensitive() -> None:
    """Whitespace is forgiven, case is not, and the reason is intent.

    A mobile keyboard adds a trailing space by itself, so refusing that would
    refuse people who typed exactly what they were asked to. An autocorrect
    that lowercases "DELETE" into "delete" has not established that anybody
    meant to destroy an account, so the comparison stays case sensitive.
    """
    assert account_deletion.phrase_matches("DELETE") is True
    assert account_deletion.phrase_matches("  DELETE  ") is True
    assert account_deletion.phrase_matches("delete") is False
    assert account_deletion.phrase_matches("Delete") is False
    assert account_deletion.phrase_matches("DELETE MY PROFILE") is False
    assert account_deletion.phrase_matches("") is False
    assert account_deletion.phrase_matches(None) is False


def test_the_warning_is_served_by_the_server_and_follows_the_copy_rules() -> None:
    """The screen renders these and authors none of its own.

    Checked over the whole catalogue rather than at a call site, the same way
    `candidate_updates` is checked: a rule enforced at one entry is a rule the
    next entry breaks.
    """
    notice = account_deletion.deletion_notice()
    assert notice["confirmation_phrase"] == account_deletion.CONFIRMATION_PHRASE
    warnings = notice["warnings"]
    # Every consequence the brief lists is present. Counted rather than
    # matched word for word, so rewording the copy does not fail the test but
    # dropping a consequence does.
    assert len(warnings) == len(account_deletion.DELETION_WARNINGS) == 7
    every_string = list(warnings) + [
        notice["heading"], notice["instruction"],
        account_deletion.WRONG_PHRASE_MESSAGE,
    ]
    for line in every_string:
        assert EM_DASH not in line, f"em dash in deletion copy: {line!r}"
        lowered = line.lower()
        for term in FORBIDDEN_TERMS:
            assert term not in lowered, f"forbidden term {term!r} in {line!r}"
    # The two irreversible facts people most underestimate are stated, not
    # implied. Both are the reason the screen exists at all.
    joined = " ".join(warnings).lower()
    assert "cannot be reversed" in joined
    assert "verification" in joined


def test_the_confirmation_letter_describes_no_record() -> None:
    """By the time it is dispatched there is nothing left to describe, and
    describing something would mean the erasure had kept a copy to write it."""
    from app.services.email_render import DEFAULT_TEMPLATES

    subject, body = DEFAULT_TEMPLATES[account_deletion.CONFIRMATION_TEMPLATE]
    assert EM_DASH not in subject and EM_DASH not in body
    # No placeholder at all: a template with a context slot is a template that
    # will eventually be handed a name, an application or a count.
    assert "{{" not in subject and "{{" not in body
    for term in FORBIDDEN_TERMS:
        assert term not in body.lower()


# -- Integration ------------------------------------------------------------


async def test_a_refused_confirmation_erases_nothing() -> None:
    """The refusal runs BEFORE the first statement, proved by reading back.

    A 422 that had already deleted the profile vectors would look identical
    from outside, which is why this reads rows rather than trusting the status.
    """
    from fastapi import HTTPException
    from starlette.responses import Response

    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope
    from app.schemas.portal import DeleteMeIn

    engine, factory = await _factory_or_skip()
    fx = _Fixture()
    try:
        await _seed(factory, fx)
        before = await _counts(factory, fx)
        assert before["candidates"] == 1 and before["profiles"] == 1

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    with pytest.raises(HTTPException) as caught:
                        await portal_mod.delete_my_profile(
                            DeleteMeIn(confirmation="delete"),
                            Response(),
                            user=_user(fx),
                            session=s,
                        )
        assert caught.value.status_code == 422
        assert account_deletion.CONFIRMATION_PHRASE in caught.value.detail

        after = await _counts(factory, fx)
        assert after == before, "a refused confirmation changed the database"
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


async def test_the_erasure_takes_the_sign_in_account_and_leaves_staff_alone(
    monkeypatch,
) -> None:
    """The ghost-account failure, and the role guard that stops it overreaching.

    Without step 4b the candidate row is gone and the `users` row survives, so
    the person still signs in and every route answers 404 forever. With a step
    4b that forgot its role guard, an erasure could reach a staff account.
    Both directions are asserted here because only one of them raises.
    """
    from starlette.responses import Response

    from app.api import portal as portal_mod
    from app.core.db import superadmin_scope
    from app.schemas.portal import DeleteMeIn

    await _redis_or_skip()
    engine, factory = await _factory_or_skip()
    fx = _Fixture()

    # The confirmation letter is dispatched, not sent. Recorded rather than
    # executed so this test does not depend on a mail provider, and asserted
    # because an erasure that silently stopped telling the person would be
    # invisible.
    dispatched: list[tuple] = []
    monkeypatch.setattr(
        portal_mod, "dispatch",
        lambda name, args=None, **kw: dispatched.append((name, args)),
    )
    cleared: dict[str, bool] = {}
    monkeypatch.setattr(
        portal_mod, "clear_auth_cookies",
        lambda response: cleared.setdefault("called", True),
    )

    try:
        await _seed(factory, fx)

        async with factory() as s:
            async with s.begin():
                async with superadmin_scope(s):
                    out = await portal_mod.delete_my_profile(
                        DeleteMeIn(confirmation="DELETE"),
                        Response(),
                        user=_user(fx),
                        session=s,
                    )

        assert out.deleted is True
        assert out.candidate_id == fx.candidate_id
        assert isinstance(out.erased_at, datetime)
        assert out.erased_at.tzinfo is not None
        # The receipt states what it did, and the number is checkable.
        assert out.sign_in_accounts_deleted == 1

        after = await _counts(factory, fx)
        assert after["candidates"] == 0
        assert after["profiles"] == 0
        assert after["candidate_user"] == 0, (
            "the sign-in account survived the erasure: the person can still "
            "log in to a profile that no longer exists"
        )
        assert after["staff_user"] == 1, (
            "the erasure reached a staff account, so the role guard on the "
            "users delete is not binding"
        )

        # The session was invalidated in the same request, not left for the
        # client to clean up.
        assert cleared.get("called") is True
        # TWO dispatches, in this order, and both are load bearing.
        #
        # The first finishes the OBJECT half of the erasure. Rows, vectors and
        # caches are settled inside this request; a resume, an assessment
        # recording and a staged project original live in an object store that
        # fails independently of Postgres, so they are deleted by the
        # resumable pass against the deletion record, which the hourly sweep
        # also picks up. Before that record existed the objects were never
        # touched at all.
        #
        # The second tells the person, at the address read before the row went.
        assert [name for name, _ in dispatched] == [
            "pickready.cascade_erasure",
            "pickready.send_email",
        ]
        _, erasure_args = dispatched[0]
        assert erasure_args[0] == str(out.candidate_id)
        assert erasure_args[2], (
            "the object pass was dispatched without a deletion request id, so "
            "it cannot know which files were owed"
        )
        _, args = dispatched[1]
        assert args[1] == fx.email
        assert args[2] == account_deletion.CONFIRMATION_TEMPLATE
        assert args[3] == {}, "the confirmation letter carries context"
        # The response says where the erasure actually stands rather than
        # claiming a completion it cannot see.
        assert out.deletion_state == "rows_erased"
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()
