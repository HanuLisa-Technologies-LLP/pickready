"""Outbound email over Gmail SMTP.

This module owns ONLY the SMTP transport + SMTP-specific failure
classification. The resilience taxonomy itself is shared with the SMS path and
is imported from ``app.services.sms_service`` — we do not re-implement
PermanentDeliveryError / TransientDeliveryError here, we reuse them so the
Celery task's ``autoretry_for`` and audit logic behave identically for email
and SMS.

Configured entirely by ``SMTP_*`` environment variables and permanently
validated as Gmail on port 587 with STARTTLS and an app password.

Failure taxonomy (same permanent/transient split as SMS):
  * PermanentDeliveryError — the same message will fail forever: SMTP auth
    rejected (bad SMTP_USER/SMTP_PASSWORD), or the server rejects the sender /
    every recipient with a 5xx. Retrying is pure waste; fail fast with an
    ACTION hint.
  * TransientDeliveryError — may succeed later: connect/DNS/timeout errors and
    4xx greylisting / temporary mailbox-busy responses. Celery retries with
    exponential backoff.

SECURITY (ESD §16): the SMTP password, OTP codes and message bodies are NEVER
logged. The SMTP server's response text (which contains only provider-side
status, e.g. "Sender address rejected") IS logged so a delivery failure is
diagnosable without re-probing the relay.
"""
from __future__ import annotations

import logging
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid

import aiosmtplib

from app.core.config import get_settings
from app.services.sms_service import (
    PermanentDeliveryError,
    TransientDeliveryError,
)

logger = logging.getLogger(__name__)

#: Operator-actionable hints, keyed by the condition we can positively identify.
_SMTP_HINTS: dict[str, str] = {
    "auth": (
        "SMTP authentication was rejected. ACTION: check SMTP_USER / "
        "SMTP_PASSWORD (for Gmail use an app password, not the account "
        "password) and that the From address is a verified sender, then "
        "restart the worker."
    ),
    "sender_recipient": (
        "The SMTP server permanently rejected the sender or every recipient "
        "(5xx). ACTION: check SMTP_USER / SMTP_PASSWORD / verified sender and "
        "the recipient address, retrying cannot help."
    ),
    "config_missing": (
        "SMTP is not configured. ACTION: set SMTP_HOST, SMTP_USER and "
        "SMTP_PASSWORD in the environment and restart the worker."
    ),
}


def _build_message(
    from_email: str,
    from_name: str,
    to: str,
    subject: str,
    html: str,
    text: str | None,
    attachments: list[dict] | None,
) -> tuple[MIMEMultipart | MIMEText, str]:
    """Assemble a MIME message. Returns (message, message_id).

    Body is multipart/alternative (text + html) when a plain-text part is
    supplied, else a single html part. When attachments are present the whole
    thing is wrapped in a multipart/mixed envelope.
    """
    message_id = make_msgid()

    if text:
        body: MIMEMultipart | MIMEText = MIMEMultipart("alternative")
        body.attach(MIMEText(text, "plain", "utf-8"))
        body.attach(MIMEText(html, "html", "utf-8"))
    else:
        body = MIMEText(html, "html", "utf-8")

    if attachments:
        root: MIMEMultipart | MIMEText = MIMEMultipart("mixed")
        root.attach(body)
        for att in attachments:
            # Shape matches the existing caller contract:
            # [{"filename": str, "content": <base64 str>}] (e.g. the .ics on an
            # interview invite). aiosmtplib base64-encodes the raw bytes, so we
            # hand MIMEApplication the decoded bytes.
            import base64

            raw = att["content"]
            data = base64.b64decode(raw) if isinstance(raw, str) else raw
            part = MIMEApplication(data, Name=att["filename"])
            part["Content-Disposition"] = f'attachment; filename="{att["filename"]}"'
            root.attach(part)
    else:
        root = body

    root["From"] = formataddr((from_name, from_email))
    root["To"] = to
    root["Subject"] = subject
    root["Message-ID"] = message_id
    return root, message_id


