"""The record of one candidate erasure, from request to verified completion.

Migration 0108. The state machine itself is `services/deletion_requests`, kept
separate for the reason `consent_lifecycle` is separate from the sweep that
uses it: the dangerous half is then testable without a database, a scheduler or
an object store.

This row survives its own subject. `candidate_id` carries NO foreign key,
because the candidate is deleted a few statements after the request is opened
and a cascade would take away the only thing that still knows which objects
have to go.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin


class CandidateDeletionRequest(Base, UUIDPKMixin):
    """One erasure, and everything needed to finish it after a failure."""

    __tablename__ = "candidate_deletion_requests"
    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'rows_erased', 'completed')",
            name="ck_candidate_deletion_requests_state",
        ),
        CheckConstraint(
            "objects_deleted >= 0 AND objects_total >= 0 "
            "AND objects_deleted <= objects_total",
            name="ck_candidate_deletion_requests_counts",
        ),
        CheckConstraint(
            "state <> 'completed' OR ("
            "rows_erased_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND objects_deleted = objects_total)",
            name="ck_candidate_deletion_requests_completed",
        ),
        Index(
            "ix_candidate_deletion_requests_candidate",
            "candidate_id",
        ),
    )

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False
    )
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str] = mapped_column(String(60), nullable=False)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    rows_erased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: `[{"key": "...", "kind": "..."}]`, captured BEFORE the rows are erased.
    #: An enumeration run afterwards returns nothing, because nothing in the
    #: database names these objects any more.
    object_keys_json: Mapped[list[dict[str, str]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    objects_total: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    objects_deleted: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    deletion_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: An exception CLASS NAME and a count. Never a payload: a message can quote
    #: a row, and this is the one record that outlives the rows.
    last_failure: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
