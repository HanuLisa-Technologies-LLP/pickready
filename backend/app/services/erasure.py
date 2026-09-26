"""Candidate erasure that reaches the vectors and the caches, not only the rows
(RPN-AI-UP-001 W9.5).

WHY A ROW DELETE IS NOT AN ERASURE
------------------------------------
Deleting `candidates` cascades to `profiles`, `job_candidate_links`,
`candidate_projects` and the rest, and it looks complete. It is not. Two classes
of the person's data survive it:

* **Vectors that no foreign key reaches.** `context_chunks` is keyed on
  `(source_type, source_id)` with its own `tenant_id` foreign key and no
  reference to `candidates` at all, so a resume's chunks and their embeddings
  outlive the candidate row entirely.
* **Caches.** Redis holds no foreign keys and answers every read that arrives
  before a TTL expires.

And a vector is not an anonymisation. Published inversion work recovers 50 to
70% of the input words from popular sentence embeddings, and because the
embedding model is public and queryable, dictionary attacks against stolen
vectors are practical, structurally like cracking password hashes. A residual
`vector(1024)` for an erased candidate is that candidate's resume in a form that
is inconvenient to read rather than impossible.

THE DATA MAP IS EXECUTABLE
----------------------------
`VECTOR_COLUMNS` is the classification, and `cascade_erasure` iterates it. A
data map written in prose drifts from the code the week after it is written;
this one cannot, because the erasure is derived from it and the test reads it.
Each entry says what the vector embeds, whether it is a candidate's personal
data, and how an erasure reaches it.

WHAT IS DELIBERATELY NOT ERASED
---------------------------------
The `audit_log` rows. `audit_log.candidate_id` is a plain column with no foreign
key, on purpose: an audit trail that a subject can delete is not an audit trail,
and the erasure itself writes one more row saying it happened.

RAISES, NEVER DEGRADES
------------------------
Every step raises on failure, including the cache purge. An erasure that
silently failed to clear a cache is the finding, and reporting success would
make it unfindable. This is the opposite of `core/cache`'s contract, which
degrades to "not cached" so a Redis outage cannot take a request down, and the
difference is deliberate: a slow page is not a data-protection breach.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache
from app.core.config import get_settings
from app.models.deletion import CandidateDeletionRequest
from app.services import audit, chunk_acl, deletion_requests

logger = logging.getLogger(__name__)

#: The audit action written when an erasure completes.
ACTION_CANDIDATE_ERASED = "candidate_data_erased"

#: The namespace every candidate-scoped cache key must be built under, so an
#: erasure can find keys nobody remembered to tell it about. A key written as
#: `cache.key("profile-summary", candidate_id)` is a key this module cannot see;
#: `cache.key(CANDIDATE_CACHE_NAMESPACE, candidate_id, "profile-summary")` is one
#: it deletes without being changed.
CANDIDATE_CACHE_NAMESPACE = "candidate"


@dataclass(frozen=True)
class VectorColumn:
    """One vector column in the product, classified.

    `candidate_pii` drives `cascade_erasure`: it is not documentation, it is the
    condition on the loop.
    """

    table: str
    column: str
    #: What the vector was computed from, in one clause.
    embeds: str
    #: True when inverting the vector would recover a person's own words.
    candidate_pii: bool
    #: How an erasure reaches it. Stated for every entry, including the ones an
    #: erasure does not touch, so "not erased" always carries its reason.
    erasure: str


#: THE DATA MAP. Every `vector(1024)` column in the schema is here; a new one
#: added without an entry fails `tests/test_erasure_cascade.py`.
VECTOR_COLUMNS: tuple[VectorColumn, ...] = (
    VectorColumn(
        table="profiles",
        column="embedding",
        embeds="the candidate's extracted resume text",
        candidate_pii=True,
        erasure="NULLed by cascade_erasure before the row is deleted",
    ),
    VectorColumn(
        table="profiles",
        column="embedding_shadow",
        embeds="the same resume text under a candidate embedding model",
        candidate_pii=True,
        erasure="NULLed by cascade_erasure before the row is deleted",
    ),
    VectorColumn(
        table="context_chunks",
        column="embedding",
        embeds="one verbatim slice of a resume, a JD or an assessment transcript",
        candidate_pii=True,
        erasure=(
            "the row is DELETED by cascade_erasure, matched on acl_candidate_id; "
            "no foreign key reaches this table from candidates, which is why a "
            "row delete alone leaves it behind"
        ),
    ),
    VectorColumn(
        table="context_chunks",
        column="embedding_shadow",
        embeds="the same slice under a candidate embedding model",
        candidate_pii=True,
        erasure="the row is DELETED by cascade_erasure alongside `embedding`",
    ),
    VectorColumn(
        table="jobs",
        column="embedding",
        embeds="the client's own job description text",
        candidate_pii=False,
        erasure=(
            "not touched. It contains no candidate data: the client wrote the "
            "text and publishes it on a public application page"
        ),
    ),
    VectorColumn(
        table="jobs",
        column="embedding_shadow",
        embeds="the same job description text under a candidate embedding model",
        candidate_pii=False,
        erasure="not touched, for the same reason as `jobs.embedding`",
    ),
    VectorColumn(
        table="jobs",
        column="reach_embedding",
        embeds="a job title and its primary skill names, for AI Reach",
        candidate_pii=False,
        erasure=(
            "not touched, and it is the ONE place cross-tenant vector similarity "
            "is a feature: AI Reach compares a prospect's role against "
            "Vivekium's own customer catalogue, for platform staff only, over "
            "text the client publishes. It embeds no resume, no candidate, no "
            "assessment and no score, and it returns a word rather than a "
            "number. The justification is recorded in migration 0092"
        ),
    ),
    VectorColumn(
        table="jobs",
        column="reach_embedding_shadow",
        embeds="the same AI Reach text under a candidate embedding model",
        candidate_pii=False,
        erasure="not touched, for the same reason as `jobs.reach_embedding`",
    ),
)


@dataclass(frozen=True)
class ErasureReceipt:
    """What an erasure actually did. Counts, never content."""

    candidate_id: uuid.UUID
    erased_at: datetime
    chunks_deleted: int
    profile_vectors_cleared: int
    projects_deleted: int
    cache_keys_deleted: int
    #: `table.column` for every entry in the data map this run acted on.
    vector_columns_handled: tuple[str, ...]
    #: The `users` row that could still have signed in. 0 is a normal answer:
    #: a databank candidate nobody ever invited has no sign-in account at all.
    sign_in_accounts_deleted: int = 0
    #: WHY this person was erased, when a clock decided rather than a person.
    #: It belongs in the audit row's FACTS and never in `actor_role`, which is
    #: `varchar(30)` and means the role of the human who acted. The consent
    #: sweep used to pass the reason there, and
    #: `inactive_for_the_retention_period` is thirty three characters, so the
    #: dormancy erasure could NEVER have committed: it raised
    #: StringDataRightTruncationError on its own audit write, every time,
    #: rolling the erasure back with it. Nothing caught it because the only
    #: armed test in the suite took the consent reason, which fits.
    reason: str | None = None

    def as_json(self) -> dict[str, object]:
        return {
            "candidate_id": str(self.candidate_id),
            "erased_at": self.erased_at.isoformat(),
            "chunks_deleted": self.chunks_deleted,
            "profile_vectors_cleared": self.profile_vectors_cleared,
            "projects_deleted": self.projects_deleted,
            "cache_keys_deleted": self.cache_keys_deleted,
            "vector_columns_handled": list(self.vector_columns_handled),
            "sign_in_accounts_deleted": self.sign_in_accounts_deleted,
            "reason": self.reason,
        }


#: The audit action written when a job closure erases its assessment data.
ACTION_JOB_ASSESSMENT_ERASED = "job_assessment_data_erased"


@dataclass(frozen=True)
class JobClosureReceipt:
    """What closing a job erased. Counts, never content."""

    job_id: uuid.UUID
    erased_at: datetime
    reports_deleted: int
    evaluations_deleted: int
    conversations_deleted: int
    questions_deleted: int
    chunks_deleted: int

    def as_json(self) -> dict[str, object]:
        return {
            "job_id": str(self.job_id),
            "erased_at": self.erased_at.isoformat(),
            "reports_deleted": self.reports_deleted,
            "evaluations_deleted": self.evaluations_deleted,
            "conversations_deleted": self.conversations_deleted,
            "questions_deleted": self.questions_deleted,
            "chunks_deleted": self.chunks_deleted,
        }


async def job_closure_erasure(
    session: AsyncSession, *, job_id: uuid.UUID
) -> JobClosureReceipt:
    """Erase a closed job's assessment data (vivekium C5, owner-ruled final).

    WHEN THIS RUNS CHANGED ON 2026-09-22, BY OWNER RULING. It used to run
    INLINE in the close transaction: "Runs INLINE in the close transaction,
    the Delete My Profile precedent ... an irreversible mass delete must not
    race a rollback of the close that authorised it." SUPERSEDED. Closure now
    WITHHOLDS the data and schedules its deletion thirty days out, and this
    function is called by `job_assessment_retention.purge_job` at the end of
    that window instead. The reason is the argument the old note was half of:
    closure is terminal and has no reopen, so an irreversible delete that
    could not race a rollback also could not survive a mistake, and a dispute
    raised a week later had nothing left to examine.

    WHAT DID NOT CHANGE IS ANYTHING BELOW THIS PARAGRAPH. The blast radius is
    identical, `tests/test_job_closure_erasure.py` still pins it against a
    real schema with a control job, and the one caller still wraps it in a
    transaction that owns the audit row. The old inline note's other half also
    still holds, in its new home: the purge is one transaction per job, so a
    failure rolls the whole deletion back rather than leaving half a job's
    reports gone.

    ITS CALLER MUST DELETE THE STORED MEDIA FIRST, and the reason is a foreign
    key. `video_recordings.conversation_id` is ON DELETE CASCADE, so removing
    `assessment_conversations` below takes the recording ROWS with it, and
    those rows are the only thing in the database that names the S3 objects.
    Calling this first would leave the media addressable for ever with nothing
    left to enumerate it, which is the orphan trap `cascade_erasure` exists
    for, arriving from the other direction.

    Stage B consent item 3 is the sentence this function makes true: "the
    assessment report for this job ... when the employer closes the position
    it is automatically and permanently removed." It deletes exactly what
    that item names, the PRISM reports, the Tatva scores and the transcripts,
    for every candidate on the job:

    * `functional_skills_reports` by job (report_dimensions and
      report_skill_evidence CASCADE from it),
    * `evaluations` by job (the working the report was composed from),
    * `assessment_conversations` by link (assessment_messages,
      assessment_answers and the proctoring rows CASCADE from it),
    * the per-candidate question sets, which carry the rubrics the answers
      were scored against,
    * `context_chunks` with source_type='assessment', whose source_id is the
      LINK id and which no foreign key reaches, the same orphan-vector trap
      `cascade_erasure` exists for.

    WHAT IT DELIBERATELY KEEPS. The `job_candidate_links` rows and their
    pipeline history (the process happened; consent item 3 scopes the
    deletion to the assessment ARTIFACTS), the `credit_ledger` rows (the
    billing fact names no candidate content, and deleting the answer to a
    billing dispute was the half of C5 the register refused), the
    `assessment_consents` rows (a consent record is the LEGITIMACY of this
    very deletion, AND UNTIL MIGRATION 0112 THAT SENTENCE WAS FALSE: that
    table's `conversation_id` was ON DELETE CASCADE, so the conversation
    delete below destroyed the consent record along with it. The prose was
    right and nothing enforced it, for as long as nothing asserted it. 0112
    makes the reference SET NULL and `tests/test_job_closure_erasure.py` now
    reads the row back), and video recordings, which the brief's own Stage A item
    2 places under the candidate's retention consents rather than under the
    job's lifetime.
    """
    links_sql = "SELECT id FROM job_candidate_links WHERE job_id = :job_id"

    chunks = (
        await session.execute(
            text(
                "DELETE FROM context_chunks WHERE source_type = 'assessment' "
                f"AND source_id IN ({links_sql}) RETURNING id"
            ),
            {"job_id": str(job_id)},
        )
    ).rowcount
    reports = (
        await session.execute(
            text(
                "DELETE FROM functional_skills_reports WHERE job_id = :job_id "
                "RETURNING id"
            ),
            {"job_id": str(job_id)},
        )
    ).rowcount
    evaluations = (
        await session.execute(
            text("DELETE FROM evaluations WHERE job_id = :job_id RETURNING id"),
            {"job_id": str(job_id)},
        )
    ).rowcount
    conversations = (
        await session.execute(
            text(
                "DELETE FROM assessment_conversations "
                f"WHERE job_candidate_link_id IN ({links_sql}) RETURNING id"
            ),
            {"job_id": str(job_id)},
        )
    ).rowcount
    # One questions table. The retired per-candidate technical track had a
    # second until migration 0128 dropped it (empty everywhere it ran), and a
    # DELETE naming a dropped table would fail every closure purge.
    questions = (
        await session.execute(
            text(
                "DELETE FROM candidate_questions "
                f"WHERE job_candidate_link_id IN ({links_sql}) RETURNING id"
            ),
            {"job_id": str(job_id)},
        )
    ).rowcount

    receipt = JobClosureReceipt(
        job_id=job_id,
        erased_at=datetime.now(timezone.utc),
        reports_deleted=int(reports or 0),
        evaluations_deleted=int(evaluations or 0),
        conversations_deleted=int(conversations or 0),
        questions_deleted=int(questions or 0),
        chunks_deleted=int(chunks or 0),
    )
    logger.info(
        "erasure.job_closure job_id=%s reports=%d evaluations=%d "
        "conversations=%d questions=%d chunks=%d",
        job_id,
        receipt.reports_deleted,
        receipt.evaluations_deleted,
        receipt.conversations_deleted,
        receipt.questions_deleted,
        receipt.chunks_deleted,
    )
    return receipt


def candidate_cache_patterns(candidate_id: uuid.UUID | str) -> tuple[str, ...]:
    """Every Redis key pattern that may hold this candidate's data."""
    scoped = cache.key(CANDIDATE_CACHE_NAMESPACE, str(candidate_id))
    return (scoped, f"{scoped}:*")


