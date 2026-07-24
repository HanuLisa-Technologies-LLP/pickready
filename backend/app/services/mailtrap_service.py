"""Outbound email via the Mailtrap Sending API (claude.md rule 5 — Mailtrap
replaced Resend for ALL outbound mail).

This module owns ONLY the provider call + Mailtrap-specific failure
classification. The resilience taxonomy itself is shared with the SMS path and
is imported from ``app.services.sms_service`` — we do not re-implement
PermanentDeliveryError / TransientDeliveryError here, we reuse them so the
Celery task's ``autoretry_for`` and audit logic behave identically for email
and SMS.

Two Mailtrap products, one code path:
  * Sending API (default, real delivery): host ``send.api.mailtrap.io``,
    ``POST /api/send``.
  * Testing/sandbox inbox (dev): host ``sandbox.api.mailtrap.io``,
    ``POST /api/send/<inbox_id>``.
The host + inbox are settings-driven (``mailtrap_api_host`` /
``mailtrap_inbox_id``) so a sandbox inbox can be used in dev without a code
change.

Request shape (JSON):
    {
      "from": {"email": <sender>, "name": <sender name>},
      "to":   [{"email": <recipient>}],
      "subject": <subject>,
      "html": <html body>,
      "text": <plain-text body>        # optional
    }
Auth: ``Authorization: Bearer <MAILTRAP_API_TOKEN>``.
Success: 200 with ``{"success": true, "message_ids": ["..."]}``.

SECURITY (ESD §16): the API token, OTP codes and message bodies are never
logged. Mailtrap's error body IS logged — it contains only provider-side
validation text (e.g. "sender is not verified"), never our payload. Keeping the
error body visible is the exact fix that made the old Resend outage debuggable;
we preserve it here.
"""
from __future__ import annotations

import logging

import httpx

from app.core.config import get_settings
from app.services.sms_service import (
    HTTP_TIMEOUT,
    PermanentDeliveryError,
    TransientDeliveryError,
    classify_exception,
    parse_error_body,
)

logger = logging.getLogger(__name__)

#: Operator-actionable hints, keyed by the condition we can positively identify.
_MAILTRAP_HINTS: dict[str, str] = {
    "bad_credentials": (
        "Mailtrap rejected the request (401/403) — the token is missing/invalid "
        "or the sender is not verified. ACTION: set a verified Mailtrap sender "
        "and check MAILTRAP_API_TOKEN, then restart the worker."
    ),
    "unverified_sender": (
        "Mailtrap will not send from an unverified sender/domain. ACTION: set a "
        "verified Mailtrap sender (verify the domain/sender in Mailtrap) and "
        "check MAILTRAP_API_TOKEN."
    ),
    "bad_request": (
        "Mailtrap rejected the request as malformed (from/to/subject/body). "
        "Retrying cannot help — inspect the payload."
    ),
}


def _classify_mailtrap_response(resp: httpx.Response) -> object:
    """Map a non-2xx Mailtrap response onto the shared permanent/transient
    taxonomy, always surfacing the parsed provider body.

    Permanent: 400/401/403/422 (bad token, unverified sender, malformed).
    Transient: 408/429 and every 5xx.
    """
    status = resp.status_code
    name, message, _raw = parse_error_body(resp)
    lowered = f"{name} {message}".lower()

    if status in (408, 429) or status >= 500:
        return TransientDeliveryError(
            "mailtrap",
            status,
            name,
            message,
            hint="Transient Mailtrap failure — retrying with exponential backoff.",
        )

    if status in (401, 403):
        # Mailtrap returns 401/403 for both a bad token and an unverified
        # sender; the body distinguishes them, and both hints name the same fix.
        hint = (
            _MAILTRAP_HINTS["unverified_sender"]
            if ("sender" in lowered or "verif" in lowered or "domain" in lowered)
            else _MAILTRAP_HINTS["bad_credentials"]
        )
    elif "sender" in lowered or "from" in lowered:
        hint = _MAILTRAP_HINTS["unverified_sender"]
    else:
        hint = _MAILTRAP_HINTS["bad_request"]
    return PermanentDeliveryError("mailtrap", status, name, message, hint=hint)


def _endpoint(settings) -> str:
    """Resolve the Mailtrap send URL. When an inbox id is configured we hit the
    Testing/sandbox host + inbox-scoped path; otherwise the real Sending API."""
    host = settings.mailtrap_api_host
    inbox = getattr(settings, "mailtrap_inbox_id", "") or ""
    if inbox:
        # Sandbox: host is typically sandbox.api.mailtrap.io; path carries the
        # inbox id. Respect an explicitly-set host, else swap to the sandbox one.
        if host == "send.api.mailtrap.io":
            host = "sandbox.api.mailtrap.io"
        return f"https://{host}/api/send/{inbox}"
    return f"https://{host}/api/send"


async def send_email_async(
    from_email: str,
    from_name: str,
    to: str,
    subject: str,
    html: str,
    text: str | None = None,
    attachments: list[dict] | None = None,
) -> str:
    """POST one email to Mailtrap. Returns the Mailtrap message id.

    Raises PermanentDeliveryError (no retry) or TransientDeliveryError (Celery
    backoff) from the shared taxonomy. Never logs the token or the body.

    ASSUMPTION: `attachments` is an optional keyword superset of the required
    (from_email, from_name, to, subject, html, text) contract — interview
    invites carry an .ics attachment (build order §9), so silently dropping it
    would break that feature. Shape matches the caller's existing
    [{"filename", "content"(base64)}]; Mailtrap accepts the same base64 field.
    """
    settings = get_settings()
    if not settings.mailtrap_api_token:
        # No token at all is permanent for this call — fail fast, don't POST.
        raise PermanentDeliveryError(
            "mailtrap", None, "config_missing", "MAILTRAP_API_TOKEN not configured",
            hint="Set MAILTRAP_API_TOKEN in the environment and restart the worker.",
        )

    payload: dict = {
        "from": {"email": from_email, "name": from_name},
        "to": [{"email": to}],
        "subject": subject,
        "html": html,
    }
    if text:
        payload["text"] = text
    if attachments:
        payload["attachments"] = [
            {"filename": a["filename"], "content": a["content"]} for a in attachments
        ]

    url = _endpoint(settings)
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {settings.mailtrap_api_token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
    except Exception as exc:  # network layer (DNS/connect/timeout) → transient
        raise classify_exception("mailtrap", exc) from exc

    if resp.status_code >= 400:
        # Surface Mailtrap's JSON body (the invisibility of the old Resend body
        # is exactly what we are guarding against). classify maps it to the
        # taxonomy with an operator hint.
        raise _classify_mailtrap_response(resp)

    # 200 — Mailtrap can still report success=false in the body for a rejected
    # request; treat that as a permanent validation failure, body preserved.
    name, message, raw = parse_error_body(resp)
    if isinstance(raw, dict) and raw.get("success") is False:
        errors = raw.get("errors")
        detail = message if not errors else "; ".join(str(e) for e in errors)
        raise PermanentDeliveryError(
            "mailtrap", resp.status_code, name or "send_rejected", detail,
            hint=_MAILTRAP_HINTS["unverified_sender"],
        )

    message_id = ""
    if isinstance(raw, dict):
        ids = raw.get("message_ids") or []
        if ids:
            message_id = str(ids[0])
        elif raw.get("message_id"):
            message_id = str(raw.get("message_id"))
    logger.info("email.delivery status=sent provider=mailtrap")  # no to, no body
    return message_id
