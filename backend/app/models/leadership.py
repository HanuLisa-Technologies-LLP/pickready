"""Leadership Intelligence: what the company's leaders want from hires.

Owner spec 2026-09-29, sections 16 to 21, migration 0135. The rework of
Drishti (spec rule 37.2: there is no second leadership system beside it).

THREE AUTHORS, THREE STREAMS, EVERY SAVE A NEW VERSION
------------------------------------------------------
A CEO and an MD each write company-wide hiring requirements and, optionally,
an expectation per department (`LeadershipDepartmentExpectation`, a child of
the version it was saved with). A Functional Head writes their OWN
department's requirements and ideal-employee expectations. Each is a stream
of versions: the CEO stream and the MD stream per tenant, and one Functional
Head stream per department. The latest version of a stream is the one in
force (spec 18.3: "do not overwrite leadership history invisibly").

INSERT-ONLY IN THE DATABASE (migration 0135): UPDATE is revoked and a trigger
refuses every UPDATE and every DELETE that is not a cascade. A row here is a
leader's own words and the record of which version shaped a job.

`compiled_json` is the ONLY thing downstream code reads
(`services/leadership/compiler`): deterministic, model-free and bounded, the
two properties Drishti's compiler held and the 2026-09-09 removal demanded.
The raw text is the leader's, shown back to them and never sent to a model
that grades a candidate.

`JobLeadershipContext` is the job's frozen leadership context, written by Save
Skills (`services/skills.save`) and named by the contract snapshot, so a
candidate who has applied is never assessed against a leadership version saved
after the freeze (spec 21, rule 37.9).
"""
from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

__all__ = [
    "AUTHOR_CEO",
    "AUTHOR_FUNCTIONAL_HEAD",
    "AUTHOR_MD",
    "AUTHOR_ROLES",
    "JobLeadershipContext",
    "LeadershipDepartmentExpectation",
    "LeadershipProfile",
]

AUTHOR_CEO = "ceo"
AUTHOR_MD = "md"
AUTHOR_FUNCTIONAL_HEAD = "functional_head"
#: Mirrored by `ck_leadership_profiles_role`.
AUTHOR_ROLES: tuple[str, ...] = (AUTHOR_CEO, AUTHOR_MD, AUTHOR_FUNCTIONAL_HEAD)


class LeadershipProfile(Base, UUIDPKMixin, CreatedAtMixin):
    """One saved version of one leader's input."""

    __tablename__ = "leadership_profiles"
    __table_args__ = (
        ForeignKeyConstraint(
            ["department_id", "tenant_id"],
            ["company_departments.id", "company_departments.tenant_id"],
            name="fk_leadership_profiles_department_same_tenant",
        ),
        CheckConstraint(
            "(author_role = 'functional_head') = (department_id IS NOT NULL)",
            name="ck_leadership_profiles_department_iff_functional_head",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    #: Who pressed Save. NULL only on a row carried across from Drishti whose
    #: author could not be read.
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", name="fk_leadership_profiles_author")
    )
    #: The AUTHOR'S role at save time, which decides the stream. Taken from the
    #: user row, never from a request (a CEO cannot write the MD's stream).
    author_role: Mapped[str] = mapped_column(String(24), nullable=False)
    #: A Functional Head's department, taken from `users.department_id` at save
    #: time. NULL for the CEO and the MD.
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    #: 1, 2, ... within the stream. The latest is in force.
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    company_requirements: Mapped[str | None] = mapped_column(Text)
    department_requirements: Mapped[str | None] = mapped_column(Text)
    ideal_employee_expectations: Mapped[str | None] = mapped_column(Text)
    #: `services/leadership/compiler.compile_profile`'s artifact.
    compiled_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    #: Whether an AI draft was generated before this save and which sources it
    #: read. Provenance only: the saved text is the leader's.
    draft_provenance_json: Mapped[dict | None] = mapped_column(JSONB)


class LeadershipDepartmentExpectation(Base, UUIDPKMixin, CreatedAtMixin):
    """A CEO's or MD's expectation for one department, in one version."""

    __tablename__ = "leadership_department_expectations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["profile_id", "tenant_id"],
            ["leadership_profiles.id", "leadership_profiles.tenant_id"],
            ondelete="CASCADE",
            name="fk_leadership_expectations_profile_same_tenant",
        ),
        ForeignKeyConstraint(
            ["department_id", "tenant_id"],
            ["company_departments.id", "company_departments.tenant_id"],
            name="fk_leadership_expectations_department_same_tenant",
        ),
    )

    profile_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    department_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    requirements: Mapped[str] = mapped_column(Text, nullable=False)
    compiled_json: Mapped[dict] = mapped_column(JSONB, nullable=False)


class JobLeadershipContext(Base, UUIDPKMixin, CreatedAtMixin):
    """The compiled leadership context one save of a job's skills was made with."""

    __tablename__ = "job_leadership_contexts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["ceo_profile_id", "tenant_id"],
            ["leadership_profiles.id", "leadership_profiles.tenant_id"],
            name="fk_job_leadership_ceo_profile",
        ),
        ForeignKeyConstraint(
            ["md_profile_id", "tenant_id"],
            ["leadership_profiles.id", "leadership_profiles.tenant_id"],
            name="fk_job_leadership_md_profile",
        ),
        ForeignKeyConstraint(
            ["functional_head_profile_id", "tenant_id"],
            ["leadership_profiles.id", "leadership_profiles.tenant_id"],
            name="fk_job_leadership_fh_profile",
        ),
        ForeignKeyConstraint(
            ["department_id", "tenant_id"],
            ["company_departments.id", "company_departments.tenant_id"],
            name="fk_job_leadership_department_same_tenant",
        ),
    )

    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    department_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    #: 1, 2, ... per job, one per Save Skills that had a context.
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    ceo_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    md_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    functional_head_profile_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    #: `services/leadership/context.LeadershipContext.as_json`.
    compiled_context_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    #: {job_competencies.id: source} for every saved skill whose draft source
    #: was recorded, so "which skills came from leadership" is frozen too.
    skill_sources_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    #: sha256 over the compiled context and the skill sources.
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
