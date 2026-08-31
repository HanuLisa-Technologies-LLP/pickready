"""The versions a candidate is evaluated against, and the only legal way to change them.

WHAT RBAC 22 AND spec-doc6 5 ACTUALLY ASK FOR
-----------------------------------------------
"A candidate's evaluation context references the exact versions in force when
they applied." That is a POINT-IN-TIME question, and the codebase answered a
different one. `evaluations.scorecard_version` is copied at SCORING time, which
is the right discipline applied to the wrong instant: a criteria revision landing
between the moment somebody applies and the moment they are scored silently
regrades them against rules that did not exist when they chose to apply.

Nothing about that failure is visible afterwards. The evaluation carries a
version, the version is real, the row looks correct, and the only way to notice
is to compare two timestamps nobody thought to compare. So the resolution is
done here, once, against `job_company_dna_bindings.frozen_at` -- an append-only
table whose whole design is "what was this job built on when I applied".

WHY THIS IS NOT A JOIN AT READ TIME
-------------------------------------
Because the answer must survive the row it was derived from. `EvaluationContext`
is resolved once and COPIED onto the evaluation, exactly as
`report_dimensions.required_level` is copied onto a report. A join would give a
different answer every time the job is re-frozen, which is the property this
whole family of columns exists to avoid.

THE REVISION WORKFLOW, AND WHY IT IS HERE AND NOT INVENTED LATER
------------------------------------------------------------------
spec-doc6 5: "Post-finalisation changes use an explicit controlled revision
workflow that preserves authorship and auditability (RBAC 12). No silent
mutation. Implement the revision workflow now; it is currently underspecified in
the repo and will otherwise be invented ad hoc later."

The workflow is three rules, and all three are refusals rather than conventions:

  * A revision NEVER mutates a finalised version. It supersedes it with a new
    one, and the old version remains readable, because a candidate evaluated
    under it needs it to still exist.
  * A revision names its AUTHOR and its REASON. An unattributed criteria change
    on a job people have already applied to is indistinguishable from the
    pipeline having changed its own rules.
  * A revision is REFUSED outright once anyone has been assessed against the
    frozen matrix, unless it is taken as an explicit new version that future
    applicants are evaluated under. Two candidates on one job graded against
    different criteria without the versions saying so is the exact failure the
    frozen matrix exists to prevent.

NOTHING HERE WRITES. `plan_revision` returns a `Revision` describing what must
happen; the module that owns the table performs it. That is deliberate: this
package would otherwise become a second writer of the scorecard, and the rule
against two implementations of one concept is the rule `tiers.py` broke.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.agents import provenance

logger = logging.getLogger("pickready.orchestration")

__all__ = [
    "EvaluationContext",
    "Revision",
    "RevisionRefused",
    "UnresolvableContext",
    "REVISION_REASONS",
    "plan_revision",
    "resolve_for_application",
]


class UnresolvableContext(RuntimeError):
    """No version was in force when this candidate applied.

    RAISED, never defaulted to "the current one". Falling back to the current
    version is the silent regrade this module exists to prevent, and it would be
    invisible: the evaluation would carry a real version number that simply was
    not the one the candidate applied under.
    """


@dataclass(frozen=True)
class EvaluationContext:
    """Every version one candidate's evaluation is anchored to.

    Frozen, and copied onto the evaluation rather than joined at read time. A
    context that could be recomputed later would answer differently after the
    next re-freeze, which is the whole failure.
    """

    job_id: str
    link_id: str
    #: When the candidate applied. The instant every version below is resolved
    #: AS OF -- carried so the resolution can be re-checked by hand.
    applied_at: datetime
    scorecard_version: int
    company_dna_version: int | None
    company_dna_id: str | None
    freeze_sequence: int
    frozen_at: datetime | None
    correlation_id: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "link_id": self.link_id,
            "applied_at": self.applied_at.isoformat(),
            "scorecard_version": self.scorecard_version,
            "company_dna_version": self.company_dna_version,
            "company_dna_id": self.company_dna_id,
            "freeze_sequence": self.freeze_sequence,
            "frozen_at": self.frozen_at.isoformat() if self.frozen_at else None,
            "correlation_id": self.correlation_id,
        }


async def resolve_for_application(
    session: AsyncSession, link_id: uuid.UUID | str
) -> EvaluationContext:
    """The versions in force at the moment this candidate applied.

    The query is the specification: the binding with the greatest `frozen_at`
    that is not AFTER the application's `created_at`. A later freeze is
    invisible to this candidate by construction, so a criteria revision cannot
    reach backwards into an application that predates it.

    `frozen_at <= applied_at` rather than `<`: a freeze and an application in
    the same instant means the criteria were in force when the person applied,
    and ties go to the candidate, which is the boundary rule this codebase
    already follows everywhere else.
    """
    row = (
        await session.execute(
            text(
                """
                SELECT l.id            AS link_id,
                       l.job_id        AS job_id,
                       l.created_at    AS applied_at,
                       b.scorecard_version,
                       b.company_dna_version,
                       b.company_dna_id,
                       b.freeze_sequence,
                       b.frozen_at,
                       b.correlation_id
                  FROM job_candidate_links l
             LEFT JOIN LATERAL (
                       SELECT *
                         FROM job_company_dna_bindings b2
                        WHERE b2.job_id = l.job_id
                          AND b2.frozen_at IS NOT NULL
                          AND b2.frozen_at <= l.created_at
                     ORDER BY b2.frozen_at DESC, b2.freeze_sequence DESC
                        LIMIT 1
                       ) b ON TRUE
                 WHERE l.id = :link_id
                """
            ),
            {"link_id": str(link_id)},
        )
    ).mappings().first()

    if row is None:
        raise UnresolvableContext(
            f"application {link_id} does not exist, so there is no moment to "
            "resolve its evaluation context as of."
        )
    if row["scorecard_version"] is None:
        raise UnresolvableContext(
            f"application {link_id} was created before this job's scorecard was "
            "ever frozen, so there is no version it applied under. Freeze the "
            "matrix and have the candidate re-apply, or record the application "
            "against an explicit version; do not evaluate it against today's."
        )

    context = EvaluationContext(
        job_id=str(row["job_id"]),
        link_id=str(row["link_id"]),
        applied_at=row["applied_at"],
        scorecard_version=int(row["scorecard_version"]),
        company_dna_version=(
            int(row["company_dna_version"])
            if row["company_dna_version"] is not None
            else None
        ),
        company_dna_id=(
            str(row["company_dna_id"]) if row["company_dna_id"] is not None else None
        ),
        freeze_sequence=int(row["freeze_sequence"]),
        frozen_at=row["frozen_at"],
        correlation_id=row["correlation_id"],
    )
    logger.info(
        "orchestration.evaluation_context %s",
        provenance.log_fields(
            correlation_id=context.correlation_id or "",
            stage=provenance.STAGE_APPLICATION,
            job_id=context.job_id,
            count=context.scorecard_version,
        ),
    )
    return context


# ── The controlled revision workflow (RBAC 12, spec-doc6 5) ──────────────────


class RevisionRefused(RuntimeError):
    """A post-finalisation change that would have mutated history."""


#: Why a finalised artifact may be revised. A closed list, because "other" is
#: how an audit trail stops answering the question it was built for. Each of
#: these produces a NEW version; none of them edits the old one.
REVISION_REASONS: tuple[str, ...] = (
    #: The role itself changed: different scope, different seniority.
    "role_changed",
    #: A criterion was wrong -- unmeasurable, duplicated, or not in the JD.
    "criterion_defective",
    #: A prohibited disqualifier was found and must be removed (Runbook 12.3).
    "prohibited_disqualifier",
    #: The client's Company DNA was re-authored, so Layer 2 moved underneath.
    "company_dna_revised",
    #: The situation type was misclassified at intake, which re-weights the
    #: whole matrix coherently and invisibly. The most expensive intake error.
    "situation_reclassified",
)


@dataclass(frozen=True)
class Revision:
    """What a controlled revision must do, described rather than performed.

    Returned by `plan_revision` and executed by the module that owns the table.
    Splitting it that way keeps one writer per concept: an orchestration package
    that could freeze a scorecard would be a second implementation of freezing.
    """

    job_id: str
    #: The version being superseded. Never deleted, never edited.
    supersedes_version: int
    new_version: int
    reason: str
    author_user_id: str
    author_role: str
    correlation_id: str
    #: Applications already evaluated under the superseded version. They stay
    #: under it: the count is carried so the person authorising the revision is
    #: told how many people it does not apply to.
    evaluated_under_previous: int
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "supersedes_version": self.supersedes_version,
            "new_version": self.new_version,
            "reason": self.reason,
            "author_user_id": self.author_user_id,
            "author_role": self.author_role,
            "correlation_id": self.correlation_id,
            "evaluated_under_previous": self.evaluated_under_previous,
            "note": self.note,
        }


async def plan_revision(
    session: AsyncSession,
    *,
    job_id: uuid.UUID | str,
    reason: str,
    author: provenance.Principal,
    note: str | None = None,
) -> Revision:
    """Plan a post-finalisation criteria change as a NEW version.

    Refuses three things outright, each because the alternative is a silent
    mutation of a record somebody is entitled to rely on:

      * an unrecognised reason, because an unclassified change is one the audit
        trail cannot answer questions about later;
      * a revision on a job that has never been frozen, because there is nothing
        to supersede and the caller means "freeze it", which is a different
        operation with a different authorization;
      * an author outside the job's tenant, which is a cross-tenant write with a
        plausible-looking audit row attached.

    It does NOT refuse a revision on a job whose candidates have already been
    assessed. That case is the reason the workflow exists: the previous version
    stays in force for them, the new one governs future applicants, and the
    count of people it does not apply to is put in front of the person
    authorising it rather than discovered afterwards.
    """
    if reason not in REVISION_REASONS:
        raise RevisionRefused(
            f"{reason!r} is not a recorded revision reason; expected one of "
            f"{list(REVISION_REASONS)}. An unclassified criteria change is one "
            "nobody can review later."
        )

    row = (
        await session.execute(
            text(
                """
                SELECT j.tenant_id,
                       j.correlation_id,
                       b.scorecard_version,
                       b.freeze_sequence
                  FROM jobs j
             LEFT JOIN LATERAL (
                       SELECT *
                         FROM job_company_dna_bindings b2
                        WHERE b2.job_id = j.id
                     ORDER BY b2.freeze_sequence DESC
                        LIMIT 1
                       ) b ON TRUE
                 WHERE j.id = :job_id
                """
            ),
            {"job_id": str(job_id)},
        )
    ).mappings().first()

    if row is None:
        raise RevisionRefused(f"job {job_id} does not exist.")
    if row["scorecard_version"] is None:
        raise RevisionRefused(
            f"job {job_id} has no frozen scorecard, so there is nothing to "
            "revise. Freeze the matrix first; freezing and revising are "
            "different operations with different authorization."
        )
    if str(row["tenant_id"]) != author.tenant_id:
        raise RevisionRefused(
            f"{author.user_id} is in tenant {author.tenant_id} and job {job_id} "
            f"belongs to tenant {row['tenant_id']}."
        )

    evaluated = (
        await session.execute(
            text(
                """
                SELECT count(*) AS n
                  FROM evaluations
                 WHERE job_id = :job_id
                   AND scorecard_version = :version
                """
            ),
            {"job_id": str(job_id), "version": int(row["scorecard_version"])},
        )
    ).scalar_one()

    revision = Revision(
        job_id=str(job_id),
        supersedes_version=int(row["scorecard_version"]),
        new_version=int(row["scorecard_version"]) + 1,
        reason=reason,
        author_user_id=author.user_id,
        author_role=author.role,
        correlation_id=row["correlation_id"] or provenance.correlation_for_job(job_id),
        evaluated_under_previous=int(evaluated or 0),
        note=note,
    )
    logger.info(
        "orchestration.revision_planned %s",
        provenance.log_fields(
            correlation_id=revision.correlation_id,
            stage=provenance.STAGE_MATRIX,
            job_id=revision.job_id,
            principal_user_id=revision.author_user_id,
            principal_role=revision.author_role,
            count=revision.evaluated_under_previous,
        ),
    )
    return revision
