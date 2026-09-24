"""`pickready.remind_unsaved_skills`: one reminder per job, to the people who
may actually save the skills, pointing at a page that exists.

It replaces the technical-questions reminder, which mailed by ROLE NAME and
linked to `/org/jobs/{id}/setup`, a page that does not exist. Recipients are
now whoever `rbac.authorize(FINALIZE_ROLE_DEFINITION)` allows ON THIS JOB: the
client super admin, and a Hiring Manager only when assigned (the SCOPED cell);
never a Recruiter (the NEVER cell).

The real task body runs against Postgres; only the SMTP send is captured.

MUTATION CHECK, recorded: mailing every tenant user with an address
(`p1b_mutate.py reminder_by_role`) fails
`test_the_reminder_goes_to_the_people_who_may_save_the_skills`.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from app.core.db import superadmin_scope
from tests import skills_fixtures as fx

EM_DASH = chr(8212)


@pytest.fixture
async def world():
    worlds: list[fx.World] = []

    async def _make(**kwargs) -> fx.World:
        w = await fx.seed(**kwargs)
        worlds.append(w)
        return w

    yield _make
    for w in worlds:
        await fx.drop(w)


@pytest.fixture
def sent(monkeypatch):
    from app.workers import tasks

    mails: list[tuple[str, str, dict]] = []

    async def _capture(session, tenant_id, to, template, context, **_kwargs):
        mails.append((tenant_id, to, context))
        return {"status": "sent"}

    monkeypatch.setattr(tasks, "_send_email_async", _capture)
    return mails


async def _remind() -> None:
    from app.workers.tasks import remind_unsaved_skills

    await asyncio.to_thread(remind_unsaved_skills)


async def _emails(w: fx.World) -> dict[str, str]:
    async with fx.sessions()() as session:
        async with superadmin_scope(session):
            rows = (
                await session.execute(
                    text("SELECT id, email FROM users WHERE tenant_id = :t"), {"t": w.tenant}
                )
            ).all()
    by_id = {str(user_id): email for user_id, email in rows}
    return {key: by_id[str(user_id)] for key, user_id in w.users.items()}


async def _assign(w: fx.World, key: str) -> None:
    async with fx.sessions()() as session:
        async with session.begin():
            async with superadmin_scope(session):
                await session.execute(
                    text(
                        "INSERT INTO job_assignments (id, tenant_id, job_id, user_id, "
                        "assignment_role, active) VALUES (:i, :t, :j, :u, 'hiring_manager', true)"
                    ),
                    {"i": uuid.uuid4(), "t": w.tenant, "j": w.job, "u": w.users[key]},
                )


def _overdue() -> dict:
    return {
        "skills": [("must_have", "Kafka", True, "sutra", None, None)],
        "draft_status": "drafted",
        "drafted_at": datetime.now(timezone.utc) - timedelta(days=5),
    }


async def test_the_reminder_goes_to_the_people_who_may_save_the_skills(world, sent) -> None:
    w = await world(
        **_overdue(),
        extra_users=[
            ("recruiter", "recruiter"),
            ("assigned_hm", "hiring_manager"),
            ("other_hm", "hiring_manager"),
        ],
    )
    await _assign(w, "assigned_hm")
    emails = await _emails(w)

    await _remind()

    to = {address for tenant, address, _ in sent if tenant == str(w.tenant)}
    assert to == {emails["client"], emails["assigned_hm"]}
    assert emails["recruiter"] not in to, "a Recruiter may never save skills"
    assert emails["other_hm"] not in to, "an unassigned Hiring Manager holds no scope"


async def test_one_reminder_per_job_with_a_link_that_exists(world, sent) -> None:
    w = await world(**_overdue())

    await _remind()
    await _remind()

    mine = [context for tenant, _to, context in sent if tenant == str(w.tenant)]
    assert len(mine) == 1, "once per job, not once per tick"
    body = mine[0]["body"]
    assert f"/org/jobs/{w.job}" in body
    assert "/setup" not in body
    assert EM_DASH not in body and EM_DASH not in mine[0]["subject"]
    assert (await fx.committed_job(w))["question_reminder_sent_at"] is not None


async def test_saved_or_recent_or_undrafted_skills_are_not_chased(world, sent) -> None:
    saved = await world(**_overdue(), saved=True)
    recent = await world(
        skills=[("must_have", "Kafka", True, "sutra", None, None)],
        draft_status="drafted",
        drafted_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    undrafted = await world()

    await _remind()

    reminded = {tenant for tenant, _to, _context in sent}
    for w in (saved, recent, undrafted):
        assert str(w.tenant) not in reminded
