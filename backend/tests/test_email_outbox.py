"""The one writer of candidate email: sender, thread, commit (Phase 6 WP6-C).

THE DEFECT
----------
Four places built `email_log` rows by hand and NONE of them set `sender_id`,
so a corporate sender a client had registered and authorized was never used
for a single email. `api/emails.send_emails` even validated the requested
sender and then dropped it. Automatic emails had no request to name a sender
in at all, and every writer dispatched the send before its request committed.

WHAT IS ASSERTED, AND FROM WHERE
--------------------------------
Every row is read back from a SECOND connection after the request answered,
because a write that answered 200 and rolled back at commit is invisible to an
assertion on the response body.

Mutation checks (each broke the code, saw the failure, restored):
  * `resolve_sender` ignoring the default -> the default-sender tests fail;
  * `_transition_or_409` not clearing `is_default` -> the revoke test fails;
  * `queue_candidate_email` dispatching with `dispatch` instead of after the
    commit -> the rollback test fails.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.api.deps import CurrentUser, get_current_user, get_tenant_db
from app.core.db import tenant_scope
from app.core.security import AUDIENCE_ORG
from app.main import app
from app.models.enums import Role
from app.services import email_outbox
from app.workers import dispatch as dispatch_mod
from tests import comms_world

SEND = "pickready.send_lifecycle_email"


@pytest.fixture
async def world():
    if not await comms_world.reachable():
        pytest.skip("no database reachable -- skipping outbox tests")
    sessions = comms_world.factory()
    w = await comms_world.seed(sessions)
    try:
        yield w
    finally:
        await comms_world.cleanup(sessions, w)


async def _queue(world, **overrides):
    """Queue one email through the outbox in its own committed transaction."""
    sessions = comms_world.factory()
    async with sessions() as session:
        async with session.begin():
            await comms_world.bypass(session)
            row = await email_outbox.queue_candidate_email(
                session,
                **{
                    "tenant_id": world.tenant,
                    "email_type": "application_confirmation",
                    "recipient_email": world.candidate_email,
                    "candidate_id": world.candidate,
                    "job_id": world.job,
                    "link_id": world.link,
                    "subject": "Subject",
                    "body": "Body",
                    "generated_by_ai": False,
                    "edited_by_human": False,
                    "sent_by": None,
                    **overrides,
                },
            )
            return None if row is None else row.id


async def _email_row(email_log_id) -> dict:
    found = await comms_world.rows(
        comms_world.factory(),
        "SELECT sender_id, conversation_id, status, dedupe_key, sent_by "
        "FROM email_log WHERE id = :id",
        {"id": str(email_log_id)},
    )
    assert found, "the email_log row was not committed"
    return found[0]


# ── Who it is from ───────────────────────────────────────────────────────────


async def test_a_named_active_sender_is_saved_on_the_row(world) -> None:
    sender = await comms_world.add_sender(comms_world.factory(), world)
    row_id = await _queue(world, requested_sender_id=sender)
    assert (await _email_row(row_id))["sender_id"] == sender


async def test_no_sender_named_uses_the_tenants_default(world) -> None:
    """The case that could never work before: an AUTOMATIC email carrying the
    company's own address."""
    default = await comms_world.add_sender(comms_world.factory(), world, is_default=True)
    await comms_world.add_sender(comms_world.factory(), world)
    row_id = await _queue(world)
    assert (await _email_row(row_id))["sender_id"] == default


async def test_no_default_is_the_platform_mailbox(world) -> None:
    await comms_world.add_sender(comms_world.factory(), world)
    row_id = await _queue(world)
    assert (await _email_row(row_id))["sender_id"] is None


async def test_a_named_sender_must_be_active_and_this_tenants(world) -> None:
    sessions = comms_world.factory()
    disabled = await comms_world.add_sender(sessions, world, status="disabled")
    with pytest.raises(email_outbox.SenderNotActive):
        await _queue(world, requested_sender_id=disabled)
    other = await comms_world.seed(sessions)
    try:
        theirs = await comms_world.add_sender(sessions, other)
        with pytest.raises(email_outbox.SenderNotFound):
            await _queue(world, requested_sender_id=theirs)
    finally:
        await comms_world.cleanup(sessions, other)


