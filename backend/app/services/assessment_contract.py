"""The ONE read API for a job's assessment contract: its skills, locked.

WHAT A CONTRACT IS
------------------
Everything every candidate on a job is assessed against, in one frozen value:
the skills in three buckets (Must-have, Nice-to-have, Behavioural), each with
a hidden priority and a hidden "what good evidence looks like" line, the hidden
role summary Sutra wrote at Save, and the grade that decides the question
budget. The recruitment team sees skill NAMES only; everything else here is
internal and crosses no API boundary.

LIVE UNTIL THE FIRST START, SNAPSHOT FOREVER AFTER (D5)
-------------------------------------------------------
Before any candidate starts, the contract is read from the live rows
(`job_competencies`, `jobs.assessment_context_json`, `jobs.assessment_grade`)
and reports `version=0, locked=False`. The first candidate start calls
`lock_contract`, which writes an immutable `job_skill_snapshots` row (the
database refuses to UPDATE it, see migration 0118) and binds the conversation
to it. From then on `load_contract` answers from the latest snapshot, and
`load_contract_for_conversation` answers from the snapshot THAT conversation
is bound to, so no later version can move a candidate who has already started.

"Locked" is the EXISTENCE of a snapshot row, never a timestamp (rule 8).

THE DIGEST COVERS CONTENT ONLY
------------------------------
`compute_digest` hashes the skills, the role summary and the grade, and never
the version or the lock state. So the live digest at the instant of the lock
equals the snapshot's digest, and Vaada (the conversation) and Miti (the
grade) can prove they read the same contract by logging the same digest:
`log_digest` is the one format, and a test compares the two.

Migration `0118_skills_contract` carries its own copy of the canonical
form, deliberately (a migration must not change behaviour when this module
does); `tests/test_assessment_contract.py` pins the two as identical.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import AssessmentConversation, JobCompetency
from app.models.job import Job
from app.models.job_skill_snapshot import (
    SNAPSHOT_SOURCE_LOCK,
    JobSkillSnapshot,
)
from app.services import audit, locks

logger = logging.getLogger(__name__)

#: The three buckets, in report order. Equal to `ppi.CATEGORIES` (asserted by
#: the test) and spelled out here so this module does not import `ppi`, which
#: sits on the generation side of the service graph.
BUCKET_MUST_HAVE = "must_have"
BUCKET_NICE_TO_HAVE = "nice_to_have"
BUCKET_BEHAVIOURAL = "behavioural"
BUCKETS: tuple[str, ...] = (BUCKET_MUST_HAVE, BUCKET_NICE_TO_HAVE, BUCKET_BEHAVIOURAL)

#: The two readers that must agree on the contract of one conversation, and
#: the `stage=` value each logs through `log_digest`.
STAGE_VAADA = "vaada"
STAGE_MITI = "miti"
DIGEST_STAGES: tuple[str, ...] = (STAGE_VAADA, STAGE_MITI)

#: `audit_log.action` for the lock. The candidate's start takes it, so there is
#: no human principal and no agent: the row names the job and the conversation.
AUDIT_SKILLS_LOCKED = "job_skills_locked"
_AUDIT_ACTOR_ROLE = "candidate"

#: Server-authored sentences. A route renders them verbatim.
CONTRACT_NOT_READY = (
    "The skills for this job have not been saved yet, so the assessment "
    "cannot start. The hiring team needs to save the skills first."
)
SKILLS_LOCKED_DETAIL = (
    "The skills are locked because a candidate has started the assessment. "
    "Every candidate on this job is assessed against the same skills."
)


class ContractNotReady(RuntimeError):
    """The skills are not saved, so there is nothing to lock or assess."""

    def __init__(self, message: str = CONTRACT_NOT_READY) -> None:
        super().__init__(message)


class SkillsLocked(RuntimeError):
    """A write was attempted on skills that a started assessment has locked."""

    def __init__(self, message: str = SKILLS_LOCKED_DETAIL) -> None:
        super().__init__(message)


class ContractNotBound(RuntimeError):
    """A STARTED conversation carries no snapshot binding.

    Every start binds one (`lock_contract`) and migration 0118 bound every
    session that had started before contracts existed, so this is a defect,
    never a state to read around: answering it from the live rows would grade
    a candidate against skills that may have changed since they were asked.
    """


class ContractIntegrityError(RuntimeError):
    """A stored snapshot, or a conversation's stored digest, no longer matches
    the content it names. Raised rather than read past, because a contract
    whose digest does not describe it cannot prove what anybody was assessed
    against."""


@dataclass(frozen=True)
class ContractSkill:
    id: uuid.UUID            # job_competencies.id
    name: str
    bucket: str              # "must_have" | "nice_to_have" | "behavioural"
    priority: int            # 1 = highest, within the bucket
    evidence_line: str       # what good evidence looks like (hidden)


@dataclass(frozen=True)
class AssessmentContract:
    job_id: uuid.UUID
    version: int             # snapshot version; 0 = live, unlocked rows
    locked: bool
    skills: tuple[ContractSkill, ...]
    role_summary: str
    digest: str              # sha256 over the canonical CONTENT (see module doc)
    grade: str               # jobs.assessment_grade, locked with the skills
    locked_at: datetime | None


# ── The canonical form and the digest ───────────────────────────────────────


def _sort_key(skill: ContractSkill) -> tuple[int, int, str, str]:
    return (BUCKETS.index(skill.bucket), skill.priority, skill.name, str(skill.id))


def canonical_payload(
    skills: Iterable[ContractSkill], role_summary: str, grade: str
) -> dict[str, Any]:
    """The exact content the digest covers, in canonical order.

    Bucket order, then priority, then name, then id: total, so two readers of
    the same rows can never serialise them differently.
    """
    ordered = sorted(skills, key=_sort_key)
    return {
        "grade": grade,
        "role_summary": role_summary,
        "skills": [
            {
                "bucket": skill.bucket,
                "evidence_line": skill.evidence_line,
                "id": str(skill.id),
                "name": skill.name,
                "priority": skill.priority,
            }
            for skill in ordered
        ],
    }


def compute_digest(
    skills: Iterable[ContractSkill], role_summary: str, grade: str
) -> str:
    """sha256 hex over `canonical_payload`, stable across processes and runs."""
    encoded = json.dumps(
        canonical_payload(skills, role_summary, grade),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def log_digest(stage: str, conversation_id: uuid.UUID | str, contract: AssessmentContract) -> None:
    """The one structured line a reader logs: Vaada at the start, Miti at grading.

    ONE FORMAT, so a single query over the logs pairs the two readers of one
    conversation, and a test can prove the two digests are identical:

        assessment_contract.digest stage=<vaada|miti> conversation_id=<id>
            job_id=<id> version=<n> contract_digest=<sha256>

    Identifiers, a version and a hash only: never a skill name, an evidence
    line or the role summary, because an ordinary log is read far more widely
    than the contract it describes. An unknown stage RAISES rather than logging
    a line nobody's query will ever match.
    """
    if stage not in DIGEST_STAGES:
        raise ValueError(
            f"unknown contract digest stage {stage!r}; expected one of {DIGEST_STAGES}"
        )
    logger.info(
        "assessment_contract.digest stage=%s conversation_id=%s job_id=%s "
        "version=%d contract_digest=%s",
        stage, conversation_id, contract.job_id, contract.version, contract.digest,
    )


# ── Reads ────────────────────────────────────────────────────────────────────


async def _job(db: AsyncSession, job_id: uuid.UUID) -> Job:
    job = (await db.execute(select(Job).where(Job.id == job_id))).scalar_one_or_none()
    if job is None:
        raise LookupError(f"job {job_id} not found")
    return job


async def _latest_snapshot(db: AsyncSession, job_id: uuid.UUID) -> JobSkillSnapshot | None:
    return (
        await db.execute(
            select(JobSkillSnapshot)
            .where(JobSkillSnapshot.job_id == job_id)
            .order_by(JobSkillSnapshot.version.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def is_locked(db: AsyncSession, job_id: uuid.UUID) -> bool:
    """True once a snapshot exists for the job. The table, never a stamp."""
    found = (
        await db.execute(
            select(JobSkillSnapshot.id).where(JobSkillSnapshot.job_id == job_id).limit(1)
        )
    ).scalar_one_or_none()
    return found is not None


async def require_unlocked(db: AsyncSession, job_id: uuid.UUID) -> None:
    """Raise `SkillsLocked` when a started assessment has locked the skills.

    A writer calls this INSIDE `locks.advisory_xact_lock(SKILLS, job_id)`, so a
    candidate's start and a skills edit cannot interleave.
    """
    if await is_locked(db, job_id):
        raise SkillsLocked()


def _saved(job: Job) -> bool:
    return job.framework_approved_at is not None and job.assessment_context_json is not None


async def skills_saved(db: AsyncSession, job_id: uuid.UUID) -> bool:
    """The Skills step has been saved: the stamp AND the hidden context exist.

    `framework_approved_at` is reused as "skills saved at" (CONTRACT v2); the
    context is required as well, because a saved job whose context was never
    written would hand Vaada and Miti a contract with no role summary written
    by anybody, silently.
    """
    return _saved(await _job(db, job_id))


def _role_summary(job: Job) -> str:
    context = job.assessment_context_json or {}
    return str(context.get("role_summary") or "")


async def _live_skills(db: AsyncSession, job_id: uuid.UUID) -> tuple[ContractSkill, ...]:
    """Active rows, with a per-bucket priority 1..n.

    The priority is the position in (force_rank NULLS LAST, ordinal, name, id)
    rather than the raw `force_rank`, so a bucket whose ranks are unset or
    sparse still reads as a dense 1..n. Migration 0118 rewrote `force_rank` to
    exactly this position for every unlocked job, so for those the two agree.
    """
    rows = (
        await db.execute(
            select(JobCompetency)
            .where(JobCompetency.job_id == job_id, JobCompetency.is_active.is_(True))
            .order_by(
                JobCompetency.category,
                JobCompetency.force_rank.asc().nulls_last(),
                JobCompetency.ordinal,
                JobCompetency.name,
                JobCompetency.id,
            )
        )
    ).scalars().all()
    positions: dict[str, int] = {}
    skills: list[ContractSkill] = []
    for row in rows:
        positions[row.category] = positions.get(row.category, 0) + 1
        skills.append(
            ContractSkill(
                id=row.id,
                name=row.name,
                bucket=row.category,
                priority=positions[row.category],
                evidence_line=row.observable_evidence or "",
            )
        )
    return tuple(sorted(skills, key=_sort_key))


def _skills_from_json(payload: Sequence[Mapping[str, Any]]) -> tuple[ContractSkill, ...]:
    return tuple(
        sorted(
            (
                ContractSkill(
                    id=uuid.UUID(str(item["id"])),
                    name=str(item["name"]),
                    bucket=str(item["bucket"]),
                    priority=int(item["priority"]),
                    evidence_line=str(item["evidence_line"]),
                )
                for item in payload
            ),
            key=_sort_key,
        )
    )


def _skills_to_json(skills: Iterable[ContractSkill]) -> list[dict[str, Any]]:
    return canonical_payload(skills, "", "")["skills"]


def _from_snapshot(snapshot: JobSkillSnapshot) -> AssessmentContract:
    skills = _skills_from_json(snapshot.skills_json or [])
    digest = compute_digest(skills, snapshot.role_summary or "", snapshot.grade)
    if digest != snapshot.digest:
        raise ContractIntegrityError(
            f"job_skill_snapshots {snapshot.id} stores digest {snapshot.digest} "
            f"but its content hashes to {digest}"
        )
    return AssessmentContract(
        job_id=snapshot.job_id,
        version=snapshot.version,
        locked=True,
        skills=skills,
        role_summary=snapshot.role_summary or "",
        digest=snapshot.digest,
        grade=snapshot.grade,
        locked_at=snapshot.locked_at,
    )


async def _live_contract(db: AsyncSession, job: Job) -> AssessmentContract:
    skills = await _live_skills(db, job.id)
    role_summary = _role_summary(job)
    return AssessmentContract(
        job_id=job.id,
        version=0,
        locked=False,
        skills=skills,
        role_summary=role_summary,
        digest=compute_digest(skills, role_summary, job.assessment_grade),
        grade=job.assessment_grade,
        locked_at=None,
    )


async def load_contract(db: AsyncSession, job_id: uuid.UUID) -> AssessmentContract:
    """The latest snapshot when the job is locked, else the live rows."""
    snapshot = await _latest_snapshot(db, job_id)
    if snapshot is not None:
        return _from_snapshot(snapshot)
    return await _live_contract(db, await _job(db, job_id))


async def load_contract_for_conversation(
    db: AsyncSession, conversation_id: uuid.UUID
) -> AssessmentContract:
    """The contract THIS conversation runs against.

    Bound: the bound snapshot, and the conversation's stored digest must equal
    it. Not bound and not started: the job's current contract (a question
    written before the start reads the same thing the start will lock). Not
    bound but started: `ContractNotBound`, never a fallback to the live rows.
    """
    conversation = (
        await db.execute(
            select(AssessmentConversation).where(AssessmentConversation.id == conversation_id)
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise LookupError(f"assessment conversation {conversation_id} not found")
    if conversation.skill_snapshot_id is None:
        if conversation.started_at is not None:
            raise ContractNotBound(
                f"assessment conversation {conversation_id} started at "
                f"{conversation.started_at.isoformat()} with no skills snapshot bound"
            )
        return await load_contract(db, conversation.job_id)
    snapshot = await db.get(JobSkillSnapshot, conversation.skill_snapshot_id)
    if snapshot is None:
        raise ContractIntegrityError(
            f"assessment conversation {conversation_id} is bound to snapshot "
            f"{conversation.skill_snapshot_id}, which is not readable"
        )
    contract = _from_snapshot(snapshot)
    if conversation.contract_digest is not None and conversation.contract_digest != contract.digest:
        raise ContractIntegrityError(
            f"assessment conversation {conversation_id} recorded digest "
            f"{conversation.contract_digest} but its snapshot is {contract.digest}"
        )
    return contract


# ── The lock ─────────────────────────────────────────────────────────────────


async def lock_contract(
    db: AsyncSession, job_id: uuid.UUID, conversation_id: uuid.UUID
) -> AssessmentContract:
    """Lock the job's skills (once) and bind this conversation. Idempotent.

    Called where a conversation's `started_at` is stamped, in the same
    transaction. Under `locks.advisory_xact_lock(SKILLS, job_id)`, so two
    candidates starting at once and a skills edit racing a start all
    serialise; `uq_job_skill_snapshots_version` is the second line.

    * A conversation already bound gets its own snapshot back, unchanged.
    * A job already locked binds the conversation to the latest snapshot.
    * Otherwise the skills must be saved and non-empty (`ContractNotReady`),
      the live contract is written as the next version with `source='lock'`,
      the conversation is bound (`skill_snapshot_id` and `contract_digest`),
      and ONE `audit_log` row records the lock, written in a single INSERT
      through `audit.record_action` (the 2026-09-20 rule).

    Nothing here calls a model, so holding the advisory lock is bounded by the
    caller's own transaction. The grade is locked with the skills: it is part
    of the snapshot and of the digest.
    """
    await locks.advisory_xact_lock(db, locks.SKILLS, job_id)
    conversation = (
        await db.execute(
            select(AssessmentConversation).where(AssessmentConversation.id == conversation_id)
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise LookupError(f"assessment conversation {conversation_id} not found")
    if conversation.job_id != job_id:
        raise ValueError(
            f"assessment conversation {conversation_id} belongs to job "
            f"{conversation.job_id}, not {job_id}"
        )
    if conversation.skill_snapshot_id is not None:
        return await load_contract_for_conversation(db, conversation_id)

    snapshot = await _latest_snapshot(db, job_id)
    if snapshot is None:
        job = await _job(db, job_id)
        if not _saved(job):
            raise ContractNotReady()
        live = await _live_contract(db, job)
        if not live.skills:
            # Saved and then emptied is not a contract anybody can be assessed
            # against. Locking it would freeze "nothing" as what every later
            # candidate on the job is graded on.
            raise ContractNotReady()
        next_version = (
            await db.execute(
                select(func.coalesce(func.max(JobSkillSnapshot.version), 0)).where(
                    JobSkillSnapshot.job_id == job_id
                )
            )
        ).scalar_one() + 1
        context = dict(job.assessment_context_json or {})
        context.pop("role_summary", None)
        snapshot = JobSkillSnapshot(
            tenant_id=job.tenant_id,
            job_id=job_id,
            version=next_version,
            skills_json=_skills_to_json(live.skills),
            role_summary=live.role_summary,
            context_json=context,
            grade=live.grade,
            digest=live.digest,
            source=SNAPSHOT_SOURCE_LOCK,
            locked_by_conversation_id=conversation_id,
            locked_at=datetime.now(timezone.utc),
        )
        db.add(snapshot)
        await db.flush()
        await audit.record_action(
            db,
            action=AUDIT_SKILLS_LOCKED,
            actor_user_id=None,
            actor_role=_AUDIT_ACTOR_ROLE,
            tenant_id=job.tenant_id,
            resource_type="job",
            resource_id=job_id,
            job_id=job_id,
            application_id=conversation.job_candidate_link_id,
            correlation_id=job.correlation_id,
            new_state={"skills_locked": True, "version": snapshot.version},
            metadata={
                "conversation_id": str(conversation_id),
                "version": snapshot.version,
                "contract_digest": snapshot.digest,
            },
        )
        logger.info(
            "assessment_contract.locked job_id=%s version=%d contract_digest=%s "
            "conversation_id=%s skills=%d",
            job_id, snapshot.version, snapshot.digest, conversation_id, len(live.skills),
        )

    contract = _from_snapshot(snapshot)
    conversation.skill_snapshot_id = snapshot.id
    conversation.contract_digest = contract.digest
    await db.flush()
    return contract
