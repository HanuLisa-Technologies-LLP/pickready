"""The conversations API, over a real database and through the real router.

WHY THIS IS A ROUTE TEST AND NOT A SERVICE TEST
-------------------------------------------------
`test_conversations.py` already proves the persistence layer: idempotency, the
unread watermark, cross-tenant refusal. Everything asserted HERE is a property
of the ROUTE and would pass a service test while being broken in production:

  * a conversation id from another customer answers 404, through the real
    `tenant_scope` session rather than a Python filter;
  * a BGV thread refuses a chat send, so the employer contact surface cannot be
    reached from the ordinary message box;
  * the realtime notification is published AFTER the transaction commits, which
    is the one ordering bug that is invisible locally and constant under load.

The last one is the reason this file exists. A publish inside the transaction
passes every test that only checks "a message came back", and then, in a
deployment with two API tasks, announces a message the receiving browser cannot
fetch yet. The assertion here reads the row from a SEPARATE connection at the
moment the publish happens, which is exactly what another instance would see.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import (
    CurrentUser,
    get_candidate_db,
    get_current_candidate,
    get_current_user,
    get_tenant_db,
)
from app.core.config import get_settings
from app.core.db import superadmin_scope, tenant_scope
from app.core.security import AUDIENCE_CANDIDATE, AUDIENCE_ORG
from app.main import app
from app.models.conversation import KIND_BGV, PARTY_CANDIDATE
from app.models.enums import Role
from app.services import conversations as conversation_service
from app.services import realtime

CONV = "/api/v1/conversations"

#: The publish is genuinely concurrent now: it is fired by SQLAlchemy's
#: `after_commit` onto the application's own loop, which runs in TestClient's
#: portal thread. So the test WAITS for it rather than assuming it has already
#: happened. This is not a sleep papering over flakiness -- it is waiting on an
#: event the production code deliberately does not make the caller block on.
SETTLE_SECONDS = 5.0


def _settle(predicate) -> bool:
    deadline = time.monotonic() + SETTLE_SECONDS
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _run(coro):
    """One fresh loop per call.

    TestClient drives the application on its own loop, so an engine bound to a
    loop that has since closed is the failure that surfaces later as an
    unrelated timeout somewhere else in the suite.
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _sessions():
    return async_sessionmaker(
        create_async_engine(get_settings().database_url, poolclass=NullPool),
        expire_on_commit=False,
    )


async def _reachable() -> bool:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.text("SELECT 1 FROM conversations LIMIT 0"))
        return True
    except Exception:  # noqa: BLE001 -- no database here
        return False
    finally:
        await engine.dispose()


class World:
    """Two customers, each with a recruiter, a job and one candidate linked."""

    def __init__(self) -> None:
        self.tenant_a = uuid.uuid4()
        self.tenant_b = uuid.uuid4()
        self.user_a = uuid.uuid4()
        self.user_b = uuid.uuid4()
        self.job_a = uuid.uuid4()
        self.job_b = uuid.uuid4()
        self.candidate_a = uuid.uuid4()
        #: Linked to tenant B's job only. Tenant A must not be able to open a
        #: thread with them by id.
        self.candidate_b = uuid.uuid4()


