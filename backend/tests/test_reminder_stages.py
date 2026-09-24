"""Both reminders send: the idempotence is keyed on the STAGE (Phase 6 WP6-C).

THE DEFECT
----------
`_autosend_lifecycle_email` refused to send "the same automatic type twice for
one application", checked as ANY `email_log` row of that type for that link.
The reminder schedule is 24 hours then 72 hours, so the second reminder always
found the first and was skipped as "already sent", while the reconciliation
counted it in `reminders_sent` regardless. No test covered reminders at all.

The key is now `assessment_reminder:<link>:<stage hours>`, UNIQUE in the
database, and the reconciliation passes the stage with the reminder.

Mutation check: keying `send_assessment_reminder` on the type again (one key
for every stage) makes `test_the_24_and_72_hour_reminders_are_two_emails` see
one row.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from app.services import credit_reconciliation, email_outbox
from app.workers import tasks
from tests import comms_world


@pytest.fixture
async def world():
    if not await comms_world.reachable():
        pytest.skip("no database reachable -- skipping reminder tests")
    sessions = comms_world.factory()
    w = await comms_world.seed(sessions)
    try:
        yield w
    finally:
        await comms_world.cleanup(sessions, w)


async def _remind(world, stage: int, hours_elapsed: int) -> dict:
    sessions = comms_world.factory()
    async with sessions() as session:
        await comms_world.bypass(session)
        return await tasks._autosend_lifecycle_email(
            session,
            str(world.link),
            "assessment_reminder",
            {"hours_elapsed": str(hours_elapsed)},
            dedupe_key=email_outbox.reminder_key(world.link, stage),
        )


async def _reminder_keys(world) -> list[str]:
    return [
        row["dedupe_key"]
        for row in await comms_world.rows(
            comms_world.factory(),
            "SELECT dedupe_key FROM email_log "
            "WHERE job_candidate_link_id = :lid AND email_type = 'assessment_reminder' "
            "ORDER BY created_at",
            {"lid": str(world.link)},
        )
    ]


async def test_the_24_and_72_hour_reminders_are_two_emails(world) -> None:
    assert (await _remind(world, 24, 25))["status"] == "queued"
    assert (await _remind(world, 72, 73))["status"] == "queued"
    assert await _reminder_keys(world) == [
        f"assessment_reminder:{world.link}:24",
        f"assessment_reminder:{world.link}:72",
    ]


async def test_a_redelivered_reminder_is_a_no_op(world) -> None:
    assert (await _remind(world, 24, 25))["status"] == "queued"
    again = await _remind(world, 24, 26)
    assert again == {"status": "skipped", "reason": "already sent"}
    assert len(await _reminder_keys(world)) == 1


async def test_the_confirmation_is_sent_once(world) -> None:
    sessions = comms_world.factory()
    for _ in range(2):
        async with sessions() as session:
            await comms_world.bypass(session)
            await tasks._autosend_lifecycle_email(
                session,
                str(world.link),
                "application_confirmation",
                dedupe_key=email_outbox.confirmation_key(world.link),
            )
    confirmations = await comms_world.rows(
        sessions,
        "SELECT dedupe_key FROM email_log "
        "WHERE job_candidate_link_id = :lid AND email_type = 'application_confirmation'",
        {"lid": str(world.link)},
    )
    assert confirmations == [{"dedupe_key": f"application_confirmation:{world.link}"}]


def test_a_payload_queued_before_stages_were_explicit_gets_its_stage() -> None:
    """A reminder dispatched by the previous release carries only the elapsed
    hours. The largest schedule entry at or below them is the stage it was."""
    assert tasks.reminder_stage_for(24) == 24
    assert tasks.reminder_stage_for(47) == 24
    assert tasks.reminder_stage_for(72) == 72
    assert tasks.reminder_stage_for(200) == 72
    assert tasks.reminder_stage_for(3) == 24


async def test_the_reconciliation_passes_each_stage_once(world) -> None:
    """Found late (80 hours after the invitation, no reminder yet), a
    conversation gets BOTH stages in one run, each named. Derived from the
    elapsed time alone, both would have been called 72 and one would have been
    deduplicated away."""
    sessions = comms_world.factory()
    conversation = uuid.uuid4()
    now = datetime.now(timezone.utc)
    async with sessions() as session:
        async with session.begin():
            await comms_world.bypass(session)
            await session.execute(
                sa.text(
                    "INSERT INTO assessment_conversations "
                    "(id, tenant_id, job_id, job_candidate_link_id, grade, status, "
                    " next_question_index, reminders_sent, follow_ups_used, "
                    " reasks_used, mode, invitation_sent_at) "
                    "VALUES (:i, :t, :j, :l, 'managerial', 'active', 0, 0, 0, 0, "
                    " 'conversational', :sent)"
                ),
                {
                    "i": str(conversation),
                    "t": str(world.tenant),
                    "j": str(world.job),
                    "l": str(world.link),
                    "sent": now - timedelta(hours=80),
                },
            )
    queued: list[tuple] = []
    async with sessions() as session:
        async with session.begin():
            await comms_world.bypass(session)
            await credit_reconciliation.reconcile(
                session,
                now=now,
                queue_reminder=lambda link, elapsed, stage: queued.append(
                    (link, elapsed, stage)
                ),
            )
    mine = [entry for entry in queued if entry[0] == str(world.link)]
    assert mine == [(str(world.link), 80, 24), (str(world.link), 80, 72)]
