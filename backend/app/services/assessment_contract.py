"""The ONE read API for a job's assessment contract: its skills, frozen.

WHAT A CONTRACT IS
------------------
Everything every candidate on a job is assessed against, in one frozen value:
the skills in three buckets (Must-have, Nice-to-have, Behavioural), each with
a hidden priority and a hidden "what good evidence looks like" line, the hidden
role summary Sutra wrote at Save, and the grade that decides the question
budget. The recruitment team sees skill NAMES only; everything else here is
internal and crosses no API boundary.

LIVE UNTIL THE FIRST GENUINE APPLICATION, SNAPSHOT FOREVER AFTER (CONTRACT v10)
-------------------------------------------------------------------------------
Owner ruling v10 (2026-09-28) SUPERSEDES D5's lock point, which was the first
candidate START. Before any candidate applies, the contract is read from the
live rows (`job_competencies`, `jobs.assessment_context_json`,
`jobs.assessment_grade`) and reports `version=0, locked=False`. The first
GENUINE application (the one apply path, portal or public `/apply`, including
a sourced link the candidate converts by applying) calls
`freeze_at_application` in the SAME transaction as the application write,
which writes an immutable `job_skill_snapshots` row (`source='application'`,
`locked_by_link_id`). A sourced upload, a databank link, the matching run's
own links and an invitation are not applications and freeze nothing.

The start keeps `lock_contract` as an idempotent BACKSTOP: it binds the
conversation to the snapshot that already exists, and writes one
(`source='lock'`) only for a job whose first application arrived before its
skills were saved, when no freeze was possible. From the first snapshot on,
`load_contract` answers from the latest snapshot, and
`load_contract_for_conversation` answers from the snapshot THAT conversation
is bound to, so no later version can move a candidate who has already started.

"Frozen" (the code says "locked") is the EXISTENCE of a snapshot row, never a
timestamp (rule 8). The database refuses to rewrite one (migrations 0118 and
0131).

THE DIGEST COVERS CONTENT ONLY
------------------------------
`compute_digest` hashes the skills, the role summary and the grade, and never
the version or the lock state. So the live digest at the instant of the freeze
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
    SNAPSHOT_SOURCE_APPLICATION,
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

#: `audit_log.action` for the freeze. A candidate's application (or, as the
#: backstop, their start) takes it, so there is no human principal and no
#: agent: the row names the job and the application or the conversation.
AUDIT_SKILLS_LOCKED = "job_skills_locked"
_AUDIT_ACTOR_ROLE = "candidate"

#: Server-authored sentences. A route renders them verbatim.
CONTRACT_NOT_READY = (
    "The skills for this job have not been saved yet, so the assessment "
    "cannot start. The hiring team needs to save the skills first."
)
#: The ONE refusal every frozen field answers with: the JD document, the
#: title, the experience band, the grade and every skills write. `{date}` is
#: the day of the FIRST snapshot. It replaces the two sentences that named a
#: started assessment (the skills lock and the grade lock), which v10 retired.
#: Nobody can start an assessment without having applied first, so "has
#: applied" is true of every snapshot, a start-time backstop included.
FROZEN_DETAIL = (
    "The job description and skills are frozen because a candidate has "
    "applied. Frozen since {date}."
)


def frozen_detail(frozen_at: datetime) -> str:
    """The frozen sentence for a job first frozen at `frozen_at`.

    A DATE (`28 Sep 2026`), never a count of days: a date is a fact about the
    record, and the no-numbers rule is about assessment signals, not the
    calendar (the closed-applications sentence prints a date the same way).
    """
    return FROZEN_DETAIL.format(date=f"{frozen_at:%d %b %Y}")


class ContractNotReady(RuntimeError):
    """The skills are not saved, so there is nothing to lock or assess."""

    def __init__(self, message: str = CONTRACT_NOT_READY) -> None:
        super().__init__(message)


class SkillsLocked(RuntimeError):
    """A write was attempted on a job whose JD and skills are frozen.

    Carries the moment of the FIRST snapshot, so every refusal says since
    when. There is deliberately no default: a refusal that cannot name the
    date was raised by code that never read the snapshot it claims exists.
    """

    def __init__(self, frozen_at: datetime) -> None:
        self.frozen_at = frozen_at
        self.detail = frozen_detail(frozen_at)
        super().__init__(self.detail)


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
    return await frozen_since(db, job_id) is not None


async def frozen_since(db: AsyncSession, job_id: uuid.UUID) -> datetime | None:
    """When the job's JD and skills were FIRST frozen, or None if they are not.

    The earliest snapshot's `locked_at`, asked of the table. A later version
    does not move the day the job stopped being editable.
    """
    frozen_at: datetime | None = (
        await db.execute(
            select(func.min(JobSkillSnapshot.locked_at)).where(
                JobSkillSnapshot.job_id == job_id
            )
        )
    ).scalar_one()
    return frozen_at


async def frozen_reason(db: AsyncSession, job_id: uuid.UUID) -> str | None:
    """The frozen sentence for the job, or None when it is not frozen."""
    frozen_at = await frozen_since(db, job_id)
    return None if frozen_at is None else frozen_detail(frozen_at)


async def require_unlocked(db: AsyncSession, job_id: uuid.UUID) -> None:
    """Raise `SkillsLocked` when the job's JD and skills are frozen.

    A writer calls this INSIDE `locks.advisory_xact_lock(SKILLS, job_id)`, so a
    candidate's application (or start) and an edit cannot interleave.
    """
    frozen_at = await frozen_since(db, job_id)
    if frozen_at is not None:
        raise SkillsLocked(frozen_at)


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


# ── The posting: skill NAMES by bucket (CONTRACT v10) ────────────────────────

#: The headings the candidate-facing posting and the recruiter's preview print.
POSTING_BUCKET_LABELS: dict[str, str] = {
    BUCKET_MUST_HAVE: "Must-have skills",
    BUCKET_NICE_TO_HAVE: "Nice-to-have skills",
    BUCKET_BEHAVIOURAL: "Behavioural competencies",
}


@dataclass(frozen=True)
class PostingBucket:
    bucket: str
    label: str
    names: tuple[str, ...]


def _posting(pairs: Iterable[tuple[str, str]]) -> tuple[PostingBucket, ...]:
    """(bucket, name) pairs as the three posting buckets, names ALPHABETICAL.

    Alphabetical rather than by priority: the priority is hidden, and an
    ordered list would publish it.
    """
    by_bucket: dict[str, list[str]] = {bucket: [] for bucket in BUCKETS}
    for bucket, name in pairs:
        if bucket in by_bucket:
            by_bucket[bucket].append(name)
    return tuple(
        PostingBucket(
            bucket=bucket,
            label=POSTING_BUCKET_LABELS[bucket],
            names=tuple(sorted(by_bucket[bucket], key=lambda name: (name.casefold(), name))),
        )
        for bucket in BUCKETS
    )


async def posting_skills(
    db: AsyncSession, jobs: Sequence[Job], *, include_unsaved: bool = False
) -> dict[uuid.UUID, tuple[PostingBucket, ...]]:
    """The skills each job's posting shows, by NAME, for many jobs at once.

    * Frozen: the latest snapshot's names (read through `_from_snapshot`, so a
      snapshot whose digest no longer describes it RAISES rather than being
      published).
    * Not frozen and skills SAVED: the active rows.
    * Otherwise: an EMPTY tuple, so the posting shows no skills section. With
      `include_unsaved` (the recruiter's preview, never a public surface) the
      active rows are shown whether or not they are saved.

    The ONE builder for every surface that shows a posting's skills, so the
    apply page, the portal, the employer page and the preview cannot
    disagree. Two queries whatever the number of jobs.
    """
    if not jobs:
        return {}
    job_ids = [job.id for job in jobs]
    latest: dict[uuid.UUID, JobSkillSnapshot] = {}
    for snapshot in (
        await db.execute(
            select(JobSkillSnapshot)
            .where(JobSkillSnapshot.job_id.in_(job_ids))
            .order_by(JobSkillSnapshot.job_id, JobSkillSnapshot.version.desc())
        )
    ).scalars():
        latest.setdefault(snapshot.job_id, snapshot)
    live_ids = [
        job.id
        for job in jobs
        if job.id not in latest and (include_unsaved or _saved(job))
    ]
    live: dict[uuid.UUID, list[tuple[str, str]]] = {job_id: [] for job_id in live_ids}
    if live_ids:
        for job_id, bucket, name in (
            await db.execute(
                select(JobCompetency.job_id, JobCompetency.category, JobCompetency.name).where(
                    JobCompetency.job_id.in_(live_ids), JobCompetency.is_active.is_(True)
                )
            )
        ).all():
            live[job_id].append((bucket, name))
    out: dict[uuid.UUID, tuple[PostingBucket, ...]] = {}
    for job in jobs:
        if job.id in latest:
            contract = _from_snapshot(latest[job.id])
            out[job.id] = _posting((skill.bucket, skill.name) for skill in contract.skills)
        elif job.id in live:
            out[job.id] = _posting(live[job.id])
        else:
            out[job.id] = ()
    return out


# ── The freeze ───────────────────────────────────────────────────────────────


async def _lockable_contract(db: AsyncSession, job: Job) -> AssessmentContract | None:
    """The live contract when it may be frozen, else None.

    Saved (the stamp AND the hidden context) and non-empty. Saved and then
    emptied is not a contract anybody can be assessed against: freezing it
    would fix "nothing" as what every later candidate is graded on.
    """
    if not _saved(job):
        return None
    live = await _live_contract(db, job)
    return live if live.skills else None


async def _write_snapshot(
    db: AsyncSession,
    job: Job,
    live: AssessmentContract,
    *,
    source: str,
    conversation_id: uuid.UUID | None,
    link_id: uuid.UUID | None,
    application_id: uuid.UUID | None,
) -> JobSkillSnapshot:
    """The ONE writer of a `job_skill_snapshots` row, and of its audit row.

    The caller holds `locks.advisory_xact_lock(SKILLS, job.id)` and has seen
    that no snapshot exists. Writes the live contract as the next version and
    ONE `audit_log` row in a single INSERT through `audit.record_action` (the
    2026-09-20 rule). `link_id` is stored on the snapshot only when an
    APPLICATION froze the job; `application_id` is the audit row's
    application either way (the start's own link for the backstop).
    """
    next_version = (
        await db.execute(
            select(func.coalesce(func.max(JobSkillSnapshot.version), 0)).where(
                JobSkillSnapshot.job_id == job.id
            )
        )
    ).scalar_one() + 1
    context = dict(job.assessment_context_json or {})
    context.pop("role_summary", None)
    snapshot = JobSkillSnapshot(
        tenant_id=job.tenant_id,
        job_id=job.id,
        version=next_version,
        skills_json=_skills_to_json(live.skills),
        role_summary=live.role_summary,
        context_json=context,
        grade=live.grade,
        digest=live.digest,
        source=source,
        locked_by_conversation_id=conversation_id,
        locked_by_link_id=link_id,
        locked_at=datetime.now(timezone.utc),
    )
    db.add(snapshot)
    await db.flush()
    metadata: dict[str, Any] = {
        "source": source,
        "version": snapshot.version,
        "contract_digest": snapshot.digest,
    }
    if conversation_id is not None:
        metadata["conversation_id"] = str(conversation_id)
    await audit.record_action(
        db,
        action=AUDIT_SKILLS_LOCKED,
        actor_user_id=None,
        actor_role=_AUDIT_ACTOR_ROLE,
        tenant_id=job.tenant_id,
        resource_type="job",
        resource_id=job.id,
        job_id=job.id,
        application_id=application_id,
        correlation_id=job.correlation_id,
        new_state={"skills_locked": True, "version": snapshot.version},
        metadata=metadata,
    )
    logger.info(
        "assessment_contract.locked job_id=%s version=%d source=%s contract_digest=%s "
        "conversation_id=%s link_id=%s skills=%d",
        job.id, snapshot.version, source, snapshot.digest,
        conversation_id, link_id, len(live.skills),
    )
    return snapshot


async def freeze_at_application(
    db: AsyncSession, job_id: uuid.UUID, link_id: uuid.UUID
) -> AssessmentContract | None:
    """Freeze the job's JD and skills at a GENUINE application. Idempotent.

    Called by the ONE apply path (`api/portal.apply_to_job`) after the
    application's link is written or converted from `sourced`, in the SAME
    transaction, so the application and the snapshot commit together and a
    rolled-back application leaves no snapshot. Under
    `locks.advisory_xact_lock(SKILLS, job_id)`, the lock every skills write,
    every JD, title, band and grade edit, and every start takes, so an edit
    and an application cannot interleave: either the snapshot carries the
    edit, or the edit is refused.

    * Already frozen: the latest snapshot, nothing written.
    * Skills saved and non-empty: the live contract becomes the next version
      with `source='application'` and `locked_by_link_id`.
    * Otherwise (a legacy published job whose skills were never saved, or
      skills edited after publication and not saved again): None. The
      application still succeeds; nothing is frozen, and the next genuine
      application after the skills are saved freezes them.

    A sourced upload, a databank link, the matching run's links and an
    invitation never call this: they are not applications.
    """
    await locks.advisory_xact_lock(db, locks.SKILLS, job_id)
    snapshot = await _latest_snapshot(db, job_id)
    if snapshot is not None:
        return _from_snapshot(snapshot)
    job = await _job(db, job_id)
    live = await _lockable_contract(db, job)
    if live is None:
        logger.info(
            "assessment_contract.not_frozen job_id=%s link_id=%s reason=skills_not_saved",
            job_id, link_id,
        )
        return None
    snapshot = await _write_snapshot(
        db, job, live, source=SNAPSHOT_SOURCE_APPLICATION,
        conversation_id=None, link_id=link_id, application_id=link_id,
    )
    return _from_snapshot(snapshot)


async def lock_contract(
    db: AsyncSession, job_id: uuid.UUID, conversation_id: uuid.UUID
) -> AssessmentContract:
    """Bind this conversation to the job's frozen contract. Idempotent.

    Called where a conversation's `started_at` is stamped, in the same
    transaction. Since CONTRACT v10 the job is normally frozen already, by its
    first genuine application (`freeze_at_application`), and this is the
    BACKSTOP that binds the conversation to that snapshot. Under
    `locks.advisory_xact_lock(SKILLS, job_id)`, so two candidates starting at
    once and an edit racing a start all serialise;
    `uq_job_skill_snapshots_version` is the second line.

    * A conversation already bound gets its own snapshot back, unchanged.
    * A job already frozen binds the conversation to the latest snapshot.
    * Otherwise (the job's first application predated its saved skills, so
      nothing could freeze then) the skills must be saved and non-empty
      (`ContractNotReady`), and the live contract is written as the next
      version with `source='lock'`. The conversation is bound
      (`skill_snapshot_id` and `contract_digest`) either way.

    Nothing here calls a model, so holding the advisory lock is bounded by the
    caller's own transaction. The grade is frozen with the skills: it is part
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
        live = await _lockable_contract(db, job)
        if live is None:
            raise ContractNotReady()
        snapshot = await _write_snapshot(
            db, job, live, source=SNAPSHOT_SOURCE_LOCK,
            conversation_id=conversation_id,
            link_id=None,
            application_id=conversation.job_candidate_link_id,
        )

    contract = _from_snapshot(snapshot)
    conversation.skill_snapshot_id = snapshot.id
    conversation.contract_digest = contract.digest
    await db.flush()
    return contract
