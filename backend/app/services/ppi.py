"""Vivekium Profile Intelligence (PPI) -- the per-job evaluation matrix.

PROPRIETARY: PPI is Vivekium's own competency framework, derived from
first-principles job analysis. It is NOT modelled on, named after, or derived
from any licensed psychometric instrument, and no such instrument may ever be
referenced in this file, the product UI, or the documentation.

THE THREE ASPECTS (Draft v4)
----------------------------
    must_have     the capabilities the role cannot be performed without. This
                  is where technical depth now lives, folded in from what used
                  to be a standalone Technical Assessment Agent.
    nice_to_have  supporting capabilities that strengthen performance.
    behavioural   observable workplace behaviours the role demands.

They were Primary Skills, Secondary Skills and Behavioural Competencies. The
first two were RENAMED, not replaced: the same criteria under names that say
what they mean in hiring language, which is what makes the hard cap below read
as the obvious rule rather than as an arbitrary weighting.

PPI IS THE SOLE OWNER OF DEPTH
------------------------------
Matching (`services/matching`) evaluates background and logistics from resume
text alone -- coarse, inferred, never verified. PPI evaluates demonstrated
depth and behaviour from a conversation. Same named territory in places, a
different question, and no overlap. Nothing outside PPI assesses skill depth.

TWO THINGS THAT MUST NOT BE CONFUSED
------------------------------------
1. **The matrix is per JOB.** Generated once from the JD and the reporting
   authority's SWOT intake, reviewed and saved by the Hiring Manager, then
   FIXED. Every candidate applying to that job is graded against the same items
   -- that is the only reason two candidates' reports are comparable.
2. **The questions are per CANDIDATE.** Once the matrix is saved, questions
   probing it are generated individually from the JD, the saved matrix, and
   that candidate's own resume. What varies is how an item is approached, never
   which items there are.

NO MINIMUM ITEM COUNT, AND A CEILING THAT IS NOT ARBITRARY
----------------------------------------------------------
Draft v4 removed the old floor of five per aspect: the agent recommends however
many items the job genuinely needs. What replaced it is a CEILING, and it comes
from a rule the product already had -- every item in the matrix is probed at
least once, because an item that is graded and charted without being asked
about is exactly the unfair output the review gate exists to prevent. The grade
therefore bounds the matrix: a matrix cannot hold more items than its grade
allows questions. `matrix_is_complete` refuses a save above that, naming the
number to remove, rather than letting a job reach candidates whose reports would
grade items nobody was asked about.

"Culture" is refused as a Behavioural Competency, at generation and at save.
Cultural fit cannot be assessed accurately in a single conversation, and PPI
does not claim otherwise.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import JobCompetency
from app.models.job import Job
from app.services import (
    job_version,
)
from app.services.rating import (
    GRADE_HIGHLY,
    GRADE_MATCHING,
    GRADE_MODERATELY,
    GRADES,
)

logger = logging.getLogger(__name__)

__all__ = [
    "CATEGORIES",
    "CATEGORY_BEHAVIOURAL",
    "CATEGORY_LABELS",
    "CATEGORY_MUST_HAVE",
    "CATEGORY_NICE_TO_HAVE",
    "FORBIDDEN_COMPETENCY_TERMS",
    "REQUIRED_LEVEL_SCORES",
    "RUBRIC_SCORED_CATEGORIES",
    "framework_is_complete",
    "is_forbidden_competency",
    "load_framework",
    "matrix_is_complete",
    "published_matrix",
    "publish_tatva_matrix",
    "verify_matrix_for_consumer",
    "required_level_score",
    "requirement_word",
]

# ── Aspects ──────────────────────────────────────────────────────────────────

CATEGORY_MUST_HAVE = "must_have"
CATEGORY_NICE_TO_HAVE = "nice_to_have"
CATEGORY_BEHAVIOURAL = "behavioural"

#: Ordered exactly as the report renders them (spec §9.3).
CATEGORIES: tuple[str, ...] = (
    CATEGORY_MUST_HAVE,
    CATEGORY_NICE_TO_HAVE,
    CATEGORY_BEHAVIOURAL,
)

CATEGORY_LABELS: dict[str, str] = {
    CATEGORY_MUST_HAVE: "Must-have",
    CATEGORY_NICE_TO_HAVE: "Nice-to-have",
    CATEGORY_BEHAVIOURAL: "Behavioural Competencies",
}

#: The two aspects whose answers are scored against the question's OWN stored
#: rubric. Behavioural is absent deliberately: there is no single correct answer
#: to a behavioural question, so it is scored by judgement (spec §8). One
#: scoring agent, two methods, and this frozenset is which is which.
RUBRIC_SCORED_CATEGORIES: frozenset[str] = frozenset(
    {CATEGORY_MUST_HAVE, CATEGORY_NICE_TO_HAVE}
)

#: Structural floor: one item per aspect. NOT a count contract -- Draft v4
#: deliberately removed those. An aspect with no items still gets a grade, a
#: remark and a radar chart in the report, and there would be nothing behind
#: any of the three.
MINIMUM_PER_CATEGORY = 1


# ── The Culture refusal (spec §5) ────────────────────────────────────────────
# Enforced at THREE layers: the generator is told not to produce it, the save
# handler refuses it, and a Postgres CHECK refuses the row. A prompt instruction
# is a request rather than a guarantee, and the Hiring Manager's Edit control
# can type anything.

FORBIDDEN_COMPETENCY_TERMS: tuple[str, ...] = (
    "culture",
    "cultural",
)

FORBIDDEN_COMPETENCY_DETAIL = (
    "Culture is not assessable as a Behavioural Competency. Cultural fit cannot "
    "be judged accurately from a single assessment, so PPI does not claim to "
    "measure it. Please use a competency describing an observable behaviour."
)


def is_forbidden_competency(name: str) -> bool:
    """True when `name` is a culture-fit competency, in any casing or phrasing.

    Matches on word boundaries so a legitimate competency that merely CONTAINS
    the letters (there is no such English word in practice, but "agricultural"
    is the shape of the risk) is not caught.
    """
    lowered = str(name or "").casefold()
    return any(
        re.search(rf"\b{term}\b", lowered) for term in FORBIDDEN_COMPETENCY_TERMS
    )


# ── Required level ───────────────────────────────────────────────────────────
# The radar plots TWO shapes: what the job needs and what the candidate showed
# (spec §9.4). The job's shape comes from a required level the matrix agent
# assigns to each item, stated as one of the same four grade WORDS the client
# already reads -- never a number, at generation or at display.
#
# It is stored as the band's representative internal score purely so it shares
# the column type and the grade projection with the candidate's score; nothing
# reads it as a number outside this module and `rating.grade_for_percent`.

REQUIRED_LEVEL_SCORES: dict[str, int] = {
    GRADE_HIGHLY: 95,
    GRADE_MATCHING: 82,
    GRADE_MODERATELY: 67,
}

#: A job that requires NOTHING of an item would not have it in its matrix, so
#: "Not Matching" is not an offered requirement level. An unrecognised value
#: settles on the middle band rather than raising.
DEFAULT_REQUIRED_LEVEL = REQUIRED_LEVEL_SCORES[GRADE_MATCHING]


def required_level_score(label: Any) -> int:
    return REQUIRED_LEVEL_SCORES.get(str(label).strip(), DEFAULT_REQUIRED_LEVEL)


# ── Sutra builds the matrix: `hiring/scorecard.compile_matrix` ──────────────
#
# DELETED 2026-08-29 (spec-doc6 D1, section 4.1 "delete on activation").
#
# `generate_framework` lived here: one model call asking for a whole matrix in
# one pass, with `_fallback_framework` assembling a matrix out of the JD's own
# noun phrases whenever the model was unavailable. Both are gone, along with
# `_normalise`, `_ensure_every_aspect`, `_maximum_total`, `_valid_competency`,
# `_jd_terms`, `_swot_terms`, `_captured_points`, `_consume_swot_evidence` and
# `load_swot`. The prompt file went with them.
#
# TWO THINGS WERE WRONG WITH IT AND ONLY ONE WAS VISIBLE.
#
# The visible one: a weight a model chooses in one pass has no terms to store,
# so the matrix could not carry "which Layer 1 / Layer 2 / Layer 3 input
# produced this and what each contributed" -- which is the product requirement
# this phase is built around.
#
# The quiet one: the fallback produced a matrix that LOOKED like the real
# thing. It was reviewed, approved, and graded against for the life of the job,
# and it rested on nothing but the JD -- which Runbook section 18 calls "almost
# never an accurate specification of the hiring problem". A degradation that
# leaves no trace in what it produces is indistinguishable from success.
#
# What survives in this module is everything downstream of the matrix: loading
# it, versioning it, publishing it as an artifact, checking it may be saved, and
# generating one candidate's questions against it.


async def load_framework(session: AsyncSession, job_id: Any) -> list[JobCompetency]:
    """The job's active matrix, in report order (must-have, nice-to-have, behavioural)."""
    rows = (
        await session.execute(
            select(JobCompetency)
            .where(JobCompetency.job_id == job_id, JobCompetency.is_active.is_(True))
            .order_by(JobCompetency.ordinal)
        )
    ).scalars().all()
    return sorted(rows, key=lambda row: (CATEGORIES.index(row.category), row.ordinal))


