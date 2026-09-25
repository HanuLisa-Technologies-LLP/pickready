"""One assessment mode, one consent text, and no route that chooses between modes.

Appendix B section 1: there is ONE assessment. The video interview mode and its
mode-selection step are gone, so the consent the candidate reads is one text,
served by the server with its version, and the row it writes says
`conversational`, the one mode (the column and its CHECK stay so every row
written before today still reads).
"""
from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.services import consent_catalog
from app.services.assessment_consent import CONSENT_REQUIRED_DETAIL
from tests import conversation_world as cw
from tests.candidate_session import close_candidate_session, create_candidate_session


@pytest.fixture
async def candidate():
    if not await cw.reachable():
        pytest.skip("no database reachable")
    factory = cw.sessions()
    person = await create_candidate_session(factory)
    try:
        yield person
    finally:
        await close_candidate_session(factory, person)


def test_the_consent_text_states_what_the_session_collects_and_keeps() -> None:
    settings = get_settings()
    text = settings.assessment_consent_text
    assert settings.assessment_consent_version == "2026-09-24"
    for required in (
        "record the whole session",
        "90 days after your session",
        "30 days after the job closes",
        "only the text is kept",
        "pasting are blocked",
    ):
        assert required in text, f"the consent text no longer says {required!r}"
    assert chr(8212) not in text
    assert not hasattr(settings, "assessment_consent_text_video")
    assert not hasattr(settings, "assessment_consent_text_conversational")


async def test_the_screen_is_served_one_text_and_a_start_without_it_is_refused(
    candidate, monkeypatch
) -> None:
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    world = await cw.seed(factory, candidate_id=candidate.candidate_id, consented=False)
    try:
        with cw.client(candidate) as http:
            shown = http.get(f"{cw.BASE}/conversations/links/{world.link}/consent")
            refused = http.post(f"{cw.BASE}/conversations/links/{world.link}/start")
            keys = [item["key"] for item in shown.json()["consent"]["items"]]
            accepted = http.post(
                f"{cw.BASE}/conversations/links/{world.link}/consent",
                json={"consent_keys": keys},
            )
            started = http.post(f"{cw.BASE}/conversations/links/{world.link}/start")

        assert shown.status_code == 200, shown.text
        body = shown.json()
        assert body["consented"] is False
        assert body["consent"]["text"] == get_settings().assessment_consent_text
        assert body["consent"]["consent_version"] == "2026-09-24"
        assert "mode" not in body and "modes" not in body
        assert keys == [
            item["key"]
            for item in consent_catalog.stage_payload(consent_catalog.STAGE_ASSESSMENT)
        ]

        assert refused.status_code == 409
        assert refused.json()["detail"] == CONSENT_REQUIRED_DETAIL
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["consented"] is True
        assert started.status_code == 200, started.text

        consents = await cw.committed(
            factory,
            "SELECT assessment_mode, consent_version FROM assessment_consents "
            "WHERE conversation_id = :c",
            c=str(world.conversation),
        )
        assert [(row.assessment_mode, row.consent_version) for row in consents] == [
            ("conversational", "2026-09-24")
        ]
        row = await cw.conversation_row(factory, world)
        assert row.mode == "conversational"
    finally:
        await cw.cleanup(factory, world)


async def test_there_is_no_mode_route(candidate) -> None:
    factory = cw.sessions()
    world = await cw.seed(factory, candidate_id=candidate.candidate_id)
    try:
        with cw.client(candidate) as http:
            read = http.get(f"{cw.BASE}/conversations/links/{world.link}/mode")
            chosen = http.post(
                f"{cw.BASE}/conversations/links/{world.link}/mode",
                json={"mode": "video_interview"},
            )
        assert read.status_code in (404, 405)
        assert chosen.status_code in (404, 405)
        assert (await cw.conversation_row(factory, world)).mode == "conversational"
    finally:
        await cw.cleanup(factory, world)


async def test_a_session_begun_in_the_retired_mode_cannot_continue_as_a_conversation(
    candidate, monkeypatch
) -> None:
    """Migration 0123 relabelled every UNSTARTED video row. A started one is a
    row from before it, and its records belong to the other mode."""
    cw.quiet_models(monkeypatch)
    factory = cw.sessions()
    world = await cw.seed(factory, candidate_id=candidate.candidate_id, started=True)
    try:
        await cw.set_conversation(factory, world, mode="video_interview")
        with cw.client(candidate) as http:
            refused = http.post(f"{cw.BASE}/conversations/links/{world.link}/start")
        assert refused.status_code == 409
        assert "video interview" in refused.json()["detail"]
    finally:
        await cw.cleanup(factory, world)
