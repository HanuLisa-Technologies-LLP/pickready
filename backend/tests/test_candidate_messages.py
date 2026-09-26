"""The candidate's Messages: unread, retries, history, notification dispatch.

THE DEFECTS (audit Part 1 section 3 item 24)
--------------------------------------------
* Nothing told a candidate a recruiter had written, and the portal had no
  unread badge: `inbound` counted every message the company had EVER sent, so
  it could never go down. A candidate is not a `conversation_participants`
  row, so their read watermark lives on the thread
  (`conversations.candidate_last_read_at`, migration 0121).
* A retried send could become a second message. The server was already
  idempotent on `client_token`; what it did not do was notice a token REUSED
  for different words, which a composer that kept its token across an edit
  produces, and which silently dropped the edited text. That is a 409 now.
* "Load earlier" paged on `created_at` alone, a partial order: messages
  sharing a timestamp on the page boundary were skipped. The cursor is
  (`created_at`, `id`) when the client passes both.

The candidate side runs through a REAL session (`tests/candidate_session.py`)
with no dependency override; the recruiter side overrides the staff
principal the way `test_conversations_api.py` does. Every count is read back
from a second connection.

Mutation checks: dropping the author and body comparison in
`post_message`'s duplicate branch fails `test_a_reused_token_with_other_words_is_refused`;
paging on `created_at` alone fails
`test_load_earlier_walks_messages_that_share_a_timestamp`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.deps import CurrentUser, get_current_user, get_tenant_db
from app.core.db import tenant_scope
from app.core.security import AUDIENCE_ORG
from app.main import app
from app.models.conversation import PARTY_RECRUITER
from app.models.enums import Role
from app.services import conversations
from app.workers import dispatch as dispatch_mod
from tests import comms_world
from tests.candidate_session import close_candidate_session, create_candidate_session

CONV = "/api/v1/conversations"
NOTIFY = "pickready.notify_candidate_of_message"


@pytest.fixture
async def world():
    if not await comms_world.reachable():
        pytest.skip("no database reachable -- skipping candidate message tests")
    sessions = comms_world.factory()
    candidate = await create_candidate_session(sessions)
    w = await comms_world.seed(
        sessions, candidate_id=candidate.candidate_id, candidate_email=candidate.email
    )
    w.session = candidate
    try:
        yield w
    finally:
        await comms_world.cleanup(sessions, w)
        await close_candidate_session(sessions, candidate)


@pytest.fixture
def http(world):
    sessions = comms_world.factory()

    async def _staff() -> CurrentUser:
        return CurrentUser(
            user_id=world.recruiter,
            tenant_id=world.tenant,
            role=Role.recruiter,
            audience=AUDIENCE_ORG,
        )

    async def _tenant_db():
        async with sessions() as session:
            async with session.begin():
                async with tenant_scope(session, world.tenant):
                    yield session

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = _staff
    app.dependency_overrides[get_tenant_db] = _tenant_db
    try:
        # The candidate's REAL cookies: the candidate routes are not overridden.
        with TestClient(app, cookies=world.session.cookies()) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def _open(http, world) -> str:
    opened = http.post(f"{CONV}/candidate/{world.candidate}")
    assert opened.status_code == 200, opened.text
    return opened.json()["id"]


def _recruiter_says(http, conversation_id: str, body: str, token: str):
    return http.post(
        f"{CONV}/{conversation_id}/messages", json={"body": body, "client_token": token}
    )


def _my_threads(http) -> list[dict]:
    listed = http.get(f"{CONV}/me")
    assert listed.status_code == 200, listed.text
    return listed.json()


async def _count(conversation_id: str) -> int:
    return (
        await comms_world.rows(
            comms_world.factory(),
            "SELECT count(*) AS n FROM conversation_messages WHERE conversation_id = :c",
            {"c": conversation_id},
        )
    )[0]["n"]


# ── Unread ───────────────────────────────────────────────────────────────────


async def test_unread_counts_what_the_candidate_has_not_read(http, world) -> None:
    thread = _open(http, world)
    # An opened thread with nothing in it is not shown to the candidate.
    assert _my_threads(http) == []

    assert _recruiter_says(http, thread, "Are you free Thursday?", "tok-msg-u1").status_code == 200
    assert _recruiter_says(http, thread, "Or Friday?", "tok-msg-u2").status_code == 200
    listed = _my_threads(http)
    assert [(row["id"], row["unread"]) for row in listed] == [(thread, 2)]
    assert "inbound" not in listed[0]
    assert http.get(f"{CONV}/me/unread").json() == {"unread_count": 2}

    assert http.post(f"{CONV}/me/{thread}/read").status_code == 200
    assert http.get(f"{CONV}/me/unread").json() == {"unread_count": 0}

    _recruiter_says(http, thread, "One more thing.", "tok-msg-u3")
    assert http.get(f"{CONV}/me/unread").json() == {"unread_count": 1}


async def test_replying_reads_the_thread_and_your_own_words_are_never_unread(
    http, world
) -> None:
    thread = _open(http, world)
    _recruiter_says(http, thread, "Are you free Thursday?", "tok-msg-r1")
    replied = http.post(
        f"{CONV}/me/{thread}/messages",
        json={"body": "Thursday works.", "client_token": "tok-cand-r1"},
    )
    assert replied.status_code == 200, replied.text
    assert http.get(f"{CONV}/me/unread").json() == {"unread_count": 0}


async def test_the_watermark_never_moves_backward(http, world) -> None:
    thread = _open(http, world)
    _recruiter_says(http, thread, "Hello.", "tok-msg-w1")
    assert http.post(f"{CONV}/me/{thread}/read").status_code == 200
    sessions = comms_world.factory()
    async with sessions() as session:
        async with session.begin():
            await comms_world.bypass(session)
            await conversations.mark_candidate_read(
                session,
                conversation_id=uuid.UUID(thread),
                candidate_id=world.candidate,
                now=datetime.now(timezone.utc) - timedelta(days=1),
            )
    assert http.get(f"{CONV}/me/unread").json() == {"unread_count": 0}


async def test_another_candidate_cannot_mark_this_thread_read(http, world) -> None:
    thread = _open(http, world)
    _recruiter_says(http, thread, "Hello.", "tok-msg-o1")
    sessions = comms_world.factory()
    stranger = await create_candidate_session(sessions)
    try:
        with TestClient(app, cookies=stranger.cookies()) as other:
            assert other.post(f"{CONV}/me/{thread}/read").status_code == 404
    finally:
        await close_candidate_session(sessions, stranger)
    assert http.get(f"{CONV}/me/unread").json() == {"unread_count": 1}


# ── Retries ──────────────────────────────────────────────────────────────────


async def test_a_retried_send_is_one_message(http, world) -> None:
    thread = _open(http, world)
    _recruiter_says(http, thread, "Hello.", "tok-first")
    body = {"body": "Yes, Thursday.", "client_token": "tok-cand-retry"}
    first = http.post(f"{CONV}/me/{thread}/messages", json=body)
    second = http.post(f"{CONV}/me/{thread}/messages", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert await _count(thread) == 2


async def test_a_reused_token_with_other_words_is_refused(http, world) -> None:
    thread = _open(http, world)
    assert _recruiter_says(http, thread, "First draft.", "tok-reuse").status_code == 200
    edited = _recruiter_says(http, thread, "Edited draft.", "tok-reuse")
    assert edited.status_code == 409
    assert "different text" in edited.json()["detail"]
    # The candidate cannot collapse onto the recruiter's message either.
    theirs = http.post(
        f"{CONV}/me/{thread}/messages",
        json={"body": "First draft.", "client_token": "tok-reuse"},
    )
    assert theirs.status_code == 409
    assert await _count(thread) == 1


# ── History ──────────────────────────────────────────────────────────────────


async def test_load_earlier_walks_messages_that_share_a_timestamp(http, world) -> None:
    """Five messages written in one microsecond, read two at a time. With
    `created_at` alone as the cursor the boundary swallows some of them."""
    thread = _open(http, world)
    same_instant = datetime.now(timezone.utc)
    sessions = comms_world.factory()
    async with sessions() as session:
        async with session.begin():
            await comms_world.bypass(session)
            for index in range(5):
                await conversations.post_message(
                    session,
                    conversation_id=uuid.UUID(thread),
                    tenant_id=world.tenant,
                    author_party=PARTY_RECRUITER,
                    author_user_id=world.recruiter,
                    body=f"Message {index}",
                    client_token=f"tok-page-{index}",
                    now=same_instant,
                )

    seen: list[str] = []
    page = http.get(f"{CONV}/me/{thread}/messages", params={"limit": 2}).json()
    while page:
        seen = [row["id"] for row in page] + seen
        oldest = page[0]
        page = http.get(
            f"{CONV}/me/{thread}/messages",
            params={
                "limit": 2,
                "before": oldest["created_at"],
                "before_id": oldest["id"],
            },
        ).json()
    assert len(seen) == 5 and len(set(seen)) == 5

    # The recruiter's history pages the same way, through the same service.
    walked: list[str] = []
    page = http.get(f"{CONV}/{thread}/messages", params={"limit": 2}).json()
    while page:
        walked = [row["id"] for row in page] + walked
        oldest = page[0]
        page = http.get(
            f"{CONV}/{thread}/messages",
            params={"limit": 2, "before": oldest["created_at"], "before_id": oldest["id"]},
        ).json()
    assert walked == seen


# ── The notification is dispatched after the commit ─────────────────────────


async def test_a_recruiter_message_dispatches_the_candidate_notification(
    http, world
) -> None:
    thread = _open(http, world)
    sent = _recruiter_says(http, thread, "Hello.", "tok-notify")
    notify = [item for item in dispatch_mod.recorded() if item.name == NOTIFY]
    assert [item.args for item in notify] == [(thread, sent.json()["id"])]


async def test_a_refused_send_dispatches_nothing(http, world) -> None:
    thread = _open(http, world)
    refused = _recruiter_says(http, thread, "   ", "tok-blank-notify")
    assert refused.status_code == 422
    assert NOTIFY not in dispatch_mod.recorded_names()


async def test_a_candidate_reply_notifies_nobody_by_email(http, world) -> None:
    thread = _open(http, world)
    _recruiter_says(http, thread, "Hello.", "tok-before-reply")
    dispatch_mod.clear_recorded()
    http.post(
        f"{CONV}/me/{thread}/messages",
        json={"body": "Hi.", "client_token": "tok-cand-quiet"},
    )
    assert NOTIFY not in dispatch_mod.recorded_names()