@pytest.fixture
def world() -> Iterator[World]:
    if not _run(_reachable()):
        pytest.skip("no database reachable -- skipping conversations API test")

    w = World()
    sessions = _sessions()

    async def _seed() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    for tid, label in (
                        (w.tenant_a, "Conv-API-A"),
                        (w.tenant_b, "Conv-API-B"),
                    ):
                        await session.execute(
                            sa.text(
                                "INSERT INTO tenants (id, name, domain, "
                                " spf_dkim_status) "
                                "VALUES (:id, :name, :domain, 'pending')"
                            ),
                            {
                                "id": str(tid),
                                "name": f"{label}-{tid.hex[:6]}",
                                "domain": f"{tid.hex[:10]}.conv.test",
                            },
                        )
                    for uid, tid in ((w.user_a, w.tenant_a), (w.user_b, w.tenant_b)):
                        await session.execute(
                            sa.text(
                                "INSERT INTO users (id, tenant_id, email, full_name, "
                                " role, status) "
                                "VALUES (:id, :tid, :email, :name, 'recruiter', "
                                " 'active')"
                            ),
                            {
                                "id": str(uid),
                                "tid": str(tid),
                                "email": f"{uid.hex[:10]}@conv.test",
                                "name": "Priya Raman",
                            },
                        )
                    for jid, tid in ((w.job_a, w.tenant_a), (w.job_b, w.tenant_b)):
                        await session.execute(
                            sa.text(
                                "INSERT INTO jobs (id, tenant_id, title, jd_json, "
                                " status) "
                                "VALUES (:id, :tid, 'Backend Engineer', "
                                " CAST('{}' AS jsonb), 'draft')"
                            ),
                            {"id": str(jid), "tid": str(tid)},
                        )
                    for cid, jid, tid in (
                        (w.candidate_a, w.job_a, w.tenant_a),
                        (w.candidate_b, w.job_b, w.tenant_b),
                    ):
                        await session.execute(
                            sa.text(
                                "INSERT INTO candidates (id, full_name, email, "
                                " consent_databank, created_at) "
                                "VALUES (:id, 'Karthik Kumar', :email, false, now())"
                            ),
                            {"id": str(cid), "email": f"{cid.hex[:10]}@conv.test"},
                        )
                        await session.execute(
                            sa.text(
                                "INSERT INTO job_candidate_links "
                                "(id, tenant_id, job_id, candidate_id, source, status) "
                                "VALUES (:id, :tid, :jid, :cid, 'fresh', 'applied')"
                            ),
                            {
                                "id": str(uuid.uuid4()),
                                "tid": str(tid),
                                "jid": str(jid),
                                "cid": str(cid),
                            },
                        )

    async def _teardown() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text("DELETE FROM candidates WHERE id = ANY(:ids)"),
                        {"ids": [str(w.candidate_a), str(w.candidate_b)]},
                    )
                    await session.execute(
                        sa.text("DELETE FROM tenants WHERE id = ANY(:ids)"),
                        {"ids": [str(w.tenant_a), str(w.tenant_b)]},
                    )

    _run(_seed())
    try:
        yield w
    finally:
        _run(_teardown())


class Caller:
    def __init__(self) -> None:
        self.principal: CurrentUser | None = None
        self.http: TestClient | None = None

    def as_recruiter(self, world: World, tenant: uuid.UUID) -> None:
        self.principal = CurrentUser(
            user_id=world.user_a if tenant == world.tenant_a else world.user_b,
            tenant_id=tenant,
            role=Role.recruiter,
            audience=AUDIENCE_ORG,
        )


@pytest.fixture
def client(world: World) -> Iterator[Caller]:
    sessions = _sessions()
    caller = Caller()

    async def _current_user() -> CurrentUser:
        assert caller.principal is not None
        return caller.principal

    async def _tenant_db():
        principal = caller.principal
        assert principal is not None
        async with sessions() as session:
            async with session.begin():
                # Entered exactly as the production dependency enters it, so a
                # cross-tenant case is refused by Postgres and not only by the
                # application's own comparison.
                async with tenant_scope(session, principal.tenant_id):
                    yield session

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = _current_user
    app.dependency_overrides[get_tenant_db] = _tenant_db
    try:
        with TestClient(app) as http:
            caller.http = http
            yield caller
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def _open_thread(caller: Caller, candidate_id: uuid.UUID):
    return caller.http.post(f"{CONV}/candidate/{candidate_id}")


# ── The ordinary path ────────────────────────────────────────────────────────


