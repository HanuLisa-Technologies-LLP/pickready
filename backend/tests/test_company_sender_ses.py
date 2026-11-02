"""Mail leaves from the company's own sender, through SES only (register I-20).

Four things are pinned:

* the single From resolver (`services/email_sender_resolution`): a company's
  ACTIVE sender, else the platform address under the company name as a
  RECORDED fallback, `noreply@readypick.ai` for security codes and
  `contact@readypick.ai` for other platform mail;
* the same answers end to end on a real database, through the real worker
  functions with only the SES client replaced by a fake (`email_log.from_*`
  and the delivery audit row carry the fallback, never a silent one);
* the boot check that a platform From is on `readypick.ai`;
* the removal sweep: no SMTP path, no `email_transport`, no `@pickready.app`.
"""
from __future__ import annotations

import logging
import re
import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.models.email_sender import SENDER_ACTIVE, SENDER_DISABLED
from app.services import email_sender_resolution as resolution
from app.services import email_outbox, ses_service
from app.workers import tasks
from tests import comms_world
from tests.removal_sweep import BACKEND, REPO, sweep

TENANT = SimpleNamespace(id=uuid.uuid4(), name="Acme Corp")


def _sender(status: str = SENDER_ACTIVE, tenant_id: uuid.UUID = TENANT.id):
    return SimpleNamespace(
        id=uuid.uuid4(), tenant_id=tenant_id, email="hr@acme.test", name="Rahul",
        status=status,
    )


# ── The resolver, no database ────────────────────────────────────────────────


def test_security_codes_come_from_noreply() -> None:
    identity = resolution.security_identity()
    assert identity.email == "noreply@readypick.ai"
    assert identity.name == "Vivekium"
    assert identity.source == resolution.SOURCE_SECURITY


def test_platform_mail_comes_from_contact() -> None:
    identity = resolution.platform_identity()
    assert identity.email == "contact@readypick.ai"
    assert identity.source == resolution.SOURCE_PLATFORM


def test_an_active_company_sender_is_the_from_under_the_company_name() -> None:
    sender = _sender()
    identity = resolution.tenant_identity(TENANT, sender)
    assert identity.email == "hr@acme.test"
    assert identity.name == "Acme Corp"
    assert identity.source == resolution.SOURCE_COMPANY_SENDER
    assert identity.sender_id == sender.id


def test_no_sender_falls_back_to_contact_and_the_fallback_is_logged(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=resolution.logger.name):
        identity = resolution.tenant_identity(TENANT, None)
    assert identity.email == "contact@readypick.ai"
    assert identity.name == "Acme Corp via Vivekium"
    assert identity.source == resolution.SOURCE_PLATFORM_FALLBACK
    assert "email.sender_fallback" in caplog.text
    assert str(TENANT.id) in caplog.text


@pytest.mark.parametrize(
    "sender",
    [_sender(SENDER_DISABLED), _sender(tenant_id=uuid.uuid4())],
    ids=["not active", "another tenant's"],
)
def test_a_sender_that_cannot_send_is_refused_not_used(sender) -> None:
    with pytest.raises(ValueError):
        resolution.tenant_identity(TENANT, sender)


async def test_platform_mail_ignores_a_tenant_default_sender(monkeypatch) -> None:
    async def _boom(session, tenant_id):
        raise AssertionError("platform mail must not look for a company sender")

    monkeypatch.setattr(resolution, "default_active_sender", _boom)
    for tenant, template in ((None, "staff_invite"), (TENANT, "client_invite"),
                             (TENANT, "credit_invoice")):
        identity = await resolution.resolve_identity(
            None, tenant=tenant, template_name=template
        )
        assert identity.source == resolution.SOURCE_PLATFORM
        assert identity.email == "contact@readypick.ai"


async def test_tenant_mail_looks_up_the_default_sender(monkeypatch) -> None:
    sender = _sender()

    async def _default(session, tenant_id):
        assert tenant_id == TENANT.id
        return sender

    monkeypatch.setattr(resolution, "default_active_sender", _default)
    identity = await resolution.resolve_identity(
        None, tenant=TENANT, template_name="staff_invite"
    )
    assert identity.email == "hr@acme.test"


# ── The boot check ───────────────────────────────────────────────────────────


def test_the_default_platform_senders_are_on_the_ses_domain() -> None:
    settings = Settings(_env_file=None)
    assert settings.platform_sender_email == "contact@readypick.ai"
    assert settings.platform_security_sender_email == "noreply@readypick.ai"


