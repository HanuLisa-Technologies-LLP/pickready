import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin
from app.models.enums import Role


class Tenant(Base, UUIDPKMixin, CreatedAtMixin):
    """One row per client company engagement, protected by RLS.

    The company *profile* (industry / culture / details) lives here rather than
    on `companies` because it is captured at Owner-console onboarding time,
    before the client has ever signed in and authored their company profile. The
    `companies` row remains the client-authored, candidate-facing page.

    RLS uses ``id`` as the tenant discriminator. Provider/BD administration and
    pre-tenant identity lookup use explicit bypass scopes; ordinary org
    sessions see exactly their own row.
    """
    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Legacy: the tenant's email domain. Kept (harmless, and still the stable
    # UNIQUE key) but no longer collected in the onboarding UI — it is derived
    # from the owner's email address. Outbound mail is SMTP, so there is no
    # per-tenant sending domain to verify (claude.md rule 5).
    domain: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    # Legacy sending-domain verification state. Retained for schema stability;
    # the SMTP sender does not consult it.
    spf_dkim_status: Mapped[str] = mapped_column(String(50), default="pending", nullable=False)

    # ── Company profile (migration 0009) ─────────────────────────────────────
    # One of INDUSTRY_CHOICES (schemas/admin.py). Stored as free text so the
    # option list can grow without a migration.
    industry: Mapped[str | None] = mapped_column(String(100))
    culture: Mapped[str | None] = mapped_column(Text)
    # ASSUMPTION: free-form prose (size, HQ, founding year, mission) rather
    # than JSONB — the requirement is narrative context for candidates and for
    # JD generation, not queryable structured fields.
    details: Mapped[str | None] = mapped_column(Text)

    # ── Provider Portal customer lifecycle (migration 0020) ──────────────────
    # `active` | `archived`. Archiving is the REVERSIBLE counterpart to the
    # hard `DELETE /admin/tenants/{id}`: it only hides the customer from the
    # default Provider list — no job, application, report or user is touched,
    # and unarchiving restores the row exactly as it was.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Provider-editable customer metadata. Distinct from `domain` above, which
    # is the immutable internal tenant key derived at onboarding.
    website_domain: Mapped[str | None] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(Text)

    # ── Subscription + credits (migration 0026) ──────────────────────────────
    # A customer IS a tenant, so the Razorpay linkage lives here rather than on
    # `companies` (which does not exist until the client first signs in — see
    # models/billing.py for the full reasoning).
    razorpay_customer_id: Mapped[str | None] = mapped_column(String(100))
    razorpay_subscription_id: Mapped[str | None] = mapped_column(String(100))
    current_plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pricing_plans.id", ondelete="SET NULL")
    )
    subscription_status: Mapped[str | None] = mapped_column(String(20))
    subscription_current_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Derived from the ledger (balance < 0) but STORED: the invitation gate runs
    # on every send and must not re-aggregate the whole ledger to answer it.
    credit_deficit: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    # A permanent demonstration company (Sarkar Corp, ACRM Corp, Specter & Co.).
    # Billing still RECORDS everything for these tenants -- a demo of a billing
    # page with no usage on it proves nothing -- but it never refuses anything:
    # `credits.has_credit_headroom` is unconditionally true and `credit_deficit`
    # is never set. Kept as a column rather than a UUID list in Python so the
    # exemption is visible in the table and a future demo tenant is an UPDATE
    # rather than a release (migration 0037).
    is_demo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )


#: `tenants.status` values. Mirrors the CHECK constraint in migration 0020.
CUSTOMER_ACTIVE = "active"
CUSTOMER_ARCHIVED = "archived"
CUSTOMER_STATUSES: tuple[str, ...] = (CUSTOMER_ACTIVE, CUSTOMER_ARCHIVED)


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
    migration). tenant_id NULL for platform-level events.

    Deliberately carries NO foreign key to `tenants`: the trail must survive a
    tenant deletion (api/admin.py delete_tenant relies on this)."""
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

    # ── RBAC_SPECIFICATION.md 30 (migration 0061) ────────────────────────────
    # Nine of these are things 30 names and the blob above was carrying by
    # convention, which meant the Super Admin activity view (31) could not
    # filter on any of them without parsing every row. All nullable, so a
    # rolling deploy has an old writer and a new reader coexisting.
    #
    # `actor_role` is the role AT THE TIME OF THE ACTION, copied rather than
    # joined: a person's role changes, and what authority a past action was
    # taken under does not.
    actor_role: Mapped[str | None] = mapped_column(String(30))
    previous_state: Mapped[dict | None] = mapped_column(JSONB)
    new_state: Mapped[dict | None] = mapped_column(JSONB)
    # 30's "relevant job/application/candidate context". Columns rather than
    # blob keys because every question 31 asks is scoped by one of them.
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    application_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # 30's "source/request metadata".
    request_method: Mapped[str | None] = mapped_column(String(10))
    request_path: Mapped[str | None] = mapped_column(String(512))
    request_ip: Mapped[str | None] = mapped_column(String(64))
    # spec-doc6 4.1: one correlation id, issued at job creation, traceable
    # through every stage and appearing in every audit row for that flow.
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    # 34: an AI-initiated mutation is attributable to BOTH the human
    # principal (actor_user_id, which stays the human, always) and the agent
    # that executed it. Two columns because one cannot hold both, and
    # overloading the actor would make "which human authorised this"
    # unanswerable exactly where it matters most.
    agent_name: Mapped[str | None] = mapped_column(String(50))
    # A 24-asterisked cell was used: allowed, and recorded as a deviation
    # from the canonical flow (7.5 requires the Super Admin override to be
    # recorded; spec-doc6 C13 requires the same of an HR Manager publish).
    exceptional: Mapped[bool | None] = mapped_column(Boolean)


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