class CachePurgeFailed(RuntimeError):
    """The cache could not be reached, so the erasure is not complete."""


async def purge_candidate_cache(candidate_id: uuid.UUID | str) -> int:
    """Delete every cached value under the candidate namespace. Raises.

    Builds its own client rather than reusing `core/cache`, whose whole contract
    is to swallow a Redis failure and answer "not cached". That is right for a
    read path and wrong here: an erasure that could not reach Redis has not
    erased anything from it, and must say so.
    """
    import redis.asyncio as redis_asyncio  # noqa: PLC0415 -- one call path

    client = redis_asyncio.from_url(
        get_settings().redis_url,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    deleted = 0
    try:
        for pattern in candidate_cache_patterns(candidate_id):
            async for found in client.scan_iter(match=pattern, count=200):
                deleted += int(await client.delete(found))
    except Exception as exc:  # noqa: BLE001 -- re-raised as a named failure
        raise CachePurgeFailed(
            f"the candidate cache could not be purged: {type(exc).__name__}"
        ) from exc
    finally:
        await client.aclose()
    return deleted


async def cascade_erasure(
    session: AsyncSession,
    candidate_id: uuid.UUID | str,
    *,
    actor_user_id: uuid.UUID | str | None = None,
    actor_role: str | None = None,
    reason: str | None = None,
) -> ErasureReceipt:
    """Erase one candidate: rows, vectors and caches, in that dependency order.

    THE SESSION MUST ALREADY BE IN A BYPASS SCOPE (`core.db.superadmin_scope`).
    A candidate spans tenants by design and their chunks may sit under a tenant
    the erasing operator is not scoped to, so a tenant-scoped session would
    delete the subset it can see and report a complete erasure.

    Ordering matters and is not arbitrary. Vectors are cleared and chunks
    deleted BEFORE the candidate row goes, so a failure part way through leaves
    a candidate row with no vectors rather than vectors with no candidate row:
    the first is a visible, repeatable half-erasure and the second is
    unreachable data.

    Does not commit. The caller owns the transaction, because an erasure and the
    audit row that records it must land together or not at all.
    """
    identifier = uuid.UUID(str(candidate_id))
    now = datetime.now(timezone.utc)

    # 1. Chunks. Matched on the ACL column, which migration 0092's trigger
    # maintains for every writer, so this statement does not need to know which
    # source-id shape produced which chunk.
    chunks = await session.execute(
        text(
            "DELETE FROM context_chunks WHERE "
            f"{chunk_acl.candidate_scope_clause()} RETURNING id"
        ),
        chunk_acl.candidate_scope_params(identifier),
    )
    chunks_deleted = len(chunks.fetchall())

    # 2. Profile vectors and the derived text they were computed from. Cleared
    # explicitly rather than relying on the cascade, so the receipt states a
    # number somebody can check and so a cascade that is later loosened to SET
    # NULL does not silently stop erasing.
    profiles = await session.execute(
        text(
            """
            UPDATE profiles
               SET embedding = NULL,
                   embedding_shadow = NULL,
                   resume_text = NULL,
                   parsed_fields_json = NULL,
                   aspects_json = NULL,
                   intake_scan_json = NULL
             WHERE candidate_id = :candidate_id
            RETURNING id
            """
        ),
        {"candidate_id": str(identifier)},
    )
    profile_vectors_cleared = len(profiles.fetchall())

    # 3. Derived project evidence. Deleted rather than cleared: the originals
    # were already deleted by the intake pipeline, so this row IS the copy.
    projects = await session.execute(
        text(
            "DELETE FROM candidate_projects WHERE candidate_id = :candidate_id "
            "RETURNING id"
        ),
        {"candidate_id": str(identifier)},
    )
    projects_deleted = len(projects.fetchall())

    # 4. The person. Every remaining reference cascades or is set null.
    #
    # Read the sign-in account BEFORE the delete, because `candidates.user_id`
    # is the only thing that names it and the next statement removes the row
    # holding it.
    sign_in_account = (
        await session.execute(
            text("SELECT user_id FROM candidates WHERE id = :candidate_id"),
            {"candidate_id": str(identifier)},
        )
    ).scalar_one_or_none()

    await session.execute(
        text("DELETE FROM candidates WHERE id = :candidate_id"),
        {"candidate_id": str(identifier)},
    )

    # 4b. THE SIGN-IN ACCOUNT, OR THE ERASURE LEAVES A GHOST THAT CAN LOG IN.
    #
    # `candidates.user_id` is ON DELETE SET NULL, so the statement above leaves
    # the `users` row untouched. Without this step the person still holds a
    # working Firebase identity and a working `pr_access` cookie, signs in
    # successfully, and every portal route answers 404 "No candidate record
    # yet" forever: erased in substance, and visibly still an account holder.
    # The right to erasure is not satisfied by a profile that still has a door.
    #
    # Deleting it is also what makes "start entirely from scratch" true rather
    # than aspirational: `api/auth` provisions a fresh User AND a fresh
    # Candidate on the first sign-in of an identity it does not recognise, so
    # removing the row restores exactly the state before registration.
    #
    # ROLE-GUARDED, and the guard is not decoration. A staff account is reached
    # by `users.id` from a dozen tables and two of them are ON DELETE RESTRICT
    # (`review_dispositions.decided_by` and `bgv_verifications.decided_by`,
    # both of which record that a HUMAN decided). A candidate is never written
    # to either column, so this delete cannot hit a RESTRICT today; if that
    # ever stops being true the statement RAISES and the whole transaction
    # rolls back, which is the safe direction: the candidate is told the
    # deletion failed rather than being handed a half-erasure.
    #
    # THE FIREBASE IDENTITY IS NOT THIS FUNCTION'S, and that split is
    # deliberate (2026-09-24). A person who asks to be deleted
    # (`api/portal.delete_my_profile`) has their sign-in identity deleted too,
    # inline, before the same transaction commits, so the next sign-in cannot
    # silently recreate them. The worker sweeps that also call this function
    # (consent expiry, dormancy) do NOT: workers hold no Firebase key by
    # design, and a person who never asked to leave keeps the door back. The
    # "fresh start" sentence above is exactly what such a person gets.
    sign_in_accounts_deleted = 0
    if sign_in_account is not None:
        deleted_users = await session.execute(
            text(
                "DELETE FROM users WHERE id = :user_id AND role = 'candidate' "
                "RETURNING id"
            ),
            {"user_id": str(sign_in_account)},
        )
        sign_in_accounts_deleted = len(deleted_users.fetchall())

    # 5. Caches. After the rows, so nothing can repopulate a key from a row that
    # still exists.
    cache_keys_deleted = await purge_candidate_cache(identifier)

    handled = tuple(
        f"{entry.table}.{entry.column}"
        for entry in VECTOR_COLUMNS
        if entry.candidate_pii
    )
    receipt = ErasureReceipt(
        candidate_id=identifier,
        erased_at=now,
        chunks_deleted=chunks_deleted,
        profile_vectors_cleared=profile_vectors_cleared,
        projects_deleted=projects_deleted,
        cache_keys_deleted=cache_keys_deleted,
        vector_columns_handled=handled,
        sign_in_accounts_deleted=sign_in_accounts_deleted,
        reason=reason,
    )

    # 6. The record that it happened. `audit_log.candidate_id` has no foreign
    # key to `candidates`, so this row survives the deletion above, which is the
    # whole reason it is written last and the whole reason the column is not a
    # foreign key.
    await audit.record_action(
        session,
        action=ACTION_CANDIDATE_ERASED,
        actor_user_id=actor_user_id,
        actor_role=actor_role,
        tenant_id=None,
        resource_type="candidate",
        resource_id=identifier,
        candidate_id=identifier,
        new_state=receipt.as_json(),
    )
    logger.info(
        "erasure.cascade_complete candidate_id=%s chunks=%d profiles=%d "
        "projects=%d cache_keys=%d sign_in_accounts=%d",
        identifier,
        chunks_deleted,
        profile_vectors_cleared,
        projects_deleted,
        cache_keys_deleted,
        sign_in_accounts_deleted,
    )
    return receipt


# ── The objects, which a row delete never reached ───────────────────────────
#
# THE HOLE THIS CLOSES. Everything above deletes ROWS, VECTORS and CACHE KEYS,
# and `account_deletion.DELETION_WARNINGS` promises the candidate that their
# "complete profile, including all background verification records and
# assessment data, will be permanently deleted". Their resume, their assessment
# recording and any staged project original live in an OBJECT STORE that no
# foreign key reaches and no cascade touches, so every one of them survived an
# erasure whose own warning screen said they had not.
#
# It is the same class of miss as `context_chunks`: data the database does not
# know it is holding. The difference is that an object store fails
# independently of Postgres, so this half cannot be settled by a transaction
# and needs the record and the sweep that `services/deletion_requests`
# describes.

#: What an enumerated object WAS, for an operator reading a stuck request. The
#: kind never changes behaviour, with one exception that is not a behaviour at
#: all: `KIND_RESUME_LEGACY` names an object this product CANNOT delete, and
#: the deleter refuses it rather than reporting a success it did not achieve.
KIND_RESUME = "resume"
KIND_RESUME_LEGACY = "resume_legacy_object_store"
KIND_VIDEO_RAW = "assessment_video_raw"
KIND_VIDEO_COMPRESSED = "assessment_video_compressed"
KIND_PROJECT_INTAKE = "project_intake_original"
KIND_CONVERSATION_ATTACHMENT = "conversation_attachment"


class LegacyObjectNotDeletable(RuntimeError):
    """A stored object lives in a store other than the current one.

    Raised rather than skipped, and this is the whole reason the type exists.
    Calling `object_storage.delete` on a key from another store reaches the
    current store, which has no such key, and the HEAD that follows then
    answers "absent" -- so the erasure would report the candidate's resume
    deleted while the actual bytes sat untouched elsewhere. A deletion that
    cannot be performed must be visible, not confirmed.

    Decided by `resume_storage.is_in_current_store`, the same answer the
    resume READ path uses, rather than by naming the pre-AWS provider: that
    constant and its readers went in the 2026-09 final sweeps, and the
    database CHECK still admits the old value until a migration narrows it,
    so the refusal must not depend on a name.
    """


def _in_current_store(provider: str | None, url: str | None) -> bool:
    """`resume_storage.is_in_current_store`, imported late to avoid a cycle."""
    from app.services import resume_storage  # noqa: PLC0415 -- avoids a cycle

    return resume_storage.is_in_current_store(provider, url)


async def candidate_object_keys(
    session: AsyncSession, candidate_id: uuid.UUID | str
) -> tuple[dict[str, str], ...]:
    """Every object key this candidate owns, across every store in the product.

    READ THIS BEFORE THE ROWS ARE ERASED. Nothing in the database names these
    objects except the rows `cascade_erasure` is about to delete, so an
    enumeration run afterwards returns an empty tuple and a caller acting on it
    would report a complete deletion of nothing.

    Raw SQL for the same reason the cascade uses it: several of these tables
    have no ORM relationship to `candidates` and one of them (the conversation
    attachment) is two joins away from it.

    THE SESSION MUST BE IN A BYPASS SCOPE. A candidate's recordings and
    attachments sit under tenants the erasing session is not scoped to, and a
    tenant-scoped read here would enumerate the subset it can see and leave the
    rest addressable for ever.
    """
    identifier = uuid.UUID(str(candidate_id))
    params = {"candidate_id": str(identifier)}
    found: list[dict[str, str]] = []

    # 1. Resumes. The key is `resume_public_id`; `resume_storage_provider`
    # says which store it is in, and a row written before the AWS migration is
    # named rather than silently mishandled (see LegacyObjectNotDeletable).
    resumes = await session.execute(
        text(
            "SELECT resume_public_id, resume_storage_provider, resume_url "
            "FROM profiles "
            "WHERE candidate_id = :candidate_id "
            "AND resume_public_id IS NOT NULL AND btrim(resume_public_id) <> ''"
        ),
        params,
    )
    for key, provider, url in resumes.all():
        legacy = not _in_current_store(provider, url)
        found.append(
            {"key": str(key), "kind": KIND_RESUME_LEGACY if legacy else KIND_RESUME}
        )

    # 2. Assessment recordings, BOTH keys. The raw object is normally deleted
    # by the video pipeline once the compressed one is HEAD-verified, so
    # `s3_raw_key` is usually already gone; it is enumerated anyway, because
    # "usually already gone" is not a property an erasure may rely on, and a
    # recording whose pipeline failed half way is exactly the row that still
    # has one.
    videos = await session.execute(
        text(
            "SELECT s3_raw_key, s3_compressed_key FROM video_recordings "
            "WHERE candidate_id = :candidate_id"
        ),
        params,
    )
    for raw_key, compressed_key in videos.all():
        if raw_key:
            found.append({"key": str(raw_key), "kind": KIND_VIDEO_RAW})
        if compressed_key:
            found.append({"key": str(compressed_key), "kind": KIND_VIDEO_COMPRESSED})

    # 3. Staged project originals. `candidate_projects` rows are DELETED by the
    # cascade above, and the brief's own contract is that these objects are
    # temporary and deleted once the derived evidence is durable. A project
    # whose deletion had failed still carries its keys here, and those are
    # precisely the ones an erasure must not leave behind.
    projects = await session.execute(
        text(
            "SELECT intake_objects_json FROM candidate_projects "
            "WHERE candidate_id = :candidate_id "
            "AND intake_objects_json IS NOT NULL"
        ),
        params,
    )
    for (rows,) in projects.all():
        for row in rows or []:
            key = str((row or {}).get("key") or "")
            if key:
                found.append({"key": key, "kind": KIND_PROJECT_INTAKE})

    # 4. Files on the candidate's conversations. Two joins from `candidates`,
    # which is why a cascade reaches the ROWS and nothing reaches the bytes.
    # BOTH kinds of thread are included on purpose: a file the candidate
    # attached is theirs, and a file on the BGV thread ABOUT them is a document
    # obtained to verify them, which the deletion warning names explicitly.
    attachments = await session.execute(
        text(
            "SELECT a.object_key FROM conversation_attachments a "
            "JOIN conversation_messages m ON m.id = a.message_id "
            "JOIN conversations c ON c.id = m.conversation_id "
            "WHERE c.candidate_id = :candidate_id "
            "AND a.object_key IS NOT NULL AND btrim(a.object_key) <> ''"
        ),
        params,
    )
    for (key,) in attachments.all():
        found.append({"key": str(key), "kind": KIND_CONVERSATION_ATTACHMENT})

    # Deduplicated on (key, kind) while KEEPING ORDER. Two profiles holding the
    # same content-addressed resume is the normal case for a candidate who
    # applied twice with the same file, and deleting one key twice would make
    # `objects_total` a number that can never be reached.
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, str]] = []
    for entry in found:
        signature = (entry["key"], entry["kind"])
        if signature not in seen:
            seen.add(signature)
            unique.append(entry)
    return tuple(unique)


def delete_object_verified(entry: dict[str, str]) -> bool:
    """Delete one enumerated object and CONFIRM with a HEAD that it is gone.

    The project-intake contract, applied to every store: the answer is the
    HEAD, not the delete, because "the call returned" is the same class of
    non-evidence as "the pipeline was green". Synchronous, like every function
    in `object_storage`; the caller owns the threadpool hop.

    `video/storage.delete_verified` IS the delete-then-HEAD pair, over the same
    shared client every kind here is stored through, so one call covers all of
    them. Writing the pair again for resumes would be two helpers that must
    agree about what "confirmed" means and will eventually not.
    """
    from app.services.video import storage as video_storage  # noqa: PLC0415

    if entry.get("kind") == KIND_RESUME_LEGACY:
        raise LegacyObjectNotDeletable(
            "a resume object predates the current object store and cannot be "
            "deleted from here"
        )
    key = str(entry.get("key") or "")
    if not key:
        # An empty key is a defect in the enumeration, not an object that does
        # not exist, and treating it as "already gone" would let a bug in
        # `candidate_object_keys` read as a successful erasure.
        raise ValueError("an enumerated object carried no key")
    return video_storage.delete_verified(key)


async def delete_candidate_objects(
    entries: Sequence[dict[str, str]],
) -> deletion_requests.ObjectDeletionOutcome:
    """One pass over the enumerated objects. Deletes, verifies, counts.

    NEVER RAISES, and that is the difference between this and every other
    function in this module. The rest of an erasure is one transaction and
    raising is how a half-written one is rolled back; this half has no
    transaction to roll back and no way to undo the objects it already
    removed, so an outage part way through must leave a SHORTER remaining
    list and a recorded reason rather than discarding the work.

    A failure is recorded as a CLASS NAME. The record outlives the rows, so a
    message that could quote one must never reach it.
    """
    from starlette.concurrency import run_in_threadpool  # noqa: PLC0415

    remaining: list[dict[str, str]] = []
    deleted = 0
    failure: str | None = None
    for entry in entries:
        try:
            gone = await run_in_threadpool(delete_object_verified, entry)
        except Exception as exc:  # noqa: BLE001 -- recorded, never swallowed
            failure = type(exc).__name__
            remaining.append(dict(entry))
            continue
        if gone:
            deleted += 1
        else:
            # The delete call returned and the object is STILL THERE. That is
            # not an error anything raised, which is exactly why the HEAD is
            # the answer rather than the call.
            failure = failure or "ObjectStillPresent"
            remaining.append(dict(entry))
    return deletion_requests.ObjectDeletionOutcome(
        deleted=deleted, remaining=tuple(remaining), failure=failure
    )


