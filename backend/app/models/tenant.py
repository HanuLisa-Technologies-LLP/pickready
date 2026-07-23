import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin
from app.models.enums import Role


class Tenant(Base, UUIDPKMixin, CreatedAtMixin):
    """One row per client company engagement. Global table (no RLS)."""
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # Resend sending-domain verification state for client-domain email (ESD §11)
    spf_dkim_status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)


class RolePermission(Base, UUIDPKMixin):
    """The RBAC engine's data (ESD §6): permissions are data, not code.
    tenant_id NULL rows form the global default template (Super Admin-editable)."""
    __tablename__ = "role_permissions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "role", "capability", name="uq_role_permissions"),
        Index("ix_role_permissions_lookup", "tenant_id", "role", "capability"),
    )

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True
    )
    role: Mapped[Role] = mapped_column(Enum(Role, native_enum=False, length=30), nullable=False)
    capability: Mapped[str] = mapped_column(String(100), nullable=False)
    allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AuditLog(Base, UUIDPKMixin):
    """Append-only (no UPDATE/DELETE grants for the app role — enforced in the
    migration). tenant_id NULL for platform-level events."""
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_tenant_at", "tenant_id", "at"),)

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(50))
    target_id: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict | None] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default="now()", nullable=False
    )


class LLMProviderKey(Base, UUIDPKMixin, CreatedAtMixin):
    """Nine keys (3× Groq/Gemini/OpenRouter), encrypted at rest, with a
    circuit-breaker health flag (ESD §8.4). Global table."""
    __tablename__ = "llm_provider_keys"

    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    role_hint: Mapped[str] = mapped_column(String(30), nullable=False)
    priority: Mapped[int] = mapped_column(nullable=False, default=0)
    healthy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
