"""Outbound email/SMS delivery hardening tests (no network).

Covers:
  * sender-path selection: tenant verified vs unverified vs tenant_id=None,
    and the development-environment override;
  * permanent-vs-transient classification of provider/MSG91 responses;
  * the SMTP send path: success returns a message id, an auth failure is
    permanent (not retried), a connection/timeout failure is transient
    (retried), and missing SMTP creds fail fast as permanent;
  * that a permanent failure is NOT retried while a transient one IS;
  * the missing-key startup preflight (SMTP_HOST/USER/PASSWORD).

The network layer (aiosmtplib / httpx) is mocked throughout — these tests must
never hit the network.
"""
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError


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
    """Run _send_email_async with dependencies stubbed and capture SMTP fields."""
    from app.workers import tasks

    captured = {}

    async def _fake_smtp(
        from_email, from_name, to, subject, html, text=None, attachments=None
    ):
        captured["from"] = from_email
        captured["from_name"] = from_name
        captured["html"] = html
        captured["text"] = text
        return "msg_123"

    async def _fake_render(session, tid, name, ctx):
        return ("subject", "body")

    async def _fake_audit(session, tenant_id, action, ttype, tid, meta):
        captured["audit"] = meta
        return None

    monkeypatch.setattr(tasks, "smtp_send", _fake_smtp)
    monkeypatch.setattr(
        tasks, "get_settings",
        lambda: SimpleNamespace(
            environment=environment,
            smtp_from_email="sender@gmail.com",
            smtp_from_name="PickReady",
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
async def test_sender_tenant_verified_still_uses_gmail(monkeypatch):
    tenant = SimpleNamespace(
        id=uuid.uuid4(), domain="acme.com", spf_dkim_status="verified"
    )
    captured = await _capture_sender(monkeypatch, tenant=tenant, environment="production")
    assert captured["from"] == "sender@gmail.com"
    assert captured["from_name"] == "PickReady"
    assert captured["audit"]["sender_path"] == "gmail"


@pytest.mark.asyncio
async def test_sender_tenant_unverified_uses_gmail(monkeypatch):
    tenant = SimpleNamespace(
        id=uuid.uuid4(), domain="acme.com", spf_dkim_status="pending"
    )
    captured = await _capture_sender(monkeypatch, tenant=tenant, environment="production")
    assert captured["from"] == "sender@gmail.com"
    assert captured["audit"]["sender_path"] == "gmail"


@pytest.mark.asyncio
async def test_sender_verified_development_uses_gmail(monkeypatch):
    tenant = SimpleNamespace(
        id=uuid.uuid4(), domain="acme.com", spf_dkim_status="verified"
    )
    captured = await _capture_sender(monkeypatch, tenant=tenant, environment="development")
    # Even a verified domain must not send from the tenant in development.
    assert captured["from"] == "sender@gmail.com"
    assert captured["audit"]["sender_path"] == "gmail"


@pytest.mark.asyncio
async def test_sender_tenant_none_does_not_crash(monkeypatch):
    # Platform users (Owner OTP) have tenant_id=None — this was a real past bug.
    captured = await _capture_sender(monkeypatch, tenant=None, environment="production")
    assert captured["from"] == "sender@gmail.com"
    assert captured["audit"]["sender_path"] == "gmail"
    # html body is derived from the plain-text body and sent alongside text.
    assert captured["text"] == "body"
    assert "body" in captured["html"]


# ── SMTP send path (mocked aiosmtplib, no network) ───────────────────────────

def _smtp_settings(**overrides):
    base = dict(
        smtp_host="smtp.example.test",
        smtp_port=587,
        smtp_user="apikey",
        smtp_password="secret",
        smtp_from_email="noreply@pickready.app",
        smtp_from_name="PickReady",
        smtp_starttls=True,
        smtp_ssl=False,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_settings_reject_non_gmail_smtp_provider():
    from app.core.config import Settings

    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            smtp_host="smtp.example.test",
            smtp_port=587,
            smtp_user="sender@example.test",
            smtp_password="secret",
            smtp_from_email="sender@example.test",
            smtp_starttls=True,
            smtp_ssl=False,
        )


def test_settings_accept_gmail_starttls_contract():
    from app.core.config import Settings

    settings = Settings(
        _env_file=None,
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_user="sender@gmail.com",
        smtp_password="secret",
        smtp_from_email="sender@gmail.com",
        smtp_starttls=True,
        smtp_ssl=False,
    )
    assert settings.smtp_host == "smtp.gmail.com"


@pytest.mark.asyncio
async def test_smtp_success_returns_message_id(monkeypatch):
    from app.services import smtp_service

    monkeypatch.setattr(smtp_service, "get_settings", _smtp_settings)

    async def _fake_send(message, **kwargs):
        _fake_send.kwargs = kwargs
        return ({}, "250 OK")

    monkeypatch.setattr(smtp_service.aiosmtplib, "send", _fake_send)
    mid = await smtp_service.send_email_async(
        "noreply@pickready.app", "PickReady", "hr@corp.test", "subj", "<p>hi</p>", "hi"
    )
    # A Message-ID is generated for every sent message.
    assert mid and mid.startswith("<") and mid.endswith(">")
    # STARTTLS on 587, implicit SSL off.
    assert _fake_send.kwargs["start_tls"] is True
    assert _fake_send.kwargs["use_tls"] is False
    assert _fake_send.kwargs["hostname"] == "smtp.example.test"
    assert _fake_send.kwargs["port"] == 587


@pytest.mark.asyncio
async def test_smtp_ssl_465_disables_starttls(monkeypatch):
    from app.services import smtp_service

    monkeypatch.setattr(
        smtp_service, "get_settings",
        lambda: _smtp_settings(smtp_port=465, smtp_ssl=True, smtp_starttls=True),
    )

    async def _fake_send(message, **kwargs):
        _fake_send.kwargs = kwargs
        return ({}, "250 OK")

    monkeypatch.setattr(smtp_service.aiosmtplib, "send", _fake_send)
    await smtp_service.send_email_async(
        "noreply@pickready.app", "PickReady", "hr@corp.test", "s", "<p>h</p>"
    )
    # Implicit TLS from the start; STARTTLS must NOT be attempted on top of it.
    assert _fake_send.kwargs["use_tls"] is True
    assert _fake_send.kwargs["start_tls"] is False


@pytest.mark.asyncio
async def test_smtp_attachment_is_included(monkeypatch):
    import base64

    from app.services import smtp_service

    monkeypatch.setattr(smtp_service, "get_settings", _smtp_settings)

    async def _fake_send(message, **kwargs):
        _fake_send.message = message
        return ({}, "250 OK")

    monkeypatch.setattr(smtp_service.aiosmtplib, "send", _fake_send)
    ics = base64.b64encode(b"BEGIN:VCALENDAR").decode()
    await smtp_service.send_email_async(
        "noreply@pickready.app", "PickReady", "hr@corp.test", "s", "<p>h</p>", "h",
        attachments=[{"filename": "invite.ics", "content": ics}],
    )
    # A multipart/mixed envelope carrying the attachment part.
    assert _fake_send.message.is_multipart()
    payload = _fake_send.message.as_string()
    assert "invite.ics" in payload


@pytest.mark.asyncio
async def test_smtp_auth_error_is_permanent(monkeypatch):
    import aiosmtplib

    from app.services import smtp_service

    monkeypatch.setattr(smtp_service, "get_settings", _smtp_settings)

    async def _fake_send(message, **kwargs):
        raise aiosmtplib.SMTPAuthenticationError(535, "Authentication failed")

    monkeypatch.setattr(smtp_service.aiosmtplib, "send", _fake_send)
    with pytest.raises(PermanentDeliveryError) as ei:
        await smtp_service.send_email_async(
            "noreply@pickready.app", "PickReady", "hr@corp.test", "s", "<p>h</p>"
        )
    assert "SMTP_USER" in ei.value.hint and "SMTP_PASSWORD" in ei.value.hint


@pytest.mark.asyncio
async def test_smtp_5xx_recipient_reject_is_permanent(monkeypatch):
    import aiosmtplib

    from app.services import smtp_service

    monkeypatch.setattr(smtp_service, "get_settings", _smtp_settings)

    async def _fake_send(message, **kwargs):
        raise aiosmtplib.SMTPResponseException(550, "Sender address rejected")

    monkeypatch.setattr(smtp_service.aiosmtplib, "send", _fake_send)
    with pytest.raises(PermanentDeliveryError):
        await smtp_service.send_email_async(
            "noreply@pickready.app", "PickReady", "hr@corp.test", "s", "<p>h</p>"
        )


@pytest.mark.asyncio
async def test_smtp_4xx_greylist_is_transient(monkeypatch):
    import aiosmtplib

    from app.services import smtp_service

    monkeypatch.setattr(smtp_service, "get_settings", _smtp_settings)

    async def _fake_send(message, **kwargs):
        raise aiosmtplib.SMTPResponseException(451, "Greylisted, try again later")

    monkeypatch.setattr(smtp_service.aiosmtplib, "send", _fake_send)
    with pytest.raises(TransientDeliveryError):
        await smtp_service.send_email_async(
            "noreply@pickready.app", "PickReady", "hr@corp.test", "s", "<p>h</p>"
        )


@pytest.mark.asyncio
async def test_smtp_connect_error_is_transient(monkeypatch):
    import aiosmtplib

    from app.services import smtp_service

    monkeypatch.setattr(smtp_service, "get_settings", _smtp_settings)

    async def _fake_send(message, **kwargs):
        raise aiosmtplib.SMTPConnectError("cannot connect to host")

    monkeypatch.setattr(smtp_service.aiosmtplib, "send", _fake_send)
    with pytest.raises(TransientDeliveryError):
        await smtp_service.send_email_async(
            "noreply@pickready.app", "PickReady", "hr@corp.test", "s", "<p>h</p>"
        )


@pytest.mark.asyncio
async def test_smtp_timeout_is_transient(monkeypatch):
    import aiosmtplib

    from app.services import smtp_service

    monkeypatch.setattr(smtp_service, "get_settings", _smtp_settings)

    async def _fake_send(message, **kwargs):
        raise aiosmtplib.SMTPTimeoutError("timed out")

    monkeypatch.setattr(smtp_service.aiosmtplib, "send", _fake_send)
    with pytest.raises(TransientDeliveryError):
        await smtp_service.send_email_async(
            "noreply@pickready.app", "PickReady", "hr@corp.test", "s", "<p>h</p>"
        )


@pytest.mark.asyncio
async def test_smtp_missing_creds_is_permanent(monkeypatch):
    from app.services import smtp_service

    monkeypatch.setattr(
        smtp_service, "get_settings",
        lambda: _smtp_settings(smtp_host="", smtp_user="", smtp_password=""),
    )
    with pytest.raises(PermanentDeliveryError) as ei:
        await smtp_service.send_email_async(
            "noreply@pickready.app", "PickReady", "hr@corp.test", "s", "<p>h</p>"
        )
    assert "SMTP_HOST" in ei.value.hint or "SMTP" in ei.value.hint


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
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("SMTP_USER", "")
    monkeypatch.setenv("SMTP_PASSWORD", "")
    monkeypatch.setenv("MSG91_API_KEY", "")
    monkeypatch.setenv("MSG91_SENDER_ID", "")
    config.get_settings.cache_clear()
    missing = config.preflight_delivery_config()
    assert "SMTP_HOST" in missing
    assert "SMTP_USER" in missing
    assert "SMTP_PASSWORD" in missing
    assert "MSG91_API_KEY" in missing
    config.get_settings.cache_clear()


def test_preflight_ok_when_keys_present(monkeypatch):
    from app.core import config

    config.get_settings.cache_clear()
    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_USER", "sender@gmail.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "sender@gmail.com")
    monkeypatch.setenv("MSG91_API_KEY", "mk_x")
    monkeypatch.setenv("MSG91_SENDER_ID", "PCKRDY")
    config.get_settings.cache_clear()
    assert config.preflight_delivery_config() == []
    config.get_settings.cache_clear()


def test_every_template_name_the_app_sends_has_a_default() -> None:
    """Every literal template name passed to `pickready.send_email` anywhere in
    backend/app MUST resolve without a tenant row.

    This is the invariant that was broken in production. `email_render.render`
    raises ValueError when a name matches neither a tenant row nor a default,
    and it raises inside the Celery task — long after the API answered 200. Two
    names (`candidate_outreach`, `client_invite`) were being sent with no
    default and nothing seeding a row, so those invitations were discarded with
    no email_log row and no audit_log row. Nothing in the product could show
    the user that their invitation had evaporated.

    Scanning the source rather than listing the names by hand is deliberate: a
    hand-maintained list is exactly what drifted in the first place.
    """
    import ast
    from pathlib import Path

    from app.services.email_render import DEFAULT_TEMPLATES

    app_root = Path(__file__).resolve().parents[1] / "app"
    # Parsed, not regexed: the call is
    # `send_task("pickready.send_email", args=[<tenant>, <to>, "<name>", {...}])`
    # and the template name is args[2]. A regex over this shape kept picking up
    # keys out of the context dict that follows it.
    found: set[str] = set()
    for path in app_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            first = node.args[0]
            if not (
                isinstance(first, ast.Constant)
                and first.value == "pickready.send_email"
            ):
                continue
            task_args = next(
                (kw.value for kw in node.keywords if kw.arg == "args"), None
            )
            if isinstance(task_args, ast.List) and len(task_args.elts) >= 3:
                name = task_args.elts[2]
                if isinstance(name, ast.Constant) and isinstance(name.value, str):
                    found.add(name.value)

    assert found, "scanner matched nothing — the send_task shape must have changed"
    missing = sorted(name for name in found if name not in DEFAULT_TEMPLATES)
    assert not missing, (
        f"template name(s) {missing} are sent by the app but have no entry in "
        "DEFAULT_TEMPLATES; every one of those emails would be silently lost"
    )


# ── Queue isolation for delivery (regression, 2026-08-01) ────────────────────
#
# Production incident: every task shared one `celery` queue against a
# `--concurrency=2` worker. Two `generate_technical_questions` runs wedged both
# slots in an unterminating loop, and a staff invitation enqueued at 14:07 UTC
# was not delivered until 14:13, when a slot briefly freed at the soft-time-limit
# boundary. The API had already answered 201 with `email_dispatch: "queued"`, so
# nothing surfaced the delay. Delivery now has its own queue.

def _celery_app():
    # Importing tasks is what registers them; the routing assertions below are
    # meaningless against an empty registry.
    import app.workers.tasks  # noqa: F401
    from app.workers.celery_app import celery_app

    return celery_app


def test_mail_queue_is_declared_alongside_the_default() -> None:
    """A worker started without -Q consumes exactly the declared queues.

    If `mail` were routed but never declared, the existing single worker pool
    would stop consuming it and every email would queue forever.
    """
    names = {q.name for q in _celery_app().amqp.queues.values()}
    assert {"celery", "mail"} <= names


def test_declared_queue_routing_keys_match_their_names() -> None:
    """The Redis transport picks the destination list by ROUTING KEY.

    Left unset, Celery fills a queue's routing key from
    task_default_routing_key, which is the DEFAULT queue's name, so `mail`
    would be declared with routing key "celery". Mail would then land back in
    the `celery` list and a worker started with `--queues=mail` would sit idle
    while invitations piled up behind the AI work the split exists to escape.
    """
    for queue in _celery_app().amqp.queues.values():
        assert queue.routing_key == queue.name, (
            f"queue {queue.name!r} has routing key {queue.routing_key!r}; on "
            "Redis its messages would be delivered to the wrong list"
        )


def test_every_outbound_delivery_task_is_routed_to_the_mail_queue() -> None:
    """Delivery must never sit behind an LLM chain that legitimately takes
    minutes. Scanned from the task registry rather than a hand-kept list."""
    celery = _celery_app()
    delivery = {
        name
        for name in celery.tasks
        if name.startswith("pickready.")
        and ("send_" in name or name.endswith("_email"))
    }
    assert delivery, "task registry scan matched nothing"
    misrouted = sorted(
        name
        for name in delivery
        if celery.amqp.router.route({}, name)["queue"].name != "mail"
    )
    assert not misrouted, (
        f"delivery task(s) {misrouted} still route to the slow queue; an "
        "invitation there can be starved by AI work"
    )


def test_routed_task_names_all_exist() -> None:
    """A typo in task_routes fails open: the task keeps using the slow queue
    and nothing reports it."""
    celery = _celery_app()
    unknown = sorted(
        n for n in celery.conf.task_routes if n not in celery.tasks
    )
    assert not unknown, f"task_routes names no such task: {unknown}"


def test_a_timed_out_task_is_not_auto_retried() -> None:
    """SoftTimeLimitExceeded derives from Exception, so `autoretry_for=
    (Exception,)` used to hand a hung task straight back to the pool: the
    incident log shows `retry: Retry in 1s: SoftTimeLimitExceeded()` and the
    slot re-occupied in the same second. A task that could not finish in ten
    minutes will not finish in ten more, and the retry costs the pool slot
    delivery needs."""
    from celery.exceptions import SoftTimeLimitExceeded

    celery = _celery_app()
    offenders = []
    for name, task in celery.tasks.items():
        if not name.startswith("pickready."):
            continue
        if Exception not in (getattr(task, "autoretry_for", None) or ()):
            continue
        if SoftTimeLimitExceeded not in (
            getattr(task, "dont_autoretry_for", None) or ()
        ):
            offenders.append(name)
    assert not offenders, (
        f"task(s) {sorted(offenders)} auto-retry on a soft-timeout and can "
        "wedge a worker slot indefinitely"
    )
