"""Agent framework tables: episodic traces and extracted learnings (0055)."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

STATUS_SUCCESS = "success"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"


class AgentExecutionTrace(Base, UUIDPKMixin, CreatedAtMixin):
    """One agent run, recorded in identifiers and numbers only.

    No prompt, answer or remark text reaches this table. `stages` holds the
    per-stage timings and statuses; `defects` holds the typed defect objects
    `agent_loop` already produces, which are instructions rather than content.
    """

    __tablename__ = "agent_execution_traces"

    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: NULL for platform-level work that belongs to no customer.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE")
    )
    agent_type: Mapped[str] = mapped_column(String(40), nullable=False)
    task_type: Mapped[str] = mapped_column(String(40), nullable=False)
    job_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    link_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    complexity: Mapped[str | None] = mapped_column(String(20))
    fast_path: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generated_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stages: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    defects: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    failure_category: Mapped[str | None] = mapped_column(String(40))


class AgentLearning(Base, UUIDPKMixin, CreatedAtMixin):
    """A failure pattern and the adjustment that fixed it.

    Not tenant-scoped: a lesson about how an agent misreads a JD is a property
    of the product, and scoping it per customer would mean every customer
    relearns it separately.
    """

    __tablename__ = "agent_learnings"

    agent_type: Mapped[str] = mapped_column(String(40), nullable=False)
    task_type: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The defect type that triggered it, e.g. "remark_word_count".
    failure_pattern: Mapped[str] = mapped_column(String(120), nullable=False)
    #: The instruction to prepend on a later attempt at the same task.
    applied_fix: Mapped[str] = mapped_column(Text, nullable=False)
    observations: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    successes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def success_rate(self) -> float:
        return round(self.successes / self.observations, 4) if self.observations else 0.0