@pytest.mark.parametrize(
    "field, value",
    [
        ("platform_sender_email", "noreply@" + "pickready.app"),
        ("platform_sender_email", "someone@gmail.com"),
        ("platform_sender_email", "contact@mail.readypick.ai"),
        ("platform_sender_email", "readypick.ai"),
        ("platform_security_sender_email", "contact@example.test"),
        ("platform_security_sender_email", "@readypick.ai"),
    ],
)
def test_a_platform_from_off_the_ses_domain_refuses_to_boot(field, value) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


# ── SES is the one transport: a fake client sees the whole message ───────────


class _FakeSes:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    def send_raw_email(self, **kwargs):
        self.sent.append(kwargs)
        return {"MessageId": "fake-ses-id"}


async def test_the_ses_call_carries_the_resolved_from_and_the_reply_to(monkeypatch) -> None:
    fake = _FakeSes()
    monkeypatch.setattr(ses_service, "_ses_client", lambda: fake)
    identity = resolution.tenant_identity(TENANT, _sender())
    message_id = await tasks._deliver_email(
        to="cand@example.test", subject="Hello", html="<p>x</p>", text="x",
        identity=identity, reply_to="conversations+tok@reply.test",
    )
    assert message_id == "fake-ses-id"
    [call] = fake.sent
    assert call["Source"] == "hr@acme.test"
    raw = call["RawMessage"]["Data"].decode()
    assert "From: Acme Corp <hr@acme.test>" in raw
    assert "Reply-To: conversations+tok@reply.test" in raw


# ── End to end on a real database ────────────────────────────────────────────


@pytest.fixture
async def world():
    if not await comms_world.reachable():
        pytest.skip("no database reachable: the sender resolution tests did not run")
    sessions = comms_world.factory()
    w = await comms_world.seed(sessions)
    try:
        yield w
    finally:
        await comms_world.cleanup(sessions, w)


@pytest.fixture
def fake_ses(monkeypatch):
    sent: list[dict] = []

    async def _send(**kwargs):
        sent.append(kwargs)
        return "ses-id-1"

    monkeypatch.setattr(ses_service, "send_email_async", _send)
    return sent


async def _tenant_name(world) -> str:
    return (
        await comms_world.rows(
            comms_world.factory(), "SELECT name FROM tenants WHERE id = :id",
            {"id": str(world.tenant)},
        )
    )[0]["name"]


async def _queue_and_send(world) -> dict:
    sessions = comms_world.factory()
    async with sessions() as session:
        async with session.begin():
            await comms_world.bypass(session)
            row = await email_outbox.queue_candidate_email(
                session, tenant_id=world.tenant, email_type="shortlist",
                recipient_email=world.candidate_email, candidate_id=world.candidate,
                job_id=world.job, link_id=world.link, subject="S", body="B",
                generated_by_ai=False, edited_by_human=True, sent_by=None,
                thread=False,
            )
            row_id = row.id
    async with sessions() as session:
        await comms_world.bypass(session)
        assert (await tasks._send_lifecycle_email_async(session, str(row_id)))[
            "status"
        ] == "sent"
    return (
        await comms_world.rows(
            comms_world.factory(),
            "SELECT status, transport, from_email, from_source "
            "FROM email_log WHERE id = :id",
            {"id": str(row_id)},
        )
    )[0]


async def test_lifecycle_mail_leaves_from_the_default_company_sender(
    world, fake_ses
) -> None:
    sender_id = await comms_world.add_sender(
        comms_world.factory(), world, is_default=True
    )
    email = (
        await comms_world.rows(
            comms_world.factory(),
            "SELECT email FROM client_email_senders WHERE id = :id",
            {"id": str(sender_id)},
        )
    )[0]["email"]
    row = await _queue_and_send(world)
    [call] = fake_ses
    assert call["from_email"] == email
    assert call["from_name"] == await _tenant_name(world)
    assert (row["transport"], row["from_email"], row["from_source"]) == (
        "ses", email, "company_sender",
    )


async def test_lifecycle_mail_with_no_sender_records_the_fallback(
    world, fake_ses, caplog
) -> None:
    with caplog.at_level(logging.WARNING):
        row = await _queue_and_send(world)
    [call] = fake_ses
    assert call["from_email"] == "contact@readypick.ai"
    assert call["from_name"] == f"{await _tenant_name(world)} via Vivekium"
    assert (row["transport"], row["from_email"], row["from_source"]) == (
        "ses", "contact@readypick.ai", "platform_fallback",
    )
    assert "email.sender_fallback" in caplog.text


