"""Razorpay Subscriptions client and signature verification (killer-spec Part 2).

This talks to Razorpay's REST API over httpx rather than pulling in the official
`razorpay` package, for one reason: that SDK is synchronous (requests), and
every route and task in this codebase is async. A blocking HTTP call inside an
async handler stalls the whole event loop, which is precisely the class of
latency problem the rest of this build pass is removing.

The Key Secret never leaves this module and is never logged. Errors carry
Razorpay's own message where it is safe (it is written for the merchant, not
the cardholder) and never the credentials that produced them.

Recurring monthly billing means the **Subscriptions** API, not Orders: an Order
is a one-time charge and would silently turn a monthly plan into a single
payment.
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

# Razorpay requires a finite billing count on a subscription. 120 monthly cycles
# is ten years — long enough that no live subscription reaches it, short enough
# that Razorpay accepts it.
DEFAULT_TOTAL_COUNT = 120

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


# ── Plans ────────────────────────────────────────────────────────────────────

async def create_plan(*, name: str, price_inr: int, notes: dict[str, str] | None = None) -> str:
    """Create a monthly Razorpay Plan and return its id."""
    body = await _request(
        "POST",
        "/plans",
        {
            "period": "monthly",
            "interval": 1,
            "item": {
                "name": name,
                "amount": price_inr * PAISE_PER_RUPEE,
                "currency": "INR",
            },
            "notes": notes or {},
        },
    )
    return body["id"]


# ── Customers ────────────────────────────────────────────────────────────────

async def create_customer(*, name: str, email: str | None, contact: str | None) -> str:
    payload: dict[str, Any] = {"name": name, "fail_existing": 0}
    if email:
        payload["email"] = email
    if contact:
        payload["contact"] = contact
    body = await _request("POST", "/customers", payload)
    return body["id"]


# ── Subscriptions ────────────────────────────────────────────────────────────

async def create_subscription(
    *,
    plan_id: str,
    customer_id: str | None = None,
    total_count: int = DEFAULT_TOTAL_COUNT,
    notes: dict[str, str] | None = None,
) -> dict:
    payload: dict[str, Any] = {
        "plan_id": plan_id,
        "total_count": total_count,
        # Razorpay emails the customer its own receipts. ReadyPick sends its
        # own lifecycle mail, so this stays off to avoid two messages for one
        # event.
        "customer_notify": 0,
        "notes": notes or {},
    }
    if customer_id:
        payload["customer_id"] = customer_id
    return await _request("POST", "/subscriptions", payload)


async def fetch_subscription(subscription_id: str) -> dict:
    return await _request("GET", f"/subscriptions/{subscription_id}")


async def update_subscription(
    subscription_id: str, *, plan_id: str, schedule_change_at: str = "now"
) -> dict:
    """Move a live subscription to a different plan.

    `schedule_change_at="now"` makes Razorpay compute the proration itself and
    charge or credit the difference on the spot; `"cycle_end"` defers to the next
    renewal with no proration. We do NOT compute proration locally — the amount
    Razorpay actually charges is the only one that matters, and a second opinion
    computed here would only ever disagree with the customer's card statement.
    """
    return await _request(
        "PATCH",
        f"/subscriptions/{subscription_id}",
        {"plan_id": plan_id, "schedule_change_at": schedule_change_at, "customer_notify": 0},
    )


async def cancel_subscription(subscription_id: str, *, at_cycle_end: bool = True) -> dict:
    return await _request(
        "POST",
        f"/subscriptions/{subscription_id}/cancel",
        {"cancel_at_cycle_end": 1 if at_cycle_end else 0},
    )


# ── Signatures ───────────────────────────────────────────────────────────────

def _hmac_hex(secret: str, message: str) -> str:
    return hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_checkout_signature(
    *, payment_id: str, subscription_id: str, signature: str
) -> bool:
    """Verify the handler payload Razorpay Checkout returns in the browser.

    For SUBSCRIPTIONS the signed message is ``payment_id|subscription_id`` — the
    reverse of the Orders flow's ``order_id|payment_id``. Getting that order
    wrong produces a verification that fails 100% of the time on perfectly good
    payments, so it is asserted directly in tests/test_billing.py.
    """
    cfg = config()
    if not cfg.key_secret:
        return False
    expected = _hmac_hex(cfg.key_secret, f"{payment_id}|{subscription_id}")
    return hmac.compare_digest(expected, signature or "")


def verify_webhook_signature(*, raw_body: bytes, signature: str) -> bool:
    """Verify X-Razorpay-Signature over the EXACT bytes Razorpay sent.

    Re-serializing the parsed JSON and hashing that would change key order and
    whitespace and fail every time, which is why the caller passes raw bytes.

    With no RAZORPAY_WEBHOOK_SECRET configured this returns False. The route is
    what decides whether an unverified payload is acceptable, and it only ever
    is outside production — see api/billing.razorpay_webhook.
    """
    cfg = config()
    if not cfg.webhook_secret:
        return False
    expected = hmac.new(
        cfg.webhook_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature or "")
