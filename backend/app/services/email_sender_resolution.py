"""The ONE place that decides which From identity an outbound email carries.

Register I-20: Amazon SES only, three kinds of mail, three answers.

* SECURITY (a code, an account notice): `noreply@readypick.ai`. Never a
  company address, never an address a request supplied.
* PLATFORM (a customer invitation, a credit notice, a departmental inquiry,
  anything addressed from Vivekium to somebody with no tenant context):
  `contact@readypick.ai`.
* TENANT (candidate lifecycle mail, outreach, staff invitations, background
  verification requests: every email sent on a company's behalf): the
  company's own approved sender, displayed under the company name. A company
  with no ACTIVE sender is not an error and is not silent: the mail leaves
  from `contact@readypick.ai` under the company name, and the fallback is
  recorded as `email_log.from_source = platform_fallback`, as a log field
  and as a field of the delivery audit row.

The From domain is `readypick.ai` or an ACTIVE sender the company's Super
Admin authorised (`models/email_sender`). Settings enforce the first half at
boot (`core/config`); the second half is the status check below, repeated at
send time by the worker.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.email_sender import SENDER_ACTIVE, ClientEmailSender
from app.models.tenant import Tenant

logger = logging.getLogger(__name__)

SOURCE_COMPANY_SENDER = "company_sender"
SOURCE_PLATFORM_FALLBACK = "platform_fallback"
SOURCE_PLATFORM = "platform"
SOURCE_SECURITY = "security"

#: Mirrored by migration 0139. Keep both in step.
FROM_SOURCES: tuple[str, ...] = (
    SOURCE_COMPANY_SENDER,
    SOURCE_PLATFORM_FALLBACK,
    SOURCE_PLATFORM,
    SOURCE_SECURITY,
)

#: Templates that are Vivekium writing to a customer even though a tenant is
#: named (so the customer can be found), never the company writing to its own
#: candidates or staff. They leave from the platform address.
PLATFORM_TEMPLATES: frozenset[str] = frozenset(
    {
        "client_invite",
        "credit_warning_low",
        "credit_warning_critical",
        "credit_invoice",
    }
)


@dataclass(frozen=True)
class FromIdentity:
    email: str
    name: str
    source: str
    sender_id: uuid.UUID | None = None


def security_identity() -> FromIdentity:
    settings = get_settings()
    return FromIdentity(
        email=settings.platform_security_sender_email.strip(),
        name=settings.platform_security_sender_name.strip() or "Vivekium",
        source=SOURCE_SECURITY,
    )


def platform_identity(*, source: str = SOURCE_PLATFORM, name: str | None = None) -> FromIdentity:
    settings = get_settings()
    return FromIdentity(
        email=settings.platform_sender_email.strip(),
        name=(name or "").strip() or settings.platform_sender_name.strip() or "Vivekium",
        source=source,
    )


async def default_active_sender(
    session: AsyncSession, tenant_id: uuid.UUID
) -> ClientEmailSender | None:
    return (
        await session.execute(
            select(ClientEmailSender).where(
                ClientEmailSender.tenant_id == tenant_id,
                ClientEmailSender.is_default.is_(True),
                ClientEmailSender.status == SENDER_ACTIVE,
            )
        )
    ).scalar_one_or_none()


def tenant_identity(
    tenant: Tenant, sender: ClientEmailSender | None
) -> FromIdentity:
    """From for mail sent on `tenant`'s behalf. `sender` must already be an
    ACTIVE sender of this tenant, or None for the recorded fallback."""
    if sender is not None:
        if sender.status != SENDER_ACTIVE or sender.tenant_id != tenant.id:
            raise ValueError("a tenant From identity needs an active sender of that tenant")
        return FromIdentity(
            email=sender.email,
            name=tenant.name,
            source=SOURCE_COMPANY_SENDER,
            sender_id=sender.id,
        )
    logger.warning(
        "email.sender_fallback tenant_id=%s reason=no_active_sender from=%s",
        tenant.id,
        get_settings().platform_sender_email,
    )
    return platform_identity(source=SOURCE_PLATFORM_FALLBACK, name=tenant.name)


async def resolve_identity(
    session: AsyncSession,
    *,
    tenant: Tenant | None,
    template_name: str,
    sender: ClientEmailSender | None = None,
) -> FromIdentity:
    """From for a `pickready.send_email` message: platform mail by nature
    (no tenant, or a platform template) or mail on a company's behalf."""
    if tenant is None or template_name in PLATFORM_TEMPLATES:
        return platform_identity()
    if sender is None:
        sender = await default_active_sender(session, tenant.id)
    return tenant_identity(tenant, sender)
