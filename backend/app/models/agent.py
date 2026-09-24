"""Agent framework tables: episodic traces (0055) and tool approval rules (0089)."""
from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String
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


# `AgentLearning` (`agent_learnings`) is UNMAPPED since the Vivekium release:
# the experience memory that was its only writer and reader could not be
# reached from any route or worker and was deleted. The TABLE is kept as
# history (S4 posture) and is still reached by raw SQL in the legacy reset
# classification and the AI baseline count.


class AgentToolApprovalRule(Base, UUIDPKMixin, CreatedAtMixin):
    """A tenant requiring a human to approve a risk class the platform would
    otherwise run automatically (0089).

    THE TABLE HAS NO BOOLEAN, AND THAT IS THE DESIGN. A row's existence is the
    requirement. A `requires_approval` column would have a False, and a False
    row would be a customer switching OFF a requirement the platform declared,
    which would make `tools.policy.RiskClass` decorative through the back door.
    There is no column here that can express "less", so no admin screen and no
    support override can ever write it. Same shape as `hiring/layers.py`:
    a lower layer may tune within declared bounds and may never suspend.
    """

    __tablename__ = "agent_tool_approval_rules"

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: One of `tools.policy.RiskClass`, pinned by a database CHECK.
    risk_class: Mapped[str] = mapped_column(String(20), nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