# ── Once, when it must be once ───────────────────────────────────────────────


async def test_a_dedupe_key_admits_one_row(world) -> None:
    first = await _queue(world, dedupe_key=f"application_confirmation:{world.link}")
    second = await _queue(world, dedupe_key=f"application_confirmation:{world.link}")
    assert first is not None and second is None
    counted = await comms_world.rows(
        comms_world.factory(),
        "SELECT count(*) AS n FROM email_log WHERE job_candidate_link_id = :lid",
        {"lid": str(world.link)},
    )
    assert counted[0]["n"] == 1


# ── When it is sent ──────────────────────────────────────────────────────────


async def test_the_send_is_dispatched_by_the_commit_and_never_by_a_rollback(
    world,
) -> None:
    sessions = comms_world.factory()
    async with sessions() as session:
        await session.begin()
        await comms_world.bypass(session)
        row = await email_outbox.queue_candidate_email(
            session,
            tenant_id=world.tenant,
            email_type="application_confirmation",
            recipient_email=world.candidate_email,
            candidate_id=world.candidate,
            job_id=world.job,
            link_id=world.link,
            subject="Subject",
            body="Body",
            generated_by_ai=False,
            edited_by_human=False,
            sent_by=None,
        )
        assert row is not None
        # Nothing yet: the row is not visible to a worker until the commit.
        assert SEND not in dispatch_mod.recorded_names()
        await session.rollback()
    assert SEND not in dispatch_mod.recorded_names()

    row_id = await _queue(world)
    sends = [item for item in dispatch_mod.recorded() if item.name == SEND]
    assert [item.args[0] for item in sends] == [str(row_id)]


# ── The recruiter's send, through the route ──────────────────────────────────


@pytest.fixture
def as_staff(world):
    principal: dict = {}
    sessions = comms_world.factory()

    async def _user() -> CurrentUser:
        return principal["user"]

    async def _db():
        async with sessions() as session:
            async with session.begin():
                async with tenant_scope(session, world.tenant):
                    yield session

    def _set(role: Role) -> None:
        principal["user"] = CurrentUser(
            user_id=world.recruiter,
            tenant_id=world.tenant,
            role=role,
            audience=AUDIENCE_ORG,
        )

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_tenant_db] = _db
    _set(Role.recruiter)
    try:
        with TestClient(app) as http:
            yield http, _set
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


async def test_a_recruiters_email_carries_the_default_and_joins_the_thread(
    world, as_staff
) -> None:
    http, _ = as_staff
    default = await comms_world.add_sender(comms_world.factory(), world, is_default=True)
    sent = http.post(
        "/api/v1/emails/send",
        json={
            "email_type": "shortlist",
            "messages": [
                {"link_id": str(world.link), "subject": "Good news", "body": "Hello."}
            ],
        },
    )
    assert sent.status_code == 202, sent.text
    row_id = sent.json()["logs"][0]["id"]

    row = await _email_row(row_id)
    assert row["sender_id"] == default
    assert row["sent_by"] == world.recruiter
    assert row["conversation_id"] is not None
    # The email is IN the thread, so a reply to it has context, and the
    # recruiter is a participant, so an emailed reply counts as unread for them.
    messages = await comms_world.rows(
        comms_world.factory(),
        "SELECT author_party, channel, delivery_status, email_log_id "
        "FROM conversation_messages WHERE conversation_id = :cid",
        {"cid": str(row["conversation_id"])},
    )
    assert messages == [
        {
            "author_party": "recruiter",
            "channel": "email",
            "delivery_status": "pending",
            "email_log_id": uuid.UUID(row_id),
        }
    ]
    participants = await comms_world.rows(
        comms_world.factory(),
        "SELECT user_id FROM conversation_participants WHERE conversation_id = :cid",
        {"cid": str(row["conversation_id"])},
    )
    assert participants == [{"user_id": world.recruiter}]
    assert [i.args[0] for i in dispatch_mod.recorded() if i.name == SEND] == [row_id]


