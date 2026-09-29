"""The platform security email: fixed templates, the platform sender, SES only
in production, and a context that is never logged (auth spec 9.3, 10)."""
from __future__ import annotations

import logging

import pytest

from app.core.config import get_settings
from app.services import security_email
from app.workers import dispatch, registry
from app.workers.registry import RLS_BYPASS, Route

EM_DASH = chr(8212)


@pytest.mark.parametrize(
    "template, context",
    [
        ("security_code_registration", {"code": "042917"}),
        ("security_code_password", {"code": "042917", "action": "change"}),
        ("security_code_password", {"code": "042917", "action": "reset"}),
        ("account_exists_notice", {}),
        ("account_exists_notice", {"surface": "candidate"}),
    ],
)
def test_every_template_renders_in_house_style(template, context) -> None:
    subject, body = security_email.render(template, context)
    assert subject and body
    assert EM_DASH not in subject + body
    lowered = (subject + body).lower()
    assert "one-time password" not in lowered and "otp" not in lowered.split()
    if "code" in context:
        assert context["code"] in body
        assert "security code" in body


def test_the_notice_links_to_a_sign_in_page_the_server_chose() -> None:
    _subject, body = security_email.render("account_exists_notice", {})
    assert f"{get_settings().frontend_url.rstrip('/')}/company/login" in body


@pytest.mark.parametrize(
    "template, context",
    [
        ("login_code", {"code": "123456"}),
        ("security_code_registration", {}),
        ("security_code_registration", {"code": "12345"}),
        ("security_code_registration", {"code": "123456", "from": "boss@evil.test"}),
        ("security_code_password", {"code": "123456", "action": "delete"}),
        ("account_exists_notice", {"surface": "https://evil.test"}),
    ],
)
def test_a_malformed_context_is_refused_before_anything_renders(template, context) -> None:
    with pytest.raises(security_email.SecurityEmailRefused) as exc:
        security_email.render(template, context)
    # The refusal names keys, never values: a value may be a code.
    assert "123456" not in str(exc.value)


@pytest.fixture
def settings():
    value = get_settings()
    before = (value.environment, value.email_transport)
    yield value
    object.__setattr__(value, "environment", before[0])
    object.__setattr__(value, "email_transport", before[1])


@pytest.mark.parametrize("environment", ["production", "pilot"])
async def test_production_refuses_a_non_ses_transport(settings, environment, monkeypatch) -> None:
    object.__setattr__(settings, "environment", environment)
    object.__setattr__(settings, "email_transport", "smtp")
    sent: list = []

    async def _smtp(**kwargs):
        sent.append(kwargs)

    monkeypatch.setattr("app.services.smtp_service.send_email_async", _smtp)
    with pytest.raises(security_email.SecurityEmailRefused):
        await security_email.deliver(to="a@b.test", subject="s", html="<p>h</p>", text="t")
    assert sent == [], "no SMTP fallback in production"


async def test_ses_sends_from_the_platform_sender_only(settings, monkeypatch) -> None:
    object.__setattr__(settings, "environment", "production")
    object.__setattr__(settings, "email_transport", "ses")
    captured: list[dict] = []

    async def _ses(**kwargs):
        captured.append(kwargs)
        return "mid-1"

    monkeypatch.setattr("app.services.ses_service.send_email_async", _ses)
    assert await security_email.deliver(
        to="person@x.test", subject="s", html="<p>h</p>", text="t"
    ) == "mid-1"
    assert captured[0]["from_email"] == settings.platform_security_sender_email
    assert captured[0]["from_email"] == "contact@readypick.ai"
    assert captured[0]["from_name"] == settings.platform_security_sender_name
    assert "reply_to" not in captured[0]


def test_the_task_is_registered_as_a_short_platform_task() -> None:
    import app.workers.tasks  # noqa: F401  registration side effect

    spec = registry.resolve("pickready.send_security_email")
    assert spec.route == Route.LAMBDA
    assert spec.rls == RLS_BYPASS and spec.rls_reason
    # The signature has no sender field: a From address cannot be passed in.
    import inspect

    assert list(inspect.signature(spec.fn).parameters) == ["to", "template", "context"]


def test_dispatch_validates_first_then_dispatches() -> None:
    with pytest.raises(security_email.SecurityEmailRefused):
        security_email.dispatch_security_email("a@b.test", "security_code_password", {"code": "1"})
    assert "pickready.send_security_email" not in dispatch.recorded_names()
    security_email.dispatch_security_email(
        "a@b.test", "security_code_password", {"code": "123456", "action": "reset"}
    )
    [record] = [r for r in dispatch.recorded() if r.name == "pickready.send_security_email"]
    assert record.args == ("a@b.test", "security_code_password", {"code": "123456", "action": "reset"})


def test_the_task_never_logs_the_context(settings, monkeypatch, caplog) -> None:
    """Run the task body with the transport and the audit write doubled; the
    code appears in no log line, and the audit metadata carries none of the
    context."""
    import app.workers.tasks  # noqa: F401
    from app.workers import tasks_security_email

    object.__setattr__(settings, "environment", "development")
    delivered: list[dict] = []
    audited: list[tuple] = []

    async def _deliver(**kwargs):
        delivered.append(kwargs)
        return "mid-2"

    async def _record(*args):
        audited.append(args)

    monkeypatch.setattr(security_email, "deliver", _deliver)
    monkeypatch.setattr(tasks_security_email, "_record_delivery", _record)
    caplog.set_level(logging.DEBUG)
    result = tasks_security_email.send_security_email(
        "person@x.test", "security_code_password", {"code": "918273", "action": "change"}
    )
    assert result == {"status": "sent"}
    assert "918273" in delivered[0]["text"]
    assert "918273" not in caplog.text
    assert "918273" not in repr(audited)
    assert audited[0][:3] == ("person@x.test", "security_code_password", "sent")
