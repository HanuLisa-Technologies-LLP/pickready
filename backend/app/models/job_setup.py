"""The two job-setup inputs the Draft v4 agent layer added.

Both are generated once per job and finalised by a human, and both feed work
that then runs with no further human involvement:

  `JobSwotIntake`        what the reporting authority said the role demands,
                         captured as Strengths / Weaknesses / Opportunities /
                         Threats. Read by the PPI agent alongside the JD.
  `JobMatchingCategory`  the job's own Matching category list. Matching is no
                         longer four parameters fixed across the whole product.
"""
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

#: The four areas, in the order the intake asks them. The PPI agent reads all
#: four; the conversation walks them in this order so a half-finished intake is
#: half-finished in a predictable place.
SWOT_AREAS: tuple[str, ...] = ("strengths", "weaknesses", "opportunities", "threats")

SWOT_STATUS_ACTIVE = "active"
SWOT_STATUS_COMPLETE = "complete"


class JobSwotIntake(Base, UUIDPKMixin, CreatedAtMixin):
    """One reporting-authority SWOT intake, one per job (spec §5.1).

    The four arrays are what was CAPTURED and `transcript_json` is what was
    SAID. The PPI agent reads only the arrays, so changing how the conversation
    is conducted cannot change what the matrix is generated from.

    `conducted_by` is ON DELETE SET NULL: a hiring manager can leave the
    company, and their departure must not take with it the criteria every
    candidate on the job is graded against.
    """

    __tablename__ = "job_swot_intakes"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_job_swot_intake_job"),
        Index("ix_job_swot_intakes_tenant", "tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    conducted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SWOT_STATUS_ACTIVE, server_default=SWOT_STATUS_ACTIVE
    )
    #: 0..3 while walking the areas, 4 once every area is captured. Persisted
    #: rather than derived from the arrays: an area the authority genuinely had
    #: nothing to add to would otherwise send the conversation back round to it
    #: forever.
    area_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    follow_ups_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    strengths: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    weaknesses: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    opportunities: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    threats: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default="[]")
    transcript_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    #: The question the authority is currently looking at. Written when the
    #: previous answer is submitted and answered on the next request, the same
    #: shape `AssessmentConversation.delivered_prompt` uses and for the same
    #: reason: the transcript must record what was actually read.
    pending_prompt: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def captured(self) -> dict[str, list]:
        """The four areas as the PPI agent reads them."""
        return {area: list(getattr(self, area) or []) for area in SWOT_AREAS}

    def is_empty(self) -> bool:
        return not any(self.captured().values())


class JobMatchingCategory(Base, UUIDPKMixin, CreatedAtMixin):
    """One category the job's resumes are matched against (spec §3.2).

    `key` is a stable slug the scorer files scores under, so renaming a
    category's display name during review does not orphan the scores already
    written against it. `name` is what the recruiter and the client read.

    Editing is soft (`is_active`), because a score written against this category
    may already exist on a ranked candidate.
    """

    __tablename__ = "job_matching_categories"
    __table_args__ = (
        UniqueConstraint("job_id", "key", name="uq_job_matching_category_key"),
        Index("ix_job_matching_categories_job", "job_id", "ordinal"),
        Index("ix_job_matching_categories_tenant", "tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(60), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
