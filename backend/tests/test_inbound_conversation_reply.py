"""An employer's reply lands in the thread its ADDRESS names.

WHY THE ADDRESS AND NOT THE SUBJECT
-------------------------------------
Every email-threading bug this product could have has the same shape: the reply
arrives, nothing matches it, and nobody finds out until a recruiter asks why an
employer never answered. Matching on a subject line loses to "Re: Fwd: Re:", to
a translated prefix, to a client that rewrites the subject, and to a recruiter
forwarding the thread. The Reply-To carries the conversation's own token, and an
address is the one part of a message every mail system on the path reproduces
verbatim.

WHAT IS DELIBERATELY NOT DONE HERE
------------------------------------
The reply decides nothing. `responded_at` is stamped and the verification STATUS
is untouched, because a person reads the employer's answer and says which it
was. Inferring "verified" from the arrival of a reply would be an automated
hiring decision wearing a convenience feature's clothes, and the last assertion
in this file is what keeps it from being added by accident.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import get_public_db
from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.main import app
from app.models.conversation import KIND_BGV, PARTY_EMPLOYER_HR
from app.services import conversations

INBOUND = "/api/v1/verification/inbound-email"
DOMAIN = "reply.readypick.test"


def _run(coro):
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


class Thread:
    def __init__(self) -> None:
        self.tenant = uuid.uuid4()
        self.candidate = uuid.uuid4()
        self.employment = uuid.uuid4()
        self.verification = uuid.uuid4()
        self.conversation = uuid.uuid4()
        self.token = conversations.mint_thread_token()


@pytest.fixture
def thread(monkeypatch: pytest.MonkeyPatch) -> Iterator[Thread]:
    if not _run(_reachable()):
        pytest.skip("no database reachable -- skipping inbound reply test")

    # `reply_address` reads the setting through the cached settings object, so
    # the attribute is patched rather than the environment.
    monkeypatch.setattr(get_settings(), "inbound_email_domain", DOMAIN, raising=False)
    conversations._warned_no_inbound_domain = False

    t = Thread()
    sessions = _sessions()

    async def _seed() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text(
                            "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
                            "VALUES (:id, :name, :domain, 'pending')"
                        ),
                        {
                            "id": str(t.tenant),
                            "name": f"Inbound-{t.tenant.hex[:6]}",
                            "domain": f"{t.tenant.hex[:10]}.inbound.test",
                        },
                    )
                    await session.execute(
                        sa.text(
                            "INSERT INTO candidates (id, full_name, email, "
                            " consent_databank, created_at) "
                            "VALUES (:id, 'Karthik Kumar', :email, false, now())"
                        ),
                        {
                            "id": str(t.candidate),
                            "email": f"{t.candidate.hex[:10]}@inbound.test",
                        },
                    )
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
                            "id": str(t.employment),
                            "cid": str(t.candidate),
                            "email": f"hr-{t.employment.hex[:8]}@acme.test",
                        },
                    )
                    # The verification first: the two tables reference each
                    # other, so one is written incomplete and filled in.
                    await session.execute(
                        sa.text(
                            "INSERT INTO bgv_verifications (id, tenant_id, "
                            " candidate_id, candidate_employment_id, status, "
                            " first_sent_at, created_at) "
                            "VALUES (:id, :tid, :cid, :eid, 'pending', now(), now())"
                        ),
                        {
                            "id": str(t.verification),
                            "tid": str(t.tenant),
                            "cid": str(t.candidate),
                            "eid": str(t.employment),
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
                            "id": str(t.conversation),
                            "tid": str(t.tenant),
                            "kind": KIND_BGV,
                            "subject": "Karthik Kumar, Acme, background verification",
                            "cid": str(t.candidate),
                            "vid": str(t.verification),
                            "token": t.token,
                        },
                    )
                    await session.execute(
                        sa.text(
                            "UPDATE bgv_verifications SET conversation_id = :conv "
                            "WHERE id = :id"
                        ),
                        {"conv": str(t.conversation), "id": str(t.verification)},
                    )

    async def _teardown() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text("DELETE FROM tenants WHERE id = :id"),
                        {"id": str(t.tenant)},
                    )
                    await session.execute(
                        sa.text("DELETE FROM candidates WHERE id = :id"),
                        {"id": str(t.candidate)},
                    )

    _run(_seed())
    try:
        yield t
    finally:
        _run(_teardown())


@pytest.fixture
def client(thread: Thread) -> Iterator[TestClient]:
    sessions = _sessions()

    async def _public_db():
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    yield session

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_public_db] = _public_db
    try:
        with TestClient(app) as http:
            yield http
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def _messages(conversation_id: uuid.UUID) -> list[dict]:
    sessions = _sessions()

    async def _read() -> list[dict]:
        async with sessions() as session:
            async with superadmin_scope(session):
                rows = (
                    (
                        await session.execute(
                            sa.text(
                                "SELECT body, author_party, channel, author_email "
                                "FROM conversation_messages "
                                "WHERE conversation_id = :cid ORDER BY created_at"
                            ),
                            {"cid": str(conversation_id)},
                        )
                    )
                    .mappings()
                    .all()
                )
        return [dict(row) for row in rows]

    return _run(_read())


def _verification(verification_id: uuid.UUID) -> dict:
    sessions = _sessions()

    async def _read() -> dict:
        async with sessions() as session:
            async with superadmin_scope(session):
                row = (
                    (
                        await session.execute(
                            sa.text(
                                "SELECT status, responded_at, decided_by, decided_at "
                                "FROM bgv_verifications WHERE id = :vid"
                            ),
                            {"vid": str(verification_id)},
                        )
                    )
                    .mappings()
                    .first()
                )
        return dict(row)

    return _run(_read())


def test_the_reply_address_carries_the_threads_own_token(thread: Thread) -> None:
    address = conversations.reply_address(thread.token)
    assert address == f"conversations+{thread.token}@{DOMAIN}"
    assert conversations.token_from_address(address) == thread.token


def test_a_reply_lands_in_the_thread_its_address_names(
    client: TestClient, thread: Thread
) -> None:
    response = client.post(
        INBOUND,
        json={
            "to": [conversations.reply_address(thread.token)],
            "from": "meera.nair@acme.test",
            # A subject that matches NOTHING, on purpose: routing must not
            # depend on it.
            "subject": "Antwort: (kein Betreff)",
            "text": "Yes, Karthik worked with us in that role for those dates.",
            "messageId": "<reply-1@acme.test>",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["matched"] is True

    stored = _messages(thread.conversation)
    assert len(stored) == 1
    assert stored[0]["author_party"] == PARTY_EMPLOYER_HR
    assert stored[0]["channel"] == "email"
    assert stored[0]["author_email"] == "meera.nair@acme.test"
    assert "worked with us" in stored[0]["body"]


def test_the_same_reply_delivered_twice_is_one_message(
    client: TestClient, thread: Thread
) -> None:
    """SNS delivers at least once, so a redelivery is the DEFAULT behaviour
    unless something prevents it. The Message-ID is what prevents it."""
    payload = {
        "to": [conversations.reply_address(thread.token)],
        "from": "meera.nair@acme.test",
        "text": "Confirming the dates.",
        "messageId": "<reply-duplicate@acme.test>",
    }
    assert client.post(INBOUND, json=payload).json()["matched"] is True
    assert client.post(INBOUND, json=payload).json()["matched"] is True
    assert len(_messages(thread.conversation)) == 1


def test_a_token_on_the_cc_line_still_routes(
    client: TestClient, thread: Thread
) -> None:
    """A reply-all puts the thread address on Cc rather than To, and a handler
    that only read To would drop exactly the replies a recruiter was copied
    on."""
    response = client.post(
        INBOUND,
        json={
            "to": ["someone.else@acme.test"],
            "cc": [conversations.reply_address(thread.token)],
            "from": "meera.nair@acme.test",
            "text": "Copying the team.",
            "messageId": "<reply-cc@acme.test>",
        },
    )
    assert response.json()["matched"] is True
    assert len(_messages(thread.conversation)) == 1


def test_an_empty_reply_is_recorded_rather_than_dropped(
    client: TestClient, thread: Thread
) -> None:
    """An attachment with no words is a real reply. "The employer has answered"
    is the fact the recruiter is waiting for, so it is written down."""
    response = client.post(
        INBOUND,
        json={
            "to": [conversations.reply_address(thread.token)],
            "from": "meera.nair@acme.test",
            "text": "   ",
            "messageId": "<reply-empty@acme.test>",
        },
    )
    assert response.json()["matched"] is True
    assert len(_messages(thread.conversation)) == 1


def test_a_token_nobody_holds_matches_nothing_and_writes_nothing(
    client: TestClient, thread: Thread
) -> None:
    stranger = conversations.reply_address(conversations.mint_thread_token())
    response = client.post(
        INBOUND,
        json={
            "to": [stranger],
            "from": "somebody@elsewhere.test",
            "text": "Wrong thread.",
            "messageId": "<reply-stranger@elsewhere.test>",
        },
    )
    assert response.status_code == 200
    assert response.json()["matched"] is False
    assert _messages(thread.conversation) == []


def test_a_reply_stamps_responded_at_and_decides_absolutely_nothing(
    client: TestClient, thread: Thread
) -> None:
    """The assertion that keeps a hiring decision out of a mail parser.

    A reply arriving means a person has something to read. It does not mean the
    employment is verified, and nothing here may ever make it mean that.
    """
    before = _verification(thread.verification)
    assert before["responded_at"] is None

    client.post(
        INBOUND,
        json={
            "to": [conversations.reply_address(thread.token)],
            "from": "meera.nair@acme.test",
            "text": "Yes, that is correct, he worked here.",
            "messageId": "<reply-decides-nothing@acme.test>",
        },
    )

    after = _verification(thread.verification)
    assert after["responded_at"] is not None
    assert after["status"] == "pending"
    assert after["decided_by"] is None
    assert after["decided_at"] is None


def test_with_no_inbound_domain_there_is_no_reply_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployment that has not provisioned inbound mail still SENDS. What it
    must not do is pretend the reply will come back to the thread."""
    monkeypatch.setattr(get_settings(), "inbound_email_domain", "", raising=False)
    assert conversations.reply_address(conversations.mint_thread_token()) is None
