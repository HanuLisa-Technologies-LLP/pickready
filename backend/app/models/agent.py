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
    """A failure pattern and the adjustment that fixed it, per tenant.

    TENANT-SCOPED SINCE MIGRATION 0089, AND THE OLD REASONING IS RECORDED
    RATHER THAN DELETED. This class used to say: a lesson about how an agent
    misreads a JD is a property of the product, and scoping it per customer
    would mean every customer relearns it separately. That is true of the
    LESSON and false of its INPUT. The pattern is extracted from a run whose
    prompt carried a real candidate's answers and a real client's job
    description, so an unscoped table is a path by which a hostile paragraph in
    one customer's resume upload shapes the guidance prepended to another
    customer's grading. RPN-AI-UP-001 W3.5 names that as the single place one
    tenant's untrusted input can reach another tenant's decision, and
    `services/memory/provenance.py` carries the decision that closed it.

    The cost is exactly what the old docstring warned of: a genuine
    product-wide lesson is relearned per tenant. That buys repetition. Sharing
    buys a cross-tenant influence channel.
    """

    __tablename__ = "agent_learnings"

    #: NOT NULL. A learning that cannot say which tenant it came from is the
    #: learning the scope exists to stop.
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
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

    # ── Provenance (W3.5) ────────────────────────────────────────────────────
    #: What produced this, by name. The handle a revocation grabs:
    #: `memory.experience.revoke_learnings_from_source` matches this exactly.
    source: Mapped[str] = mapped_column(String(60), nullable=False)
    #: Which version of that producer. A prompt fingerprint from
    #: `memory.procedural.source_version`, a release tag, a schema version.
    source_version: Mapped[str | None] = mapped_column(String(64))
    #: The human the run acted under, when there was one. ON DELETE SET NULL,
    #: because the learning outlives the person and a row that vanished with
    #: them would take its own observation count with it.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    #: Who authored the text this was extracted from, not how good it is.
    #: Vocabulary in `memory.provenance`, pinned by a database CHECK.
    trust_level: Mapped[str] = mapped_column(String(24), nullable=False)
    #: Identifiers of the evidence, never content. A quoted answer here would
    #: put one candidate's words in a table read to build another's prompt.
    evidence_ids: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    #: When this stops being applied unless it is seen again. NOT NULL: a
    #: learning with no expiry is one that outlives whatever made it true.
    revalidate_after: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    @property
    def success_rate(self) -> float:
        return round(self.successes / self.observations, 4) if self.observations else 0.0


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