async def send_email_async(
    from_email: str,
    from_name: str,
    to: str,
    subject: str,
    html: str,
    text: str | None = None,
    attachments: list[dict] | None = None,
) -> str | None:
    """Send one email over SMTP. Returns the Message-ID on success.

    Raises PermanentDeliveryError (no retry) or TransientDeliveryError (Celery
    backoff) from the shared taxonomy. Never logs the SMTP password, the OTP or
    the message body — only the secret-free SMTP response text on failure.

    ASSUMPTION: `attachments` is an optional keyword superset of the required
    (from_email, from_name, to, subject, html, text) contract — interview
    invites carry an .ics attachment (build order §9); shape matches the
    caller's existing [{"filename", "content"(base64)}].
    """
    settings = get_settings()

    missing = [
        n for n, v in (
            ("SMTP_HOST", settings.smtp_host),
            ("SMTP_USER", settings.smtp_user),
            ("SMTP_PASSWORD", settings.smtp_password),
        ) if not v
    ]
    if missing:
        # No transport config → permanent for this call; fail fast, don't dial.
        raise PermanentDeliveryError(
            "smtp", None, "config_missing",
            f"{', '.join(missing)} not configured",
            hint=_SMTP_HINTS["config_missing"],
        )

    message, message_id = _build_message(
        from_email, from_name, to, subject, html, text, attachments
    )

    use_ssl = bool(settings.smtp_ssl)
    # STARTTLS only makes sense on a plaintext connection; suppress it when the
    # socket is already TLS-wrapped (implicit SSL, port 465).
    use_starttls = bool(settings.smtp_starttls) and not use_ssl

    try:
        await aiosmtplib.send(
            message,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password,
            use_tls=use_ssl,
            start_tls=use_starttls,
        )
    except aiosmtplib.SMTPAuthenticationError as exc:
        raise PermanentDeliveryError(
            "smtp", getattr(exc, "code", None), "auth_error",
            _smtp_message(exc), hint=_SMTP_HINTS["auth"],
        ) from exc
    except (
        aiosmtplib.SMTPRecipientsRefused,
        aiosmtplib.SMTPSenderRefused,
        aiosmtplib.SMTPResponseException,
    ) as exc:
        code = getattr(exc, "code", None)
        # 4xx = greylisting / mailbox busy → transient; 5xx = permanent reject.
        if code is not None and 400 <= code < 500:
            raise TransientDeliveryError(
                "smtp", code, type(exc).__name__, _smtp_message(exc),
                hint="Transient SMTP failure (4xx), retrying with backoff.",
            ) from exc
        raise PermanentDeliveryError(
            "smtp", code, type(exc).__name__, _smtp_message(exc),
            hint=_SMTP_HINTS["sender_recipient"],
        ) from exc
    except (aiosmtplib.SMTPConnectError, aiosmtplib.SMTPTimeoutError, OSError) as exc:
        # Connect/DNS/timeout at the transport layer → may succeed later.
        raise TransientDeliveryError(
            "smtp", None, type(exc).__name__, str(exc) or repr(exc),
            hint="Network/connection error reaching the SMTP server, "
                 "retrying with backoff.",
        ) from exc
    except aiosmtplib.SMTPException as exc:
        # Any other SMTP-protocol error we did not positively classify: treat as
        # transient so a retry gets a chance rather than dropping the message.
        raise TransientDeliveryError(
            "smtp", getattr(exc, "code", None), type(exc).__name__,
            _smtp_message(exc),
            hint="Unclassified SMTP error, retrying with backoff.",
        ) from exc

    logger.info("email.delivery status=sent provider=smtp")  # no to, no body
    return message_id


def _smtp_message(exc: Exception) -> str:
    """Extract a secret-free, human-readable SMTP response string from an
    aiosmtplib exception (its .message may be bytes)."""
    msg = getattr(exc, "message", None)
    if isinstance(msg, bytes):
        msg = msg.decode("utf-8", "replace")
    return str(msg) if msg else (str(exc) or repr(exc))
