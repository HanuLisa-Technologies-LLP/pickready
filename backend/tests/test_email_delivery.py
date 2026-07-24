"""Outbound email/SMS delivery hardening tests (no network).

Covers:
  * sender-path selection: tenant verified vs unverified vs tenant_id=None,
    and the development-environment override;
  * permanent-vs-transient classification of Resend/MSG91 responses;
  * that a permanent failure is NOT retried while a transient one IS;
  * the missing-key startup preflight.

The HTTP layer is mocked throughout — these tests must never hit the network.
"""
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest


@asynccontextmanager
async def _noop_session():
    """Stand-in for tasks._worker_session — no DB, no engine."""
    yield SimpleNamespace()

from app.services import sms_service
from app.services.sms_service import (
    PermanentDeliveryError,
    TransientDeliveryError,
    classify_exception,
    classify_response,
    parse_error_body,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _resp(status: int, json_body=None, text: str = "") -> httpx.Response:
    req = httpx.Request("POST", "https://example.test/send")
    if json_body is not None:
        return httpx.Response(status, json=json_body, request=req)
    return httpx.Response(status, text=text, request=req)


# The exact 403 body the live Resend account returns for a non-owner recipient.
RESEND_403 = {
    "name": "validation_error",
    "message": (
        "You can only send testing emails to your own email address "
        "(manjuchro@gmail.com). To send emails to other recipients, please "
        "verify a domain at resend.com/domains ..."
    ),
}
RESEND_422 = {"name": "validation_error", "message": "Invalid `to` field."}
RESEND_401 = {"name": "restricted_api_key", "message": "This API key is restricted."}


# ── error-body surfacing (the highest-value fix) ─────────────────────────────

def test_parse_error_body_extracts_name_and_message():
    name, message, raw = parse_error_body(_resp(403, RESEND_403))
    assert name == "validation_error"
    assert "verify a domain" in message
    assert raw["name"] == "validation_error"


def test_parse_error_body_falls_back_to_text_when_not_json():
    name, message, raw = parse_error_body(_resp(502, text="upstream boom"))
    assert name == ""
    assert "upstream boom" in message
    assert raw == {}


# ── classification: permanent vs transient ───────────────────────────────────

def test_403_unverified_domain_is_permanent_with_hint():
    err = classify_response("resend", _resp(403, RESEND_403))
    assert isinstance(err, PermanentDeliveryError)
    assert err.permanent is True
    assert err.status == 403
    assert "resend.com/domains" in err.hint
    # audit metadata is secret-free and carries the taxonomy
    meta = err.as_audit_metadata()
    assert meta["permanent"] is True
    assert meta["status"] == 403


def test_422_invalid_recipient_is_permanent():
    err = classify_response("resend", _resp(422, RESEND_422))
    assert isinstance(err, PermanentDeliveryError)
    assert "recipient" in err.hint.lower()


def test_401_restricted_key_is_permanent_credentials():
    err = classify_response("resend", _resp(401, RESEND_401))
    assert isinstance(err, PermanentDeliveryError)
    assert "api-keys" in err.hint or "key" in err.hint.lower()


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_429_and_5xx_are_transient(status):
    err = classify_response("resend", _resp(status, {"message": "later"}))
    assert isinstance(err, TransientDeliveryError)
    assert err.permanent is False


def test_network_error_is_transient():
    exc = httpx.ConnectError("dns fail")
    err = classify_exception("resend", exc)
    assert isinstance(err, TransientDeliveryError)


# ── SMS: MSG91 send + 200-with-error-body handling ───────────────────────────

@pytest.mark.asyncio
async def test_sms_success(monkeypatch):
    monkeypatch.setattr(
        sms_service, "get_settings",
        lambda: SimpleNamespace(msg91_api_key="k", msg91_sender_id="PCKRDY"),
    )

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k):
            return _resp(200, {"type": "success", "message": "ok"})

    monkeypatch.setattr(sms_service.httpx, "AsyncClient", _Client)
    await sms_service.send_sms_async("+919999999999", "code 123456")  # no raise


@pytest.mark.asyncio
async def test_sms_200_with_error_body_is_permanent(monkeypatch):
    monkeypatch.setattr(
        sms_service, "get_settings",
        lambda: SimpleNamespace(msg91_api_key="k", msg91_sender_id="PCKRDY"),
    )

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k):
            return _resp(200, {"type": "error", "message": "bad sender id"})

    monkeypatch.setattr(sms_service.httpx, "AsyncClient", _Client)
    with pytest.raises(PermanentDeliveryError):
        await sms_service.send_sms_async("+919999999999", "x")


@pytest.mark.asyncio
async def test_sms_missing_key_is_permanent(monkeypatch):
    monkeypatch.setattr(
        sms_service, "get_settings",
        lambda: SimpleNamespace(msg91_api_key="", msg91_sender_id="PCKRDY"),
    )
    with pytest.raises(PermanentDeliveryError):
        await sms_service.send_sms_async("+919999999999", "x")


# ── sender-path selection ────────────────────────────────────────────────────
# Exercises the real _send_email_async selection logic with the DB/render/HTTP
# boundaries stubbed, so we assert purely on which From/Reply-To/path is chosen.

class _FakeSession:
    async def get(self, model, ident):
        return self._tenant

    async def execute(self, *a, **k):
        return SimpleNamespace(scalar_one_or_none=lambda: None)

    async def commit(self):
        return None


