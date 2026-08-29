"""Part A's data model (spec-doc5 §A.4).

spec-doc5 asks for "the core objects from Runbook §59 (`Role`, `Scorecard`,
`Candidate`, `Evaluation`, `CalibrationRecord`) as your PostgreSQL schema
baseline for this layer, extended with a `CompanyDNA` object ... and a `Claim` /
`EvidenceNode` pair".

THREE OF THOSE FIVE ALREADY EXIST UNDER OTHER NAMES, AND THEY ARE NOT DUPLICATED
---------------------------------------------------------------------------------
    Runbook §59      this schema
    ---------------  ------------------------------------------------------
    Role             `jobs` -- already carries the JD, the grade, the
                     experience band, the posting window, the tenant
    Scorecard        `job_competencies` -- already one row per matrix item,
                     already UNIQUE on (job, category, name), already soft
                     -deleted rather than removed because a written report
                     references it
    Candidate        `candidates` + `profiles` + `job_candidate_links`
    Claim            `evidence_claims` (migration 0056)
    EvidenceNode     `evidence_items` (migration 0056)

Creating parallel tables for those would have produced two answers to "what is
this job's matrix", and the second one would have been discovered by whoever
first read a report generated from the wrong one. This is the same substitution
the billing work already made when the spec wrote `companies` and the schema
meant `tenants`.

SO WHAT IS ACTUALLY NEW HERE IS THREE THINGS:

    CompanyDNA          Layer 2. Client-scoped, versioned, referenced by every
                        job under that client. Nothing like it existed.
    Evaluation          The Miti run itself, with `dimension_scores` and
                        `competency_scores` carrying `evidence_refs[]`. The
                        existing `functional_skills_reports` is the DELIVERED
                        artifact; this is the WORKING that produced it, and they
                        are different objects with different lifetimes -- a
                        report is immutable and client-facing, an evaluation is
                        internal and is what a rescore would replace.
    ReviewDisposition   G4. Who looked at a flag and what they decided.
    CalibrationRecord   Whether the pipeline's grades track reality.

`evidence_refs[]` IS NOT RETROFITTABLE AND THAT IS WHY IT IS HERE
------------------------------------------------------------------
spec-doc5: "Every `Evaluation.competency_scores` and `dimension_scores` entry
must carry `evidence_refs[]` -- this is what makes Siddhi's citation enforcement
possible; it cannot be retrofitted onto a schema that doesn't track provenance
from the start."

That is exactly right and it is worth saying why. Citation enforcement is a
check that every delivered statement traces to an evidence node. If the
evaluation stored only its conclusions, the refs would have to be reconstructed
at report time from the transcript -- which is guesswork dressed as provenance,
and would confidently attach the wrong evidence to the right conclusion.

EVERY TABLE HERE IS TENANT-SCOPED AND UNDER RLS
-------------------------------------------------
Same rule as everything else: the Postgres RLS policy is the real boundary and
app-level filtering is defence in depth, not a substitute (claude.md rule 1).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

__all__ = [
    "CompanyDNA",
    "Evaluation",
    "ReviewDisposition",
    "CalibrationRecord",
]


class CompanyDNA(Base, UUIDPKMixin, CreatedAtMixin):
    """LAYER 2: one client's hiring philosophy, compiled and versioned.

    VERSIONED RATHER THAN UPDATED IN PLACE, and the reason is the same one that
    makes a report immutable. Every job generated under version 3 was scored
    against version 3's weights and thresholds; overwriting the row would make
    "what criteria was this candidate actually graded under" unanswerable for
    every job already run. `is_current` marks the one new jobs read; the others
    stay, unread, exactly as `technical_questions` does.

    `answers_json` is the RAW intake and `artifact_json` is the COMPILED output.
    Both are kept and they are not interchangeable: Sutra reads only the
    artifact (spec-doc5 §A.3 is explicit that it must "never [read] the client's
    free-text preferences directly"), while the answers are what a recompilation
    runs over when the compiler itself changes.

    The one-per-tenant-per-version uniqueness is what stops a double-submit from
    creating two version 4s that disagree.
    """

    __tablename__ = "company_dna"
    __table_args__ = (
        UniqueConstraint("tenant_id", "version", name="uq_company_dna_version"),
        Index("ix_company_dna_current", "tenant_id", "is_current"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: Exactly one row per tenant carries this. Enforced by a partial unique
    #: index in the migration rather than here, because SQLAlchemy cannot
    #: express `WHERE is_current` in a UniqueConstraint.
    is_current: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    #: draft while the session is running, complete once compiled.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )
    #: ON DELETE SET NULL: an HR manager can leave, and their departure must not
    #: take with it the philosophy every job for that client is built on. Same
    #: reasoning as `job_swot_intakes.conducted_by`.
    conducted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    #: {question_key: answer}. The RAW intake.
    answers_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: `company_dna.CompanyDNA.as_dict()`. What Sutra reads.
    artifact_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: What was SAID. Kept for the same reason the SWOT transcript is: the
    #: artifact is what the system uses and the transcript is what a person can
    #: check it against.
    transcript_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    #: The question the client is currently looking at. Same
    #: `pending_prompt` mechanism `JobSwotIntake` and `AssessmentConversation`
    #: use, so the transcript records what was actually read rather than what
    #: was next in the list.
    pending_prompt: Mapped[str | None] = mapped_column(Text)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Evaluation(Base, UUIDPKMixin, CreatedAtMixin):
    """One Miti run over one candidate against one job's scorecard.

    NOT `functional_skills_reports`, AND THE DIFFERENCE MATTERS. That table is
    the DELIVERED artifact: immutable, client-facing, one per assessment, and
    explicitly forbidden from being edited or deleted. This is the WORKING --
    the five dimension bands, the evidence each cited, the contradictions found,
    the gate verdicts. Internal, and legitimately replaceable if a rescore runs.

    Keeping them in one table would have forced a choice between making the
    working immutable (so a rescore could never correct anything) or making the
    report mutable (which breaks the product's oldest rule).

    `scorecard_version` is COPIED, never joined. Same rule
    `report_dimensions.required_level` follows: an evaluation is a permanent
    record of the criteria it was run against, and the job's matrix may be
    edited afterwards.
    """

    __tablename__ = "evaluations"
    __table_args__ = (
        Index("ix_evaluations_link", "link_id", "created_at"),
        Index("ix_evaluations_tenant_job", "tenant_id", "job_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    #: The APPLICATION, not the report. An evaluation exists from the moment
    #: scoring starts and the report does not exist until it finishes, so
    #: hanging this off the report would make the failed-scoring case -- the one
    #: an operator most wants -- unreachable. Same reasoning as the transcript
    #: route being keyed on the link.
    link_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("job_candidate_links.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: The report this produced, once there is one. NULL while scoring is in
    #: flight or if it never completed.
    report_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("functional_skills_reports.id", ondelete="SET NULL")
    )
    #: The matrix version this was run against. COPIED.
    scorecard_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    #: The Company DNA version in force at the time. Also copied, so a client
    #: who later changes their philosophy does not retroactively change what an
    #: existing evaluation was run under.
    company_dna_version: Mapped[int | None] = mapped_column(Integer)
    situation_type: Mapped[str | None] = mapped_column(String(30))

    #: {dimension: {band, evidence_refs[], insufficient_evidence}}.
    #: `evidence_refs` is what makes citation enforcement possible and is why
    #: this cannot be retrofitted.
    dimension_scores: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: {competency: {band, evidence_refs[]}}. Same rule.
    competency_scores: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: `aggregation.Aggregate.as_dict()`. INTERNAL: carries scores, and must
    #: never be returned by a report route.
    aggregate_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: `TriangulationResult.as_dict()`.
    triangulation_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: [{gate, passed, blocking, reasons[]}]. Every gate, every run. A gate
    #: whose verdict was not recorded is a gate nobody can audit.
    gate_results_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    #: full | degraded | stub, mirroring `reliability.degradation`. A stub is
    #: always flagged for human review; what makes that honest rather than
    #: misleading is that it says so here.
    scoring_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="full", server_default="full"
    )
    confidence: Mapped[str | None] = mapped_column(String(10))
    needs_human_review: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewDisposition(Base, UUIDPKMixin, CreatedAtMixin):
    """G4: a person looked at a flag and decided something.

    THE EXISTENCE OF THIS TABLE IS THE PROOF OF THE NO-AUTO-REJECT RULE.
    spec-doc5's acceptance criterion is "No flag has ever caused an
    auto-rejection; every flag has a human disposition recorded", and that is
    answerable only if there is somewhere the human decision lives.

    `decided_by` IS NOT NULLABLE, and that is the whole design. Every other
    user reference in this schema is `ON DELETE SET NULL` so a departure cannot
    destroy data -- here it is `RESTRICT`, because a disposition whose person
    has been erased is a row asserting that a human decided while being unable
    to say who, which is indistinguishable from the pipeline having written it
    itself. A user with dispositions cannot be hard-deleted; they are disabled,
    which is what the product does anyway.

    There is no `auto_cleared` disposition and there must never be one.
    """

    __tablename__ = "review_dispositions"
    __table_args__ = (
        Index("ix_review_dispositions_evaluation", "evaluation_id", "created_at"),
        Index("ix_review_dispositions_tenant", "tenant_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    #: SET NULL, not CASCADE, and nullable since migration 0062. The cascade
    #: that was here walked straight past the `decided_by` RESTRICT below: it
    #: protected the row against losing its author and left the whole row
    #: deletable by a machine artifact being replaced. Deleting an evaluation
    #: must never delete the record that a person looked at it.
    evaluation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evaluations.id", ondelete="SET NULL")
    )
    #: The candidate and job this decision was about, copied so the decision
    #: survives the evaluation. RBAC section 29 requires a human remark to
    #: preserve author, timestamp, candidate and job context, and before 0062
    #: the last two were reachable only THROUGH the evaluation.
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="SET NULL")
    )
    link_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("job_candidate_links.id", ondelete="SET NULL")
    )
    #: The evaluation identifier as a plain value, kept when the reference is
    #: nulled so the surviving decision can be joined by hand to the export the
    #: purge wrote. A detachment that left no trace is indistinguishable from a
    #: row that never had a reference, which is why the next two exist.
    evaluation_ref: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    detached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detached_note: Mapped[str | None] = mapped_column(Text)
    #: cleared | escalated | overridden | rejected (`hiring.gates.DISPOSITIONS`).
    disposition: Mapped[str] = mapped_column(String(20), nullable=False)
    #: RESTRICT, not SET NULL. See the class docstring.
    decided_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    #: Which flags this decision covers, copied from the evaluation at the time.
    #: Copied rather than joined so a later rescore cannot silently change what
    #: the reviewer is recorded as having seen.
    flags_json: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    note: Mapped[str | None] = mapped_column(Text)


class CalibrationRecord(Base, UUIDPKMixin, CreatedAtMixin):
    """Did the grade turn out to be right?

    Runbook §59's fifth object, and the only one that closes a loop with
    reality. Everything else in this schema records what the pipeline concluded;
    this records what happened afterwards -- was the candidate interviewed, were
    they hired, did they work out.

    WHY IT IS SEPARATE FROM THE PIPELINE STATUS. `pipeline_status` already
    tracks the hiring stages, and this could in principle be derived from it.
    It is not, for two reasons: the useful outcome arrives MONTHS after the
    final pipeline transition (nobody knows at offer-accepted whether the hire
    worked), and it is a JUDGMENT somebody enters rather than a state machine
    transition.

    NOTHING READS THIS YET, and that is stated rather than hidden. It is the
    substrate for calibration analysis -- do candidates the pipeline graded
    Highly Matching actually get hired more often -- and that analysis needs a
    volume of outcomes that does not exist yet. Writing the table now is what
    makes the analysis possible later; deriving it retrospectively would not be.
    """

    __tablename__ = "calibration_records"
    __table_args__ = (
        UniqueConstraint("evaluation_id", name="uq_calibration_evaluation"),
        Index("ix_calibration_tenant_job", "tenant_id", "job_id"),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    #: SET NULL, not CASCADE, and nullable since migration 0062. An outcome
    #: assessment is a person's judgment entered months later; purging the
    #: prediction it was measured against must not delete the measurement.
    #: `job_id` above is already copied, so the row keeps its context.
    evaluation_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evaluations.id", ondelete="SET NULL")
    )
    evaluation_ref: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    detached_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detached_note: Mapped[str | None] = mapped_column(Text)
    #: The grade the pipeline gave. Copied, so a rescore cannot rewrite history.
    predicted_grade: Mapped[str] = mapped_column(String(30), nullable=False)
    predicted_confidence: Mapped[str | None] = mapped_column(String(10))
    #: interviewed | offered | hired | rejected | withdrew.
    outcome: Mapped[str | None] = mapped_column(String(20))
    #: A human's later judgment: was the grade right? too high? too low?
    outcome_assessment: Mapped[str | None] = mapped_column(String(20))
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    note: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