# ── The request record, so a half-finished erasure is findable ──────────────


async def open_deletion_request(
    session: AsyncSession,
    candidate_id: uuid.UUID | str,
    *,
    reason: str,
    requested_by_user_id: uuid.UUID | str | None = None,
) -> CandidateDeletionRequest:
    """Record that this erasure is starting, and capture the object keys.

    WRITTEN BEFORE THE ROWS GO, not after. A record written afterwards cannot
    exist for the failure it is supposed to describe: if the process dies
    between the cascade and the insert, the candidate is erased from the
    database, their documents are still in the store, and nothing anywhere
    knows either fact.

    The keys are enumerated here for the same reason. After the cascade
    nothing in the database names them.
    """
    identifier = uuid.UUID(str(candidate_id))
    keys = await candidate_object_keys(session, identifier)
    request = CandidateDeletionRequest(
        candidate_id=identifier,
        state=deletion_requests.STATE_PENDING,
        reason=reason,
        requested_by_user_id=(
            uuid.UUID(str(requested_by_user_id))
            if requested_by_user_id is not None
            else None
        ),
        requested_at=datetime.now(timezone.utc),
        object_keys_json=[dict(entry) for entry in keys],
        objects_total=len(keys),
        objects_deleted=0,
        deletion_attempts=0,
    )
    session.add(request)
    await session.flush()
    return request


