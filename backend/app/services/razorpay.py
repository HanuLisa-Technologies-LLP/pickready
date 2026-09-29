"""Razorpay Orders client and signature verification.

The product sells credits as ONE-TIME purchases, so the Orders API is the
only Razorpay API this module speaks: an Order is exactly one charge. The
monthly Subscriptions client that shipped first was deleted with the
subscription model (owner spec, 2026-09-29, section 23).

This talks to Razorpay's REST API over httpx rather than pulling in the official
`razorpay` package, for one reason: that SDK is synchronous (requests), and
every route and task in this codebase is async. A blocking HTTP call inside an
async handler stalls the whole event loop.

The Key Secret never leaves this module and is never logged. Errors carry
Razorpay's own message where it is safe (it is written for the merchant, not
the cardholder) and never the credentials that produced them.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)

API_BASE = "https://api.razorpay.com/v1"

# Razorpay quotes money in paise. Every amount crossing this boundary is an
# integer number of paise; rupees appear only in our own tables and in the UI.
PAISE_PER_RUPEE = 100

_TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class RazorpayNotConfigured(RuntimeError):
    """Raised when RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are absent."""


class RazorpayError(RuntimeError):
    """A non-2xx response from Razorpay, with its message where available."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class RazorpayConfig:
    key_id: str
    key_secret: str
    webhook_secret: str

    @property
    def configured(self) -> bool:
        return bool(self.key_id and self.key_secret)


def config() -> RazorpayConfig:
    settings = get_settings()
    return RazorpayConfig(
        key_id=settings.razorpay_key_id.strip(),
        key_secret=settings.razorpay_key_secret.strip(),
        webhook_secret=settings.razorpay_webhook_secret.strip(),
    )


def require_config() -> RazorpayConfig:
    cfg = config()
    if not cfg.configured:
        raise RazorpayNotConfigured(
            "Razorpay is not configured on this server. Set RAZORPAY_KEY_ID and "
            "RAZORPAY_KEY_SECRET."
        )
    return cfg


async def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> dict:
    cfg = require_config()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        try:
            response = await client.request(
                method,
                f"{API_BASE}{path}",
                json=payload,
                auth=(cfg.key_id, cfg.key_secret),
                headers={"Content-Type": "application/json"},
            )
        except httpx.HTTPError as exc:
            # No response body to quote, and the exception's repr can contain the
            # request URL but never the credentials (httpx keeps basic auth in a
            # header, not the URL).
            log.warning("razorpay.transport_error path=%s err=%s", path, type(exc).__name__)
            raise RazorpayError("Could not reach Razorpay. Try again in a moment.") from exc
    if response.status_code >= 400:
        detail = "Razorpay rejected the request"
        try:
            body = response.json()
            detail = (body.get("error") or {}).get("description") or detail
        except ValueError:
            pass
        log.warning("razorpay.api_error path=%s status=%s", path, response.status_code)
        raise RazorpayError(detail, status_code=response.status_code)
    return response.json()


# ── Orders (one-time credit-pack purchases, Master Directive Part 5) ─────────

async def create_order(
    *, amount_paise: int, receipt: str, notes: dict | None = None
) -> dict:
    """Create a one-time Razorpay Order and return the full entity.

    Credit packs use the ORDERS API, not Subscriptions: Part 5 §1 is explicit
    that there are no monthly plans, and an Order is exactly one charge. The
    receipt is our purchase row's id, so a stray order in the Razorpay
    dashboard is traceable back to its `credit_purchases` row by inspection.
    """
    return await _request(
        "POST",
        "/orders",
        {
            "amount": amount_paise,
            "currency": "INR",
            "receipt": receipt,
            "notes": notes or {},
        },
    )


# ── Signatures ───────────────────────────────────────────────────────────────

def _hmac_hex(secret: str, message: str) -> str:
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_order_signature(*, order_id: str, payment_id: str, signature: str) -> bool:
    """Verify the Checkout handler payload for an ORDERS payment.

    Orders sign ``order_id|payment_id``, in that order. The wrong order fails
    100% of good payments, which is exactly the kind of defect a happy-path
    test never sees, so the direction is pinned by tests/test_credit_packs.py.
    """
    cfg = config()
    if not cfg.key_secret:
        return False
    expected = _hmac_hex(cfg.key_secret, f"{order_id}|{payment_id}")
    return hmac.compare_digest(expected, signature or "")


def verify_webhook_signature(*, raw_body: bytes, signature: str) -> bool:
    """Verify X-Razorpay-Signature over the EXACT bytes Razorpay sent.

    Re-serializing the parsed JSON and hashing that would change key order and
    whitespace and fail every time, which is why the caller passes raw bytes.

    With no RAZORPAY_WEBHOOK_SECRET configured this returns False, and the
    route refuses every webhook in that state (api/billing.razorpay_webhook):
    a signature check that cannot be performed has failed, not passed.
    """
    cfg = config()
    if not cfg.webhook_secret:
        return False
    expected = hmac.new(
        cfg.webhook_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature or "")