# ── Sutra publishes the matrix (spec §5) ─────────────────────────────────────
#
# The version authority is the append-only freeze binding
# (`job_scorecard_bindings.scorecard_version`), read where the artifact is
# published. Batch-counting over `job_competencies.created_at` was the earlier
# answer and was DELETED with its last caller on 2026-09-23: compilation can
# reuse rows and a human edit preserves row identity, so creation batches are
# not versions.


def requirement_word(required_level: Any) -> str:
    """The required level as a WORD.

    An integer here would be a number crossing an agent boundary on its way
    towards a report, and the point at which it stops being convertible is the
    point at which somebody renders it. The internal score exists so the radar
    has a radius; nothing downstream of this artifact needs it.
    """
    for label, score in REQUIRED_LEVEL_SCORES.items():
        if score == required_level:
            return label
    return GRADE_MATCHING


def _matrix_item(row: JobCompetency) -> dict[str, Any]:
    return {
        "competency_id": str(row.id),
        "name": row.name,
        "description": row.description or "",
        # The criterion an answer is measured against at MATRIX level. The
        # per-question rubric bands are written with each question and belong to
        # that question, not here: a rubric copied onto the matrix would be a
        # second copy that drifts from the one the scorer actually reads.
        "rubric": row.description or "",
        "required_level": requirement_word(row.required_level),
        "evidence_expectation": (
            "At least one answer in the candidate's own words describing what "
            "they did, in what context, and what resulted."
        ),
        "ordinal": row.ordinal,
    }


