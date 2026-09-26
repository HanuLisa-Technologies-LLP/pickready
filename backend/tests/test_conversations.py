"""Conversation persistence: idempotency, isolation, and the unread watermark.

These are the three properties a chat system gets wrong quietly. A duplicate
message looks like a user double-clicking; a cross-tenant read looks like
nothing at all until it is a breach; an unread badge that counts backwards
looks like a UI glitch. All three are asserted against a real database, because
all three are enforced by constraints and SQL rather than by Python.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.models.conversation import CHANNEL_EMAIL, PARTY_EMPLOYER_HR, PARTY_RECRUITER
from app.services import conversations

pytestmark = pytest.mark.asyncio


async def _factory_or_skip():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 -- any connect failure means "no DB here"
        await engine.dispose()
        pytest.skip("no database reachable -- skipping conversation tests")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _tenant(session, label: str) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
            "VALUES (:id, :n, :d, 'pending')"
        ),
        {
            "id": tenant_id,
            "n": f"{label}-{tenant_id.hex[:6]}",
            "d": f"{tenant_id.hex[:8]}.example.com",
        },
    )
    return tenant_id


async def _user(session, tenant_id: uuid.UUID) -> uuid.UUID:
    user_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO users (id, tenant_id, role, email, status) "
            "VALUES (:id, :tid, 'recruiter', :email, 'active')"
        ),
        {"id": user_id, "tid": tenant_id, "email": f"r-{user_id.hex[:8]}@example.com"},
    )
    return user_id


async def _candidate(session) -> uuid.UUID:
    candidate_id = uuid.uuid4()
    await session.execute(
        text(
            "INSERT INTO candidates (id, full_name, email, consent_databank, created_at) "
            "VALUES (:id, 'Karthik Kumar', :email, false, now())"
        ),
        {"id": candidate_id, "email": f"c-{candidate_id.hex[:8]}@example.com"},
    )
    return candidate_id


async def test_one_candidate_thread_per_tenant_is_reused() -> None:
    """A second thread would split a conversation with one person across two
    screens, and whoever read the newer one would not know the older existed."""
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    tenant_id = await _tenant(session, "CONV")
                    user_id = await _user(session, tenant_id)
                    candidate_id = await _candidate(session)

                    first = await conversations.ensure_candidate_conversation(
                        session,
                        tenant_id=tenant_id,
                        candidate_id=candidate_id,
                        candidate_name="Karthik Kumar",
                        actor_user_id=user_id,
                    )
                    second = await conversations.ensure_candidate_conversation(
                        session,
                        tenant_id=tenant_id,
                        candidate_id=candidate_id,
                        candidate_name="Karthik Kumar",
                        actor_user_id=user_id,
                    )
                    assert first["id"] == second["id"]
                await session.rollback()
    finally:
        await engine.dispose()


async def test_a_retried_send_produces_one_message_not_two() -> None:
    """The double-clicked button, the websocket replay and the task retry all
    arrive here carrying the same client token."""
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    tenant_id = await _tenant(session, "CONV")
                    user_id = await _user(session, tenant_id)
                    candidate_id = await _candidate(session)
                    conversation = await conversations.ensure_candidate_conversation(
                        session,
                        tenant_id=tenant_id,
                        candidate_id=candidate_id,
                        candidate_name="Karthik Kumar",
                        actor_user_id=user_id,
                    )
                    token = uuid.uuid4().hex

                    first = await conversations.post_message(
                        session,
                        conversation_id=uuid.UUID(str(conversation["id"])),
                        tenant_id=tenant_id,
                        author_party=PARTY_RECRUITER,
                        author_user_id=user_id,
                        body="Are you available for a call on Thursday?",
                        client_token=token,
                    )
                    second = await conversations.post_message(
                        session,
                        conversation_id=uuid.UUID(str(conversation["id"])),
                        tenant_id=tenant_id,
                        author_party=PARTY_RECRUITER,
                        author_user_id=user_id,
                        body="Are you available for a call on Thursday?",
                        client_token=token,
                    )
                    assert first["id"] == second["id"], "a retry created a second row"

                    count = (
                        await session.execute(
                            text(
                                "SELECT count(*) FROM conversation_messages "
                                "WHERE conversation_id = :cid"
                            ),
                            {"cid": conversation["id"]},
                        )
                    ).scalar_one()
                    assert count == 1
                await session.rollback()
    finally:
        await engine.dispose()


async def test_a_redelivered_inbound_reply_is_stored_once() -> None:
    """SES and SNS both deliver at least once. The provider's own message id is
    the idempotency key, so the same reply arriving twice is one row."""
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    tenant_id = await _tenant(session, "CONV")
                    user_id = await _user(session, tenant_id)
                    candidate_id = await _candidate(session)
                    conversation = await conversations.ensure_candidate_conversation(
                        session,
                        tenant_id=tenant_id,
                        candidate_id=candidate_id,
                        candidate_name="Karthik Kumar",
                        actor_user_id=user_id,
                    )
                    provider_id = f"<{uuid.uuid4().hex}@email.amazonses.com>"

                    first = await conversations.post_message(
                        session,
                        conversation_id=uuid.UUID(str(conversation["id"])),
                        tenant_id=tenant_id,
                        author_party=PARTY_EMPLOYER_HR,
                        author_email="asha@companya.example",
                        author_name="Asha R",
                        body="Confirming the dates are correct.",
                        channel=CHANNEL_EMAIL,
                        inbound_message_id=provider_id,
                    )
                    second = await conversations.post_message(
                        session,
                        conversation_id=uuid.UUID(str(conversation["id"])),
                        tenant_id=tenant_id,
                        author_party=PARTY_EMPLOYER_HR,
                        author_email="asha@companya.example",
                        author_name="Asha R",
                        body="Confirming the dates are correct.",
                        channel=CHANNEL_EMAIL,
                        inbound_message_id=provider_id,
                    )
                    assert first["id"] == second["id"]
                await session.rollback()
    finally:
        await engine.dispose()


async def test_a_conversation_from_another_tenant_is_not_found() -> None:
    """Not "forbidden": distinguishing the two would confirm the existence of
    another customer's data to somebody who cannot read it."""
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    tenant_a = await _tenant(session, "CONV-A")
                    tenant_b = await _tenant(session, "CONV-B")
                    user_id = await _user(session, tenant_a)
                    candidate_id = await _candidate(session)
                    conversation = await conversations.ensure_candidate_conversation(
                        session,
                        tenant_id=tenant_a,
                        candidate_id=candidate_id,
                        candidate_name="Karthik Kumar",
                        actor_user_id=user_id,
                    )
                    with pytest.raises(conversations.ConversationNotFound):
                        await conversations.authorize_participant(
                            session,
                            conversation_id=uuid.UUID(str(conversation["id"])),
                            tenant_id=tenant_b,
                        )
                await session.rollback()
    finally:
        await engine.dispose()


