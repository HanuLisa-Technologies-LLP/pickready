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
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import cache
from app.core.config import get_settings
from app.services import audit, chunk_acl

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
            "ReadyPick's own customer catalogue, for platform staff only, over "
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
    very deletion), and video recordings, which the brief's own Stage A item
    2 places under the candidate's retention consents rather than under the
    job's lifetime.

    Runs INLINE in the close transaction, the Delete My Profile precedent:
    pure SQL against one job, and an irreversible mass delete must not race
    a rollback of the close that authorised it.
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
    questions = 0
    for table in ("candidate_questions", "candidate_technical_questions"):
        questions += (
            await session.execute(
                text(
                    f"DELETE FROM {table} "
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
