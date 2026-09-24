"""Delete My Profile deletes the Firebase sign-in identity too (WP6-B).

THE DEFECT. `cascade_erasure` deleted the `users` row and left the Firebase
account alive, so the next sign-in with the same Google account or password
silently created a fresh, empty profile for a person who had just been told
they were erased.

THE RULES THIS PINS, each read back on a SECOND connection:

* success: the rows are gone, the identity is deleted, and the object pass and
  the confirmation letter are dispatched only after the commit;
* the identity deletion FAILS: 503 with the server's sentence, and nothing was
  deleted at all (rows, sign-in account and deletion request all still there,
  nothing dispatched), because a live sign-in over erased rows is exactly the
  state being closed and "try again" must be true advice;
* the same address is ALSO a staff sign-in: the candidate is erased and the
  identity is KEPT, because it is somebody's door into an employer workspace.

Over HTTP as a real signed-in candidate, no dependency overrides. Firebase is
replaced at `firebase_auth.delete_identity`, the one function the route calls.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services import account_deletion, firebase_auth
from app.workers import dispatch as dispatch_mod
from tests.candidate_session import close_candidate_session, create_candidate_session
from tests.portal_fixtures import drop_employer, engine_and_factory, scalar, seed_employer


@pytest.fixture
async def doomed(monkeypatch):
    """A signed-in candidate, the uid Firebase knows them by, and a recorder
    standing in for the Firebase Admin call."""
    engine, factory = engine_and_factory()
    employer = await seed_employer(factory, jobs=1)
    candidate = await create_candidate_session(factory)
    uid = await scalar(
        factory, "SELECT firebase_uid FROM users WHERE id = :u", u=str(candidate.user_id)
    )
    calls: list[str] = []
    outcome: dict[str, bool] = {"fail": False}

    def fake_delete_identity(firebase_uid: str) -> bool:
        calls.append(firebase_uid)
        if outcome["fail"]:
            raise firebase_auth.IdentityDeletionFailed("ConnectionError")
        return True

    monkeypatch.setattr(firebase_auth, "delete_identity", fake_delete_identity)
    try:
        yield factory, employer, candidate, uid, calls, outcome
    finally:
        await scalar(
            factory,
            "DELETE FROM candidate_deletion_requests WHERE candidate_id = :c "
            "RETURNING id",
            c=str(candidate.candidate_id),
        )
        await drop_employer(factory, employer)
        await close_candidate_session(factory, candidate)
        await engine.dispose()


def _delete(candidate):
    with TestClient(app, cookies=candidate.cookies()) as http:
        return http.request(
            "DELETE",
            "/api/v1/portal/me",
            json={"confirmation": account_deletion.CONFIRMATION_PHRASE},
            headers=candidate.headers(activity=True),
        )


async def _counts(factory, candidate) -> dict[str, int]:
    return {
        "candidate": await scalar(
            factory, "SELECT count(*) FROM candidates WHERE id = :c",
            c=str(candidate.candidate_id),
        ),
        "user": await scalar(
            factory, "SELECT count(*) FROM users WHERE id = :u", u=str(candidate.user_id)
        ),
        "request": await scalar(
            factory,
            "SELECT count(*) FROM candidate_deletion_requests WHERE candidate_id = :c",
            c=str(candidate.candidate_id),
        ),
    }


async def test_the_identity_goes_with_the_profile(doomed) -> None:
    factory, _employer, candidate, uid, calls, _outcome = doomed
    dispatch_mod.clear_recorded()

    response = _delete(candidate)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sign_in_identity_deleted"] is True
    assert body["sign_in_identity_note"] is None
    assert calls == [uid], "the Firebase identity was not deleted"

    assert await _counts(factory, candidate) == {"candidate": 0, "user": 0, "request": 1}
    assert dispatch_mod.recorded_names() == [
        "pickready.cascade_erasure",
        "pickready.send_email",
    ]


async def test_a_failed_identity_deletion_deletes_nothing(doomed) -> None:
    factory, _employer, candidate, uid, calls, outcome = doomed
    outcome["fail"] = True
    dispatch_mod.clear_recorded()

    response = _delete(candidate)
    assert response.status_code == 503, response.text
    assert response.json()["detail"] == account_deletion.IDENTITY_DELETION_FAILED_MESSAGE
    assert calls == [uid]

    assert await _counts(factory, candidate) == {"candidate": 1, "user": 1, "request": 0}, (
        "rows were erased while the sign-in identity survived"
    )
    assert dispatch_mod.recorded_names() == [], (
        "a rolled-back erasure dispatched its object pass or its letter"
    )


async def test_a_shared_staff_address_keeps_the_identity(doomed) -> None:
    factory, employer, candidate, _uid, calls, _outcome = doomed
    staff_id = uuid.uuid4()
    await scalar(
        factory,
        "INSERT INTO users (id, email, role, status, full_name, tenant_id) VALUES "
        "(:u, :e, 'recruiter', 'active', 'Also A Recruiter', :t) RETURNING id",
        u=str(staff_id),
        e=candidate.email.upper(),
        t=str(employer.tenant_id),
    )
    employer.extra_users.append(staff_id)

    response = _delete(candidate)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["sign_in_identity_deleted"] is False
    assert body["sign_in_identity_note"] == account_deletion.SHARED_IDENTITY_NOTE
    assert calls == [], "a staff sign-in's Firebase identity was deleted"

    counts = await _counts(factory, candidate)
    assert counts["candidate"] == 0 and counts["user"] == 0
    assert await scalar(
        factory, "SELECT count(*) FROM users WHERE id = :u", u=str(staff_id)
    ) == 1
