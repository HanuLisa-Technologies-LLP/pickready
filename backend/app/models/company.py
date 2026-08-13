import uuid

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin


class Company(Base, UUIDPKMixin, CreatedAtMixin):
    """Company Profile storage and company-level configuration. One per tenant.

    approval_levels_config maps each of the 4 levels to its assignment
    (ESD §7), e.g.:
      {"requested":   {"active": true,  "approver_user_id": "<uuid>"},
       "recommended": {"active": false, "approver_user_id": null},
       "approved":    {"active": true,  "approver_user_id": "<uuid>"},
       "ratified":    {"active": true,  "approver_user_id": "<uuid>"}}
    """
    __tablename__ = "companies"
    __table_args__ = (UniqueConstraint("tenant_id", name="uq_companies_tenant"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    brief: Mapped[str | None] = mapped_column(Text)
    culture: Mapped[str | None] = mapped_column(Text)
    policies: Mapped[str | None] = mapped_column(Text)
    benefits: Mapped[str | None] = mapped_column(Text)
    approval_levels_config: Mapped[dict | None] = mapped_column(JSONB)

    # ── Company Profile (migration 0016) ─────────────────────────────────────
    # The company-wide defaults edited on Company Portal -> Profile. Every new
    # job SNAPSHOTS these three onto its own columns at creation; editing them
    # here therefore affects future jobs only, never a job already published
    # (2026-07-27 spec §3.2).
    #
    # `benefits_text` is deliberately distinct from the retired `benefits`
    # column above. Migration 0016 seeded it from historical data; Company
    # Profile is now the only runtime reader and writer.
    about_company: Mapped[str | None] = mapped_column(Text)
    work_life: Mapped[str | None] = mapped_column(Text)
    benefits_text: Mapped[str | None] = mapped_column(Text)


class HiringManager(Base, UUIDPKMixin, CreatedAtMixin):
    """Max 5 per tenant — enforced in the service layer AND by a DB trigger
    (see initial migration)."""
    __tablename__ = "hiring_managers"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_hiring_managers_user"),)

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    approval_level: Mapped[str | None] = mapped_column(String(20))  # JobStatus value if assigned


class EmailTemplate(Base, UUIDPKMixin, CreatedAtMixin):
    """Per-tenant editable templates, versioned (FR-8.5 / ESD §12) — the
    platform ships an editor, not fixed copy."""
    __tablename__ = "email_templates"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", "version", name="uq_email_templates_version"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)  # outreach | verification | interview_invite | ...
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)  # supports {{placeholders}}
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(nullable=False, default=True)
