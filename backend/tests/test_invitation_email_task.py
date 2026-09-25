"""The invitation email: drafted by a worker, once, and repaired when lost (PLAN-p3 WP1).

WHAT CHANGED
------------
The invitation used to be drafted inside the recruiter's click and written by
hand into `email_log` with no sender. It is now `pickready.send_assessment_invitation`,
dispatched after the invitation commits, which drafts through
`email_outbox.queue_transition_email`: the tenant's default sender is on the
row, the row carries `assessment_invitation:<link>` as its dedupe key, and the
send is dispatched after the TASK's commit.

A dispatch that is lost after the commit leaves an invited candidate who was
never told, and who the credit reconciliation would then charge as a no-show a
week later. `credit_reconciliation.reconcile` repairs it by asking the TABLE.

WHAT IS ASSERTED, AND FROM WHERE
--------------------------------
The task runs for real (its own worker session and its own commit, on a
thread, exactly as the Lambda runs it); only the model is replaced, at the
`lifecycle_email.draft` boundary. Every row is read from a SECOND connection.

Mutation checks (each broke the code, saw the failure, restored):
  * the "already queued" read and the dedupe key both removed -> the rerun
    test fails with a second row;
  * the application-moved-on check removed -> the rejected test fails;
  * the repair's NOT EXISTS removed -> the repair test fails (a failed email
    is re-sent from the sweep);
  * the repair's window floor removed -> the repair test fails (a day-old
    invitation is sent after its reminder).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from app.core.db import tenant_scope
from app.services import (
    assessment_invitations,
    credit_reconciliation,
    email_outbox,
    lifecycle_email,
)
from app.workers import dispatch as dispatch_mod
from app.workers import tasks_invitations
from tests import comms_world, invite_world

INVITE = "pickready.send_assessment_invitation"
SEND = "pickready.send_lifecycle_email"
STEM_REPORT = 90


@pytest.fixture
async def seeded():
    if not await comms_world.reachable():
        pytest.skip("no database reachable -- skipping invitation email tests")
    sessions = comms_world.factory()
    made: list[invite_world.InviteWorld] = []

    async def _make(**kwargs) -> invite_world.InviteWorld:
        world = await invite_world.seed(sessions, **kwargs)
        made.append(world)
        return world

    try:
        yield _make
    finally:
        for world in made:
            await invite_world.cleanup(sessions, world)


@pytest.fixture
def drafts(monkeypatch) -> list[dict]:
    """The model boundary. Records the context each draft was asked for and
    answers deterministically, so the test is about the pipeline, not a
    provider."""
    seen: list[dict] = []

    async def _draft(email_type, context, *, session=None):  # noqa: ARG001
        seen.append({"email_type": email_type, **context})
        body = f"Hello {context['candidate_name']}. Start here: {context['assessment_link']}"
        return {
            "email_type": email_type,
            "subject": f"Your assessment for {context['job_title']}",
            "body": body,
            "html": body,
            "generated_by_ai": False,
        }

    monkeypatch.setattr(lifecycle_email, "draft", _draft)
    return seen


async def _invite(world, links, *, now=None) -> None:
    async with comms_world.factory()() as session:
        async with session.begin():
            async with tenant_scope(session, world.tenant):
                await assessment_invitations.invite_batch(
                    session,
                    tenant_id=world.tenant,
                    job_id=world.job,
                    link_ids=list(links),
                    actor_user_id=world.recruiter,
                    now=now,
                )


async def _run_task(link, actor) -> dict:
    """The task body as the Lambda runs it: its own event loop, its own
    worker session, its own commit."""
    return await asyncio.to_thread(
        tasks_invitations.send_assessment_invitation, str(link), str(actor)
    )


async def _invitation_rows(link) -> list[dict]:
    return await comms_world.rows(
        comms_world.factory(),
        "SELECT id, email_type, sender_id, sent_by, dedupe_key, conversation_id, "
        " status, recipient_email, body, edited_by_human "
        "FROM email_log WHERE job_candidate_link_id = :l "
        "AND email_type = 'assessment_invitation'",
        {"l": str(link)},
    )


async def _execute(sql: str, params: dict) -> None:
    async with comms_world.factory()() as session:
        async with session.begin():
            await comms_world.bypass(session)
            await session.execute(sa.text(sql), params)


# ── The task ─────────────────────────────────────────────────────────────────


async def test_the_task_queues_one_invitation_from_the_default_sender_and_a_rerun_none(
    seeded, drafts
) -> None:
    world = await seeded(applicants=1, grant_subunits=STEM_REPORT)
    link = world.links[0]
    default = await comms_world.add_sender(comms_world.factory(), world.base, is_default=True)
    await _invite(world, [link])
    dispatch_mod.clear_recorded()

    first = await _run_task(link, world.recruiter)
    assert first["status"] == "queued", first

    rows = await _invitation_rows(link)
    assert len(rows) == 1
    row = rows[0]
    assert row["sender_id"] == default
    assert row["sent_by"] == world.recruiter
    assert row["dedupe_key"] == email_outbox.invitation_key(link)
    assert row["status"] == "queued"
    assert row["recipient_email"] == world.email_for[link]
    assert row["edited_by_human"] is False
    # Machine copy nobody read is not posted into the recruiter's thread as
    # the recruiter's own words.
    assert row["conversation_id"] is None
    # The draft was handed the SIGNED invitation link, and it is in the email.
    assert len(drafts) == 1
    assert "/assessments/invite/" in drafts[0]["assessment_link"]
    assert drafts[0]["assessment_link"] in row["body"]
    # The send was released by the task's commit, naming this row.
    assert [item.args for item in dispatch_mod.recorded() if item.name == SEND] == [
        (str(row["id"]),)
    ]

    again = await _run_task(link, world.recruiter)
    assert again == {"status": "skipped", "reason": "already queued"}
    assert len(await _invitation_rows(link)) == 1
    assert len(drafts) == 1, "a rerun paid for a second draft"
    assert [item.name for item in dispatch_mod.recorded()].count(SEND) == 1


async def test_an_application_that_moved_on_is_not_invited(seeded, drafts) -> None:
    """A recruiter rejected the applicant in the seconds between the click and
    the run. An invitation now would be a false promise."""
    world = await seeded(applicants=1, grant_subunits=STEM_REPORT)
    link = world.links[0]
    await _invite(world, [link])
    await _execute(
        "UPDATE job_candidate_links SET status = 'rejected' WHERE id = :l",
        {"l": str(link)},
    )
    result = await _run_task(link, world.recruiter)
    assert result == {"status": "skipped", "reason": "application moved on"}
    assert await _invitation_rows(link) == []
    assert drafts == []


async def test_a_candidate_with_no_address_is_a_skip_and_writes_nothing(
    seeded, drafts
) -> None:
    world = await seeded(applicants=1, grant_subunits=STEM_REPORT)
    link = world.links[0]
    await _invite(world, [link])
    await _execute(
        "UPDATE candidates SET email = NULL WHERE id = :c",
        {"c": str(world.candidate_for[link])},
    )
    result = await _run_task(link, world.recruiter)
    assert result["status"] == "skipped"
    assert await _invitation_rows(link) == []
    assert drafts == []
    assert SEND not in dispatch_mod.recorded_names()


async def test_a_dispatch_naming_an_uninvited_application_writes_nothing(
    seeded, drafts
) -> None:
    world = await seeded(applicants=1)
    link = world.links[0]
    result = await _run_task(link, world.recruiter)
    assert result == {"status": "skipped", "reason": "not invited"}
    assert await _invitation_rows(link) == []
    assert drafts == []


# ── The repair ───────────────────────────────────────────────────────────────


async def test_reconciliation_repairs_a_lost_invitation_inside_its_window_only(
    seeded, caplog
) -> None:
    """Five invited applications, one of which is a lost dispatch:

    - LOST: invited twenty minutes ago, no invitation email. Re-dispatched.
    - FRESH: invited five minutes ago. Its own dispatch may still be running.
    - OLD: invited thirty hours ago with no email. Past the first reminder,
      which carries the same link: counted and reported at ERROR, not sent.
    - FAILED: twenty minutes ago, with an invitation row that FAILED. A
      delivery problem with its own record, never re-sent from here.
    - REJECTED: twenty minutes ago, since rejected. Nobody to invite.

    The re-dispatch is released by the run's COMMIT, never before it.
    """
    now = datetime.now(timezone.utc)
    world = await seeded(applicants=5, demo=True)
    lost, fresh, old, failed, rejected = world.links
    for link, age in (
        (lost, timedelta(minutes=20)),
        (fresh, timedelta(minutes=5)),
        (old, timedelta(hours=30)),
        (failed, timedelta(minutes=20)),
        (rejected, timedelta(minutes=20)),
    ):
        await _invite(world, [link], now=now - age)
    async with comms_world.factory()() as session:
        async with session.begin():
            await comms_world.bypass(session)
            row = await email_outbox.queue_candidate_email(
                session,
                tenant_id=world.tenant,
                email_type="assessment_invitation",
                recipient_email=world.email_for[failed],
                candidate_id=world.candidate_for[failed],
                job_id=world.job,
                link_id=failed,
                subject="S",
                body="B",
                generated_by_ai=False,
                edited_by_human=False,
                sent_by=None,
                thread=False,
            )
            row.status = "failed"
    await _execute(
        "UPDATE job_candidate_links SET status = 'rejected' WHERE id = :l",
        {"l": str(rejected)},
    )
    dispatch_mod.clear_recorded()

    async with comms_world.factory()() as session:
        await session.begin()
        await comms_world.bypass(session)
        with caplog.at_level(logging.WARNING, logger="app.services.credit_reconciliation"):
            result = await credit_reconciliation.reconcile(session, now=now)
        assert INVITE not in dispatch_mod.recorded_names(), "dispatched before the commit"
        await session.commit()

    mine = {str(link) for link in world.links}
    redispatched = [
        item.args for item in dispatch_mod.recorded()
        if item.name == INVITE and item.args[0] in mine
    ]
    assert redispatched == [(str(lost), str(world.recruiter))]
    assert result.invitations_redispatched >= 1
    assert result.invitations_missing_unrepaired >= 1
    assert result.as_dict()["invitations_redispatched"] == result.invitations_redispatched
    assert any(
        record.getMessage().startswith("credits.invitation_email_missing")
        and record.levelno == logging.ERROR
        for record in caplog.records
    )


async def test_a_reconciliation_that_rolls_back_redispatches_nothing(seeded) -> None:
    now = datetime.now(timezone.utc)
    world = await seeded(applicants=1, demo=True)
    await _invite(world, world.links, now=now - timedelta(minutes=20))
    dispatch_mod.clear_recorded()
    async with comms_world.factory()() as session:
        await session.begin()
        await comms_world.bypass(session)
        await credit_reconciliation.reconcile(session, now=now)
        await session.rollback()
    assert INVITE not in dispatch_mod.recorded_names()


def test_the_repair_window_closes_at_the_first_reminder() -> None:
    """The repair and the first reminder must not overlap: a reminder already
    carries the link, and an invitation after it reads as a mistake."""
    assert credit_reconciliation.REMINDER_SCHEDULE_HOURS[0] * 60 > (
        credit_reconciliation.INVITATION_REPAIR_AFTER_MINUTES
    )
    assert credit_reconciliation.INVITATION_TASK == assessment_invitations.INVITATION_TASK


def test_the_task_is_registered_on_the_short_route() -> None:
    from app.workers.registry import Route, resolve

    spec = resolve(INVITE)
    assert spec.route is Route.LAMBDA
    assert spec.max_attempts >= 2, "an idempotent task should survive a blip"
    assert uuid.UUID(email_outbox.invitation_key(uuid.UUID(int=1)).split(":")[1])
