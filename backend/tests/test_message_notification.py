"""A recruiter writes; the candidate is told, once per burst (Phase 6 WP6-C).

Before this, a recruiter's message sat unread until the candidate happened to
sign in: no email, no Updates entry, nothing. `candidate_message_notifications`
writes both, from `pickready.notify_candidate_of_message`, and the debounce on
`conversations.candidate_notified_at` makes a burst one notification.

Everything is read back from a second connection.

Mutation checks: removing the "already read" check fails
`test_a_message_read_before_the_task_runs_is_not_announced`; claiming the
notification unconditionally fails `test_a_burst_is_one_notification`.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.config import get_settings
from app.models.conversation import PARTY_CANDIDATE, PARTY_RECRUITER
from app.services import candidate_message_notifications as notifications
from app.services import conversations
from app.workers import dispatch as dispatch_mod
from tests import comms_world

EM_DASH = chr(8212)
GRADE_WORDS = ("Highly Matching", "Moderately Matching", "Not Matching", "Matching")


@pytest.fixture
async def world():
    if not await comms_world.reachable():
        pytest.skip("no database reachable -- skipping notification tests")
    sessions = comms_world.factory()
    w = await comms_world.seed(sessions)
    try:
        yield w
    finally:
        await comms_world.cleanup(sessions, w)


async def _thread(world) -> uuid.UUID:
    sessions = comms_world.factory()
    async with sessions() as session:
        async with session.begin():
            await comms_world.bypass(session)
            conversation = await conversations.ensure_candidate_conversation(
                session,
                tenant_id=world.tenant,
                candidate_id=world.candidate,
                candidate_name="Karthik Kumar",
                actor_user_id=world.recruiter,
            )
            return uuid.UUID(str(conversation["id"]))


async def _say(world, thread, body: str, *, party=PARTY_RECRUITER, at=None) -> uuid.UUID:
    sessions = comms_world.factory()
    async with sessions() as session:
        async with session.begin():
            await comms_world.bypass(session)
            message = await conversations.post_message(
                session,
                conversation_id=thread,
                tenant_id=world.tenant,
                author_party=party,
                author_user_id=world.recruiter if party == PARTY_RECRUITER else None,
                body=body,
                client_token=f"tok-{uuid.uuid4().hex[:12]}",
                now=at,
            )
            return uuid.UUID(str(message["id"]))


async def _notify(thread, message) -> notifications.NotificationOutcome:
    sessions = comms_world.factory()
    async with sessions() as session:
        async with session.begin():
            await comms_world.bypass(session)
            return await notifications.notify(
                session, conversation_id=thread, message_id=message
            )


async def _emails(world) -> list[dict]:
    return await comms_world.rows(
        comms_world.factory(),
        "SELECT subject, body, conversation_id, dedupe_key, generated_by_ai, "
        " sent_by FROM email_log WHERE candidate_id = :cid "
        " AND email_type = 'message_notification' ORDER BY created_at",
        {"cid": str(world.candidate)},
    )


async def _updates(world) -> list[dict]:
    return await comms_world.rows(
        comms_world.factory(),
        "SELECT kind, title, body, link_path, emailed FROM candidate_updates "
        "WHERE candidate_id = :cid ORDER BY created_at",
        {"cid": str(world.candidate)},
    )


async def test_a_recruiter_message_is_one_email_and_one_update(world) -> None:
    thread = await _thread(world)
    message = await _say(world, thread, "Are you free on Thursday at 3pm?")
    outcome = await _notify(thread, message)
    assert outcome.notified and outcome.emailed

    emails = await _emails(world)
    assert len(emails) == 1
    email = emails[0]
    assert email["conversation_id"] == thread
    assert email["dedupe_key"] == f"message_notification:{message}"
    assert email["generated_by_ai"] is False and email["sent_by"] is None
    # The recruiter's words travel verbatim; that is the content.
    assert "Are you free on Thursday at 3pm?" in email["body"]
    assert f"/portal/messages?conversation={thread}" in email["body"]

    updates = await _updates(world)
    assert updates == [
        {
            "kind": "message_received",
            "title": "New message",
            "body": updates[0]["body"],
            "link_path": f"/portal/messages?conversation={thread}",
            "emailed": True,
        }
    ]
    assert updates[0]["body"].startswith("Comms Co")
    # And the email's own send is dispatched by the commit.
    assert "pickready.send_lifecycle_email" in dispatch_mod.recorded_names()


async def test_a_burst_is_one_notification(world) -> None:
    thread = await _thread(world)
    for body in ("One.", "Two.", "Three."):
        message = await _say(world, thread, body)
        await _notify(thread, message)
    assert len(await _emails(world)) == 1
    assert len(await _updates(world)) == 1


async def test_reading_the_thread_rearms_the_notification(world) -> None:
    thread = await _thread(world)
    await _notify(thread, await _say(world, thread, "One."))
    sessions = comms_world.factory()
    async with sessions() as session:
        async with session.begin():
            await comms_world.bypass(session)
            # Margins of seconds, because the stamp this is compared with is
            # the database's clock and the test runs on the host's.
            await conversations.mark_candidate_read(
                session,
                conversation_id=thread,
                candidate_id=world.candidate,
                now=datetime.now(timezone.utc) + timedelta(seconds=30),
            )
    later = datetime.now(timezone.utc) + timedelta(seconds=60)
    outcome = await _notify(thread, await _say(world, thread, "Two.", at=later))
    assert outcome.notified
    assert len(await _emails(world)) == 2


async def test_a_zero_debounce_notifies_every_message(world, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "candidate_message_notify_debounce_minutes", 0)
    thread = await _thread(world)
    for body in ("One.", "Two."):
        await _notify(thread, await _say(world, thread, body))
    assert len(await _emails(world)) == 2


async def test_a_message_read_before_the_task_runs_is_not_announced(world) -> None:
    thread = await _thread(world)
    message = await _say(world, thread, "Hello.")
    sessions = comms_world.factory()
    async with sessions() as session:
        async with session.begin():
            await comms_world.bypass(session)
            await conversations.mark_candidate_read(
                session,
                conversation_id=thread,
                candidate_id=world.candidate,
                now=datetime.now(timezone.utc) + timedelta(seconds=5),
            )
    outcome = await _notify(thread, message)
    assert outcome.reason == "already read"
    assert await _emails(world) == [] and await _updates(world) == []


async def test_the_candidates_own_message_announces_nothing(world) -> None:
    thread = await _thread(world)
    outcome = await _notify(
        thread, await _say(world, thread, "Thanks.", party=PARTY_CANDIDATE)
    )
    assert outcome.reason == "written by the candidate"
    assert await _emails(world) == []


async def test_a_redelivered_task_is_not_a_second_email(world, monkeypatch) -> None:
    """Even with no debounce, the same MESSAGE is announced once: the email's
    dedupe key names the message."""
    monkeypatch.setattr(get_settings(), "candidate_message_notify_debounce_minutes", 0)
    thread = await _thread(world)
    message = await _say(world, thread, "Hello.")
    await _notify(thread, message)
    await _notify(thread, message)
    assert len(await _emails(world)) == 1


# ── The copy ─────────────────────────────────────────────────────────────────


def _compose(reply_by_email: bool) -> tuple[str, str]:
    return notifications.compose_email(
        candidate_name="Karthik",
        company_name="Acme Systems",
        message_body="RECRUITER WORDS",
        portal_url="https://example.invalid/portal/messages?conversation=x",
        reply_by_email=reply_by_email,
    )


def test_the_copy_carries_no_em_dash_no_grade_and_no_number_of_ours() -> None:
    for reply in (True, False):
        subject, body = _compose(reply)
        fixed = (subject + body).replace("RECRUITER WORDS", "").replace(
            "https://example.invalid/portal/messages?conversation=x", ""
        )
        assert EM_DASH not in fixed
        assert not re.search(r"\d", fixed), fixed
        for word in GRADE_WORDS:
            assert word not in fixed


def test_reply_by_email_is_promised_only_when_it_works() -> None:
    assert "reply to this email" in _compose(True)[1]
    assert "reply to this email" not in _compose(False)[1]


def test_a_long_message_says_that_it_was_cut() -> None:
    long_body = "word " * 1000
    _, body = notifications.compose_email(
        candidate_name=None,
        company_name="Acme",
        message_body=long_body,
        portal_url="https://example.invalid/x",
        reply_by_email=False,
    )
    assert "The full message is in your candidate portal." in body
    assert len(body) < len(long_body)
