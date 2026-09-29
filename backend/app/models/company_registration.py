"""A company's public self-registration, before it is a customer (migration 0134).

Owner spec 2026-09-29, sections 2.2, 5 and 24: the first Company Super Admin
registers the company, proves the company mailbox with a security code, buys
the first credit pack and only then sets a password and enters the portal.
Every step before the last happens with NO org session and, until the code is
verified, with no tenant at all, so the durable record of "somebody is part way
through registering this company" has to live somewhere that is not a tenant.
This is that record.

WHAT IS STORED, AND WHAT IS DERIVED
-----------------------------------
`status` is the registration's OWN lifecycle and nothing else:

* `email_verification_pending`  the form was accepted and a code was sent;
* `email_verified`              the code was right; the tenant (status
                                `onboarding`) and its invited `client` user
                                exist and are linked here;
* `activated`                   the password was set and the workspace opened;
* `expired`                     a pending registration nobody verified inside
                                `REGISTRATION_PENDING_DAYS`, closed when the
                                same address registers again;
* `cancelled`                   closed by an operator
                                (`app.scripts.cleanup_test_accounts`);
* `locked`                      closed by an operator for abuse. Reserved
                                vocabulary: no route writes it.

Whether the first purchase is PAID is never stored here (claude.md rule 8, a
timestamp is not evidence): it is read from `credit_purchases`, the one table
that knows, every time it is asked (`services/company_onboarding.has_paid`).
A second copy of "paid" here would be one more place for the webhook and the
browser's verify call to disagree.

WHO MAY READ IT
---------------
Nobody through a tenant session. The row exists before its tenant does, so
the RLS policy admits the explicit bypass scope only, and the onboarding
routes are the one reader and writer, through the public session, each
handler filtering by the one registration its signed cookie names.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

REGISTRATION_PENDING = "email_verification_pending"
REGISTRATION_VERIFIED = "email_verified"
REGISTRATION_ACTIVATED = "activated"
REGISTRATION_EXPIRED = "expired"
REGISTRATION_CANCELLED = "cancelled"
REGISTRATION_LOCKED = "locked"

#: Mirrors `ck_company_registrations_status` (migration 0134).
REGISTRATION_STATUSES: tuple[str, ...] = (
    REGISTRATION_PENDING,
    REGISTRATION_VERIFIED,
    REGISTRATION_ACTIVATED,
    REGISTRATION_EXPIRED,
    REGISTRATION_CANCELLED,
    REGISTRATION_LOCKED,
)

#: The states a registration can still move out of. One per address at a
#: time, by the partial UNIQUE index `uq_company_registrations_open_email`.
OPEN_STATUSES: tuple[str, ...] = (REGISTRATION_PENDING, REGISTRATION_VERIFIED)


class CompanyRegistration(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "company_registrations"

    #: Lowercased and trimmed at the route, so the partial UNIQUE index and
    #: every lookup compare one spelling.
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    #: E.164 (`+` and digits), canonicalised at the route.
    phone: Mapped[str] = mapped_column(String(20), nullable=False)
    company_name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: One of `schemas.admin.INDUSTRY_CHOICES`, the one industry vocabulary.
    industry: Mapped[str] = mapped_column(String(100), nullable=False)
    #: What the person typed when they chose "Other"; NULL otherwise.
    industry_other: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(
        String(40), nullable=False, default=REGISTRATION_PENDING,
        server_default=REGISTRATION_PENDING,
    )
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="SET NULL")
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )
