"""The Skills step: one implementation of every operation on a job's skills.

WHAT A SKILL IS (Vivekium release, owner decision D1)
------------------------------------------------------
A row of `job_competencies`, which IS the skills table (one implementation per
concept, CONTRACT v1). Three buckets, Must-have, Nice-to-have and Behavioural,
at most five in each; saving needs at least one Must-have and one Behavioural.
The team sees NAMES. Everything else on the row is internal: the priority
(`force_rank`, 1 = highest, per bucket), the "what good evidence looks like"
line (`observable_evidence`, mirrored into `description`), the SWOT quotation
(`swot_origin`) and who wrote it (`authored_by`).

THE FLOW
--------
    SWOT saved for the first time, no skill rows of ANY kind
        -> `request_draft` -> `pickready.draft_job_skills` -> `draft`
    the team adds, pastes, renames, moves and removes (`add`, `add_many`,
        `rename`, `move`, `remove`); every edit makes the skills UNSAVED again
    Save Skills -> `save`: ONE Sutra call writes the hidden context BEFORE any
        row changes, then one transaction writes it all
    the first candidate START locks everything (`assessment_contract.lock_contract`)

THE RULES EVERY WRITE FOLLOWS, IN THIS ORDER
--------------------------------------------
1. `locks.advisory_xact_lock(SKILLS, job)`, the lock a candidate's start takes
   too, so an edit and a start can never interleave.
2. `assessment_contract.require_unlocked`: once a candidate has started, every
   write is refused with `SKILLS_LOCKED_DETAIL` and nothing changes (D5).
3. The write.
4. `_mark_unsaved`: an edited set of skills is not the set that was saved, so
   the job stops being invitable until somebody saves again. A published job
   keeps taking applications meanwhile.

SEMANTICS CARRIED OVER, DELIBERATELY UNCHANGED
----------------------------------------------
* REVIVE, NEVER RE-INSERT (2026-09-21). Deletion is soft because an issued
  candidate question may reference the row, and `uq_job_competency_name` has no
  predicate, so a removed name still holds its slot. Adding it back revives the
  row, keeping its `swot_origin` and its evidence line: the same name coming
  back is the same skill.
* A RENAME CLEARS `swot_origin` AND EVERYTHING DERIVED (2026-09-23). A renamed
  row is a different skill wearing the old row's identity; carrying the SWOT
  quotation across would be a fabricated citation. It also becomes the team's
  (`authored_by = 'human'`).
* A RENAME OR A MOVE ONTO AN OCCUPIED NAME IS A 409 NAMING IT, including an
  occupant that is only soft-deleted and therefore invisible. A MOVE onto a
  soft-deleted occupant REVIVES that row instead, which is what fixes the old
  reorder 500 (audit #17).
* ADDING A NAME ALREADY THERE IS IDEMPOTENT. A paste of five where one is
  present must not discard the other four.

A name matches another name case-insensitively after collapsing whitespace,
because "Python" and "python " are one skill to a reader. A name already active
in ANOTHER bucket is refused rather than added twice: one skill is graded once.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.assessment import AUTHORED_BY_HUMAN, AUTHORED_BY_SUTRA, JobCompetency
from app.models.job import (
    SKILLS_DRAFT_DRAFTED,
    SKILLS_DRAFT_DRAFTING,
    SKILLS_DRAFT_FAILED,
    SKILLS_DRAFT_NOT_STARTED,
    Job,
)
from app.models.job_setup import JobSwotAnalysis
from app.services import assessment_contract, job_version, locks, ppi, swot_analysis
from app.services.assessment_contract import SKILLS_LOCKED_DETAIL, SkillsLocked
from app.services.audit import record_action, record_agent_action
from app.services.hiring import sutra
from app.services.hiring_pipeline import DRAFTING_STATES, JobLifecycleState
from app.workers.dispatch import TaskHandle, dispatch_after_commit

logger = logging.getLogger(__name__)

__all__ = [
    "AUTHORED_HUMAN",
    "AUTHORED_SUTRA",
    "BUCKETS",
    "BUCKET_LABELS",
    "DRAFT_STALE_AFTER",
    "DRAFT_TASK",
    "MAX_PER_BUCKET",
    "MIN_BEHAVIOURAL",
    "MIN_MUST_HAVE",
    "PENDING_REVIEW",
    "READY_FOR_CANDIDATES",
    "SKILLS_CONTEXT_UNAVAILABLE",
    "SKILLS_LOCKED_DETAIL",
    "SkillClash",
    "SkillLimitReached",
    "SkillNotFound",
    "SkillRefused",
    "SkillsChangedDuringSave",
    "SkillsError",
    "SkillsInvalid",
    "SkillsLocked",
    "SkillsView",
    "DraftRefused",
    "HumanSkillsWouldBeReplaced",
    "add",
    "add_many",
    "after_swot_saved",
    "draft",
    "move",
    "redraft_available",
    "refresh_setup_status",
    "remove",
    "rename",
    "request_draft",
    "save",
    "validate_for_save",
    "view",
]

BUCKETS: tuple[str, ...] = assessment_contract.BUCKETS
MAX_PER_BUCKET = sutra.MAX_PER_BUCKET
MIN_MUST_HAVE = 1
MIN_BEHAVIOURAL = 1
AUTHORED_SUTRA = AUTHORED_BY_SUTRA
AUTHORED_HUMAN = AUTHORED_BY_HUMAN

#: How the recruitment team reads each bucket. The Skills step's own words;
#: `ppi.CATEGORY_LABELS` names the report sections, which is a different
#: surface.
BUCKET_LABELS: dict[str, str] = {
    ppi.CATEGORY_MUST_HAVE: "Must-have",
    ppi.CATEGORY_NICE_TO_HAVE: "Nice-to-have",
    ppi.CATEGORY_BEHAVIOURAL: "Behavioural",
}

#: `jobs.assessment_status`, kept at its two persisted values so every reader of
#: the ready state (matching, invitations) needs no change.
READY_FOR_CANDIDATES = "ready_for_candidates"
PENDING_REVIEW = "questions_pending_review"

#: A draft still `drafting` after this long is treated as lost: it READS as
#: failed and `pickready.reconcile_job_setup` dispatches it again.
DRAFT_STALE_AFTER = timedelta(minutes=15)

DRAFT_TASK = "pickready.draft_job_skills"

#: Draft modes. The FIRST draft refuses when any row exists, which is what
#: makes a redelivered message a no-op; a redraft replaces what is there.
MODE_INITIAL = "initial"
MODE_REDRAFT = "redraft"

#: Server-authored sentences. A route renders them verbatim.
SKILLS_CONTEXT_UNAVAILABLE = (
    "Skills could not be saved because the assessment writer is unavailable. "
    "Nothing was changed. Try again in a moment."
)
SKILLS_CHANGED_DURING_SAVE = (
    "The skills changed while they were being saved. Review them and save again."
)
DRAFT_FAILED_DETAIL = (
    "The skills draft did not finish. Draft them again, or add them yourself."
)
DRAFT_EDITED_MEANWHILE = (
    "The skills were edited while the draft was being written, so nothing was "
    "replaced. Draft again to replace them."
)
SWOT_NOT_SAVED_DETAIL = "Save the SWOT first. The skills are drafted from it."


# ── Errors: each carries the HTTP status a route answers with ────────────────


class SkillsError(RuntimeError):
    """A refusal with a sentence the reviewer reads. Nothing was written."""

    http_status = 409

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class SkillNotFound(SkillsError):
    http_status = 404


class SkillRefused(SkillsError):
    """A name that cannot be a skill (empty, or culture into Behavioural)."""

    http_status = 422


class SkillLimitReached(SkillsError):
    pass


class SkillClash(SkillsError):
    pass


class SkillsInvalid(SkillsError):
    """Save refused. Carries EVERY problem, not the first."""

    http_status = 422

    def __init__(self, problems: Sequence[str]) -> None:
        self.problems: tuple[str, ...] = tuple(problems)
        super().__init__(" ".join(self.problems))


class SkillsChangedDuringSave(SkillsError):
    def __init__(self) -> None:
        super().__init__(SKILLS_CHANGED_DURING_SAVE)


class SkillsContextUnavailable(SkillsError):
    """The writer was unavailable. Names no skill; nothing changed."""

    http_status = 503

    def __init__(self) -> None:
        super().__init__(SKILLS_CONTEXT_UNAVAILABLE)


class SkillsContextRefused(SkillsError):
    """The writer could not describe these skills. Names every one."""

    http_status = 422

    def __init__(self, refused: Sequence[tuple[str, str]]) -> None:
        self.refused: tuple[tuple[str, str], ...] = tuple(refused)
        super().__init__(sutra.refusal_message(self.refused))


class DraftRefused(SkillsError):
    pass


class HumanSkillsWouldBeReplaced(SkillsError):
    """A redraft over the team's own skills, without their confirmation."""

    def __init__(self, names: Sequence[str]) -> None:
        self.names: tuple[str, ...] = tuple(names)
        listed = ", ".join(f'"{name}"' for name in self.names)
        super().__init__(
            f"Re-drafting replaces the skills your team wrote ({listed}). "
            "Confirm to replace them."
        )


# ── Reading ──────────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clean_name(name: str) -> str:
    return " ".join(str(name or "").split())


def _key(name: str) -> str:
    return _clean_name(name).casefold()


async def _rows(
    db: AsyncSession, job_id: uuid.UUID, *, active_only: bool = False
) -> list[JobCompetency]:
    query = select(JobCompetency).where(JobCompetency.job_id == job_id)
    if active_only:
        query = query.where(JobCompetency.is_active.is_(True))
    rows = (
        await db.execute(query.order_by(JobCompetency.ordinal, JobCompetency.name))
    ).scalars().all()
    return sorted(
        rows,
        key=lambda row: (
            BUCKETS.index(row.category) if row.category in BUCKETS else len(BUCKETS),
            row.force_rank if row.force_rank is not None else 10_000,
            row.ordinal,
            row.name,
        ),
    )


async def has_any_row(db: AsyncSession, job_id: uuid.UUID) -> bool:
    """One row of ANY kind, active or not, is proof a draft or an edit landed.

    A set the team EMPTIED is not a set nobody wrote (2026-09-21): both read as
    zero active rows, and only this question tells them apart.
    """
    found = (
        await db.execute(
            select(JobCompetency.id).where(JobCompetency.job_id == job_id).limit(1)
        )
    ).scalar_one_or_none()
    return found is not None


@dataclass(frozen=True)
class SkillEntry:
    id: uuid.UUID
    name: str
    #: `swot` | `jd` | `company` | `team`. `team` means the hiring team wrote it.
    source: str
    #: The SWOT sentence this skill answers, verbatim, or None.
    from_swot: str | None


@dataclass(frozen=True)
class SkillsView:
    job_id: uuid.UUID
    draft_status: str
    draft_error: str | None
    saved: bool
    locked: bool
    swot_saved: bool
    redraft_available: bool
    human_authored_names: tuple[str, ...]
    #: Why the current skills cannot be saved as they stand, or None.
    blocking_reason: str | None
    buckets: dict[str, tuple[SkillEntry, ...]] = field(default_factory=dict)
    max_per_bucket: int = MAX_PER_BUCKET


def _source(row: JobCompetency) -> str:
    """Where a skill came from, in the reviewer's words.

    ASSUMPTION: a Sutra row written by the retired matrix compiler has no
    `source` recorded. One carrying a SWOT quotation came from the SWOT; any
    other came from the JD's own requirements or the department menu, and
    `jd` is the nearer of the two sources this screen names.
    """
    if row.authored_by == AUTHORED_HUMAN:
        return "team"
    recorded = (row.provenance_json or {}).get("source")
    if recorded in sutra.DRAFT_SOURCES:
        return str(recorded)
    return sutra.SOURCE_SWOT if row.swot_origin else sutra.SOURCE_JD


def effective_draft_state(job: Job, *, now: datetime | None = None) -> tuple[str, str | None]:
    """(status, error) as a reader sees them. A lost draft reads as failed."""
    status = job.skills_draft_status or SKILLS_DRAFT_NOT_STARTED
    if status == SKILLS_DRAFT_DRAFTING:
        requested = job.skills_draft_requested_at
        if requested is None or (now or _now()) - requested > DRAFT_STALE_AFTER:
            return SKILLS_DRAFT_FAILED, DRAFT_FAILED_DETAIL
    return status, job.skills_draft_error


def redraft_available(
    job: Job, swot: JobSwotAnalysis | None, *, locked: bool, any_row: bool
) -> bool:
    """Whether the team may be OFFERED a re-draft from the current SWOT.

    Offered, never performed: the UI asks, and a redraft over the team's own
    skills needs a second confirmation on top of that. Only when the skills are
    not locked, the SWOT is saved, a draft is not already running, some skill
    row exists (otherwise the first draft is automatic), and the saved SWOT is
    newer than the one the current draft was written from.
    """
    if locked or not any_row or not swot_analysis.is_saved(swot):
        return False
    status, _error = effective_draft_state(job)
    if status == SKILLS_DRAFT_DRAFTING:
        return False
    drafted_from = job.skills_drafted_swot_version
    return drafted_from is None or (swot is not None and swot.version > drafted_from)


def validate_for_save(rows: Sequence[JobCompetency]) -> list[str]:
    """Every reason these ACTIVE rows cannot be saved, in bucket order. Empty
    means they can. Every problem is named, so one Save shows all of them."""
    active = [row for row in rows if row.is_active]
    problems: list[str] = []
    for bucket in BUCKETS:
        count = sum(1 for row in active if row.category == bucket)
        if count > MAX_PER_BUCKET:
            surplus = count - MAX_PER_BUCKET
            problems.append(
                f"{BUCKET_LABELS[bucket]} holds {_spelled(count)} skills and the "
                f"limit is {_spelled(MAX_PER_BUCKET)}. Remove {_spelled(surplus)} "
                "before saving."
            )
    if sum(1 for row in active if row.category == ppi.CATEGORY_MUST_HAVE) < MIN_MUST_HAVE:
        problems.append("Add at least one Must-have skill before saving.")
    if sum(1 for row in active if row.category == ppi.CATEGORY_BEHAVIOURAL) < MIN_BEHAVIOURAL:
        problems.append("Add at least one Behavioural skill before saving.")
    for row in active:
        if not _clean_name(row.name):
            problems.append("A skill has no name. Name it or remove it.")
        elif row.category == ppi.CATEGORY_BEHAVIOURAL and ppi.is_forbidden_competency(row.name):
            problems.append(f'"{row.name}": {ppi.FORBIDDEN_COMPETENCY_DETAIL}')
    return problems


#: Counts in a refusal are SPELLED OUT. A refusal is copy a client reads, and
#: the no-numbers rule has no exception left (D3).
_SPELLED = (
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
)


def _spelled(count: int) -> str:
    return _SPELLED[count] if 0 <= count < len(_SPELLED) else "more than fifteen"


async def view(db: AsyncSession, job: Job) -> SkillsView:
    """The Skills step as the reviewer sees it. Names and states only.

    `can_edit` per bucket and `can_save` are NOT here: they are answers about
    a PERSON on this job and belong to the route, resolved by the same
    `rbac.authorize` calls the writes enforce with.
    """
    rows = await _rows(db, job.id)
    active = [row for row in rows if row.is_active]
    locked = await assessment_contract.is_locked(db, job.id)
    swot = await swot_analysis.get(db, job)
    status, error = effective_draft_state(job)
    problems = validate_for_save(active)
    return SkillsView(
        job_id=job.id,
        draft_status=status,
        draft_error=error,
        saved=await assessment_contract.skills_saved(db, job.id),
        locked=locked,
        swot_saved=swot_analysis.is_saved(swot),
        redraft_available=redraft_available(job, swot, locked=locked, any_row=bool(rows)),
        human_authored_names=tuple(
            row.name for row in active if row.authored_by == AUTHORED_HUMAN
        ),
        blocking_reason=None if locked or not problems else " ".join(problems),
        buckets={
            bucket: tuple(
                SkillEntry(
                    id=row.id,
                    name=row.name,
                    source=_source(row),
                    from_swot=row.swot_origin,
                )
                for row in active
                if row.category == bucket
            )
            for bucket in BUCKETS
        },
    )


async def refresh_setup_status(db: AsyncSession, job: Job) -> None:
    """`jobs.assessment_status` from the one rule: ready once skills are saved.

    Replaces `api/assessments._refresh_setup_status`, whose second condition
    (`matching_categories_finalized_at`) went with the Matching Categories
    editor. The rule reads the saved stamp AND the hidden context, through
    `assessment_contract`, so "ready" and "lockable" can never disagree.
    """
    await db.flush()
    saved = await assessment_contract.skills_saved(db, job.id)
    target = READY_FOR_CANDIDATES if saved else PENDING_REVIEW
    if job.assessment_status != target:
        job.assessment_status = target
    await db.flush()


# ── Writes ───────────────────────────────────────────────────────────────────


async def _begin_write(db: AsyncSession, job: Job) -> None:
    await locks.advisory_xact_lock(db, locks.SKILLS, job.id)
    await assessment_contract.require_unlocked(db, job.id)


async def _mark_unsaved(db: AsyncSession, job: Job) -> None:
    """An edited set of skills is not the set that was saved."""
    if job.framework_approved_at is not None:
        job.framework_approved_at = None
    await refresh_setup_status(db, job)


def _validate_name(name: str, bucket: str) -> str:
    if bucket not in BUCKETS:
        raise SkillRefused(f"{bucket!r} is not a skills bucket.")
    clean = _clean_name(name)
    if not clean:
        raise SkillRefused("A skill needs a name.")
    if len(clean) > sutra.MAX_NAME_CHARS:
        raise SkillRefused("That name is too long for a skill. Shorten it.")
    if bucket == ppi.CATEGORY_BEHAVIOURAL and ppi.is_forbidden_competency(clean):
        raise SkillRefused(ppi.FORBIDDEN_COMPETENCY_DETAIL)
    return clean


def _clash_detail(name: str, bucket: str) -> str:
    return f'"{name}" is already an entry under {BUCKET_LABELS[bucket]} on this job.'


def _find(rows: Sequence[JobCompetency], bucket: str, name: str) -> JobCompetency | None:
    """The row in `bucket` holding `name`, active or not. Exact spelling first."""
    exact = [row for row in rows if row.category == bucket and row.name == name]
    if exact:
        return exact[0]
    key = _key(name)
    folded = [row for row in rows if row.category == bucket and _key(row.name) == key]
    active = [row for row in folded if row.is_active]
    return (active or folded or [None])[0]


def _active_elsewhere(
    rows: Sequence[JobCompetency], bucket: str, name: str, *, exclude: uuid.UUID | None = None
) -> JobCompetency | None:
    key = _key(name)
    for row in rows:
        if row.is_active and row.category != bucket and _key(row.name) == key and row.id != exclude:
            return row
    return None


def _next_ordinal(rows: Sequence[JobCompetency], bucket: str) -> int:
    return max((row.ordinal for row in rows if row.category == bucket), default=0) + 1


def _active_count(rows: Sequence[JobCompetency], bucket: str) -> int:
    return sum(1 for row in rows if row.is_active and row.category == bucket)


def _limit_detail(bucket: str, fits: int) -> str:
    label = BUCKET_LABELS[bucket]
    if fits <= 0:
        return (
            f"{label} already holds {_spelled(MAX_PER_BUCKET)} skills, the most a "
            "bucket can hold. Remove one first. Nothing was added."
        )
    return (
        f"{label} can take {_spelled(fits)} more "
        f"skill{'' if fits == 1 else 's'} (the limit is {_spelled(MAX_PER_BUCKET)}). "
        "Nothing was added."
    )


def _revive(row: JobCompetency, *, ordinal: int) -> JobCompetency:
    """Bring a soft-deleted row back. Its SWOT quotation and its evidence line
    are KEPT: the same name coming back is the same skill (2026-09-21)."""
    row.is_active = True
    row.ordinal = ordinal
    row.updated_at = _now()
    return row


def _clear_derived(row: JobCompetency) -> None:
    """A renamed row is a different skill: nothing derived for the old name may
    survive onto it. `swot_origin` above all (2026-09-23)."""
    row.observable_evidence = None
    row.description = None
    row.swot_origin = None
    row.force_rank = None
    row.provenance_json = None
    row.dimension = None
    row.evidence_sources = None
    row.assessment_method = None
    row.weight = None
    row.threshold_json = None
    row.disqualifier = None
    row.anchor_key = None


async def add(
    db: AsyncSession, job: Job, bucket: str, name: str, *, actor_user_id: uuid.UUID | None
) -> JobCompetency:
    """Add one skill. Revives a removed row of that name; idempotent if present."""
    rows = await add_many(db, job, bucket, [name], actor_user_id=actor_user_id)
    return rows[0]


async def add_many(
    db: AsyncSession,
    job: Job,
    bucket: str,
    names: Sequence[str],
    *,
    actor_user_id: uuid.UUID | None,
) -> list[JobCompetency]:
    """Add a pasted list, all or nothing against the limit.

    Duplicates inside the paste collapse. A name already active in this bucket
    is returned untouched (a paste must not discard the rest because one was
    there); a removed one is revived. A paste that would take the bucket past
    five is refused WHOLE, naming how many fit, so the reviewer chooses which
    to keep rather than the server silently dropping the tail.
    """
    await _begin_write(db, job)
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in names:
        if not _clean_name(raw):
            continue
        clean = _validate_name(raw, bucket)
        if _key(clean) not in seen:
            seen.add(_key(clean))
            cleaned.append(clean)
    if not cleaned:
        raise SkillRefused("Add at least one name.")

    rows = await _rows(db, job.id)
    for clean in cleaned:
        other = _active_elsewhere(rows, bucket, clean)
        if other is not None:
            raise SkillClash(_clash_detail(other.name, other.category))
    needed = [
        clean for clean in cleaned
        if not ((found := _find(rows, bucket, clean)) is not None and found.is_active)
    ]
    fits = MAX_PER_BUCKET - _active_count(rows, bucket)
    if len(needed) > fits:
        raise SkillLimitReached(_limit_detail(bucket, fits))

    ordinal = _next_ordinal(rows, bucket)
    out: list[JobCompetency] = []
    for clean in cleaned:
        found = _find(rows, bucket, clean)
        if found is not None and found.is_active:
            out.append(found)
            continue
        if found is not None:
            out.append(_revive(found, ordinal=ordinal))
        else:
            fresh = JobCompetency(
                tenant_id=job.tenant_id,
                job_id=job.id,
                category=bucket,
                name=clean,
                ordinal=ordinal,
                is_active=True,
                authored_by=AUTHORED_HUMAN,
                required_level=ppi.DEFAULT_REQUIRED_LEVEL,
            )
            db.add(fresh)
            out.append(fresh)
        ordinal += 1
    await db.flush()
    if needed:
        await _mark_unsaved(db, job)
    logger.info(
        "skills.added job_id=%s bucket=%s requested=%d written=%d actor=%s",
        job.id, bucket, len(cleaned), len(needed), actor_user_id,
    )
    return out


async def _row(db: AsyncSession, job: Job, skill_id: uuid.UUID) -> JobCompetency:
    row = await db.get(JobCompetency, skill_id)
    if row is None or row.job_id != job.id or not row.is_active:
        raise SkillNotFound("That skill is not on this job.")
    return row


async def rename(
    db: AsyncSession, job: Job, skill_id: uuid.UUID, name: str, *, actor_user_id: uuid.UUID | None
) -> JobCompetency:
    """Rename one skill. It becomes the team's, and nothing derived survives."""
    await _begin_write(db, job)
    row = await _row(db, job, skill_id)
    clean = _validate_name(name, row.category)
    if clean == row.name:
        return row
    rows = await _rows(db, job.id)
    occupant = _find(rows, row.category, clean)
    if occupant is not None and occupant.id != row.id:
        raise SkillClash(_clash_detail(occupant.name, row.category))
    other = _active_elsewhere(rows, row.category, clean, exclude=row.id)
    if other is not None:
        raise SkillClash(_clash_detail(other.name, other.category))
    row.name = clean
    _clear_derived(row)
    row.authored_by = AUTHORED_HUMAN
    row.updated_at = _now()
    await db.flush()
    await _mark_unsaved(db, job)
    return row


async def move(
    db: AsyncSession, job: Job, skill_id: uuid.UUID, bucket: str, *, actor_user_id: uuid.UUID | None
) -> JobCompetency:
    """Move one skill to another bucket. Checks the name BEFORE it moves.

    The old reorder route changed `category` with no look at the target bucket,
    and `uq_job_competency_name` answered a 500 whenever the target already held
    the name, even soft-deleted (audit #17). Now: an ACTIVE occupant is a 409
    naming it; a REMOVED occupant is revived in the target bucket (it keeps its
    own provenance) and the moved row is retired; otherwise the row moves.

    Behavioural IS a destination now. Every skill is graded from the answers
    (D1, Miti), so the old reason for refusing a move into Behavioural went
    with the per-question rubric scorer.
    """
    await _begin_write(db, job)
    row = await _row(db, job, skill_id)
    if bucket not in BUCKETS:
        raise SkillRefused(f"{bucket!r} is not a skills bucket.")
    if bucket == row.category:
        return row
    _validate_name(row.name, bucket)
    rows = await _rows(db, job.id)
    if _active_count(rows, bucket) >= MAX_PER_BUCKET:
        raise SkillLimitReached(_limit_detail(bucket, 0))
    occupant = _find(rows, bucket, row.name)
    if occupant is not None and occupant.is_active:
        raise SkillClash(_clash_detail(occupant.name, bucket))
    ordinal = _next_ordinal(rows, bucket)
    if occupant is not None:
        _revive(occupant, ordinal=ordinal)
        row.is_active = False
        row.updated_at = _now()
        result = occupant
    else:
        row.category = bucket
        row.ordinal = ordinal
        # The priority was a position inside the OLD bucket. Save assigns the
        # new one; carrying the old number across would rank it against
        # skills it was never compared with.
        row.force_rank = None
        row.updated_at = _now()
        result = row
    await db.flush()
    await _mark_unsaved(db, job)
    return result


async def remove(
    db: AsyncSession, job: Job, skill_id: uuid.UUID, *, actor_user_id: uuid.UUID | None
) -> None:
    """Soft delete: an issued candidate question may reference the row."""
    await _begin_write(db, job)
    row = await _row(db, job, skill_id)
    row.is_active = False
    row.updated_at = _now()
    await db.flush()
    await _mark_unsaved(db, job)


# ── Save Skills ──────────────────────────────────────────────────────────────