async def _send_template(world, template: str):
    sessions = comms_world.factory()
    async with sessions() as session:
        await comms_world.bypass(session)
        await tasks._send_email_async(
            session, str(world.tenant), "someone@example.test", template,
            {"full_name": "Asha"},
        )
    return (
        await comms_world.rows(
            comms_world.factory(),
            "SELECT metadata_json FROM audit_log WHERE tenant_id = :t "
            "AND action = 'email.delivery' ORDER BY at DESC LIMIT 1",
            {"t": str(world.tenant)},
        )
    )[0]["metadata_json"]


@pytest.fixture
def stub_render(monkeypatch):
    from app.services import email_render

    async def _render(session, tenant_id, name, context):
        return ("Subject", "Body")

    monkeypatch.setattr(email_render, "render", _render)


async def test_tenant_templates_use_the_company_sender_and_platform_ones_do_not(
    world, fake_ses, stub_render
) -> None:
    sender_id = await comms_world.add_sender(
        comms_world.factory(), world, is_default=True
    )
    email = (
        await comms_world.rows(
            comms_world.factory(),
            "SELECT email FROM client_email_senders WHERE id = :id",
            {"id": str(sender_id)},
        )
    )[0]["email"]

    audit = await _send_template(world, "staff_invite")
    assert fake_ses[-1]["from_email"] == email
    assert audit["from_source"] == "company_sender" and audit["from_email"] == email

    audit = await _send_template(world, "client_invite")
    assert fake_ses[-1]["from_email"] == "contact@readypick.ai"
    assert audit["from_source"] == "platform"


async def test_tenant_templates_without_a_sender_fall_back_and_the_audit_says_so(
    world, fake_ses, stub_render
) -> None:
    audit = await _send_template(world, "staff_invite")
    assert fake_ses[-1]["from_email"] == "contact@readypick.ai"
    assert audit["from_source"] == "platform_fallback"


# ── Removal sweep ────────────────────────────────────────────────────────────

GONE = re.compile(
    r"smtp_service|aiosmtplib|email_transport|EMAIL_TRANSPORT"
    r"|SMTP_(?:HOST|PORT|USER|PASSWORD|FROM_EMAIL|FROM_NAME|STARTTLS|SSL)"
    r"|smtp_(?:host|port|user|password|from_email|from_name|starttls|ssl|send|configured)\b"
    r"|preflight_delivery_config|missing_delivery_keys|validate_gmail_outreach"
    r"|platform_from_email|@pickready\.app"
)

#: The SMTP_PASSWORD secret container is kept on purpose: deleting a Secrets
#: Manager secret has no undo and is the owner's call. The hygiene test pins it
#: as present and granted to no service, and the placeholder test as unread.
EXEMPT = (
    BACKEND / "tests" / "test_company_sender_ses.py",
    BACKEND / "tests" / "test_deploy_secret_hygiene.py",
    BACKEND / "tests" / "test_placeholder_secret.py",
    REPO / "infra" / "modules" / "secrets",
)


def test_the_sweep_pattern_matches_samples() -> None:
    for sample in (
        "from app.services.smtp_service import send_email_async",
        "email_transport = 'smtp'",
        "SMTP_FROM_EMAIL=x",
        "settings.smtp_user",
        "noreply@" + "pickready.app",
        "preflight_delivery_config()",
    ):
        assert GONE.search(sample), sample
    assert not GONE.search("platform_sender_email = 'contact@readypick.ai'")


def test_no_code_path_names_the_smtp_transport_or_the_obsolete_domain() -> None:
    hits = sweep(GONE, exempt=EXEMPT)
    assert not hits, hits


def test_the_smtp_modules_and_settings_are_gone() -> None:
    assert not (BACKEND / "app" / "services" / "smtp_service.py").exists()
    assert not (BACKEND / "app" / "scripts" / "validate_gmail_outreach.py").exists()
    fields = set(Settings.model_fields)
    assert not {name for name in fields if name.startswith("smtp_")}
    assert "email_transport" not in fields
    assert "aiosmtplib" not in (BACKEND / "requirements.txt").read_text(encoding="utf-8")
