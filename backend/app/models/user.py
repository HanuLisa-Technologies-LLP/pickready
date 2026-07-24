import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin
from app.models.enums import OTPChannel, Role, UserStatus


class User(Base, UUIDPKMixin, CreatedAtMixin):
    """tenant_id is NULL for super_admin (platform-wide) and candidates
    (external actors; candidate data lives in `candidates`)."""
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("tenant_id", "email", "role", name="uq_users_tenant_email_role"),
        Index("ix_users_email", "email"),
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True
    )
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False, length=30), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20))
    full_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[UserStatus] = mapped_column(
        Enum(UserStatus, native_enum=False, length=20), nullable=False, default=UserStatus.invited
    )
    # First client login must dual-verify email AND mobile (FR-1.2/1.3)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    phone_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    firebase_uid: Mapped[str | None] = mapped_column(String(128), unique=True)
    auth_providers: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)


class OTPChallenge(Base, UUIDPKMixin, CreatedAtMixin):
    """Only the code hash is stored (HMAC, see core.security). Attempt counters
    also mirrored in Redis for atomic increments under concurrency (ESD §5)."""
    __tablename__ = "otp_challenges"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    identifier: Mapped[str] = mapped_column(String(320), nullable=False)  # email or phone
    channel: Mapped[OTPChannel] = mapped_column(
        Enum(OTPChannel, native_enum=False, length=10), nullable=False
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
