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
    # ── Embedding provenance (migration 0062) ────────────────────────────────
    # Same four columns, same reason, as `profiles.embedding`: the width of a
    # vector says nothing about which model produced it, and this index holds
    # rows from both sides of the single-vendor consolidation.
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    embedding_contract_version: Mapped[str | None] = mapped_column(String(32))
    embedding_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    embedding_shadow: Mapped[list[float] | None] = mapped_column(Vector(1024))
    # ── Chunk-level ACL (migration 0092, W9.5) ───────────────────────────────
    # THE ACL IS ON THE CHUNK, NOT ONLY ON THE SOURCE DOCUMENT, and it is
    # enforced at RETRIEVAL TIME rather than at write time, because permissions
    # change after storage: a recruiter loses `view_review_screen`, a person
    # leaves the team, a role is re-scoped, and every chunk written before that
    # moment is still sitting in the index.
    #
    # An embedding is not a one-way hash. Published inversion work recovers 50
    # to 70% of the input words from popular sentence embeddings, and because
    # the embedding model is public and queryable, a dictionary attack against
    # stolen vectors is practical. A resume chunk's vector is therefore the
    # candidate's personal data at rest, and it is classified as such in
    # `services/erasure.VECTOR_COLUMNS`.
    #
    # Both columns are maintained by a database TRIGGER (0092) and not only by
    # the indexer, so a chunk written by any future writer carries its ACL. A
    # column only a remembering caller populates is a column that is NULL on the
    # row that mattered.
    #: The capability a reader must hold. NULL means the chunk carries no
    #: personal data beyond what the tenant itself authored (a JD), so tenant
    #: membership is the whole check.
    acl_capability: Mapped[str | None] = mapped_column(String(64))
    #: The candidate whose personal data this chunk contains. NULL for a JD.
    #: `services/erasure.cascade_erasure` erases on this column, so an erasure
    #: does not depend on knowing which source id shape produced the chunk.
    acl_candidate_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    # ── Contextual retrieval (migration 0091, RPN-AI-UP-001 W6.2) ────────────
    # The generated situating context, in its OWN column. It is MODEL OUTPUT
    # and `content` is what a candidate or a client actually wrote, so the two
    # never share a column: retrieval returns `content`, the evidence ledger's
    # `text_ref` points at `content`, and Siddhi cites `content`. A prefix
    # folded into `content` would make a model's summary quotable as a
    # candidate's own words, with nothing downstream able to tell them apart.
    # Same separation `candidate_projects` draws between `ai_interpretation_json`
    # and `evidence_json`.
    #
    # It reaches exactly two places: the string handed to the embedding model
    # (`rag/contextual.embedding_input`, which joins and discards), and the
    # `content_tsv` expression, which 0091 redefines so the lexical half of
    # retrieval gets the same benefit. A tsvector is stemmed lexemes in a
    # search index; nothing renders it and nothing quotes it.
    context_prefix: Mapped[str | None] = mapped_column(Text)
    # The same provenance pair, for the same reason, as `embedding_model`
    # above: the text of a prefix says nothing about what produced it, and this
    # index will outlive more than one model id.
    prefix_model: Mapped[str | None] = mapped_column(Text)
    prefix_generated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
