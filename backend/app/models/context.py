"""The chunk-level retrieval index (migration 0054).

Mapped for reads and for tests. Writes go through `services/rag/index`, which
UPSERTs on (source_type, source_id, ordinal) in one statement -- a per-row ORM
write would re-embed and re-insert a document one chunk at a time, and a partial
failure would leave half a resume indexed against half a version.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin


class ContextChunk(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "context_chunks"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    #: jd | resume | assessment
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    #: The job, profile or link this chunk was cut from.
    source_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    #: Fingerprint of the WHOLE source document, so a query can exclude chunks
    #: belonging to a superseded version rather than blending two of them.
    source_version: Mapped[str] = mapped_column(String(64), nullable=False)
    section_type: Mapped[str] = mapped_column(String(30), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    #: Fingerprint of THIS chunk, so re-indexing an edited document re-embeds
    #: only the chunks whose text actually changed.
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