def _matrix_payload(
    job: Job, rows: list[JobCompetency], *, version: int, locked: bool
) -> dict[str, Any]:
    from app.services.agents import identity  # noqa: PLC0415
    # Local, because `assessment_questions.budget` imports this module's
    # category constants: a module-level import would close a cycle.
    from app.services.assessment_questions.budget import resolve_question_range  # noqa: PLC0415
    by_category = {
        category: [_matrix_item(row) for row in rows if row.category == category]
        for category in CATEGORIES
    }
    minimum, maximum = resolve_question_range(
        job.assessment_grade, len(rows), job.role_classification
    )
    return {
        **by_category,
        "coverage": {
            category: len(items) for category, items in by_category.items()
        },
        # The RANGE, not a number of questions to ask. Sutra decides the band and
        # Vaada decides where inside it a conversation stops; publishing a single
        # figure would hand a consumer the old pre-2026-08-23 contract under the
        # new name.
        "question_count_range": {"minimum": minimum, "maximum": maximum},
        "grade": job.assessment_grade,
        "version": version,
        "locked": locked,
        "jd_version": job_version.jd_version(job),
        "provenance": {
            "producer": identity.SUTRA,
            "job_id": str(job.id),
            "generated_at": (
                job.framework_generated_at.isoformat()
                if job.framework_generated_at
                else None
            ),
            "approved_at": (
                job.framework_approved_at.isoformat()
                if job.framework_approved_at
                else None
            ),
        },
        # What `sutra_gate` compares. The JD's critical requirements are the
        # Must-have names themselves: this product has no separate list of what
        # the JD demanded, and inventing one from JD text here would be a second
        # extraction nobody reviews. Stated plainly rather than left out, so the
        # gate is measuring something real rather than an empty set.
        "critical_requirements": [item["name"] for item in by_category[CATEGORY_MUST_HAVE]],
        "covered_requirements": [
            item["name"] for items in by_category.values() for item in items
        ],
    }