async def test_unread_ignores_your_own_messages_and_never_counts_backwards() -> None:
    """A thread does not become unread because you answered it, and a second
    tab catching up must not un-read what the first tab already read."""
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    tenant_id = await _tenant(session, "CONV")
                    user_id = await _user(session, tenant_id)
                    candidate_id = await _candidate(session)
                    conversation = await conversations.ensure_candidate_conversation(
                        session,
                        tenant_id=tenant_id,
                        candidate_id=candidate_id,
                        candidate_name="Karthik Kumar",
                        actor_user_id=user_id,
                    )
                    conversation_id = uuid.UUID(str(conversation["id"]))

                    await conversations.post_message(
                        session,
                        conversation_id=conversation_id,
                        tenant_id=tenant_id,
                        author_party=PARTY_RECRUITER,
                        author_user_id=user_id,
                        body="My own message, which is not unread to me.",
                        client_token=uuid.uuid4().hex,
                    )
                    counts = await conversations.unread_counts(
                        session, tenant_id=tenant_id, user_id=user_id
                    )
                    assert counts.get(str(conversation_id), 0) == 0

                    await conversations.post_message(
                        session,
                        conversation_id=conversation_id,
                        tenant_id=tenant_id,
                        author_party=PARTY_EMPLOYER_HR,
                        author_email="asha@companya.example",
                        body="A reply the recruiter has not read.",
                        channel=CHANNEL_EMAIL,
                        inbound_message_id=uuid.uuid4().hex,
                    )
                    counts = await conversations.unread_counts(
                        session, tenant_id=tenant_id, user_id=user_id
                    )
                    assert counts.get(str(conversation_id)) == 1

                    now = datetime.now(timezone.utc)
                    await conversations.mark_read(
                        session,
                        conversation_id=conversation_id,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        now=now,
                    )
                    # A stale tab reports an EARLIER watermark. It must not win.
                    await conversations.mark_read(
                        session,
                        conversation_id=conversation_id,
                        tenant_id=tenant_id,
                        user_id=user_id,
                        now=now - timedelta(hours=1),
                    )
                    counts = await conversations.unread_counts(
                        session, tenant_id=tenant_id, user_id=user_id
                    )
                    assert counts.get(str(conversation_id), 0) == 0
                await session.rollback()
    finally:
        await engine.dispose()


async def test_seven_employers_produce_seven_independent_threads() -> None:
    """The brief's worked example, at the conversation layer."""
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    tenant_id = await _tenant(session, "CONV")
                    user_id = await _user(session, tenant_id)
                    candidate_id = await _candidate(session)
                    subjects = set()
                    for index in range(7):
                        employment_id = uuid.uuid4()
                        await session.execute(
                            text(
                                "INSERT INTO candidate_employments (id, candidate_id, "
                                " employer_name, designation, started_on, ended_on, "
                                " hr_name, hr_email) VALUES (:id, :cid, :name, 'Dev', "
                                " DATE '2018-01-01', DATE '2019-01-01', 'HR', :email)"
                            ),
                            {
                                "id": employment_id,
                                "cid": candidate_id,
                                "name": f"Company {index}",
                                "email": f"hr@company{index}.example",
                            },
                        )
                        verification_id = uuid.uuid4()
                        await session.execute(
                            text(
                                "INSERT INTO bgv_verifications (id, tenant_id, "
                                " candidate_id, candidate_employment_id, status) "
                                "VALUES (:id, :tid, :cid, :eid, 'not_started')"
                            ),
                            {
                                "id": verification_id,
                                "tid": tenant_id,
                                "cid": candidate_id,
                                "eid": employment_id,
                            },
                        )
                        conversation = await conversations.create_bgv_conversation(
                            session,
                            tenant_id=tenant_id,
                            verification_id=verification_id,
                            candidate_id=candidate_id,
                            candidate_name="Karthik Kumar",
                            employer_name=f"Company {index}",
                            hr_name="Asha R",
                            hr_email=f"hr@company{index}.example",
                            actor_user_id=user_id,
                        )
                        subjects.add(conversation["subject"])

                    # Seven threads, seven DIFFERENT titles, seven tokens.
                    assert len(subjects) == 7
                    tokens = (
                        await session.execute(
                            text(
                                "SELECT count(DISTINCT thread_token) FROM conversations "
                                "WHERE tenant_id = :tid AND kind = 'bgv'"
                            ),
                            {"tid": tenant_id},
                        )
                    ).scalar_one()
                    assert tokens == 7
                await session.rollback()
    finally:
        await engine.dispose()
