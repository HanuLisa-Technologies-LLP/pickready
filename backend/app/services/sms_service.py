"""Outbound delivery providers: MSG91 SMS + the failure taxonomy shared with
the Resend email path in app.workers.tasks.

Why this module exists
----------------------
Before this, `_resend_send` called `resp.raise_for_status()`, which raises an
`httpx.HTTPStatusError` whose string form is just
``Client error '403 Forbidden' for url ...`` — Resend's JSON body (which says
*exactly* what is wrong and how to fix it) was thrown away. Diagnosing a
delivery outage therefore required re-probing the API by hand. Every failure
now carries the provider's status + parsed body, and is classified as either:

  PermanentDeliveryError — the same request will fail forever (bad/unverified
      sender domain, recipient not allowed by a restricted key, malformed
      recipient, bad credentials). Retrying is pure waste and hides the real
      error behind N identical log lines. Fail fast, log one loud actionable
      line, write an audit row.

  TransientDeliveryError — may succeed later (429 rate-limit, 5xx, timeouts,
      connection errors). Retried by the task runtime with EXPONENTIAL backoff,
      max 3 attempts.

SECURITY (ESD §16): API keys, OTP codes and message bodies are never logged.
Provider error bodies are logged, but those contain only provider-side
validation text, never our payload.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

MSG91_URL = "https://control.msg91.com/api/v2/sendsms"
HTTP_TIMEOUT = 30.0

#: Retry policy for transient delivery failures (claude.md rule 4 — all of
#: this runs inside background tasks, never inline in a request handler).
MAX_DELIVERY_ATTEMPTS = 3          # 1 initial attempt + 2 retries
RETRY_BACKOFF_BASE_SECONDS = 5     # 5s, 10s, 20s … (exponential, jittered)
RETRY_BACKOFF_MAX_SECONDS = 300


class DeliveryError(Exception):
    """Base class for outbound email/SMS delivery failures.

    Carries the provider's own error body so the operator never has to re-probe
    the API to find out what happened.
    """

    permanent: bool = False

    def __init__(
        self,
        provider: str,
        status: int | None,
        error_name: str | None,
        message: str,
        hint: str = "",
    ) -> None:
        self.provider = provider
        self.status = status
        self.error_name = error_name or ""
        self.provider_message = message
        self.hint = hint
        super().__init__(
            f"{provider} send failed: status={status} name={self.error_name!r} "
            f"message={message!r}" + (f" | ACTION: {hint}" if hint else "")
        )

    def as_audit_metadata(self) -> dict[str, Any]:
        """Secret-free dict for the audit_log row."""
        return {
            "provider": self.provider,
            "status": self.status,
            "error_name": self.error_name,
            "provider_message": self.provider_message[:500],
            "hint": self.hint,
            "permanent": self.permanent,
        }


class PermanentDeliveryError(DeliveryError):
    """Will never succeed on retry — do not burn retries on it."""

    permanent = True


class TransientDeliveryError(DeliveryError):
    """May succeed later — the task runtime retries with exponential backoff."""

    permanent = False


# ── Failure classification ───────────────────────────────────────────────────

#: Operator-actionable hints keyed by the condition we can positively identify.
#: These are the exact failures confirmed against the live Resend account
#: (2026-07-24: no verified domain + a restricted, send-only API key).
_RESEND_HINTS: dict[str, str] = {
    "unverified_domain": (
        "Resend has no verified sending domain, so this key may only mail the "
        "account owner. Verify a domain at https://resend.com/domains, then set "
        "the tenant's sending domain and flip tenants.spf_dkim_status to "
        "'verified'. Until then all mail must go to the account owner address "
        "from settings.resend_dev_sender."
    ),
    "invalid_recipient": (
        "Resend rejected the recipient address outright (reserved/invalid "
        "domain, e.g. example.com). Fix the recipient, retrying cannot help."
    ),
    "bad_credentials": (
        "MSG91_AUTH_KEY is missing, revoked, or too restricted for this call. "
        "Create or rotate the key in MSG91 and redeploy."
    ),
    "bad_request": (
        "Provider rejected the request as malformed, inspect the payload "
        "(from/to/subject/attachments). Retrying cannot help."
    ),
}


def parse_error_body(resp: httpx.Response) -> tuple[str, str, dict[str, Any]]:
    """Return (error_name, message, raw_json) from a provider error response.

    Falls back to the raw text body when the provider did not send JSON — the
    whole point is that we never discard what the provider told us.
    """
    try:
        body = resp.json()
    except Exception:
        text = (resp.text or "").strip()
        return "", text[:500], {}
    if isinstance(body, dict):
        name = str(body.get("name") or body.get("type") or body.get("code") or "")
        message = str(
            body.get("message")
            or body.get("error")
            or body.get("detail")
            or body
        )
        return name, message[:1000], body
    return "", str(body)[:1000], {}


def classify_response(provider: str, resp: httpx.Response) -> DeliveryError:
    """Map a non-2xx provider response onto the permanent/transient taxonomy.

    Permanent: 400/401/403/404/405/409/413/422 — bad credentials, unverified
    sending domain, recipient not permitted, malformed recipient/payload.
    Transient: 408/429 and every 5xx.
    """
    status = resp.status_code
    name, message, _raw = parse_error_body(resp)
    lowered = f"{name} {message}".lower()

    if status in (408, 429) or status >= 500:
        return TransientDeliveryError(
            provider,
            status,
            name,
            message,
            hint="Transient provider failure, retrying with exponential backoff.",
        )

    if status in (401, 407) or "restricted_api_key" in lowered or "api key" in lowered:
        hint = _RESEND_HINTS["bad_credentials"]
    elif status == 403 or "verify a domain" in lowered or "own email address" in lowered:
        hint = _RESEND_HINTS["unverified_domain"]
    elif status == 422:
        hint = _RESEND_HINTS["invalid_recipient"]
    else:
        hint = _RESEND_HINTS["bad_request"]
    return PermanentDeliveryError(provider, status, name, message, hint=hint)


def classify_exception(provider: str, exc: Exception) -> DeliveryError:
    """Network-level failures (DNS, connect, read timeout) are transient."""
    if isinstance(exc, DeliveryError):
        return exc
    if isinstance(exc, httpx.RequestError):
        return TransientDeliveryError(
            provider,
            None,
            type(exc).__name__,
            str(exc) or repr(exc),
            hint="Network error reaching the provider, retrying with backoff.",
        )
    return TransientDeliveryError(
        provider, None, type(exc).__name__, str(exc) or repr(exc)
    )


def log_delivery_error(channel: str, err: DeliveryError, **fields: Any) -> None:
    """One loud, greppable, secret-free line per failure.

    Permanent failures log at ERROR with the operator action attached;
    transient ones log at WARNING since a retry is coming.
    """
    extra = " ".join(f"{k}={v}" for k, v in fields.items())
    line = (
        "%s.delivery_failed kind=%s provider=%s status=%s error_name=%s "
        "provider_message=%r %s%s"
    )
    args = (
        channel,
        "permanent" if err.permanent else "transient",
        err.provider,
        err.status,
        err.error_name or "-",
        err.provider_message,
        extra,
        f" | ACTION: {err.hint}" if err.hint else "",
    )
    if err.permanent:
        logger.error(line, *args)
    else:
        logger.warning(line, *args)


# ── MSG91 SMS ────────────────────────────────────────────────────────────────

async def send_sms_async(phone: str, message: str) -> None:
    """POST one transactional SMS to MSG91.

    Neither the phone number nor the message body (which may be an OTP) is
    logged — only status and the provider's error body on failure.
    MSG91 answers 200 with ``{"type": "error", ...}`` for some validation
    failures, so a 200 is not blindly trusted.
    """
    settings = get_settings()
    missing = [
        n for n, v in (
            ("MSG91_API_KEY", settings.msg91_api_key),
            ("MSG91_SENDER_ID", settings.msg91_sender_id),
        ) if not v
    ]
    if missing:
        raise PermanentDeliveryError(
            "msg91", None, "config_missing",
            f"{', '.join(missing)} not configured",
            hint=f"Set {', '.join(missing)} in the environment and restart the worker.",
        )

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            resp = await client.post(
                MSG91_URL,
                headers={"authkey": settings.msg91_api_key},
                json={
                    "sender": settings.msg91_sender_id,
                    "route": "4",  # transactional
                    "country": "91",
                    "sms": [{"message": message, "to": [phone]}],
                },
            )
    except Exception as exc:  # network layer
        raise classify_exception("msg91", exc) from exc

    if resp.status_code >= 400:
        raise classify_response("msg91", resp)

    name, msg, raw = parse_error_body(resp)
    if isinstance(raw, dict) and str(raw.get("type", "")).lower() == "error":
        # 200 OK but MSG91 reports a validation error in the body.
        raise PermanentDeliveryError(
            "msg91", resp.status_code, name or "error", msg,
            hint="MSG91 rejected the request (sender id / template / number). "
                 "Retrying cannot help, fix the sender id or recipient.",
        )
    logger.info("sms.delivery status=sent provider=msg91")  # no phone, no body