def publish_tatva_matrix(
    job: Job,
    rows: list[JobCompetency],
    *,
    version: int,
    correlation_id: str | None = None,
) -> artifacts.Artifact | None:
    """Run Sutra's gate, then publish the `tatva_matrix` artifact.

    Returns None rather than raising, for the same reason Bodha's publish does:
    this runs inside job setup, which is the step that gates a job reaching
    candidates at all. A publish failure here must cost the hand-off and never
    the matrix -- the rows and the stamps are already flushed by the time this
    is called, and a raised exception would roll back a generation that
    succeeded.

    LOCKED IS DERIVED FROM THE JOB, NOT PASSED IN. `framework_approved_at` is
    stamped by the review handler, and a caller that could assert "locked" for
    itself could publish a mutable matrix as immutable, which is the one claim a
    consumer has no way to check.
    """
    from app.services.agents import artifacts, envelope as run_envelope, gates, identity  # noqa: PLC0415
    if not rows:
        return None
    try:
        locked = job.framework_approved_at is not None
        payload = _matrix_payload(job, rows, version=version, locked=locked)
        verdict = gates.run_gate(identity.SUTRA, payload)
        envelope = run_envelope.Envelope.for_run(
            tenant_id=str(job.tenant_id),
            agent_id=identity.SUTRA,
            task_type="jd_generation",
            interactive=False,
            job_id=str(job.id),
            workflow_id=correlation_id,
            context_version=str(version),
        )
        payload["correlation_id"] = envelope.workflow_id
        artifact = artifacts.publish(
            producer=identity.SUTRA,
            artifact_type="tatva_matrix",
            payload=payload,
            tenant_id=str(job.tenant_id),
            job_id=str(job.id),
            version=version,
            # A locked matrix is what makes two reports on one job comparable,
            # so it is published under the frozen status rather than merely
            # flagged: `verify_for_consumer` reads status, and a flag it does not
            # read is a flag nobody enforces.
            status=(
                artifacts.STATUS_LOCKED if locked else artifacts.STATUS_PUBLISHED
            ),
            source_refs=tuple(f"job_competencies:{row.id}" for row in rows),
            validated=verdict.passed,
        )
        logger.info(
            "ppi.matrix_artifact_published job_id=%s artifact_id=%s version=%d "
            "locked=%s validated=%s",
            job.id,
            artifact.artifact_id,
            version,
            locked,
            verdict.passed,
        )
        return artifact
    except Exception:
        logger.warning(
            "ppi.matrix_artifact_publish_failed job_id=%s", job.id, exc_info=True
        )
        return None