def _signature(rows: Sequence[JobCompetency]) -> tuple[tuple[str, str, str], ...]:
    return tuple(sorted((str(row.id), row.category, row.name) for row in rows if row.is_active))


async def save(
    db: AsyncSession, job: Job, *, actor_user_id: uuid.UUID, actor_role: str
) -> SkillsView:
    """Save the skills: ONE Sutra call first, then ONE transaction of writes.

    THE ORDER IS THE CONTRACT (the 2026-09-23 Save Matrix rule, carried over):
    every refusal and every model call finishes BEFORE the first row changes,
    and the advisory lock is taken only AROUND the writes, never across the
    model call, so a candidate starting meanwhile waits on a write and never on
    a provider.

    1. Refuse if locked; refuse with EVERY problem if the rows cannot be saved.
    2. `sutra.build_context` writes the hidden context for exactly these rows.
       An outage is `SkillsContextUnavailable` (503, names no skill); a skill
       the writer could not describe is `SkillsContextRefused` (422, names
       every one). Nothing has changed in either case.
    3. Lock, re-check the lock, and compare the rows with what was sent: an
       edit that landed during the model call is a 409, never lost silently.
    4. Write evidence lines, per-bucket priorities, the context, the saved
       stamp, the lifecycle (DRAFT to FINALIZED, never backwards) and the audit
       rows, each audit row in one INSERT (the 2026-09-20 rule).

    Synchronous in the request, and that is the recorded exception to rule 4:
    the context must land in the same transaction as the human's save, the
    call is bounded by the interactive tier (`assessment_context`), and the
    refusal precedes any write. The same shape as the `scorecard.freeze` it
    replaces.
    """
    if await assessment_contract.is_locked(db, job.id):
        raise SkillsLocked()
    rows = await _rows(db, job.id, active_only=True)
    problems = validate_for_save(rows)
    if problems:
        raise SkillsInvalid(problems)
    before = _signature(rows)
    swot = await swot_analysis.get(db, job)
    submitted = [(row.category, row.name) for row in rows]
    try:
        # Only a SWOT the team SAVED is context. A model draft nobody has read
        # is not the team's analysis, and must not shape what every candidate
        # is assessed against.
        context = await sutra.build_context(
            db, job, submitted, swot.sections() if swot_analysis.is_saved(swot) else None
        )
    except sutra.SutraUnavailable as exc:
        raise SkillsContextUnavailable() from exc
    except sutra.SutraRefused as exc:
        raise SkillsContextRefused(exc.refused) from exc

    await _begin_write(db, job)
    current = await _rows(db, job.id, active_only=True)
    if _signature(current) != before:
        raise SkillsChangedDuringSave()

    now = _now()
    for row in current:
        written = context.for_skill(row.category, row.name)
        row.observable_evidence = written.evidence_line
        # `description` mirrors the evidence line, so the two fields a reader
        # might open can never disagree (2026-09-21).
        row.description = written.evidence_line
        row.force_rank = written.priority
        row.updated_at = now
    job.assessment_context_json = {
        "role_summary": context.role_summary,
        "generated_by": "sutra",
        "model_id": context.model_id,
        "prompt_version": context.prompt_version,
        "generated_at": now.isoformat(),
    }
    first_save = job.finalized_at is None
    job.framework_approved_at = now
    if job.lifecycle_state is None or job.lifecycle_state in DRAFTING_STATES:
        job.lifecycle_state = JobLifecycleState.FINALIZED.value
    job.finalized_by = actor_user_id
    job.finalized_at = now
    if not first_save:
        # RBAC 22: an explicit revision, which a re-save is. The first save
        # keeps the column's starting version.
        job.criteria_version = (job.criteria_version or 1) + 1
    await db.flush()
    contract = await assessment_contract.load_contract(db, job.id)
    job.assessment_context_json = {
        **job.assessment_context_json,
        "skills_digest": contract.digest,
    }
    await refresh_setup_status(db, job)

    counts = {bucket: sum(1 for row in current if row.category == bucket) for bucket in BUCKETS}
    await record_action(
        db,
        action="job_skills_saved",
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        tenant_id=job.tenant_id,
        resource_type="job",
        resource_id=job.id,
        job_id=job.id,
        correlation_id=job.correlation_id,
        new_state={"lifecycle_state": job.lifecycle_state, "skills_saved": True},
        metadata={
            "counts": counts,
            "criteria_version": job.criteria_version,
            "jd_version": job_version.jd_version(job),
            "contract_digest": contract.digest,
        },
    )
    await record_agent_action(
        db,
        action="job_assessment_context_written",
        agent_name="sutra",
        principal_user_id=actor_user_id,
        principal_role=actor_role,
        tenant_id=job.tenant_id,
        resource_type="job",
        resource_id=job.id,
        job_id=job.id,
        correlation_id=job.correlation_id,
        metadata={
            "model_id": context.model_id,
            "prompt_version": context.prompt_version,
            "attempts": context.attempts,
            "skills": len(current),
        },
    )
    logger.info(
        "skills.saved job_id=%s criteria_version=%d contract_digest=%s skills=%d",
        job.id, job.criteria_version, contract.digest, len(current),
    )
    return await view(db, job)


# ── The Sutra draft ──────────────────────────────────────────────────────────


async def after_swot_saved(
    db: AsyncSession, job: Job, *, actor_user_id: uuid.UUID
) -> TaskHandle | None:
    """What a human SWOT save or restore does to the skills.

    The FIRST time, with no skill row of any kind and no draft running or
    done, it asks Sutra for a draft (after the commit). Every other time it
    does NOTHING: the response offers a redraft (`redraft_available`) and the
    team decides. A SWOT edit never silently rewrites the skills.
    """
    swot = await swot_analysis.get(db, job)
    if not swot_analysis.is_saved(swot):
        return None
    status, _error = effective_draft_state(job)
    if status not in (SKILLS_DRAFT_NOT_STARTED, SKILLS_DRAFT_FAILED):
        return None
    if await has_any_row(db, job.id) or await assessment_contract.is_locked(db, job.id):
        return None
    return await request_draft(db, job, requested_by=actor_user_id, confirm_overwrite=False)


async def request_draft(
    db: AsyncSession,
    job: Job,
    *,
    requested_by: uuid.UUID | None,
    confirm_overwrite: bool,
) -> TaskHandle | None:
    """Ask Sutra for a draft, after the commit. Refuses before writing anything.

    * Locked: `SkillsLocked`.
    * No saved SWOT: `DraftRefused`, because the draft is written from it.
    * A redraft over the team's own active skills without confirmation:
      `HumanSkillsWouldBeReplaced`, naming them.
    * A draft already running and not stale: returned as it is, no second
      dispatch.

    `requested_by` is None only for `pickready.reconcile_job_setup`, which has
    no human behind it. A lost invoke is repaired by that sweep: a `drafting`
    state older than `DRAFT_STALE_AFTER` is dispatched again.
    """
    await _begin_write(db, job)
    swot = await swot_analysis.get(db, job)
    if not swot_analysis.is_saved(swot):
        raise DraftRefused(SWOT_NOT_SAVED_DETAIL)
    status = job.skills_draft_status or SKILLS_DRAFT_NOT_STARTED
    if status == SKILLS_DRAFT_DRAFTING:
        effective, _error = effective_draft_state(job)
        if effective == SKILLS_DRAFT_DRAFTING:
            return None
    mode = MODE_REDRAFT if await has_any_row(db, job.id) else MODE_INITIAL
    if mode == MODE_REDRAFT and not confirm_overwrite:
        rows = await _rows(db, job.id, active_only=True)
        human = [row.name for row in rows if row.authored_by == AUTHORED_HUMAN]
        if human:
            raise HumanSkillsWouldBeReplaced(human)
    job.skills_draft_status = SKILLS_DRAFT_DRAFTING
    job.skills_draft_requested_at = _now()
    job.skills_draft_error = None
    await db.flush()
    assert swot is not None  # is_saved(None) is False
    return dispatch_after_commit(
        db,
        DRAFT_TASK,
        args=[str(job.id)],
        kwargs={
            "requested_swot_version": swot.version,
            "mode": mode,
            "confirm_overwrite": bool(confirm_overwrite),
            "requested_by": str(requested_by) if requested_by else None,
        },
    )


@dataclass(frozen=True)
class DraftOutcome:
    #: drafted | noop | superseded | locked | failed
    outcome: str
    skills: int = 0


