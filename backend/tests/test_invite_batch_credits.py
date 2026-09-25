"""Inviting a batch: one credit question, nothing drafted in the click (PLAN-p3 WP1).

THE DEFECTS
-----------
`POST /pipeline/jobs/{id}/select-candidates` asked whether the balance held
ONE report and then invited every ticked applicant, so a balance holding one
assessment let a recruiter invite two hundred people, each charged at
completion into a deficit nobody chose. It also drafted every invitation email
INSIDE the request (a model call bounded at thirty seconds, per applicant) and
dispatched the send and the question generation BEFORE its transaction
committed. Two recruiters inviting the same applicant at once both saw
`applied`, and the second request's `apply_transition` raised and 500'd the
whole batch.

WHAT IS ASSERTED, AND FROM WHERE
--------------------------------
Every row is read back from a SECOND connection after the request answered,
and the dispatches are read from the record the COMMIT released: a write that
answered 202 and rolled back, or a dispatch sent before the commit, cannot
pass.

Mutation checks (each broke the code, saw the failure, restored):
  * `count=len(eligible)` replaced by the default of one -> the refusal test
    fails (the batch is invited into a deficit);
  * `dispatch_after_commit` for the invitation replaced by `dispatch` -> the
    rollback test fails (a dispatch escapes a rolled-back batch);
  * `FOR UPDATE OF l` removed -> the concurrent invitation test fails (the
    second batch no longer skips the applicant it lost).
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from app.api.deps import CurrentUser, get_current_user, get_tenant_db
from app.core.db import tenant_scope
from app.core.security import AUDIENCE_ORG
from app.main import app
from app.models.enums import Role
from app.services import assessment_invitations, lifecycle_email, skills
from app.workers import dispatch as dispatch_mod
from tests import comms_world, invite_world

INVITE = "pickready.send_assessment_invitation"
QUESTIONS = "pickready.generate_candidate_questions"
#: One STEM report, in sub-units (1.5 credits).
STEM_REPORT = 90


async def _reachable_or_skip() -> None:
    if not await comms_world.reachable():
        pytest.skip("no database reachable -- skipping invitation tests")


@pytest.fixture
def no_drafting_in_the_request(monkeypatch):
    """The invitation is drafted by a worker. A draft inside the request is
    the defect, so it fails the test rather than calling a model."""

    async def _refuse(*_args, **_kwargs):
        raise AssertionError("an invitation email was drafted inside the request")

    monkeypatch.setattr(lifecycle_email, "draft", _refuse)


def _client(world: invite_world.InviteWorld):
    sessions = comms_world.factory()

    async def _user() -> CurrentUser:
        return CurrentUser(
            user_id=world.recruiter,
            tenant_id=world.tenant,
            role=Role.recruiter,
            audience=AUDIENCE_ORG,
        )

    async def _db():
        async with sessions() as session:
            async with session.begin():
                async with tenant_scope(session, world.tenant):
                    yield session

    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_tenant_db] = _db
    return TestClient(app)


@pytest.fixture
async def seeded():
    await _reachable_or_skip()
    sessions = comms_world.factory()
    made: list[invite_world.InviteWorld] = []
    previous = dict(app.dependency_overrides)

    async def _make(**kwargs) -> invite_world.InviteWorld:
        world = await invite_world.seed(sessions, **kwargs)
        made.append(world)
        return world

    try:
        yield _make
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)
        for world in made:
            await invite_world.cleanup(sessions, world)


async def _conversations(world) -> int:
    return await invite_world.scalar(
        "SELECT count(*) FROM assessment_conversations WHERE job_id = :j",
        {"j": str(world.job)},
    )


async def _statuses(world) -> list[str]:
    rows = await comms_world.rows(
        comms_world.factory(),
        "SELECT status FROM job_candidate_links WHERE job_id = :j ORDER BY id",
        {"j": str(world.job)},
    )
    return [row["status"] for row in rows]


def _invite(http: TestClient, world, link_ids) -> object:
    return http.post(
        f"/api/v1/pipeline/jobs/{world.job}/select-candidates",
        json={"link_ids": [str(link) for link in link_ids]},
    )


# ── The one credit question ──────────────────────────────────────────────────


async def test_a_batch_the_balance_cannot_cover_is_refused_whole_and_writes_nothing(
    seeded, no_drafting_in_the_request
) -> None:
    """Two STEM reports of credit, three applicants ticked: 402, naming the
    count, the cost per report, the balance and the gap, and NOTHING written.
    The old gate asked about one report and would have invited all three."""
    world = await seeded(applicants=3, grant_subunits=2 * STEM_REPORT)
    with _client(world) as http:
        refused = _invite(http, world, world.links)

    assert refused.status_code == 402, refused.text
    detail = refused.json()["detail"]
    assert "3 candidates" in detail
    assert "STEM role requires 4.50 credits" in detail
    assert "1.50 per assessment" in detail
    assert "Current balance: 3.00 credits, 1.50 short" in detail
    assert "Nobody was invited" in detail

    assert await _conversations(world) == 0
    assert await _statuses(world) == ["applied"] * 3
    assert await invite_world.scalar(
        "SELECT count(*) FROM pipeline_status ps JOIN job_candidate_links l "
        " ON l.id = ps.job_candidate_link_id WHERE l.job_id = :j",
        {"j": str(world.job)},
    ) == 0
    assert dispatch_mod.recorded_names() == []


async def test_a_batch_the_balance_covers_is_invited_and_mailed_after_the_commit(
    seeded, no_drafting_in_the_request
) -> None:
    world = await seeded(applicants=3, grant_subunits=2 * STEM_REPORT)
    chosen = world.links[:2]
    with _client(world) as http:
        sent = _invite(http, world, chosen)

    assert sent.status_code == 202, sent.text
    assert sent.json() == {"invited": 2, "skipped": []}

    rows = await comms_world.rows(
        comms_world.factory(),
        "SELECT job_candidate_link_id, invitation_sent_at, invited_by, grade "
        "FROM assessment_conversations WHERE job_id = :j",
        {"j": str(world.job)},
    )
    assert {row["job_candidate_link_id"] for row in rows} == set(chosen)
    assert all(row["invitation_sent_at"] is not None for row in rows)
    assert all(row["invited_by"] == world.recruiter for row in rows)
    assert all(row["grade"] == "managerial" for row in rows)
    statuses = await comms_world.rows(
        comms_world.factory(),
        "SELECT id, status FROM job_candidate_links WHERE job_id = :j",
        {"j": str(world.job)},
    )
    by_link = {row["id"]: row["status"] for row in statuses}
    assert [by_link[link] for link in chosen] == ["assessment_invited"] * 2
    assert by_link[world.links[2]] == "applied"
    # Drafted later, by a worker: the request wrote no email at all.
    assert await invite_world.scalar(
        "SELECT count(*) FROM email_log WHERE job_id = :j", {"j": str(world.job)}
    ) == 0
    # No credit is taken at invitation: an assessment is charged when it ends.
    assert await invite_world.scalar(
        "SELECT count(*) FROM credit_ledger WHERE tenant_id = :t "
        "AND event_type <> 'grant'",
        {"t": str(world.tenant)},
    ) == 0

    invites = [item for item in dispatch_mod.recorded() if item.name == INVITE]
    questions = [item for item in dispatch_mod.recorded() if item.name == QUESTIONS]
    assert sorted(item.args for item in invites) == sorted(
        (str(link), str(world.recruiter)) for link in chosen
    )
    assert sorted(item.args for item in questions) == sorted(
        (str(link),) for link in chosen
    )
    audited = await comms_world.rows(
        comms_world.factory(),
        "SELECT metadata_json FROM audit_log WHERE tenant_id = :t "
        "AND action = 'assessment_invitations_sent'",
        {"t": str(world.tenant)},
    )
    assert len(audited) == 1
    assert audited[0]["metadata_json"]["invited"] == 2


async def test_a_demo_tenant_is_never_refused_and_still_charged_nothing_now(
    seeded, no_drafting_in_the_request
) -> None:
    world = await seeded(applicants=2, demo=True)
    with _client(world) as http:
        sent = _invite(http, world, world.links)
    assert sent.status_code == 202, sent.text
    assert sent.json()["invited"] == 2


# ── Who is skipped, and why ──────────────────────────────────────────────────


async def test_skips_are_named_and_a_duplicate_is_one_invitation(
    seeded, no_drafting_in_the_request
) -> None:
    """A stranger id, an application on another job, and an application
    already past `applied` are each skipped WITH a reason; the same id ticked
    twice is one invitation, and only the eligible count is asked about."""
    world = await seeded(applicants=3, grant_subunits=STEM_REPORT)
    other = await seeded(applicants=1, grant_subunits=0)
    stranger = uuid.uuid4()
    with _client(world) as http:
        first = _invite(http, world, [world.links[1]])
        assert first.status_code == 202, first.text
        again = _invite(
            http,
            world,
            [world.links[0], world.links[0], world.links[1], stranger, other.links[0]],
        )

    assert again.status_code == 202, again.text
    body = again.json()
    assert body["invited"] == 1
    reasons = {item["link_id"]: item["reason"] for item in body["skipped"]}
    assert reasons == {
        str(world.links[1]): "Already at stage 'Assessment invitation sent'",
        str(stranger): "Application not found",
        # Another tenant's application is invisible to this one: the same
        # answer as an id that does not exist, never a hint that it does.
        str(other.links[0]): "Application not found",
    }
    assert await _conversations(world) == 2
    assert await _conversations(other) == 0


async def test_a_job_whose_skills_are_not_saved_invites_nobody(
    seeded, no_drafting_in_the_request
) -> None:
    world = await seeded(applicants=1, assessment_status="questions_pending_review")
    with _client(world) as http:
        refused = _invite(http, world, world.links)
    assert refused.status_code == 409
    assert refused.json()["detail"] == assessment_invitations.NOT_READY_DETAIL
    assert await _conversations(world) == 0


async def test_a_closed_job_invites_nobody(seeded, no_drafting_in_the_request) -> None:
    """Closing withholds the job's assessment records from the team
    (2026-09-22). An invitation after closing would charge for a report
    nobody on the employer's side could open."""
    world = await seeded(applicants=1, grant_subunits=STEM_REPORT)
    async with comms_world.factory()() as session:
        async with session.begin():
            await comms_world.bypass(session)
            await session.execute(
                sa.text("UPDATE jobs SET closed_at = now() WHERE id = :id"),
                {"id": str(world.job)},
            )
    with _client(world) as http:
        refused = _invite(http, world, world.links)
    assert refused.status_code == 409
    assert refused.json()["detail"] == assessment_invitations.CLOSED_DETAIL
    assert await _conversations(world) == 0


