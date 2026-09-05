"""Outbound email over Amazon SES (Corporate Email System spec section 6).

The SECOND real transport, never a fallback: `settings.email_transport`
selects "smtp" or "ses" per deployment, and the two are never chained. This
module owns only the SES transport plus SES-specific failure classification;
the resilience taxonomy (PermanentDeliveryError / TransientDeliveryError) is
imported from `app.services.sms_service` exactly as `smtp_service` imports
it, so the task layer's retry policy is identical whichever transport a
deployment runs.

ASSUMPTION (spec section 6): the spec's FastAPI -> SQS -> worker -> SES chain
maps onto the EXISTING dispatch system. Email jobs already leave the request
handler through `dispatch("pickready.send_email" / "pickready.send_lifecycle_email")`
to Route.LAMBDA, which is this platform's durable async stage; standing up a
second queue for the same property would be two implementations of one
concept. This module is only the last hop, worker -> SES.

Under SES an ACTIVE corporate sender's address IS the From address, which is
the whole point of the feature: SES sends for any identity the ACCOUNT has
verified (domain or address), no mailbox password involved. Under SMTP that
is impossible (Gmail refuses arbitrary From on an authenticated mailbox), so
the tasks layer keeps Gmail's mailbox as From and carries the corporate
sender as Reply-To instead.

The boto3 client is built with explicit connect/read timeouts and a bounded
retry count, same as `workers/dispatch`: botocore's default is a 60-second
connect timeout with retries stacked on top, and an endpoint that HANGS
defeats every try/except around the call.

SECURITY (ESD section 16): no credential lives here (boto3 resolves the task
role), and no message body, OTP or recipient list is ever logged.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.config import get_settings
from app.services.sms_service import (
    PermanentDeliveryError,
    TransientDeliveryError,
)
from app.services.smtp_service import _build_message

logger = logging.getLogger(__name__)

#: SES error codes that mean "this exact message will fail forever": the
#: identity is not verified, the account is paused, or SES rejected the
#: message outright. Retrying cannot help; fail fast with an ACTION hint.
_PERMANENT_CODES: frozenset[str] = frozenset(
    {
        "MessageRejected",
        "MailFromDomainNotVerifiedException",
        "EmailAddressNotVerifiedException",
        "AccountSendingPausedException",
        "SendingPausedException",
        "AccessDenied",
        "AccessDeniedException",
        "InvalidParameterValue",
        "ValidationError",
    }
)

_client: Any = None


def _ses_client():
    """One classic `ses` client per process, bounded like every boto3 client
    in this codebase (the dispatch lesson: an unbounded client hangs)."""
    global _client
    if _client is None:
        import boto3
        from botocore.config import Config

        settings = get_settings()
        _client = boto3.client(
            "ses",
            region_name=settings.aws_region,
            config=Config(
                connect_timeout=5,
                read_timeout=15,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )
    return _client


def reset_client() -> None:
    """Drop the cached client. Used by tests and after a credential change."""
    global _client
    _client = None


async def send_email_async(
    from_email: str,
    from_name: str,
    to: str,
    subject: str,
    html: str,
    text: str | None = None,
    attachments: list[dict] | None = None,
    reply_to: str | None = None,
) -> str | None:
    """Send one email through SES. Returns the SES MessageId on success.

    Same signature contract as `smtp_service.send_email_async` (plus the
    shared `reply_to`), so the task layer selects a transport without
    reshaping the call. Raises PermanentDeliveryError or
    TransientDeliveryError from the shared taxonomy; never logs a body.
    """
    message, _mime_id = _build_message(
        from_email, from_name, to, subject, html, text, attachments,
        reply_to=reply_to,
    )
    raw = message.as_bytes()
    client = _ses_client()

    from botocore.exceptions import (  # imported here like boto3 itself
        BotoCoreError,
        ClientError,
        ConnectionError as BotoConnectionError,
    )

    try:
        # boto3 is synchronous; a thread keeps the worker's event loop free.
        response = await asyncio.to_thread(
            client.send_raw_email,
            Source=from_email,
            Destinations=[to],
            RawMessage={"Data": raw},
        )
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        provider_message = str(exc.response.get("Error", {}).get("Message", ""))[:500]
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if code in _PERMANENT_CODES:
            raise PermanentDeliveryError(
                "ses", status, code, provider_message,
                hint=(
                    "SES permanently rejected the message. ACTION: verify the "
                    "From identity (domain or address) in this SES account and "
                    "region, and check the account is not paused or sandboxed."
                ),
            ) from exc
        # Throttling, quota, 5xx and anything unclassified: give a retry a
        # chance rather than dropping the message.
        raise TransientDeliveryError(
            "ses", status, code or type(exc).__name__, provider_message,
            hint="Transient SES failure, retrying with backoff.",
        ) from exc
    except (BotoConnectionError, BotoCoreError, OSError) as exc:
        raise TransientDeliveryError(
            "ses", None, type(exc).__name__, str(exc) or repr(exc),
            hint="Network/connection error reaching SES, retrying with backoff.",
        ) from exc

    message_id = str(response.get("MessageId", "")) or None
    logger.info("email.delivery status=sent provider=ses")  # no to, no body
    return message_id
