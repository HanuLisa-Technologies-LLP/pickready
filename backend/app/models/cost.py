"""What one assessment cost the platform to run.

WHY A TABLE AND NOT THE ROUTER'S COUNTERS
------------------------------------------
`llm_router.model_stats()` already answers "which model is spending the
budget", and it answers it for the PROCESS, since it started. That is the right
shape for a health endpoint and the wrong shape for every question the owner
actually has about unit economics: what does one assessment cost, which client
is expensive, is the average drifting. None of those survives a container
restart, and all of them need the spend attributed to the application it was
spent on rather than to the worker that happened to run it.

ONE ROW PER APPLICATION, ACCUMULATED IN PLACE
-----------------------------------------------
Keyed UNIQUE on `job_candidate_link_id`, which is this schema's application.
The spend arrives in pieces over time -- a conversation turn today, the scoring
run when the candidate finishes -- so the row is created by whichever piece
lands first and every later piece adds to it. `services/cost_telemetry` is the
one writer and it takes a row lock, so two pieces landing together add rather
than overwrite.

EVERY FIGURE HERE IS AN ESTIMATE UNTIL SOMETHING SAYS OTHERWISE
-----------------------------------------------------------------
`cost_basis` carries that in the row rather than in a comment. `estimated` is
list price from `config/llm_providers.TOKEN_PRICES_USD_PER_MILLION`, which is
not an invoice: the rates are unverified for these two model ids, prompt
caching is recorded but not yet discounted, and the vendor's own rounding is
invisible from here. `finalized` means a real figure has been written over it.
Reporting an estimate as a fact is the failure this column exists to prevent,
and it is the same discipline `/admin/llm/stats` already states in its payload.

WHAT IS NULL AND WHY IT STAYS NULL
------------------------------------
`media_cost_usd` and `proctoring_cost_usd` are nullable and are NOT defaulted
to zero. The analysis service is a standing ECS service with a flat bill; there
is no per-assessment figure to read, so the honest value is absent. A zero
would be read as "this assessment used no media", which for a video interview
is simply false. A metric that cannot be computed reports unavailable.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

#: The two values `cost_basis` may hold, mirrored by the database CHECK. An
#: enum in Python and a constraint in SQL, never one without the other: an
#: unknown value read back into a model 500s every read of every row that
#: carries it, for that whole tenant.
COST_BASIS_ESTIMATED = "estimated"
COST_BASIS_FINALIZED = "finalized"
COST_BASIS_VALUES = (COST_BASIS_ESTIMATED, COST_BASIS_FINALIZED)


class AssessmentCostRecord(Base, UUIDPKMixin, CreatedAtMixin):
    __tablename__ = "assessment_cost_records"
    __table_args__ = (
        UniqueConstraint(
            "job_candidate_link_id", name="uq_assessment_cost_records_link"
        ),
        CheckConstraint(
            "cost_basis IN ('estimated', 'finalized')",
            name="ck_assessment_cost_records_basis",
        ),
        # The owner dashboard's only access path: a time window, grouped by
        # tenant. Both orders are useful because the month rollup is
        # cross-tenant and the per-client view is not.
        Index("ix_assessment_cost_records_created", "created_at"),
        Index("ix_assessment_cost_records_tenant", "tenant_id", "created_at"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    job_candidate_link_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_candidate_links.id", ondelete="CASCADE"),
        nullable=False,
    )

    # ── Tokens ───────────────────────────────────────────────────────────────
    #
    # BIGINT rather than INTEGER. A long assessment is already several hundred
    # thousand prompt tokens and the ceiling on an INTEGER is two billion; the
    # column is cheap and an overflow here would be a hard failure in the
    # middle of somebody's interview.
    input_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    output_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    #: Prompt-cache hits, summed over the calls that REPORTED one, and a SUBSET
    #: of `input_tokens` rather than an addition to it. Read beside
    #: `calls_reporting_cache`: a zero with a zero beside it means the endpoint
    #: said nothing about caching, which is not the same as a cache that missed.
    cached_input_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )

    # ── Call counts, which is what makes the tokens readable ─────────────────
    calls: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: Calls whose response carried a `usage` block at all. `calls` minus this
    #: is the number of calls whose tokens are a character-heuristic ESTIMATE
    #: rather than a vendor measurement, which is the one thing a reader needs
    #: in order to know how much of the total was measured.
    calls_with_usage: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    calls_reporting_cache: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # ── Siddhi's own line ────────────────────────────────────────────────────
    #
    # Report synthesis is seven sections in one response on the reasoning tier
    # and is routinely the single largest call in an assessment, so it is
    # broken out rather than buried in the total. Attributed by TASK TYPE
    # (`report_synthesis`) rather than by a phase label a caller has to
    # remember to set: the task type is already on every call.
    synthesis_input_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    synthesis_output_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    synthesis_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(14, 6), nullable=False, default=Decimal("0"), server_default="0"
    )

    # ── Vaada ────────────────────────────────────────────────────────────────
    #: Answered turns, counted once per request that processed one. Incremental
    #: because the assessment is spread over many requests.
    conversation_turns: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: Base questions reached, an ABSOLUTE reading taken from the conversation
    #: rather than a count of turns. The two differ, deliberately: a follow-up
    #: and a re-ask are turns that advance no question, and conflating them
    #: would make the question count disagree with the candidate's own progress
    #: label.
    questions_asked: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    # ── Money ────────────────────────────────────────────────────────────────
    estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(14, 6), nullable=False, default=Decimal("0"), server_default="0"
    )
    #: NULL means NOT MEASURABLE, never "free". See the module docstring.
    media_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    proctoring_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    cost_basis: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=COST_BASIS_ESTIMATED,
        server_default=COST_BASIS_ESTIMATED,
    )
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ── Reproducibility ──────────────────────────────────────────────────────
    #: The tenant's plan slug AT THE TIME, snapshotted rather than joined. A
    #: customer who upgrades next month must not retroactively change what
    #: their assessments last month are reported under.
    pricing_tier: Mapped[str | None] = mapped_column(String(50))
    #: Per-model token and cost breakdown: `{model: {calls, input_tokens,
    #: output_tokens, cached_input_tokens, estimated_cost_usd}}`. This is what
    #: makes the total reproducible rather than merely believable.
    models_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: The provider and the exact per-million rates that were applied, so a row
    #: can be repriced when the rates are confirmed or corrected. Without it a
    #: historical total is unexplainable the first time the price table is
    #: edited, and the price table is explicitly expected to be edited.
    pricing_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: Why a figure is what it is, when there is something to say. Free text on
    #: purpose and read by a person, never branched on.
    notes: Mapped[str | None] = mapped_column(Text)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
