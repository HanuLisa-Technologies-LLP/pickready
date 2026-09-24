"""Background verification, from the candidate's declaration to the offer gate.

WHAT THIS ADDS OVER `test_bgv_workflow.py`
-------------------------------------------
That file proves the arithmetic: `derive_status` over every combination, the
immutability trigger, the chokepoint. This one walks the ROUTES a person
actually touches, in order, because several of the properties that matter are
properties of the sequence rather than of any one function:

  * a declaration is editable until it is submitted and final afterwards, and
    the refusal is the SERVER's, not the form's;
  * initiating twice produces one verification per employer, not two;
  * the drafted email is grounded in what the candidate submitted, and says so
    when the model was unavailable rather than passing a template off as
    generation;
  * marking the LAST employer verified is what opens the offer gate, and until
    then `apply_transition` refuses with the same sentence the screen shows.

THE LAST ONE IS THE POINT OF THE FEATURE. Everything else here exists so that
assertion can be made against a real pipeline transition rather than against a
status column somebody set by hand.
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Iterator

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
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
from app.models.enums import Role
from app.services import bgv_workflow, hiring_pipeline

BGV = "/api/v1/bgv"


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
            await conn.execute(sa.text("SELECT 1 FROM bgv_verifications LIMIT 0"))
        return True
    except Exception:  # noqa: BLE001 -- no database here
        return False
    finally:
        await engine.dispose()


class World:
    def __init__(self) -> None:
        self.tenant = uuid.uuid4()
        self.other_tenant = uuid.uuid4()
        self.recruiter = uuid.uuid4()
        self.other_recruiter = uuid.uuid4()
        self.job = uuid.uuid4()
        self.candidate = uuid.uuid4()
        self.link = uuid.uuid4()
        #: The portal account the candidate signs in with. LINKED to the
        #: candidate row by `candidates.user_id`, which is the state
        #: `candidate_identity.link_on_sign_in` leaves after a verified first
        #: sign-in and the only thing a request resolves a candidate by.
        self.candidate_user = uuid.uuid4()
        self.email = f"cand-{self.candidate.hex[:10]}@bgvapi.test"


@pytest.fixture
def world() -> Iterator[World]:
    if not _run(_reachable()):
        pytest.skip("no database reachable -- skipping BGV API test")

    w = World()
    sessions = _sessions()

    async def _seed() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    for tid, label in (
                        (w.tenant, "BGV-API-A"),
                        (w.other_tenant, "BGV-API-B"),
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
                                "domain": f"{tid.hex[:10]}.bgvapi.test",
                            },
                        )
                    for uid, tid in (
                        (w.recruiter, w.tenant),
                        (w.other_recruiter, w.other_tenant),
                    ):
                        await session.execute(
                            sa.text(
                                "INSERT INTO users (id, tenant_id, email, full_name, "
                                " role, status) "
                                "VALUES (:id, :tid, :email, 'Priya Raman', "
                                " 'client', 'active')"
                            ),
                            {
                                "id": str(uid),
                                "tid": str(tid),
                                "email": f"{uid.hex[:10]}@bgvapi.test",
                            },
                        )
                    await session.execute(
                        sa.text(
                            "INSERT INTO users (id, tenant_id, email, full_name, "
                            " role, status) "
                            "VALUES (:id, NULL, :email, 'Karthik Kumar', "
                            " 'candidate', 'active')"
                        ),
                        {"id": str(w.candidate_user), "email": w.email},
                    )
                    await session.execute(
                        sa.text(
                            "INSERT INTO jobs (id, tenant_id, title, jd_json, status) "
                            "VALUES (:id, :tid, 'Backend Engineer', "
                            " CAST('{}' AS jsonb), 'draft')"
                        ),
                        {"id": str(w.job), "tid": str(w.tenant)},
                    )
                    await session.execute(
                        sa.text(
                            "INSERT INTO candidates (id, user_id, full_name, email, "
                            " consent_databank, created_at) "
                            "VALUES (:id, :uid, 'Karthik Kumar', :email, false, now())"
                        ),
                        {
                            "id": str(w.candidate),
                            "uid": str(w.candidate_user),
                            "email": w.email,
                        },
                    )
                    await session.execute(
                        sa.text(
                            "INSERT INTO job_candidate_links "
                            "(id, tenant_id, job_id, candidate_id, source, status) "
                            "VALUES (:id, :tid, :jid, :cid, 'fresh', 'shortlisted')"
                        ),
                        {
                            "id": str(w.link),
                            "tid": str(w.tenant),
                            "jid": str(w.job),
                            "cid": str(w.candidate),
                        },
                    )

    async def _teardown() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text("DELETE FROM candidates WHERE id = :id"),
                        {"id": str(w.candidate)},
                    )
                    await session.execute(
                        sa.text("DELETE FROM users WHERE id = :id"),
                        {"id": str(w.candidate_user)},
                    )
                    await session.execute(
                        sa.text("DELETE FROM tenants WHERE id = ANY(:ids)"),
                        {"ids": [str(w.tenant), str(w.other_tenant)]},
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

    def as_recruiter(self, world: World, tenant: uuid.UUID | None = None) -> None:
        tenant = tenant or world.tenant
        self.principal = CurrentUser(
            user_id=(
                world.recruiter if tenant == world.tenant else world.other_recruiter
            ),
            tenant_id=tenant,
            role=Role.client,
            audience=AUDIENCE_ORG,
        )

    def as_candidate(self, world: World) -> None:
        # The candidate's own BGV routes read `get_current_candidate` and run
        # on `get_candidate_db`: a CANDIDATE-audience principal, the only kind
        # a real candidate's cookie can produce. The handler resolves the
        # candidate from this user rather than from an id the client sent.
        # `test_bgv_real_candidate_token.py` makes the same calls with a real
        # session and no overrides; this file keeps the overrides for speed.
        self.principal = CurrentUser(
            user_id=world.candidate_user,
            tenant_id=None,
            role=Role.candidate,
            audience=AUDIENCE_CANDIDATE,
        )


@pytest.fixture
def client(world: World) -> Iterator[Caller]:
    sessions = _sessions()
    caller = Caller()

    # Each override refuses the other audience exactly as the real dependency
    # does, so a route that asked for the wrong principal fails here too
    # instead of being handed whatever the test set.
    async def _current_user() -> CurrentUser:
        assert caller.principal is not None
        if caller.principal.audience == AUDIENCE_CANDIDATE:
            raise HTTPException(status_code=401, detail="staff session required")
        return caller.principal

    async def _current_candidate() -> CurrentUser:
        assert caller.principal is not None
        if (
            caller.principal.audience != AUDIENCE_CANDIDATE
            or caller.principal.role != Role.candidate
        ):
            raise HTTPException(status_code=401, detail="candidate session required")
        return caller.principal

    async def _tenant_db():
        principal = caller.principal
        assert principal is not None and principal.tenant_id is not None
        async with sessions() as session:
            async with session.begin():
                async with tenant_scope(session, principal.tenant_id):
                    yield session

    async def _candidate_db():
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    yield session

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_current_user] = _current_user
    app.dependency_overrides[get_current_candidate] = _current_candidate
    app.dependency_overrides[get_tenant_db] = _tenant_db
    app.dependency_overrides[get_candidate_db] = _candidate_db
    try:
        with TestClient(app) as http:
            caller.http = http
            yield caller
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def _employer(name: str, hr: str) -> dict:
    return {
        "employer_name": name,
        "designation": "Senior Engineer",
        "started_on": "2021-01-01",
        "ended_on": "2023-01-01",
        "hr_name": "Meera Nair",
        "hr_email": hr,
    }


def _declare(caller: Caller, employers: list[dict], finalize: bool = True):
    return caller.http.put(
        f"{BGV}/me",
        json={
            "background": "experienced" if employers else "fresher",
            "employments": employers,
            "finalize": finalize,
        },
    )


async def _transition(world: World, to_status: str) -> None:
    """Move the application through the REAL pipeline, not a status update.

    The gate lives inside `apply_transition`, so a test that wrote the column
    directly would assert nothing about it.
    """
    sessions = _sessions()
    async with sessions() as session:
        async with session.begin():
            async with tenant_scope(session, world.tenant):
                await hiring_pipeline.apply_transition(
                    session,
                    link_id=world.link,
                    tenant_id=world.tenant,
                    target=to_status,
                    actor_user_id=world.recruiter,
                )


# ── The candidate's declaration ──────────────────────────────────────────────


def test_a_fresher_declares_and_needs_no_verification(
    client: Caller, world: World
) -> None:
    client.as_candidate(world)
    saved = _declare(client, [])
    assert saved.status_code == 200, saved.text
    assert saved.json()["background"] == "fresher"
    assert saved.json()["employments"] == []
    # The warning comes from the SERVER, so the sentence describing the rule and
    # the rule itself cannot drift.
    assert "final" in saved.json()["submission_warning"].lower()

    client.as_recruiter(world)
    view = client.http.get(f"{BGV}/candidates/{world.candidate}")
    assert view.status_code == 200, view.text
    assert view.json()["required"] is False
    assert view.json()["status"] == bgv_workflow.CANDIDATE_BGV_NOT_REQUIRED
    assert view.json()["offer_blocked_reason"] is None


def test_a_declaration_is_editable_until_it_is_submitted(
    client: Caller, world: World
) -> None:
    client.as_candidate(world)
    first = _declare(
        client, [_employer("Acme Systems", "hr@acme-systems.example.com")], finalize=False
    )
    assert first.status_code == 200, first.text
    assert first.json()["finalized"] is False

    # REPLACE, not merge: the candidate submits the list they can see, and a
    # merge would leave a row they deleted still in the database and still about
    # to be emailed.
    second = _declare(client, [_employer("Beta Labs", "hr@beta-labs.example.com")], finalize=False)
    assert [e["employer_name"] for e in second.json()["employments"]] == ["Beta Labs"]
    # Nothing has been sent, so nothing can have bounced: the correction
    # control is offered nowhere.
    assert [e["correction_needed"] for e in second.json()["employments"]] == [False]


def test_a_submitted_declaration_is_refused_by_the_server(
    client: Caller, world: World
) -> None:
    """The form can hide the button; only the server can enforce the rule."""
    client.as_candidate(world)
    assert (
        _declare(client, [_employer("Acme Systems", "hr@acme-systems.example.com")]).status_code == 200
    )

    refused = _declare(client, [_employer("Rewritten Ltd", "hr@rewritten.example.com")])
    assert refused.status_code == 409
    assert "final" in refused.json()["detail"].lower()
    # And the original survives, which is the fact the refusal exists to protect.
    kept = client.http.get(f"{BGV}/me").json()["employments"]
    assert [e["employer_name"] for e in kept] == ["Acme Systems"]


def test_an_experienced_candidate_cannot_submit_an_empty_list(
    client: Caller, world: World
) -> None:
    client.as_candidate(world)
    refused = client.http.put(
        f"{BGV}/me",
        json={"background": "experienced", "employments": [], "finalize": True},
    )
    assert refused.status_code == 422


# ── The recruiter's side ─────────────────────────────────────────────────────


def _two_employers(client: Caller, world: World) -> list[dict]:
    client.as_candidate(world)
    _declare(
        client,
        [
            _employer("Acme Systems", "meera@acme-systems.example.com"),
            _employer("Beta Labs", "hr@beta-labs.example.com"),
        ],
    )
    client.as_recruiter(world)
    started = client.http.post(f"{BGV}/candidates/{world.candidate}/initiate")
    assert started.status_code == 200, started.text
    return started.json()["verifications"]


def test_initiating_twice_produces_one_record_per_employer(
    client: Caller, world: World
) -> None:
    """A recruiter clicking twice, or two recruiters on one candidate, must not
    double the employers anybody is about to email."""
    first = _two_employers(client, world)
    assert len(first) == 2
    again = client.http.post(f"{BGV}/candidates/{world.candidate}/initiate")
    assert {v["id"] for v in again.json()["verifications"]} == {v["id"] for v in first}


def test_the_draft_is_grounded_in_what_the_candidate_submitted(
    client: Caller, world: World
) -> None:
    """The agent may only say what the fact block carries.

    No model credential is configured in the suite, so this exercises the
    DETERMINISTIC path on purpose, and the flag is what makes that honest:
    template output presented as generation is a lie about how the text was
    produced.
    """
    verifications = _two_employers(client, world)
    acme = next(
        v for v in verifications if v["employment"]["employer_name"] == "Acme Systems"
    )
    drafted = client.http.post(f"{BGV}/verifications/{acme['id']}/draft", json={})
    assert drafted.status_code == 200, drafted.text
    body = drafted.json()

    assert "Acme Systems" in body["body"]
    assert "Senior Engineer" in body["body"]
    assert "2021" in body["body"]
    assert body["generated_by_ai"] is False
    # The other employer's name must not appear in this employer's email.
    assert "Beta Labs" not in body["body"]


def test_sending_records_the_message_and_moves_the_record_to_pending(
    client: Caller, world: World
) -> None:
    verifications = _two_employers(client, world)
    target = verifications[0]
    sent = client.http.post(
        f"{BGV}/verifications/{target['id']}/send",
        json={"subject": "Employment verification", "body": "Please confirm."},
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["status"] == "pending"
    assert sent.json()["first_sent_at"] is not None

    # The conversation row and the email are the same event seen from two sides.
    conversation_id = sent.json()["conversation_id"]
    assert conversation_id is not None
    thread = client.http.get(f"/api/v1/conversations/{conversation_id}/messages")
    assert thread.status_code == 200, thread.text
    assert [m["body"] for m in thread.json()] == ["Please confirm."]
    assert thread.json()[0]["channel"] == "email"


def test_another_tenant_cannot_see_or_decide_this_verification(
    client: Caller, world: World
) -> None:
    verifications = _two_employers(client, world)
    target = verifications[0]["id"]

    client.as_recruiter(world, world.other_tenant)
    assert (
        client.http.post(f"{BGV}/verifications/{target}/draft", json={}).status_code
        == 404
    )
    assert (
        client.http.post(
            f"{BGV}/verifications/{target}/decision",
            json={"verified": True, "note": None},
        ).status_code
        == 404
    )


# ── The gate, through a real transition ──────────────────────────────────────


def test_the_offer_is_refused_until_every_employer_is_verified(
    client: Caller, world: World
) -> None:
    """THE ASSERTION THE WHOLE FEATURE EXISTS FOR.

    Made through `apply_transition` rather than against a status column, because
    the gate lives inside it and a column write would prove nothing.
    """
    verifications = _two_employers(client, world)

    view = client.http.get(f"{BGV}/candidates/{world.candidate}").json()
    assert view["offer_blocked_reason"] is not None
    with pytest.raises(bgv_workflow.BGVIncomplete) as refused:
        _run(_transition(world, hiring_pipeline.OFFER_EXTENDED))
    # The screen renders the server's own sentence, so the two cannot disagree
    # about why the button did not work.
    assert str(refused.value) == view["offer_blocked_reason"]

    # One of two verified is still not enough.
    client.http.post(
        f"{BGV}/verifications/{verifications[0]['id']}/decision",
        json={"verified": True, "note": "Confirmed by phone."},
    )
    assert (
        client.http.get(f"{BGV}/candidates/{world.candidate}").json()[
            "offer_blocked_reason"
        ]
        is not None
    )
    with pytest.raises(bgv_workflow.BGVIncomplete):
        _run(_transition(world, hiring_pipeline.OFFER_EXTENDED))

    # The LAST one opens it.
    final = client.http.post(
        f"{BGV}/verifications/{verifications[1]['id']}/decision",
        json={"verified": True, "note": None},
    )
    assert final.status_code == 200, final.text
    assert final.json()["status"] == bgv_workflow.CANDIDATE_BGV_VERIFIED
    assert final.json()["offer_blocked_reason"] is None
    _run(_transition(world, hiring_pipeline.OFFER_EXTENDED))


def test_one_not_verified_closes_the_gate_again(client: Caller, world: World) -> None:
    """Reversible on purpose, in both directions: an employer who replies late
    and a discrepancy that turns out to be a payroll artefact both have to be
    recordable. What is not reversible is that somebody decided."""
    verifications = _two_employers(client, world)
    for item in verifications:
        client.http.post(
            f"{BGV}/verifications/{item['id']}/decision",
            json={"verified": True, "note": None},
        )
    assert (
        client.http.get(f"{BGV}/candidates/{world.candidate}").json()[
            "offer_blocked_reason"
        ]
        is None
    )

    reversed_one = client.http.post(
        f"{BGV}/verifications/{verifications[0]['id']}/decision",
        json={"verified": False, "note": "The dates do not match their records."},
    )
    assert reversed_one.json()["status"] == bgv_workflow.CANDIDATE_BGV_NOT_VERIFIED
    assert reversed_one.json()["offer_blocked_reason"] is not None
    with pytest.raises(bgv_workflow.BGVIncomplete):
        _run(_transition(world, hiring_pipeline.OFFER_EXTENDED))

    # Every decision names its decider. A verification that asserts a human
    # decided while being unable to say who is indistinguishable from the
    # pipeline having written it.
    decided = [
        v for v in reversed_one.json()["verifications"] if v["decided_at"] is not None
    ]
    assert decided and all(v["decided_by_name"] for v in decided)


def test_a_rejection_is_never_gated(client: Caller, world: World) -> None:
    """A gate that could stop a rejection would trap somebody in a pipeline over
    paperwork, which is worse for the candidate than the offer gate is good for
    the employer."""
    _two_employers(client, world)
    _run(_transition(world, hiring_pipeline.REJECTED))


def test_a_fresher_reaches_an_offer_with_nothing_to_verify(
    client: Caller, world: World
) -> None:
    client.as_candidate(world)
    _declare(client, [])
    client.as_recruiter(world)
    assert (
        client.http.get(f"{BGV}/candidates/{world.candidate}").json()[
            "offer_blocked_reason"
        ]
        is None
    )
    _run(_transition(world, hiring_pipeline.OFFER_EXTENDED))
