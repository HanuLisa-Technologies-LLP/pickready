"""Drishti: one strategic profile per function per tenant (migration 0105).

The five sections are the functional head's own words and stay theirs;
`compiled_json` is the deterministic artifact and the ONLY thing a prompt
or a weight ever reads (`hiring/drishti.compile_profile`). Vivekium C3.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin


class DrishtiProfile(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "drishti_profiles"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: The FUNCTION, matched case-insensitively against jobs.department.
    function_name: Mapped[str] = mapped_column(String(120), nullable=False)
    strategic_purpose: Mapped[str | None] = mapped_column(Text)
    people_philosophy: Mapped[str | None] = mapped_column(Text)
    non_negotiables: Mapped[str | None] = mapped_column(Text)
    culture_expectations: Mapped[str | None] = mapped_column(Text)
    strategic_gap: Mapped[str | None] = mapped_column(Text)
    compiled_json: Mapped[dict | None] = mapped_column(JSONB)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