def test_the_ready_state_is_the_one_skills_writes() -> None:
    """Read by value in `assessment_invitations` so an invitation does not
    load the Sutra stack; this is what keeps the two from drifting."""
    assert assessment_invitations.READY_FOR_CANDIDATES == skills.READY_FOR_CANDIDATES


# ── After the commit, and only after it ─────────────────────────────────────


async def test_a_batch_that_rolls_back_dispatches_nothing(
    seeded, no_drafting_in_the_request
) -> None:
    world = await seeded(applicants=2, grant_subunits=2 * STEM_REPORT)
    async with comms_world.factory()() as session:
        await session.begin()
        async with tenant_scope(session, world.tenant):
            result = await assessment_invitations.invite_batch(
                session,
                tenant_id=world.tenant,
                job_id=world.job,
                link_ids=world.links,
                actor_user_id=world.recruiter,
            )
            assert len(result.invited) == 2
            # Nothing leaves the process while the transaction is open.
            assert dispatch_mod.recorded_names() == []
        await session.rollback()
    assert dispatch_mod.recorded_names() == []
    assert await _conversations(world) == 0


async def test_two_recruiters_inviting_the_same_applicant_invite_them_once(
    seeded, no_drafting_in_the_request
) -> None:
    """The second batch WAITS on the first's row lock, then reads the stage
    the first wrote and skips the applicant, instead of racing to the same
    transition and failing its whole batch."""
    world = await seeded(applicants=1, grant_subunits=2 * STEM_REPORT)
    factory = comms_world.factory()

    async def _batch(session):
        return await assessment_invitations.invite_batch(
            session,
            tenant_id=world.tenant,
            job_id=world.job,
            link_ids=world.links,
            actor_user_id=world.recruiter,
        )

    async with factory() as first, factory() as second:
        await first.begin()
        await second.begin()
        async with tenant_scope(first, world.tenant):
            won = await _batch(first)
            async with tenant_scope(second, world.tenant):
                waiting = asyncio.create_task(_batch(second))
                await asyncio.sleep(0.5)
                assert not waiting.done(), "the second batch did not wait for the lock"
                await first.commit()
                lost = await asyncio.wait_for(waiting, timeout=10)
        await second.commit()

    assert won.invited == (world.links[0],)
    assert lost.invited == ()
    assert [item["reason"] for item in lost.skipped] == [
        "Already at stage 'Assessment invitation sent'"
    ]
    assert await _conversations(world) == 1
    assert dispatch_mod.recorded_names().count(INVITE) == 1


