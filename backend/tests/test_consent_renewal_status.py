"""`GET /portal/me/consent/renewal`: the "Keep my profile" card's status.

The renewal act (`POST /portal/me/consent/renew`) existed with no screen. The
card needs to say whether confirmation is due and when the next one is, and it
must say what the SWEEP will do, so the route derives everything through
`consent_lifecycle` over the same columns and the same thresholds the sweep
reads. Reading it renews nothing. Over HTTP as a real signed-in candidate.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api import portal as portal_mod
from app.core.config import get_settings
from app.main import app
from app.services import consent_lifecycle
from tests.candidate_session import close_candidate_session, create_candidate_session
from tests.portal_fixtures import engine_and_factory, scalar

URL = "/api/v1/portal/me/consent/renewal"


@pytest.fixture
async def signed_in():
    engine, factory = engine_and_factory()
    candidate = await create_candidate_session(factory)
    try:
        yield factory, candidate
    finally:
        await close_candidate_session(factory, candidate)
        await engine.dispose()


def _call(candidate, method: str, path: str):
    with TestClient(app, cookies=candidate.cookies()) as http:
        return http.request(method, path, headers=candidate.headers(activity=True))


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def test_a_new_profile_is_active_and_says_when_it_is_due(signed_in) -> None:
    factory, candidate = signed_in
    response = _call(candidate, "GET", URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stage"] == consent_lifecycle.STAGE_ACTIVE
    assert body["renewal_needed"] is False
    assert body["message"] == portal_mod.RENEWAL_NOT_YET_DUE_MESSAGE
    months = get_settings().consent_renewal_months
    assert _at(body["renewal_due_at"]) - _at(body["consented_at"]) == timedelta(
        days=30 * months
    )
    # Reading is not renewing.
    assert await scalar(
        factory,
        "SELECT consent_renewed_at FROM candidates WHERE id = :c",
        c=str(candidate.candidate_id),
    ) is None


async def test_an_overdue_profile_is_due_until_it_is_renewed(signed_in) -> None:
    factory, candidate = signed_in
    months = get_settings().consent_renewal_months
    await scalar(
        factory,
        "UPDATE candidates SET created_at = now() - make_interval(days => :d) "
        "WHERE id = :c RETURNING id",
        d=30 * months + 5,
        c=str(candidate.candidate_id),
    )
    due = _call(candidate, "GET", URL).json()
    assert due["renewal_needed"] is True
    # No reminder has gone out yet, which is what the sweep would act on next.
    assert due["stage"] == consent_lifecycle.STAGE_REMINDER_DUE
    assert due["message"] == portal_mod.RENEWAL_DUE_MESSAGE

    renewed = _call(candidate, "POST", "/api/v1/portal/me/consent/renew")
    assert renewed.status_code == 200, renewed.text

    after = _call(candidate, "GET", URL).json()
    assert after["renewal_needed"] is False
    assert after["stage"] == consent_lifecycle.STAGE_ACTIVE
    assert _at(after["consented_at"]) > _at(due["consented_at"])


def test_the_card_copy_follows_the_client_copy_rules() -> None:
    for message in (
        portal_mod.RENEWAL_DUE_MESSAGE,
        portal_mod.RENEWAL_NOT_YET_DUE_MESSAGE,
    ):
        assert chr(8212) not in message
        assert not re.search(r"\d", message), "a number in candidate-facing copy"
