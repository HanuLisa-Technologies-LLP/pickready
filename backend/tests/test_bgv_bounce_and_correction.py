"""A bounce reaches the verification it belongs to, and the candidate can fix it.

THE DEFECT THIS EXISTS FOR
----------------------------
The bounce handler correlated an `email_log` row back to a verification through
the RECIPIENT ADDRESS, scoped to the tenant, newest send first, limit one. One
HR manager confirming two candidates at the same company is the ordinary case
at any large employer, and under it the wrong candidate was told to correct an
address that worked while the right one was told nothing. Nothing recorded the
mistake, because from inside the handler both rows look identical.

So the central assertion here is deliberately the awkward one: TWO open
verifications on ONE HR address, a bounce on the second, and the first must be
untouched. Under the old correlation that assertion fails.

WHAT THE CORRECTION MAY AND MAY NOT DO
----------------------------------------
The candidate may replace the ADDRESS and nothing else. The employment facts
stay immutable, and this file asserts that from the database's own side: the
0095/0103 trigger still raises on an UPDATE of `candidate_employments`, and the
declaration is still readable next to the correction afterwards. A correction
with no bounce behind it is refused, because a route that accepted one at any
time would be an employment editor with a narrower name.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api import email_senders as email_senders_api
from app.api.deps import CurrentUser, get_candidate_db, get_current_user, get_public_db
from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.core.security import AUDIENCE_ORG
from app.main import app
from app.models.enums import Role
from app.workers import dispatch as dispatch_module

BGV = "/api/v1/bgv"
TOPIC = "arn:aws:sns:ap-south-1:000000000000:bgv-test-topic"

#: One mailbox, two candidates. The whole point of the fixture.
SHARED_HR_EMAIL = "people@sharedhr.example"


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
            await conn.execute(
                sa.text("SELECT bgv_verification_id FROM email_log LIMIT 0")
            )
        return True
    except Exception:  # noqa: BLE001 -- no database, or migration 0114 unapplied
        return False
    finally:
        await engine.dispose()


class World:
    def __init__(self) -> None:
        self.tenant = uuid.uuid4()
        # Two candidates whose previous employer shares one HR mailbox.
        self.first = uuid.uuid4()
        self.second = uuid.uuid4()
        self.first_user = uuid.uuid4()
        self.second_user = uuid.uuid4()
        self.first_employment = uuid.uuid4()
        self.second_employment = uuid.uuid4()
        self.first_verification = uuid.uuid4()
        self.second_verification = uuid.uuid4()
        self.first_log = uuid.uuid4()
        self.second_log = uuid.uuid4()
        self.second_email = f"cand-{self.second.hex[:10]}@bgvbounce.test"
        self.first_email = f"cand-{self.first.hex[:10]}@bgvbounce.test"


@pytest.fixture
def world() -> Iterator[World]:
    if not _run(_reachable()):
        pytest.skip("no database with migration 0114 -- skipping BGV bounce test")
    w = World()
    sessions = _sessions()
    sent_at = datetime.now(timezone.utc) - timedelta(hours=2)

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
                            "id": str(w.tenant),
                            "name": f"BGV-BOUNCE-{w.tenant.hex[:6]}",
                            "domain": f"{w.tenant.hex[:10]}.bgvbounce.test",
                        },
                    )
                    for candidate, user, email in (
                        (w.first, w.first_user, w.first_email),
                        (w.second, w.second_user, w.second_email),
                    ):
                        await session.execute(
                            sa.text(
                                "INSERT INTO users (id, tenant_id, email, "
                                " full_name, role, status) "
                                "VALUES (:id, NULL, :email, 'Karthik Kumar', "
                                " 'candidate', 'active')"
                            ),
                            {"id": str(user), "email": email},
                        )
                        await session.execute(
                            sa.text(
                                "INSERT INTO candidates (id, full_name, email, "
                                " consent_databank, employment_background, "
                                " created_at) "
                                "VALUES (:id, 'Karthik Kumar', :email, false, "
                                " 'experienced', now())"
                            ),
                            {"id": str(candidate), "email": email},
                        )
                    for employment, candidate in (
                        (w.first_employment, w.first),
                        (w.second_employment, w.second),
                    ):
                        await session.execute(
                            sa.text(
                                "INSERT INTO candidate_employments (id, "
                                " candidate_id, employer_name, designation, "
                                " started_on, ended_on, hr_name, hr_email, "
                                " created_at) "
                                "VALUES (:id, :cid, 'Shared HR Corp', "
                                " 'Engineer', DATE '2019-06-03', "
                                " DATE '2023-05-31', 'R Menon', :hr, now())"
                            ),
                            {
                                "id": str(employment),
                                "cid": str(candidate),
                                "hr": SHARED_HR_EMAIL,
                            },
                        )
                    await session.execute(
                        sa.text(
                            "UPDATE candidates SET "
                            " employment_history_finalized_at = now() "
                            "WHERE id = ANY(:ids)"
                        ),
                        {"ids": [str(w.first), str(w.second)]},
                    )
                    for verification, candidate, employment in (
                        (w.first_verification, w.first, w.first_employment),
                        (w.second_verification, w.second, w.second_employment),
                    ):
                        await session.execute(
                            sa.text(
                                "INSERT INTO bgv_verifications (id, tenant_id, "
                                " candidate_id, candidate_employment_id, status, "
                                " first_sent_at, delivery_status, form_token, "
                                " form_token_issued_at, created_at) "
                                "VALUES (:id, :tid, :cid, :eid, 'pending', :at, "
                                " 'sent', :tok, :at, now())"
                            ),
                            {
                                "id": str(verification),
                                "tid": str(w.tenant),
                                "cid": str(candidate),
                                "eid": str(employment),
                                "at": sent_at,
                                "tok": f"tok-{verification.hex}",
                            },
                        )
                    for log, verification, candidate in (
                        (w.first_log, w.first_verification, w.first),
                        (w.second_log, w.second_verification, w.second),
                    ):
                        await session.execute(
                            sa.text(
                                "INSERT INTO email_log (id, tenant_id, "
                                " email_type, recipient_email, candidate_id, "
                                " subject, body, status, provider_message_id, "
                                " bgv_verification_id, created_at) "
                                "VALUES (:id, :tid, 'bgv_verification', :to, "
                                " :cid, 'Employment verification', 'body', "
                                " 'sent', :pmid, :vid, now())"
                            ),
                            {
                                "id": str(log),
                                "tid": str(w.tenant),
                                "to": SHARED_HR_EMAIL,
                                "cid": str(candidate),
                                "pmid": f"ses-{log.hex}",
                                "vid": str(verification),
                            },
                        )

    async def _teardown() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text("DELETE FROM candidates WHERE id = ANY(:ids)"),
                        {"ids": [str(w.first), str(w.second)]},
                    )
                    await session.execute(
                        sa.text("DELETE FROM users WHERE id = ANY(:ids)"),
                        {"ids": [str(w.first_user), str(w.second_user)]},
                    )
                    await session.execute(
                        sa.text("DELETE FROM tenants WHERE id = :id"),
                        {"id": str(w.tenant)},
                    )

    _run(_seed())
    dispatch_module.clear_recorded()
    try:
        yield w
    finally:
        _run(_teardown())


class _Settings:
    """Only the fields the SES webhook reads."""

    ses_sns_topic_arn = TOPIC


@pytest.fixture
def client(world: World, monkeypatch) -> Iterator[TestClient]:
    sessions = _sessions()
    caller: dict[str, CurrentUser | None] = {"principal": None}

    async def _public_db():
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    yield session

    async def _candidate_db():
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    yield session

    async def _current_user() -> CurrentUser:
        principal = caller["principal"]
        assert principal is not None
        return principal

    async def _signature_ok(_payload) -> bool:
        """The signature check is exercised by its own test, not bypassed here.

        This file is about what happens AFTER a message is accepted; leaving
        the real verifier in would make every case below depend on a live SNS
        certificate endpoint.
        """
        return True

    monkeypatch.setattr(email_senders_api, "_verify_sns_signature", _signature_ok)
    monkeypatch.setattr(email_senders_api, "get_settings", lambda: _Settings())

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_public_db] = _public_db
    app.dependency_overrides[get_candidate_db] = _candidate_db
    app.dependency_overrides[get_current_user] = _current_user
    try:
        with TestClient(app) as http:
            http.caller = caller  # type: ignore[attr-defined]
            yield http
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def _as_candidate(client: TestClient, world: World, user_id: uuid.UUID) -> None:
    client.caller["principal"] = CurrentUser(  # type: ignore[attr-defined]
        user_id=user_id,
        tenant_id=None,
        role=Role.candidate,
        audience=AUDIENCE_ORG,
    )


def _sns(kind: str, provider_message_id: str, *, bounce_type: str = "Permanent") -> dict:
    message: dict = {
        "eventType": kind,
        "mail": {"messageId": provider_message_id},
    }
    if kind == "Bounce":
        message["bounce"] = {
            "bounceType": bounce_type,
            "bounceSubType": "General",
        }
    elif kind == "Delivery":
        message["delivery"] = {"timestamp": "2026-09-22T10:00:00.000Z"}
    return {
        "Type": "Notification",
        "TopicArn": TOPIC,
        "Message": json.dumps(message),
    }


async def _verification(verification_id: uuid.UUID) -> dict:
    sessions = _sessions()
    async with sessions() as session:
        async with session.begin():
            async with superadmin_scope(session):
                row = (
                    await session.execute(
                        sa.text(
                            "SELECT delivery_status, delivered_at, bounced_at, "
                            " delivery_detail, form_token, reminder_sent_at "
                            "FROM bgv_verifications WHERE id = :id"
                        ),
                        {"id": str(verification_id)},
                    )
                ).mappings().first()
    return dict(row)


# ── The binding ──────────────────────────────────────────────────────────────


def test_a_bounce_reaches_only_the_verification_it_was_sent_for(
    world: World, client: TestClient
):
    """TWO open verifications, ONE HR address. The old correlation fails here."""
    response = client.post(
        "/api/v1/email-senders/events/ses",
        json=_sns("Bounce", f"ses-{world.second_log.hex}"),
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"status": "recorded"}

    bounced = _run(_verification(world.second_verification))
    untouched = _run(_verification(world.first_verification))
    assert bounced["delivery_status"] == "bounced"
    assert bounced["bounced_at"] is not None
    assert "Permanent" in (bounced["delivery_detail"] or "")
    # THE ASSERTION THE OLD CODE CANNOT MAKE.
    assert untouched["delivery_status"] == "sent"
    assert untouched["bounced_at"] is None

    alerts = [
        item
        for item in dispatch_module.recorded()
        if item.name == "pickready.send_email" and item.args[2] == "bgv_bounced"
    ]
    assert len(alerts) == 1
    # The RIGHT candidate, and the address masked rather than quoted in full.
    assert alerts[0].args[1] == world.second_email
    assert alerts[0].args[3]["masked_hr_email"] == "p***@sharedhr.example"


def test_a_transient_bounce_changes_no_verification_state(
    world: World, client: TestClient
):
    """A full mailbox is not an address the candidate can correct.

    SES is still retrying, so telling somebody to change a working address
    spends their credibility with a former employer on a delay we caused.
    """
    response = client.post(
        "/api/v1/email-senders/events/ses",
        json=_sns(
            "Bounce", f"ses-{world.second_log.hex}", bounce_type="Transient"
        ),
    )
    assert response.status_code == 200
    assert _run(_verification(world.second_verification))["delivery_status"] == "sent"
    assert [
        item
        for item in dispatch_module.recorded()
        if item.name == "pickready.send_email" and item.args[2] == "bgv_bounced"
    ] == []


def test_a_delivery_event_starts_the_employers_clock(world: World, client: TestClient):
    response = client.post(
        "/api/v1/email-senders/events/ses",
        json=_sns("Delivery", f"ses-{world.first_log.hex}"),
    )
    assert response.status_code == 200
    row = _run(_verification(world.first_verification))
    assert row["delivery_status"] == "delivered"
    assert row["delivered_at"] is not None


# ── The correction ───────────────────────────────────────────────────────────


def test_correcting_a_bounced_address_resends_with_a_fresh_token(
    world: World, client: TestClient
):
    before = _run(_verification(world.second_verification))
    client.post(
        "/api/v1/email-senders/events/ses",
        json=_sns("Bounce", f"ses-{world.second_log.hex}"),
    )
    dispatch_module.clear_recorded()

    _as_candidate(client, world, world.second_user)
    response = client.put(
        f"{BGV}/me/employers/{world.second_employment}/hr-email",
        json={"hr_email": "verification@sharedhr.example"},
    )
    assert response.status_code == 200, response.text

    after = _run(_verification(world.second_verification))
    assert after["delivery_status"] == "sent"
    # The bounce is CLEARED, so the corrected request gets its own three days
    # rather than inheriting a clock that already ran out.
    assert after["bounced_at"] is None
    assert after["reminder_sent_at"] is None
    # A FRESH single-use credential. The token in the mailbox that refused the
    # message is worthless, and reusing it would leave two live for one
    # verification if the original ever did arrive somewhere.
    assert after["form_token"] != before["form_token"]

    sends = [
        item
        for item in dispatch_module.recorded()
        if item.name == "pickready.send_email"
        and item.args[2] == "bgv_verification"
    ]
    assert len(sends) == 1
    assert sends[0].args[1] == "verification@sharedhr.example"


def test_the_employment_row_is_not_rewritten_by_a_correction(
    world: World, client: TestClient
):
    """The claim survives the correction, which is the whole design.

    A candidate who could edit the declaration could quietly redirect a
    verification to a mailbox they control and leave nothing behind to notice.
    """
    client.post(
        "/api/v1/email-senders/events/ses",
        json=_sns("Bounce", f"ses-{world.second_log.hex}"),
    )
    _as_candidate(client, world, world.second_user)
    assert (
        client.put(
            f"{BGV}/me/employers/{world.second_employment}/hr-email",
            json={"hr_email": "verification@sharedhr.example"},
        ).status_code
        == 200
    )

    async def _read() -> tuple[str, list[tuple[str, str]]]:
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    declared = (
                        await session.execute(
                            sa.text(
                                "SELECT hr_email FROM candidate_employments "
                                "WHERE id = :eid"
                            ),
                            {"eid": str(world.second_employment)},
                        )
                    ).scalar()
                    corrections = (
                        await session.execute(
                            sa.text(
                                "SELECT previous_hr_email, hr_email "
                                "FROM bgv_contact_corrections "
                                "WHERE candidate_employment_id = :eid "
                                "ORDER BY created_at"
                            ),
                            {"eid": str(world.second_employment)},
                        )
                    ).all()
        return declared, [(row[0], row[1]) for row in corrections]

    declared, corrections = _run(_read())
    assert declared == SHARED_HR_EMAIL
    assert corrections == [(SHARED_HR_EMAIL, "verification@sharedhr.example")]


def test_a_correction_without_a_bounce_is_refused(world: World, client: TestClient):
    """Otherwise this route is an employment editor with a narrower name."""
    _as_candidate(client, world, world.second_user)
    response = client.put(
        f"{BGV}/me/employers/{world.second_employment}/hr-email",
        json={"hr_email": "verification@sharedhr.example"},
    )
    assert response.status_code == 409
    assert "delivery failure" in response.json()["detail"]


def test_a_candidate_cannot_correct_somebody_elses_employer(
    world: World, client: TestClient
):
    """404, never 403: confirming the row exists confirms whose it is."""
    client.post(
        "/api/v1/email-senders/events/ses",
        json=_sns("Bounce", f"ses-{world.second_log.hex}"),
    )
    _as_candidate(client, world, world.first_user)
    response = client.put(
        f"{BGV}/me/employers/{world.second_employment}/hr-email",
        json={"hr_email": "verification@sharedhr.example"},
    )
    assert response.status_code == 404


def test_a_personal_mailbox_is_refused_as_a_correction(
    world: World, client: TestClient
):
    """A reply from gmail proves somebody owns a mailbox, not that HR answered."""
    client.post(
        "/api/v1/email-senders/events/ses",
        json=_sns("Bounce", f"ses-{world.second_log.hex}"),
    )
    _as_candidate(client, world, world.second_user)
    response = client.put(
        f"{BGV}/me/employers/{world.second_employment}/hr-email",
        json={"hr_email": "karthik.kumar@gmail.com"},
    )
    assert response.status_code == 422


def test_the_immutability_trigger_still_refuses_an_update(world: World):
    """The guarantee the correction route was built AROUND, not through.

    Nothing in this release weakened the 0103 trigger, and this is the
    assertion that keeps that true: an UPDATE of a finalised employment row
    still raises from the database itself.
    """

    async def _attempt() -> str | None:
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    try:
                        await session.execute(
                            sa.text(
                                "UPDATE candidate_employments "
                                "SET hr_email = 'anything@elsewhere.example' "
                                "WHERE id = :eid"
                            ),
                            {"eid": str(world.second_employment)},
                        )
                    except Exception as exc:  # noqa: BLE001 -- the refusal IS the result
                        return str(exc)
        return None

    message = _run(_attempt())
    assert message is not None
    assert "final" in message