async def test_a_named_sender_that_is_not_active_is_refused_and_writes_nothing(
    world, as_staff
) -> None:
    http, _ = as_staff
    revoked = await comms_world.add_sender(comms_world.factory(), world, status="revoked")
    refused = http.post(
        "/api/v1/emails/send",
        json={
            "email_type": "shortlist",
            "sender_id": str(revoked),
            "messages": [{"link_id": str(world.link), "subject": "S", "body": "B"}],
        },
    )
    assert refused.status_code == 409
    counted = await comms_world.rows(
        comms_world.factory(),
        "SELECT count(*) AS n FROM email_log WHERE tenant_id = :tid",
        {"tid": str(world.tenant)},
    )
    assert counted[0]["n"] == 0
    assert SEND not in dispatch_mod.recorded_names()


# ── The default sender routes ────────────────────────────────────────────────


async def _defaults(world) -> list[uuid.UUID]:
    return [
        row["id"]
        for row in await comms_world.rows(
            comms_world.factory(),
            "SELECT id FROM client_email_senders WHERE tenant_id = :tid AND is_default",
            {"tid": str(world.tenant)},
        )
    ]


async def test_one_default_per_tenant_and_the_new_one_replaces_the_old(
    world, as_staff
) -> None:
    http, set_role = as_staff
    set_role(Role.client)
    first = await comms_world.add_sender(comms_world.factory(), world)
    second = await comms_world.add_sender(comms_world.factory(), world)

    assert http.post(f"/api/v1/email-senders/{first}/default").status_code == 200
    assert await _defaults(world) == [first]
    answered = http.post(f"/api/v1/email-senders/{second}/default")
    assert answered.status_code == 200 and answered.json()["is_default"] is True
    assert await _defaults(world) == [second]

    cleared = http.delete(f"/api/v1/email-senders/{second}/default")
    assert cleared.status_code == 200 and cleared.json()["is_default"] is False
    assert await _defaults(world) == []


async def test_only_an_active_sender_can_be_the_default(world, as_staff) -> None:
    http, set_role = as_staff
    set_role(Role.client)
    pending = await comms_world.add_sender(
        comms_world.factory(), world, status="pending_verification"
    )
    assert http.post(f"/api/v1/email-senders/{pending}/default").status_code == 409
    assert await _defaults(world) == []


async def test_a_recruiter_cannot_choose_the_default(world, as_staff) -> None:
    http, _ = as_staff  # a recruiter holds no authorize_email_senders
    sender = await comms_world.add_sender(comms_world.factory(), world)
    assert http.post(f"/api/v1/email-senders/{sender}/default").status_code == 403


async def test_revoking_the_default_clears_it_and_the_next_email_is_the_platforms(
    world, as_staff
) -> None:
    http, set_role = as_staff
    set_role(Role.client)
    sender = await comms_world.add_sender(comms_world.factory(), world, is_default=True)
    assert http.post(f"/api/v1/email-senders/{sender}/revoke").status_code == 200
    assert await _defaults(world) == []
    row_id = await _queue(world)
    assert (await _email_row(row_id))["sender_id"] is None


async def test_the_composer_lists_active_senders_default_first(world, as_staff) -> None:
    http, _ = as_staff  # a recruiter: SEND_OUTREACH, no sender management
    plain = await comms_world.add_sender(comms_world.factory(), world)
    default = await comms_world.add_sender(comms_world.factory(), world, is_default=True)
    await comms_world.add_sender(comms_world.factory(), world, status="disabled")
    listed = http.get("/api/v1/email-senders/active")
    assert listed.status_code == 200, listed.text
    assert [row["id"] for row in listed.json()] == [str(default), str(plain)]
    assert listed.json()[0]["is_default"] is True
