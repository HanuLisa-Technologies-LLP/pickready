"""Bulk outreach: word-count discipline, placeholder substitution, request
validation, and the pass-through template contract used by send_email.

No network and no DB — the LLM router is monkeypatched and the session-touching
paths are exercised through their pure helpers.
"""
import uuid

import pytest
from pydantic import ValidationError

from app.api import outreach as outreach_api
from app.schemas.outreach import OutreachComposeIn, OutreachSendIn, RecipientOverride
from app.schemas.outreach import OutreachDeliveryStatusIn
from app.services import email_render, outreach_content

_CANDIDATE = {"name": "Ada Lovelace", "email": "ada@example.com"}
_JOB = {"title": "Staff Engineer"}
_COMPANY = {"name": "Hanulisa Technologies"}


def _words(text: str) -> int:
    return len(text.split())


# ── Word-count discipline (150–200 words) ────────────────────────────────────


def test_enforce_word_count_pads_a_short_body() -> None:
    out = outreach_content.enforce_word_count("Hi Ada, we would like to talk.")
    assert outreach_content.WORD_MIN <= _words(out) <= outreach_content.WORD_MAX


def test_enforce_word_count_trims_a_long_body() -> None:
    long_body = " ".join(["This sentence has exactly seven words here."] * 60)
    out = outreach_content.enforce_word_count(long_body)
    assert _words(out) <= outreach_content.WORD_MAX


def test_enforce_word_count_keeps_signoff_last() -> None:
    body = "Hi Ada, short note.\n\nWarm regards,\nHanulisa Talent Team"
    out = outreach_content.enforce_word_count(body)
    assert out.strip().endswith("Hanulisa Talent Team")
    assert outreach_content.WORD_MIN <= _words(out) <= outreach_content.WORD_MAX


def test_enforce_word_count_is_idempotent() -> None:
    once = outreach_content.enforce_word_count("Hi Ada.")
    assert outreach_content.enforce_word_count(once) == once


@pytest.mark.asyncio
async def test_template_fallback_is_within_word_range(monkeypatch) -> None:
    async def _down(*a, **k):
        raise outreach_content.llm_router.LLMUnavailableError("all down")

    monkeypatch.setattr(outreach_content.llm_router, "chat_completion", _down)
    email = await outreach_content.generate_outreach_email(_CANDIDATE, _JOB, _COMPANY)
    body = email["text"]
    assert outreach_content.WORD_MIN <= _words(body) <= outreach_content.WORD_MAX
    # Still personalized.
    assert "Ada Lovelace" in body and "Staff Engineer" in body


@pytest.mark.asyncio
async def test_short_llm_body_triggers_one_corrective_then_pads(monkeypatch) -> None:
    calls = {"n": 0}

    async def _always_short(*a, **k):
        calls["n"] += 1
        return '{"subject": "Next steps", "body": "Hi Ada, come chat with us."}'

    monkeypatch.setattr(outreach_content.llm_router, "chat_completion", _always_short)
    email = await outreach_content.generate_outreach_email(_CANDIDATE, _JOB, _COMPANY)
    # Exactly ONE corrective regeneration, then deterministic padding.
    assert calls["n"] == 2
    assert email["subject"] == "Next steps"
    assert outreach_content.WORD_MIN <= _words(email["text"]) <= outreach_content.WORD_MAX


@pytest.mark.asyncio
async def test_in_range_llm_body_makes_only_one_call(monkeypatch) -> None:
    good_body = " ".join(["We would love to speak with you about this role."] * 18)
    calls = {"n": 0}

    async def _good(*a, **k):
        calls["n"] += 1
        return '{"subject": "Hello Ada", "body": "%s"}' % good_body

    monkeypatch.setattr(outreach_content.llm_router, "chat_completion", _good)
    email = await outreach_content.generate_outreach_email(_CANDIDATE, _JOB, _COMPANY)
    assert calls["n"] == 1
    assert outreach_content.WORD_MIN <= _words(email["text"]) <= outreach_content.WORD_MAX


@pytest.mark.asyncio
async def test_long_llm_body_is_trimmed(monkeypatch) -> None:
    long_body = " ".join(["We really want to speak with you soon."] * 60)

    async def _long(*a, **k):
        return '{"subject": "Hello", "body": "%s"}' % long_body

    monkeypatch.setattr(outreach_content.llm_router, "chat_completion", _long)
    email = await outreach_content.generate_outreach_email(_CANDIDATE, _JOB, _COMPANY)
    assert _words(email["text"]) <= outreach_content.WORD_MAX


