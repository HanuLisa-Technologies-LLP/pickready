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

    # ── The §18.2 session protocol, persisted (migration 0064) ───────────────
    #
    # The intake is resumable, so every part of the protocol that is not one of
    # the four arrays has to survive a page reload. Derived state would be
    # worse than a column here for the same reason `area_index` is a column: a
    # phase recomputed from the arrays would send a manager who genuinely had
    # nothing more to add back round to the same question forever.

    #: Which block of §18.2's timeline the session is in. The vocabulary lives
    #: in migration 0064 and the CHECK constraint it created: the module that
    #: defined it went with the retired Role Intake conversation on
    #: 2026-09-20, and this table now holds only that conversation's history.
    phase: Mapped[str] = mapped_column(
        String(20), nullable=False, default="areas", server_default="areas"
    )
    #: One of `hiring.situations.SITUATIONS`, once the hiring manager has
    #: CONFIRMED it. Proposed classifications are not written here: §18.4 calls
    #: misclassification the most expensive error available at intake, and a
    #: proposal stored in the same column as a confirmation is a proposal that
    #: will eventually be read as one.
    situation_key: Mapped[str | None] = mapped_column(String(30))
    situation_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    #: `swot_quality.HIGH_VALUE_PROBES` keys already put to the manager. §18.3's
    #: seven, asked once each and in order.
    probes_asked: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    #: §18.5's best-performer test. THREE-VALUED: True refuses the intake, False
    #: accepts it, and NULL means the question has not been put -- which is a
    #: different state from "no" and must never be read as a pass.
    best_performer_excluded: Mapped[bool | None] = mapped_column(Boolean)
    #: The last `swot_quality.review` verdict, as `QualityReport.as_dict()`.
    quality_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: The job's correlation id, copied so this session can be tied to the flow
    #: it belongs to without a join (spec-doc6 §4.1).
    correlation_id: Mapped[str | None] = mapped_column(String(64))

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


# ── The AI-assisted Job SWOT Analysis (2026-09-13 spec, sections 23 to 33) ───

#: The lifecycle the UI renders (section 30). `edited` is reached the first
#: time a human saves and is never left, because "a human has been here" is
#: not something a later regeneration can undo.
SWOT_ANALYSIS_NOT_GENERATED = "not_generated"
SWOT_ANALYSIS_GENERATED = "generated"
SWOT_ANALYSIS_FAILED = "failed"
SWOT_ANALYSIS_EDITED = "edited"

#: The four quadrants of the analysis document, in render order.
SWOT_ANALYSIS_SECTIONS: tuple[str, ...] = (
    "strengths", "weaknesses", "opportunities", "threats",
)


class JobSwotAnalysis(Base, UUIDPKMixin, CreatedAtMixin):
    """One recruitment-facing SWOT document per job: AI drafts, humans own it.

    WHY THIS IS NOT `JobSwotIntake`
    --------------------------------
    `JobSwotIntake` above is a TRANSCRIPT: what the reporting authority said,
    in their own words, captured as evidence and read by Sutra when it compiles
    the Tatva matrix. Its points are the authority's and rewriting them would
    launder the evidence the matrix is derived from.

    This is a DOCUMENT: four narrative paragraphs about the role's hiring
    position, drafted by the model from everything the platform already knows
    about the job, and then edited freely by the recruitment team. Editing it
    is the point. Storing the two in one row would mean either the transcript
    becomes editable or the document does not, and both are wrong.

    They are related rather than duplicated: the intake's captured points are
    one of the generator's INPUTS when an intake exists, which is what makes
    this a derived analysis rather than a second opinion.

    HUMAN EDITS SURVIVE REGENERATION (section 26, section 32)
    ---------------------------------------------------------
    `human_edited` latches True on the first human save. A regeneration over a
    human-edited document is refused unless the caller confirms it explicitly,
    and a confirmed one snapshots what it replaced into `previous_json` first,
    so the one destructive action in this feature is both deliberate and
    recoverable. `version` increments on every write of either kind, so a
    stale editor cannot silently overwrite a newer save.
    """

    __tablename__ = "job_swot_analyses"
    __table_args__ = (
        UniqueConstraint("job_id", name="uq_job_swot_analysis_job"),
        Index("ix_job_swot_analyses_tenant", "tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )

    status: Mapped[str] = mapped_column(
        String(20), nullable=False,
        default=SWOT_ANALYSIS_NOT_GENERATED,
        server_default=SWOT_ANALYSIS_NOT_GENERATED,
    )
    strengths: Mapped[str | None] = mapped_column(Text)
    weaknesses: Mapped[str | None] = mapped_column(Text)
    opportunities: Mapped[str | None] = mapped_column(Text)
    threats: Mapped[str | None] = mapped_column(Text)

    #: "ai" once a generation has succeeded, NULL for a document a human wrote
    #: from an empty state. Section 26 asks for enough metadata to tell
    #: AI-generated content from human-edited content; this plus
    #: `human_edited` is that, and neither is inferred from a timestamp.
    generated_by: Mapped[str | None] = mapped_column(String(20))
    last_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: What the last failed generation said, for the `failed` state's message.
    #: Cleared on the next success, because a stale error under fresh content
    #: reads as a fresh error.
    generation_error: Mapped[str | None] = mapped_column(Text)

    human_edited: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    last_modified_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    last_modified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )

    #: The four sections as they stood immediately before the last confirmed
    #: regeneration over human-edited content. One snapshot, not a history:
    #: section 32 asks for "the minimum necessary safeguards to prevent
    #: accidental loss of human edits", and one undo is that minimum.
    previous_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )

    def sections(self) -> dict[str, str]:
        return {name: getattr(self, name) or "" for name in SWOT_ANALYSIS_SECTIONS}

    @property
    def has_content(self) -> bool:
        return any(self.sections().values())
