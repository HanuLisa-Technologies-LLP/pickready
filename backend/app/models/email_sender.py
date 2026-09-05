"""Corporate email senders (Corporate Email System spec, 2026-09-05, section 9).

One row per corporate/business mailbox a client has registered as an
authorized From identity for automated email. The spec's `client_id` is
`tenant_id` here: a customer IS a `tenants` row in this schema (the same
substitution the billing work made when its spec wrote `companies`).

ReadyPick never stores an email or SMTP password for these mailboxes (spec
section 10). Ownership is proven by a short-lived OTP delivered TO the mailbox
(services/email_senders), and the client Super Admin's authorization is what
makes the row usable; both facts are recorded here permanently while the OTP
state itself lives only in Redis.

THE LIFECYCLE IS A REAL FSM, refused in the service, stored as words here.
The spec's AUTHORIZED and ACTIVE stages are deliberately collapsed: the Super
Admin's authorize action IS activation, and `authorized_by`/`authorized_at`
record it. A separate AUTHORIZED state would exist only between two lines of
one handler and would double the states every reader has to handle.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

#: The sender lifecycle vocabulary. Mirrored by ck_client_email_senders_status
#: in migration 0080 -- keep both in step.
SENDER_PENDING_VERIFICATION = "pending_verification"
SENDER_EMAIL_VERIFIED = "email_verified"
SENDER_ACTIVE = "active"
SENDER_VERIFICATION_EXPIRED = "verification_expired"
SENDER_DISABLED = "disabled"
SENDER_REVOKED = "revoked"

SENDER_STATUSES: tuple[str, ...] = (
    SENDER_PENDING_VERIFICATION,
    SENDER_EMAIL_VERIFIED,
    SENDER_ACTIVE,
    SENDER_VERIFICATION_EXPIRED,
    SENDER_DISABLED,
    SENDER_REVOKED,
)


class ClientEmailSender(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "client_email_senders"
    __table_args__ = (
        Index("ix_client_email_senders_tenant", "tenant_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    #: The POC's display name (spec section 9's example: "Rahul").
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Stored LOWERCASED, unique per tenant (migration 0080's partial unique
    #: index). Case-folding at the write path is what makes the uniqueness real.
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default=SENDER_PENDING_VERIFICATION,
        server_default=SENDER_PENDING_VERIFICATION,
    )
    #: True once the mailbox OTP was entered correctly. Survives disable and
    #: revoke: proof of ownership is a historical fact, not a switch.
    email_verified: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    #: WHO activated it (the client Super Admin, spec section 3) and when.
    #: SET NULL, not RESTRICT: the authorization is a tenant decision the row
    #: keeps even after that person's account is deleted.
    authorized_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