async def published_matrix(
    session: AsyncSession, job: Job, *, correlation_id: str | None = None
) -> artifacts.Artifact | None:
    """The job's current matrix as a verifiable artifact, or None if it has none.

    The entry point Yukti, Vaada, Miti and Siddhi use. The append-only freeze
    binding is the version authority; row creation batches are not versions
    because compilation can reuse rows and human edits preserve row identity.
    """
    active = await load_framework(session, job.id)
    if not active:
        return None
    from app.models.job_scorecard_binding import JobScorecardBinding

    current = (
        await session.execute(
            select(JobScorecardBinding.scorecard_version)
            .where(JobScorecardBinding.job_id == job.id)
            .order_by(JobScorecardBinding.freeze_sequence.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    version = int(current) if current is not None else 1
    return publish_tatva_matrix(
        job, active, version=version, correlation_id=correlation_id
    )


def verify_matrix_for_consumer(
    artifact: artifacts.Artifact,
    consumer_id: str,
    *,
    tenant_id: str,
    job_id: str,
    expected_version: int | None = None,
) -> verification.Verdict:
    """`verify_for_consumer` plus the check only Sutra can state.

    THE FAILURE THIS PREVENTS. A candidate is assessed against matrix version 2
    while the report is synthesised against version 1, and every grade in that
    report is stated against criteria the candidate was never asked about. Both
    versions verify perfectly on their own -- right tenant, right job, right
    producer, published -- so nothing in the generic envelope check can see it.
    It is HIGH because the report is immutable: by the time anyone notices, the
    only remedy is re-running an assessment that cannot be re-run.

    A frozen matrix that arrives unfrozen is the same defect one step earlier.
    `expected_version` is compared arithmetically, never inferred, and a
    consumer that does not know which version it wants passes None and gets the
    envelope check alone rather than a check that quietly always passes.
    """
    # Imported HERE, not at module scope. Importing the verification
    # package runs its __init__, which eagerly pulls in every critic --
    # and `ppi_report` imports `functional_assessment`, which imports
    # `gap_analysis`, which imports this module. A module-level import
    # therefore closes a cycle that fails as `partially initialized
    # module` and only under some import orders, so the full suite stays
    # green while one test file goes red.
    from app.services.verification import base as verification  # noqa: PLC0415
    from app.services.agents import artifacts  # noqa: PLC0415
    findings = list(
        artifacts.verify_for_consumer(
            artifact, consumer_id, tenant_id=tenant_id, job_id=job_id
        ).findings
    )
    if expected_version is not None and int(artifact.version) != int(expected_version):
        findings.append(
            verification.high(
                "matrix_version_mismatch",
                "tatva_matrix.version",
                f"the consumer expects version {int(expected_version)} and the "
                f"artifact carries version {int(artifact.version)}",
                "Reload the job's current matrix; never grade against a version "
                "the candidate was not assessed on.",
            )
        )
    payload_version = artifact.payload.get("version")
    if payload_version is not None and int(payload_version) != int(artifact.version):
        findings.append(
            verification.high(
                "matrix_version_disagreement",
                "tatva_matrix.payload.version",
                f"the envelope says version {int(artifact.version)} and the payload "
                f"says {int(payload_version)}",
                "Republish the matrix; an artifact that disagrees with itself "
                "cannot establish which criteria were used.",
            )
        )
    if artifact.payload.get("locked") and artifact.status != artifacts.STATUS_LOCKED:
        findings.append(
            verification.high(
                "locked_matrix_published_mutable",
                "tatva_matrix.status",
                "the payload states the matrix is HM-locked and the envelope does not",
                "Republish the locked matrix under the frozen status.",
            )
        )
    return verification.verdict(f"a2a:{artifact.artifact_type}", findings)


def matrix_is_complete(
    rows: list[JobCompetency],
    grade: str | None,
    role_classification: str | None = None,
) -> tuple[bool, str | None]:
    """Whether a matrix may be saved as the job's fixed criteria.

    Two rules, and neither is a count contract in the sense the old floor of
    five was:

      * every aspect carries at least one item, because each aspect is graded,
        remarked and charted in every report written against this job;
      * the matrix holds no more items than the grade allows questions, because
        every item is probed at least once and an item nobody was asked about
        must never be graded.

    The ceiling names the number to remove rather than truncating silently. The
    Hiring Manager is looking at the matrix when this refusal arrives and is the
    right person to choose which items go.
    """
    # Local, because `assessment_questions.budget` imports this module's
    # category constants: a module-level import would close a cycle.
    from app.services.assessment_questions.budget import (  # noqa: PLC0415
        DEFAULT_GRADE,
        max_questions,
    )

    active = [row for row in rows if row.is_active]
    for category in CATEGORIES:
        if not any(row.category == category for row in active):
            return False, (
                f"{CATEGORY_LABELS[category]} has no items. Every aspect is graded "
                "and charted on each candidate's report, so each one needs at "
                "least one item before the matrix can be saved."
            )
    ceiling = max_questions(grade, role_classification)
    if len(active) > ceiling:
        surplus = len(active) - ceiling
        return False, (
            f"This matrix holds {len(active)} items and a "
            f"{(grade or DEFAULT_GRADE).replace('_', '-')} assessment asks at most "
            f"{ceiling} questions. Every item is probed at least once, so please "
            f"remove {surplus} item{'s' if surplus != 1 else ''} before saving."
        )
    offending = [
        row.name
        for row in active
        if row.category == CATEGORY_BEHAVIOURAL and is_forbidden_competency(row.name)
    ]
    if offending:
        return False, FORBIDDEN_COMPETENCY_DETAIL
    return True, None


def framework_is_complete(
    rows: list[JobCompetency],
    grade: str | None = None,
    role_classification: str | None = None,
) -> tuple[bool, str | None]:
    """Deprecated spelling of `matrix_is_complete`, kept for one release.

    The routes and the workers were renamed together; this survives because the
    name appears in tests and in the setup screen's error path, and a rename is
    not worth a broken import on a rolling deploy.
    """
    return matrix_is_complete(rows, grade, role_classification)


# Import-time integrity checks (the budget half moved to
# `assessment_questions/budget.py` with the tables it checks).
assert set(CATEGORY_LABELS) == set(CATEGORIES)
assert RUBRIC_SCORED_CATEGORIES < set(CATEGORIES)
assert set(REQUIRED_LEVEL_SCORES) <= set(GRADES)