# ── Manual mode: placeholder substitution ────────────────────────────────────


def test_manual_placeholders_substituted() -> None:
    ctx = {
        "candidate_name": "Ada Lovelace",
        "company": "Hanulisa Technologies",
        "job_title": "Staff Engineer",
    }
    out = outreach_api._substitute(
        "Hi {{candidate_name}}, about {{ job_title }} at {{company}}.", ctx
    )
    assert out == "Hi Ada Lovelace, about Staff Engineer at Hanulisa Technologies."


def test_manual_unknown_placeholder_is_left_alone() -> None:
    out = outreach_api._substitute("Hi {{nickname}}", {"candidate_name": "Ada"})
    assert out == "Hi {{nickname}}"


def test_display_name_falls_back_to_email() -> None:
    class _C:
        full_name = None
        email = "ada@example.com"

    assert outreach_api._display_name(_C()) == "ada@example.com"
    assert outreach_api._display_name(None) == "Unknown candidate"


# ── Request validation ───────────────────────────────────────────────────────


def test_empty_selection_is_rejected() -> None:
    # An empty selection is a 422 at the schema boundary, never a silent no-op.
    with pytest.raises(ValidationError):
        OutreachComposeIn(job_id=uuid.uuid4(), link_ids=[], mode="ai")


def test_defaults_to_ai_mode() -> None:
    payload = OutreachComposeIn(job_id=uuid.uuid4(), link_ids=[uuid.uuid4()])
    assert payload.mode == "ai"


@pytest.mark.asyncio
async def test_manual_mode_requires_subject_and_body() -> None:
    from fastapi import HTTPException

    payload = OutreachComposeIn(
        job_id=uuid.uuid4(), link_ids=[uuid.uuid4()], mode="manual", subject="Hi"
    )
    with pytest.raises(HTTPException) as exc:
        # Validation happens before any session/job access, so None is fine.
        await outreach_api._resolve(None, None, payload, None)
    assert exc.value.status_code == 422


def test_send_accepts_per_candidate_overrides() -> None:
    link_id = uuid.uuid4()
    payload = OutreachSendIn(
        job_id=uuid.uuid4(),
        link_ids=[link_id],
        mode="ai",
        overrides=[RecipientOverride(link_id=link_id, subject="S", body="B")],
    )
    assert payload.overrides[0].link_id == link_id


# ── Delivery-config transparency + the send_email template contract ──────────


def test_missing_smtp_config_is_surfaced(monkeypatch) -> None:
    monkeypatch.setattr(
        outreach_api,
        "get_settings",
        lambda: type("S", (), {"missing_delivery_keys": lambda self: ["SMTP_HOST"]})(),
    )
    ok, warning = outreach_api._delivery_status()
    assert ok is False and warning and "SMTP" in warning


def test_present_smtp_config_has_no_warning(monkeypatch) -> None:
    monkeypatch.setattr(
        outreach_api,
        "get_settings",
        lambda: type(
            "S", (), {"missing_delivery_keys": lambda self: ["MSG91_API_KEY"]}
        )(),
    )
    ok, warning = outreach_api._delivery_status()
    assert ok is True and warning is None


def test_direct_template_is_a_faithful_pass_through() -> None:
    """The composed subject/body must survive send_email's template render."""
    subject = email_render.substitute(
        outreach_api._DIRECT_SUBJECT, {"subject": "Next steps", "body": "Hi Ada"}
    )
    body = email_render.substitute(
        outreach_api._DIRECT_BODY, {"subject": "Next steps", "body": "Hi Ada"}
    )
    assert subject == "Next steps"
    assert body == "Hi Ada"


@pytest.mark.asyncio
async def test_delivery_status_waits_for_all_tasks(monkeypatch) -> None:
    from types import SimpleNamespace

    results = {
        "pending": SimpleNamespace(
            ready=lambda: False,
            result=None,
            successful=lambda: False,
        ),
        "sent": SimpleNamespace(
            ready=lambda: True,
            result={"status": "sent"},
            successful=lambda: True,
        ),
    }
    monkeypatch.setattr(
        outreach_api.celery_app, "AsyncResult", lambda task_id: results[task_id]
    )
    out = await outreach_api.outreach_delivery_status(
        OutreachDeliveryStatusIn(task_ids=["pending", "sent"]), _user=None
    )
    assert out.pending == 1
    assert out.sent == 1
    assert out.done is False