async def _capture_sender(monkeypatch, *, tenant, environment):
    """Run _send_email_async with everything stubbed; return the captured
    (from_addr, reply_to) passed to the Resend client."""
    from app.workers import tasks

    captured = {}

    async def _fake_resend(from_addr, reply_to, to, subject, body, attachments):
        captured["from"] = from_addr
        captured["reply_to"] = reply_to
        return "msg_123"

    async def _fake_render(session, tid, name, ctx):
        return ("subject", "body")

    async def _fake_audit(session, tenant_id, action, ttype, tid, meta):
        captured["audit"] = meta
        return None

    monkeypatch.setattr(tasks, "_resend_send", _fake_resend)
    monkeypatch.setattr(
        tasks, "get_settings",
        lambda: SimpleNamespace(
            environment=environment, resend_dev_sender="onboarding@resend.dev"
        ),
    )
    monkeypatch.setattr(tasks, "_audit", _fake_audit)

    import app.services.email_render as er
    monkeypatch.setattr(er, "render", _fake_render)

    session = _FakeSession()
    session._tenant = tenant
    tenant_id = str(tenant.id) if tenant is not None else None
    await tasks._send_email_async(
        session, tenant_id, "hr@corp.test", "otp", {"code": "1"}
    )
    return captured


@pytest.mark.asyncio
async def test_sender_tenant_verified_uses_tenant_domain(monkeypatch):
    tenant = SimpleNamespace(
        id=uuid.uuid4(), domain="acme.com", spf_dkim_status="verified"
    )
    captured = await _capture_sender(monkeypatch, tenant=tenant, environment="production")
    assert captured["from"] == "recruitment@acme.com"
    assert captured["reply_to"] == "recruitment@acme.com"
    assert captured["audit"]["sender_path"] == "tenant_domain"


@pytest.mark.asyncio
async def test_sender_tenant_unverified_uses_dev_fallback(monkeypatch):
    tenant = SimpleNamespace(
        id=uuid.uuid4(), domain="acme.com", spf_dkim_status="pending"
    )
    captured = await _capture_sender(monkeypatch, tenant=tenant, environment="production")
    assert captured["from"] == "onboarding@resend.dev"
    assert captured["reply_to"] is None
    assert captured["audit"]["sender_path"] == "dev_fallback"


@pytest.mark.asyncio
async def test_sender_verified_but_development_uses_dev_fallback(monkeypatch):
    tenant = SimpleNamespace(
        id=uuid.uuid4(), domain="acme.com", spf_dkim_status="verified"
    )
    captured = await _capture_sender(monkeypatch, tenant=tenant, environment="development")
    # Even a verified domain must not send from the tenant in development.
    assert captured["from"] == "onboarding@resend.dev"
    assert captured["reply_to"] == "recruitment@acme.com"  # kept as reply-to
    assert captured["audit"]["sender_path"] == "dev_fallback"


@pytest.mark.asyncio
async def test_sender_tenant_none_does_not_crash(monkeypatch):
    # Platform users (Owner OTP) have tenant_id=None — this was a real past bug.
    captured = await _capture_sender(monkeypatch, tenant=None, environment="production")
    assert captured["from"] == "onboarding@resend.dev"
    assert captured["reply_to"] is None
    assert captured["audit"]["sender_path"] == "dev_fallback"


# ── permanent failure is not retried; transient is ──────────────────────────

def test_permanent_failure_not_retried(monkeypatch):
    """send_email must swallow a PermanentDeliveryError (return, no retry)."""
    from app.workers import tasks

    async def _boom(session, *a, **k):
        raise PermanentDeliveryError(
            "resend", 403, "validation_error", "unverified", hint="verify domain"
        )

    monkeypatch.setattr(tasks, "_send_email_async", _boom)
    monkeypatch.setattr(tasks, "_worker_session", _noop_session)
    monkeypatch.setattr(
        tasks, "get_settings", lambda: SimpleNamespace(delivery_max_retries=2)
    )

    # bind=True → `.run` auto-binds `self` to the real task instance; on a
    # non-executing task self.request.retries defaults to 0. Must NOT raise.
    tasks.send_email.run(None, "a@b.test", "otp", {})


def test_transient_failure_reraises(monkeypatch):
    """A transient failure must propagate so Celery's autoretry can back off."""
    from app.workers import tasks

    async def _boom(session, *a, **k):
        raise TransientDeliveryError("resend", 503, "server_error", "later")

    monkeypatch.setattr(tasks, "_send_email_async", _boom)
    monkeypatch.setattr(tasks, "_worker_session", _noop_session)
    monkeypatch.setattr(
        tasks, "get_settings", lambda: SimpleNamespace(delivery_max_retries=2)
    )
    # retries below cap (0 < 2) → re-raises without the exhausted-audit row.
    with pytest.raises(TransientDeliveryError):
        tasks.send_email.run(None, "a@b.test", "otp", {})


# ── startup preflight ────────────────────────────────────────────────────────

def test_preflight_reports_missing_keys(monkeypatch):
    from app.core import config

    config.get_settings.cache_clear()
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.setenv("MSG91_API_KEY", "")
    monkeypatch.setenv("MSG91_SENDER_ID", "")
    config.get_settings.cache_clear()
    missing = config.preflight_delivery_config()
    assert "RESEND_API_KEY" in missing
    assert "MSG91_API_KEY" in missing
    config.get_settings.cache_clear()


def test_preflight_ok_when_keys_present(monkeypatch):
    from app.core import config

    config.get_settings.cache_clear()
    monkeypatch.setenv("RESEND_API_KEY", "re_x")
    monkeypatch.setenv("MSG91_API_KEY", "mk_x")
    monkeypatch.setenv("MSG91_SENDER_ID", "PCKRDY")
    config.get_settings.cache_clear()
    assert config.preflight_delivery_config() == []
    config.get_settings.cache_clear()