# ── "Move to: Assessment invitation sent" is an invitation ──────────────────


def _move(http: TestClient, link, status: str) -> object:
    return http.post(
        f"/api/v1/pipeline/applications/{link}/change-status",
        json={"status": status, "send_email": False},
    )


async def test_the_hand_move_to_invited_goes_through_the_invitation(
    seeded, no_drafting_in_the_request
) -> None:
    """Applied directly, the hand move wrote the stage with NO conversation
    row, which is the invitation: the candidate was mailed a link the start
    route refused, no credit was asked about, and `select-candidates` could
    never invite them afterwards because they had left `applied`. It is the
    one invitation path now: the row, the credit question, the email by a
    worker after the commit, and a second move is refused, not doubled.

    Mutation-checked: routing the move back to `apply_transition` fails the
    conversation-row assertion (0 rows) and the dispatch assertion."""
    world = await seeded(applicants=1, grant_subunits=STEM_REPORT)
    link = world.links[0]
    with _client(world) as http:
        moved = _move(http, link, "assessment_invited")
        assert moved.status_code == 200, moved.text
        assert moved.json()["status"] == "assessment_invited"
        # Always sent: the invitation carries the candidate's only way in.
        assert moved.json()["email_queued"] is True
        again = _move(http, link, "assessment_invited")

    assert again.status_code == 409
    assert again.json()["detail"] == "Already at stage 'Assessment invitation sent'"
    rows = await comms_world.rows(
        comms_world.factory(),
        "SELECT invitation_sent_at, invited_by FROM assessment_conversations "
        "WHERE job_candidate_link_id = :l",
        {"l": str(link)},
    )
    assert len(rows) == 1 and rows[0]["invitation_sent_at"] is not None
    assert rows[0]["invited_by"] == world.recruiter
    assert await invite_world.scalar(
        "SELECT count(*) FROM pipeline_status WHERE job_candidate_link_id = :l",
        {"l": str(link)},
    ) == 1
    assert dispatch_mod.recorded_names().count(INVITE) == 1
    assert dispatch_mod.recorded_names().count(QUESTIONS) == 1