async def mark_rows_erased(
    session: AsyncSession, request: CandidateDeletionRequest
) -> None:
    """Advance the record now that the database half is done.

    Goes through `assert_transition`, so the one place this product moves an
    erasure forward is the one place the machine is consulted.
    """
    deletion_requests.assert_transition(
        request.state, deletion_requests.STATE_ROWS_ERASED
    )
    now = datetime.now(timezone.utc)
    request.state = deletion_requests.STATE_ROWS_ERASED
    request.rows_erased_at = now
    request.updated_at = now
    await session.flush()


async def run_object_deletion(
    session: AsyncSession, request: CandidateDeletionRequest
) -> deletion_requests.ObjectDeletionOutcome:
    """One pass over this request's outstanding objects, recorded on the row.

    Idempotent and resumable: the remaining list shrinks, the counters only go
    up, and a request with nothing left completes. Safe to call on a request
    that is already complete, which is what makes a redelivered dispatch and a
    sweep arriving at the same moment harmless.
    """
    if deletion_requests.is_terminal(request.state):
        return deletion_requests.ObjectDeletionOutcome(deleted=0, remaining=())

    entries = [dict(entry) for entry in (request.object_keys_json or [])]
    outcome = await delete_candidate_objects(entries)
    now = datetime.now(timezone.utc)
    request.object_keys_json = [dict(entry) for entry in outcome.remaining]
    request.objects_deleted = int(request.objects_deleted or 0) + outcome.deleted
    request.deletion_attempts = int(request.deletion_attempts or 0) + 1
    request.last_failure = outcome.failure
    request.updated_at = now

    target = deletion_requests.next_state_after(outcome, current=request.state)
    if target != request.state:
        deletion_requests.assert_transition(request.state, target)
        request.state = target
        request.completed_at = now
    await session.flush()
    logger.info(
        "erasure.objects candidate_id=%s state=%s deleted=%d of %d remaining=%d "
        "attempts=%d failure=%s",
        request.candidate_id,
        request.state,
        request.objects_deleted,
        request.objects_total,
        len(outcome.remaining),
        request.deletion_attempts,
        outcome.failure,
    )
    return outcome