def test_a_recruiter_opens_one_thread_and_reads_back_what_they_sent(
    client: Caller, world: World
) -> None:
    client.as_recruiter(world, world.tenant_a)
    opened = _open_thread(client, world.candidate_a)
    assert opened.status_code == 200, opened.text
    conversation_id = opened.json()["id"]

    sent = client.http.post(
        f"{CONV}/{conversation_id}/messages",
        json={"body": "Are you free on Thursday?", "client_token": "tok-thursday-1"},
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["body"] == "Are you free on Thursday?"
    # The staff author is resolved live from `users`, never from a denormalised
    # copy, so a corrected name is not two people across one thread.
    assert sent.json()["author_name"] == "Priya Raman"

    history = client.http.get(f"{CONV}/{conversation_id}/messages")
    assert history.status_code == 200
    assert [m["body"] for m in history.json()] == ["Are you free on Thursday?"]


def test_the_thread_is_opened_once_and_reused(client: Caller, world: World) -> None:
    """A second thread would split one person's conversation across two screens."""
    client.as_recruiter(world, world.tenant_a)
    first = _open_thread(client, world.candidate_a).json()["id"]
    second = _open_thread(client, world.candidate_a).json()["id"]
    assert first == second


def test_the_same_client_token_twice_is_one_message(
    client: Caller, world: World
) -> None:
    """A double click, a retry after a lost response and a reconnect that
    replays the send all arrive as separate requests with identical content."""
    client.as_recruiter(world, world.tenant_a)
    conversation_id = _open_thread(client, world.candidate_a).json()["id"]

    body = {"body": "Sending this once.", "client_token": "tok-double-click"}
    first = client.http.post(f"{CONV}/{conversation_id}/messages", json=body)
    second = client.http.post(f"{CONV}/{conversation_id}/messages", json=body)

    assert first.status_code == 200 and second.status_code == 200
    # The retry answers with the message that EXISTS, not with an error the
    # client cannot act on.
    assert first.json()["id"] == second.json()["id"]
    assert len(client.http.get(f"{CONV}/{conversation_id}/messages").json()) == 1


def test_an_empty_body_is_refused_rather_than_stored(
    client: Caller, world: World
) -> None:
    client.as_recruiter(world, world.tenant_a)
    conversation_id = _open_thread(client, world.candidate_a).json()["id"]
    refused = client.http.post(
        f"{CONV}/{conversation_id}/messages",
        json={"body": "   ", "client_token": "tok-blank"},
    )
    assert refused.status_code == 422


# ── Isolation ────────────────────────────────────────────────────────────────


def test_a_second_tenant_gets_not_found_for_the_first_tenants_thread(
    client: Caller, world: World
) -> None:
    """404 and not 403: distinguishing them would confirm that another customer
    holds this id to somebody who cannot read it."""
    client.as_recruiter(world, world.tenant_a)
    conversation_id = _open_thread(client, world.candidate_a).json()["id"]

    client.as_recruiter(world, world.tenant_b)
    assert client.http.get(f"{CONV}/{conversation_id}/messages").status_code == 404
    assert (
        client.http.post(
            f"{CONV}/{conversation_id}/messages",
            json={"body": "Hello", "client_token": "tok-cross-tenant"},
        ).status_code
        == 404
    )
    assert client.http.post(f"{CONV}/{conversation_id}/read").status_code == 404


def test_a_candidate_who_is_not_linked_to_this_tenant_cannot_be_messaged(
    client: Caller, world: World
) -> None:
    """Without this check a recruiter could open a thread with anybody in the
    databank by id, which is a cross-tenant read wearing a convenience
    feature's clothes."""
    client.as_recruiter(world, world.tenant_a)
    assert _open_thread(client, world.candidate_b).status_code == 404


def test_the_listing_shows_only_this_tenants_conversations(
    client: Caller, world: World
) -> None:
    client.as_recruiter(world, world.tenant_a)
    mine = _open_thread(client, world.candidate_a).json()["id"]
    client.http.post(
        f"{CONV}/{mine}/messages",
        json={"body": "Hello from A.", "client_token": "tok-list-a"},
    )

    client.as_recruiter(world, world.tenant_b)
    theirs = _open_thread(client, world.candidate_b).json()["id"]
    client.http.post(
        f"{CONV}/{theirs}/messages",
        json={"body": "Hello from B.", "client_token": "tok-list-b"},
    )
    visible = {row["id"] for row in client.http.get(CONV).json()}
    assert theirs in visible
    assert mine not in visible


def test_a_candidate_thread_nobody_wrote_in_is_not_listed(
    client: Caller, world: World
) -> None:
    """Opening a thread is a side effect of clicking "Message". A list of
    threads that say nothing is a list of clicks, so an empty CANDIDATE thread
    is not listed until somebody writes in it."""
    client.as_recruiter(world, world.tenant_a)
    opened = _open_thread(client, world.candidate_a).json()["id"]
    assert opened not in {row["id"] for row in client.http.get(CONV).json()}

    client.http.post(
        f"{CONV}/{opened}/messages",
        json={"body": "Now it has a message.", "client_token": "tok-first-word"},
    )
    assert opened in {row["id"] for row in client.http.get(CONV).json()}


# ── The BGV refusal ──────────────────────────────────────────────────────────


def test_a_bgv_thread_refuses_a_chat_send(client: Caller, world: World) -> None:
    """An employer message is a verification act with its own capability, its
    own status transition and its own audit row. If chat could send one, the
    verification record and the email would drift apart silently."""
    sessions = _sessions()
    conversation_id = uuid.uuid4()
    employment_id = uuid.uuid4()
    verification_id = uuid.uuid4()

    async def _make_bgv_thread() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    # A real employment and a real verification, because the
                    # conversation's foreign key insists on one. Faking the id
                    # would test a row shape the database refuses.
                    await session.execute(
                        sa.text(
                            "INSERT INTO candidate_employments (id, candidate_id, "
                            " employer_name, designation, started_on, ended_on, "
                            " hr_name, hr_email, created_at) "
                            "VALUES (:id, :cid, 'Acme Systems', 'Engineer', "
                            " DATE '2021-01-01', DATE '2023-01-01', 'Meera Nair', "
                            " :email, now())"
                        ),
                        {
                            "id": str(employment_id),
                            "cid": str(world.candidate_a),
                            "email": f"hr-{employment_id.hex[:8]}@acme.test",
                        },
                    )
                    # The verification FIRST, with no conversation yet: the
                    # two tables reference each other, so one of them has to be
                    # written incomplete and filled in. This is the same order
                    # `POST /bgv/candidates/{id}/initiate` uses.
                    await session.execute(
                        sa.text(
                            "INSERT INTO bgv_verifications (id, tenant_id, "
                            " candidate_id, candidate_employment_id, status, "
                            " created_at) "
                            "VALUES (:id, :tid, :cid, :eid, 'pending', now())"
                        ),
                        {
                            "id": str(verification_id),
                            "tid": str(world.tenant_a),
                            "cid": str(world.candidate_a),
                            "eid": str(employment_id),
                        },
                    )
                    await session.execute(
                        sa.text(
                            "INSERT INTO conversations (id, tenant_id, kind, subject, "
                            " candidate_id, bgv_verification_id, status, "
                            " thread_token, created_at) "
                            "VALUES (:id, :tid, :kind, :subject, :cid, :vid, 'open', "
                            " :token, now())"
                        ),
                        {
                            "id": str(conversation_id),
                            "tid": str(world.tenant_a),
                            "kind": KIND_BGV,
                            "subject": "Karthik Kumar, Acme, background verification",
                            "cid": str(world.candidate_a),
                            "vid": str(verification_id),
                            "token": conversation_service.mint_thread_token(),
                        },
                    )
                    await session.execute(
                        sa.text(
                            "UPDATE bgv_verifications SET conversation_id = :conv "
                            "WHERE id = :id"
                        ),
                        {"conv": str(conversation_id), "id": str(verification_id)},
                    )

    _run(_make_bgv_thread())

    client.as_recruiter(world, world.tenant_a)
    refused = client.http.post(
        f"{CONV}/{conversation_id}/messages",
        json={"body": "Please confirm.", "client_token": "tok-bgv-attempt"},
    )
    assert refused.status_code == 409
    assert "background verification panel" in refused.json()["detail"]

    # Reading it is still allowed: the refusal is on SENDING, so a recruiter can
    # follow the employer exchange from the same screen.
    assert client.http.get(f"{CONV}/{conversation_id}/messages").status_code == 200


# ── Unread ───────────────────────────────────────────────────────────────────


def test_your_own_message_is_never_unread_and_the_other_partys_is(
    client: Caller, world: World
) -> None:
    client.as_recruiter(world, world.tenant_a)
    conversation_id = _open_thread(client, world.candidate_a).json()["id"]
    client.http.post(
        f"{CONV}/{conversation_id}/messages",
        json={"body": "First contact.", "client_token": "tok-unread-own"},
    )
    # A thread does not become unread because you answered it.
    assert client.http.get(f"{CONV}/unread").json()["total"] == 0

    sessions = _sessions()

    async def _candidate_replies() -> None:
        async with sessions() as session:
            async with session.begin():
                async with tenant_scope(session, world.tenant_a):
                    await conversation_service.post_message(
                        session,
                        conversation_id=uuid.UUID(conversation_id),
                        tenant_id=world.tenant_a,
                        author_party=PARTY_CANDIDATE,
                        body="Thursday works for me.",
                        client_token="tok-unread-reply",
                    )

    _run(_candidate_replies())

    assert client.http.get(f"{CONV}/unread").json()["total"] == 1
    assert client.http.post(f"{CONV}/{conversation_id}/read").status_code == 200
    assert client.http.get(f"{CONV}/unread").json()["total"] == 0


# ── The ordering that matters ────────────────────────────────────────────────


def test_the_notification_is_published_only_after_the_row_is_committed(
    client: Caller, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug this prevents is invisible on one instance and constant on four.

    A publish inside the request transaction announces a message that another
    API task cannot yet read. The check is made from a SEPARATE connection at
    the exact moment the publish runs, which is what the other task sees.
    """
    client.as_recruiter(world, world.tenant_a)
    conversation_id = _open_thread(client, world.candidate_a).json()["id"]

    sessions = _sessions()
    observed: list[tuple[dict, bool]] = []

    async def _visible_elsewhere(message_id: str) -> bool:
        async with sessions() as session:
            async with superadmin_scope(session):
                found = (
                    await session.execute(
                        sa.text("SELECT 1 FROM conversation_messages WHERE id = :mid"),
                        {"mid": message_id},
                    )
                ).scalar()
        return found is not None

    async def _fake_publish(payload: dict) -> None:
        observed.append((payload, await _visible_elsewhere(payload["message"]["id"])))

    monkeypatch.setattr(realtime.hub, "publish", _fake_publish)

    sent = client.http.post(
        f"{CONV}/{conversation_id}/messages",
        json={"body": "Committed before announced.", "client_token": "tok-order"},
    )
    assert sent.status_code == 200

    assert _settle(lambda: len(observed) == 1), (
        "the message was stored but no notification was ever published"
    )
    payload, was_committed = observed[0]
    assert was_committed, (
        "the notification was published before the transaction committed, so "
        "another API instance would announce a message it cannot read"
    )
    assert payload["type"] == "message"
    assert payload["tenant_id"] == str(world.tenant_a)
    assert payload["conversation_id"] == conversation_id
    assert payload["message"]["body"] == "Committed before announced."


def test_a_refused_send_publishes_nothing(
    client: Caller, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A notification for a message that was never stored is worse than none:
    every listening tab would render a message that does not exist."""
    client.as_recruiter(world, world.tenant_a)
    conversation_id = _open_thread(client, world.candidate_a).json()["id"]

    published: list[dict] = []

    async def _fake_publish(payload: dict) -> None:
        published.append(payload)

    monkeypatch.setattr(realtime.hub, "publish", _fake_publish)

    refused = client.http.post(
        f"{CONV}/{conversation_id}/messages",
        json={"body": "  ", "client_token": "tok-refused-publish"},
    )
    assert refused.status_code == 422
    # Waited for, not merely observed as absent right away: a publish that was
    # simply slow would pass an immediate check and still be the bug.
    assert not _settle(lambda: bool(published))
    assert published == []


# ── The candidate's own side ─────────────────────────────────────────────────
#
# A candidate has NO TENANT, so RLS by tenant cannot protect these routes and
# `get_candidate_db` runs in the bypass scope. Every guarantee is therefore in
# the handler's own WHERE clause, and a WHERE clause is exactly the kind of
# protection that is one careless edit away from being dropped. These are the
# assertions that would catch that edit.


@pytest.fixture
def candidate_client(world: World) -> Iterator[Caller]:
    """A signed-in candidate, on the candidate audience and the bypass scope.

    Built exactly as the production dependencies build it, including the bypass
    scope, so a cross-candidate case here is refused by the handler rather than
    by a tenant filter that would not exist in production.
    """
    sessions = _sessions()
    caller = Caller()

    async def _current_candidate() -> CurrentUser:
        assert caller.principal is not None
        return caller.principal

    async def _candidate_db():
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    yield session

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_candidate] = _current_candidate
    app.dependency_overrides[get_candidate_db] = _candidate_db
    try:
        with TestClient(app) as http:
            caller.http = http
            yield caller
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def _sign_in_as_candidate(caller: Caller, world: World, candidate: uuid.UUID) -> None:
    """Give the candidate a portal user LINKED to their record.

    AMENDED Phase 6: the candidate routes resolve through
    `candidate_identity.require_candidate`, which reads `candidates.user_id`
    and nothing else. The email fallback this used to rely on matched an
    address nobody had verified; linking now happens at sign-in, and only for
    a Firebase-verified address (`candidate_identity.link_on_sign_in`).
    """
    user_id = uuid.uuid4()
    sessions = _sessions()

    async def _make_user() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    email = (
                        await session.execute(
                            sa.text("SELECT email FROM candidates WHERE id = :cid"),
                            {"cid": str(candidate)},
                        )
                    ).scalar()
                    await session.execute(
                        sa.text(
                            "INSERT INTO users (id, tenant_id, email, full_name, "
                            " role, status) "
                            "VALUES (:id, NULL, :email, 'Karthik Kumar', "
                            " 'candidate', 'active')"
                        ),
                        {"id": str(user_id), "email": email},
                    )
                    await session.execute(
                        sa.text("UPDATE candidates SET user_id = :uid WHERE id = :cid"),
                        {"uid": str(user_id), "cid": str(candidate)},
                    )

    _run(_make_user())
    caller.principal = CurrentUser(
        user_id=user_id,
        tenant_id=None,
        role=Role.candidate,
        audience=AUDIENCE_CANDIDATE,
    )


def test_a_candidate_reads_and_answers_their_own_thread(
    client: Caller, candidate_client: Caller, world: World
) -> None:
    """A thread only one side can write to is not a conversation."""
    client.as_recruiter(world, world.tenant_a)
    conversation_id = _open_thread(client, world.candidate_a).json()["id"]
    client.http.post(
        f"{CONV}/{conversation_id}/messages",
        json={"body": "Are you free on Thursday?", "client_token": "tok-to-candidate"},
    )

    _sign_in_as_candidate(candidate_client, world, world.candidate_a)
    listed = candidate_client.http.get(f"{CONV}/me")
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()] == [conversation_id]
    # The COMPANY is named. A candidate's threads span employers, and a list
    # without it is a list of indistinguishable rows.
    assert listed.json()[0]["company_name"].startswith("Conv-API-A")

    read = candidate_client.http.get(f"{CONV}/me/{conversation_id}/messages")
    assert [m["body"] for m in read.json()] == ["Are you free on Thursday?"]

    replied = candidate_client.http.post(
        f"{CONV}/me/{conversation_id}/messages",
        json={"body": "Thursday works for me.", "client_token": "tok-candidate-reply"},
    )
    assert replied.status_code == 200, replied.text
    assert replied.json()["author_party"] == "candidate"
    # NULL, deliberately: writing a candidate's user id into the column the
    # recruiter's side joins against `users` would make them resolvable through
    # the employer's own directory.
    assert replied.json()["author_user_id"] is None

    # And the recruiter sees it, which is the half that makes it a conversation.
    back = client.http.get(f"{CONV}/{conversation_id}/messages")
    assert [m["body"] for m in back.json()][-1] == "Thursday works for me."