async def draft(
    db: AsyncSession,
    job: Job,
    *,
    mode: str,
    requested_swot_version: int | None,
    confirm_overwrite: bool,
    requested_by: uuid.UUID | None,
) -> DraftOutcome:
    """The worker body of `pickready.draft_job_skills`. The caller commits.

    Stands down (INFO, nothing written) when the job is no longer `drafting`.
    The model is called with NO lock held; the writes then happen under the
    skills lock, after re-checking everything the model call may have raced:
    the lock, the draft state and, for a redraft without confirmation, the
    team's own skills.

    A failure writes `failed` with a fixed sentence and ZERO skill rows. There
    is no template draft (rule 6); the team can draft again or add their own.
    Raises `sutra.SutraUnavailable` after recording that state, so the task can
    persist it and re-raise for the error metric.
    """
    if job.skills_draft_status != SKILLS_DRAFT_DRAFTING:
        logger.info(
            "skills.draft_superseded job_id=%s status=%s", job.id, job.skills_draft_status
        )
        return DraftOutcome("superseded")
    if await assessment_contract.is_locked(db, job.id):
        return await _stand_down_locked(db, job)
    if mode == MODE_INITIAL and await has_any_row(db, job.id):
        # A redelivered first draft, or rows the team wrote meanwhile. Either
        # way the first draft has nothing left to do.
        job.skills_draft_status = SKILLS_DRAFT_DRAFTED
        job.skills_draft_error = None
        await db.flush()
        logger.info("skills.draft_noop job_id=%s rows_present=true", job.id)
        return DraftOutcome("noop")

    swot = await swot_analysis.get(db, job)
    if not swot_analysis.is_saved(swot):
        job.skills_draft_status = SKILLS_DRAFT_FAILED
        job.skills_draft_error = SWOT_NOT_SAVED_DETAIL
        await db.flush()
        return DraftOutcome("failed")
    assert swot is not None
    if requested_swot_version is not None and swot.version != requested_swot_version:
        logger.info(
            "skills.draft_newer_swot job_id=%s requested=%d current=%d",
            job.id, requested_swot_version, swot.version,
        )

    try:
        result = await sutra.draft_skills(db, job, swot.sections())
    except sutra.SutraUnavailable:
        await _record_failure(db, job, DRAFT_FAILED_DETAIL)
        raise

    await _begin_write_for_draft(db, job)
    if job.skills_draft_status != SKILLS_DRAFT_DRAFTING:
        return DraftOutcome("superseded")
    if await assessment_contract.is_locked(db, job.id):
        return await _stand_down_locked(db, job)
    rows = await _rows(db, job.id)
    if mode == MODE_INITIAL and rows:
        job.skills_draft_status = SKILLS_DRAFT_DRAFTED
        await db.flush()
        return DraftOutcome("noop")
    if mode == MODE_REDRAFT and not confirm_overwrite and any(
        row.is_active and row.authored_by == AUTHORED_HUMAN for row in rows
    ):
        await _record_failure(db, job, DRAFT_EDITED_MEANWHILE)
        return DraftOutcome("failed")

    now = _now()
    for row in rows:
        if row.is_active:
            row.is_active = False
            row.updated_at = now
    provenance = {
        "generated_by": "sutra",
        "model_id": result.model_id,
        "prompt_version": result.prompt_version,
        "swot_version": swot.version,
    }
    for skill in result.skills:
        found = _find(rows, skill.bucket, skill.name)
        if found is not None:
            _revive(found, ordinal=skill.priority)
            target = found
        else:
            target = JobCompetency(
                tenant_id=job.tenant_id,
                job_id=job.id,
                category=skill.bucket,
                name=skill.name,
                ordinal=skill.priority,
                is_active=True,
                required_level=ppi.DEFAULT_REQUIRED_LEVEL,
            )
            db.add(target)
            rows.append(target)
        target.authored_by = AUTHORED_SUTRA
        target.swot_origin = skill.swot_origin
        target.force_rank = skill.priority
        target.provenance_json = {**provenance, "source": skill.source}
        target.updated_at = now
    job.skills_draft_status = SKILLS_DRAFT_DRAFTED
    job.skills_draft_error = None
    job.skills_drafted_at = now
    job.skills_drafted_swot_version = swot.version
    if job.framework_generated_at is None:
        job.framework_generated_at = now
    await db.flush()
    await _mark_unsaved(db, job)
    audit_metadata = {
        "mode": mode,
        "skills": len(result.skills),
        "swot_version": swot.version,
        "model_id": result.model_id,
        "prompt_version": result.prompt_version,
    }
    if requested_by is not None:
        from app.models.user import User  # noqa: PLC0415

        requester = await db.get(User, requested_by)
        await record_agent_action(
            db,
            action="job_skills_drafted",
            agent_name="sutra",
            principal_user_id=requested_by,
            principal_role=(
                None if requester is None else getattr(requester.role, "value", requester.role)
            ),
            tenant_id=job.tenant_id,
            resource_type="job",
            resource_id=job.id,
            job_id=job.id,
            correlation_id=job.correlation_id,
            metadata=audit_metadata,
        )
    else:
        # The repair sweep has no human principal, and an agent column with no
        # principal is refused by `ck_audit_log_agent_has_principal`, so the
        # agent is named in the facts instead (the 2026-09-20 rule).
        await record_action(
            db,
            action="job_skills_drafted",
            actor_user_id=None,
            actor_role=None,
            tenant_id=job.tenant_id,
            resource_type="job",
            resource_id=job.id,
            job_id=job.id,
            correlation_id=job.correlation_id,
            metadata={**audit_metadata, "agent": "sutra", "requested_by": "reconcile_job_setup"},
        )
    logger.info(
        "skills.drafted job_id=%s mode=%s skills=%d swot_version=%d",
        job.id, mode, len(result.skills), swot.version,
    )
    return DraftOutcome("drafted", skills=len(result.skills))


async def _begin_write_for_draft(db: AsyncSession, job: Job) -> None:
    """The skills lock, then a fresh read of the job's draft state under it."""
    await locks.advisory_xact_lock(db, locks.SKILLS, job.id)
    await db.refresh(job)


async def _record_failure(db: AsyncSession, job: Job, detail: str) -> None:
    job.skills_draft_status = SKILLS_DRAFT_FAILED
    job.skills_draft_error = detail
    await db.flush()


async def _stand_down_locked(db: AsyncSession, job: Job) -> DraftOutcome:
    """A candidate started while the draft was on its way. The skills are the
    contract now; the draft is dropped and the state says so."""
    job.skills_draft_status = (
        SKILLS_DRAFT_DRAFTED if await has_any_row(db, job.id) else SKILLS_DRAFT_FAILED
    )
    job.skills_draft_error = (
        None if job.skills_draft_status == SKILLS_DRAFT_DRAFTED else SKILLS_LOCKED_DETAIL
    )
    await db.flush()
    logger.info("skills.draft_locked job_id=%s", job.id)
    return DraftOutcome("locked")


def stale_drafting_cutoff(now: datetime | None = None) -> datetime:
    """The `skills_draft_requested_at` before which a `drafting` job is lost."""
    return (now or _now()) - DRAFT_STALE_AFTER


async def abandon_lost_draft(db: AsyncSession, job: Job) -> None:
    """Finish a lost `drafting` state as failed, when it cannot be re-sent.

    `pickready.reconcile_job_setup` calls this for a draft that went missing
    and that it may not repeat on its own: the skills locked meanwhile, or a
    re-draft would replace the team's own skills and the sweep cannot confirm
    that for them. The state then reads "draft again", which is a decision for
    a person.
    """
    if job.skills_draft_status == SKILLS_DRAFT_DRAFTING:
        await _record_failure(db, job, DRAFT_FAILED_DETAIL)