async def test_the_hand_move_to_invited_asks_the_credit_question(
    seeded, no_drafting_in_the_request
) -> None:
    world = await seeded(applicants=1, grant_subunits=0)
    link = world.links[0]
    with _client(world) as http:
        refused = _move(http, link, "assessment_invited")
    assert refused.status_code == 402, refused.text
    assert "Nobody was invited" in refused.json()["detail"]
    assert await _statuses(world) == ["applied"]
    assert await _conversations(world) == 0
    assert dispatch_mod.recorded_names() == []


async def test_the_hand_move_to_invited_needs_the_invitation_capability(
    seeded, no_drafting_in_the_request
) -> None:
    """`change-status` asks for `decide_profile`; inviting asks for
    `send_outreach`. A person holding only the first must not invite by the
    second door."""
    world = await seeded(applicants=1, grant_subunits=STEM_REPORT)
    link = world.links[0]
    async with comms_world.factory()() as session:
        async with session.begin():
            await comms_world.bypass(session)
            await session.execute(
                sa.text(
                    "UPDATE users SET permissions_json = "
                    "CAST(:p AS jsonb) WHERE id = :id"
                ),
                {"p": '{"send_outreach": false}', "id": str(world.recruiter)},
            )
    with _client(world) as http:
        refused = _move(http, link, "assessment_invited")
    assert refused.status_code == 403, refused.text
    assert await _statuses(world) == ["applied"]
    assert await _conversations(world) == 0