def test_a_candidate_cannot_reach_another_candidates_thread(
    client: Caller, candidate_client: Caller, world: World
) -> None:
    client.as_recruiter(world, world.tenant_b)
    theirs = _open_thread(client, world.candidate_b).json()["id"]

    _sign_in_as_candidate(candidate_client, world, world.candidate_a)
    assert (
        candidate_client.http.get(f"{CONV}/me/{theirs}/messages").status_code == 404
    )
    assert (
        candidate_client.http.post(
            f"{CONV}/me/{theirs}/messages",
            json={"body": "Not mine.", "client_token": "tok-other-candidate"},
        ).status_code
        == 404
    )


def test_a_candidate_cannot_reach_the_bgv_thread_about_themselves(
    candidate_client: Caller, world: World
) -> None:
    """THE ASSERTION THIS SECTION EXISTS FOR.

    A BGV conversation carries the candidate's own `candidate_id`, so a lookup
    that checked only "is this yours" would hand the candidate the correspondence
    between a recruiter and their former employer's HR contact. The kind is
    checked too, and this is what keeps that check from being simplified away.
    """
    sessions = _sessions()
    conversation_id = uuid.uuid4()
    employment_id = uuid.uuid4()
    verification_id = uuid.uuid4()

    async def _make_bgv_thread() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text(
                            "INSERT INTO candidate_employments (id, candidate_id, "
                            " employer_name, designation, started_on, ended_on, "
                            " hr_name, hr_email, created_at) "
                            "VALUES (:id, :cid, 'Acme Systems', 'Engineer', "
                            " DATE '2021-01-01', DATE '2023-01-01', 'Meera Nair', "
                            " :email, now())"
                        ),
                        {
                            "id": str(employment_id),
                            "cid": str(world.candidate_a),
                            "email": f"hr-{employment_id.hex[:8]}@acme.test",
                        },
                    )
                    await session.execute(
                        sa.text(
                            "INSERT INTO bgv_verifications (id, tenant_id, "
                            " candidate_id, candidate_employment_id, status, "
                            " created_at) "
                            "VALUES (:id, :tid, :cid, :eid, 'pending', now())"
                        ),
                        {
                            "id": str(verification_id),
                            "tid": str(world.tenant_a),
                            "cid": str(world.candidate_a),
                            "eid": str(employment_id),
                        },
                    )
                    await session.execute(
                        sa.text(
                            "INSERT INTO conversations (id, tenant_id, kind, subject, "
                            " candidate_id, bgv_verification_id, status, "
                            " thread_token, created_at) "
                            "VALUES (:id, :tid, :kind, :subject, :cid, :vid, 'open', "
                            " :token, now())"
                        ),
                        {
                            "id": str(conversation_id),
                            "tid": str(world.tenant_a),
                            "kind": KIND_BGV,
                            "subject": "Karthik Kumar, Acme, background verification",
                            "cid": str(world.candidate_a),
                            "vid": str(verification_id),
                            "token": conversation_service.mint_thread_token(),
                        },
                    )
                    await session.execute(
                        sa.text(
                            "INSERT INTO conversation_messages (id, conversation_id, "
                            " tenant_id, author_party, body, channel, "
                            " delivery_status, created_at) "
                            "VALUES (gen_random_uuid(), :cid, :tid, 'employer_hr', "
                            " 'He left after a disagreement.', 'email', "
                            " 'delivered', now())"
                        ),
                        {
                            "cid": str(conversation_id),
                            "tid": str(world.tenant_a),
                        },
                    )

    _run(_make_bgv_thread())

    _sign_in_as_candidate(candidate_client, world, world.candidate_a)
    assert candidate_client.http.get(f"{CONV}/me").json() == []
    assert (
        candidate_client.http.get(
            f"{CONV}/me/{conversation_id}/messages"
        ).status_code
        == 404
    )
    assert (
        candidate_client.http.post(
            f"{CONV}/me/{conversation_id}/messages",
            json={"body": "Let me explain.", "client_token": "tok-bgv-candidate"},
        ).status_code
        == 404
    )
