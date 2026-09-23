"""The outbound delivery failure taxonomy, shared by every mail transport.

Moved here on 2026-09-24 from the SMS sender module, where it had lived
since the SMS path was the first transport to need it. Both email transports
(`smtp_service`, `ses_service`) imported it from the SMS module, so retiring
SMS would have taken the email path's failure classes with it. The classes and
`log_delivery_error` are unchanged; only their address moved.

Every failure carries the provider's status and parsed body, and is one of:

  PermanentDeliveryError: the same request will fail forever (bad or
      unverified sender, a recipient the provider refuses, a malformed
      recipient, bad credentials). Retrying is pure waste and hides the real
      error behind N identical log lines. Fail fast, log one loud actionable
      line, write an audit row.

  TransientDeliveryError: may succeed later (429 rate-limit, 5xx, timeouts,
      connection errors). Retried by the task runtime with exponential backoff.

SECURITY (ESD §16): API keys, codes and message bodies are never logged.
Provider error bodies are logged, and those carry only the provider's own
validation text, never our payload.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class DeliveryError(Exception):
    """Base class for outbound delivery failures.

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
    """Will never succeed on retry; do not burn retries on it."""

    permanent = True


class TransientDeliveryError(DeliveryError):
    """May succeed later; the task runtime retries with exponential backoff."""

    permanent = False


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
