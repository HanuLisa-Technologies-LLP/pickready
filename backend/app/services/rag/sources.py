"""What a document IS, for the retrieval index, and which ones are missing.

RPN-AI-UP-001 W2. `rag/index.index_document` knows how to write chunks and
`rag/chunking` knows how to cut them, but nothing knew where the text lives.
That gap is why `index_document` had no caller for its whole existence: wiring
it required answering "given a resume id, what is the text and whose tenant is
it", and the answer was nowhere.

It is here, once, because there are two callers that must agree:
`pickready.index_document`, which indexes one document after it changes, and
`pickready.reconcile_context_index`, which finds the ones no dispatch ever
reached. Two copies of "a resume is `profiles.resume_text`" would drift, and
the drift would show up as a sweep that repairs documents forever because it
disagrees with the indexer about what it just indexed.

THE SWEEP ASKS THE TABLE, AND ONLY THE QUESTION IT CAN ANSWER HONESTLY
----------------------------------------------------------------------
`pending()` finds documents that have text and **no chunk rows at all**. It
deliberately does not try to find STALE chunks by recomputing the document
fingerprint in SQL. `chunking.source_version` is `sha256` over Python's
whitespace normalisation, and reimplementing that in Postgres would be a second
implementation of one concept whose disagreement is invisible: the sweep would
re-index every document on every pass, forever, and the only symptom would be a
bill.

Staleness is handled where it is cheap and certain -- at the call sites, which
dispatch on change, into an indexer that is already incremental by content
hash. The sweep exists for the failure the call sites cannot cover: a dispatch
that never arrived. That is `reconcile_job_setup`'s exact shape, and it is the
lesson "a timestamp is not evidence that work happened" applied to the index.

WHY A RESUME IS INDEXED UNDER `profiles.source_tenant_id`
---------------------------------------------------------
`context_chunks.tenant_id` is NOT NULL and the index is UNIQUE on
`(source_type, source_id, ordinal)`, so one document belongs to exactly one
tenant. A profile can be linked to jobs in several tenants, so keying the
chunks off the link's tenant would make re-indexing flip the owner back and
forth and hand the losing tenant's rows to the winner.

`source_tenant_id` is the tenant the profile was created under, and it is set
on every real creation path: the recruiter's candidate form, the databank
upload, and the candidate's own application. The one place that clears it is
the tenant-deletion anonymisation in `api/admin.py`. A profile with no tenant
is therefore not indexable under this schema, and it is REPORTED rather than
skipped silently -- a document that can never be retrieved is worth knowing
about, and a sweep that quietly ignores rows is how "nothing to do" and "not
running" produce the same empty log.

WHY PROJECT EVIDENCE IS NOT A SOURCE TYPE HERE
-----------------------------------------------
RPN-AI-UP-001 W2.1 lists project evidence completion as a call site. It is
deliberately absent, and this is the reason rather than an oversight. Project
evidence already reaches generation through `services/projects/context.py`,
which joins the DERIVED evidence into per-candidate question generation. The
originals are deleted by design and the derived evidence is a validated
structure, not prose. Indexing it here would put one artifact in two retrieval
paths that would then disagree about which is authoritative, and it would put
`ai_interpretation_json` -- model output -- one join away from being cited as
verbatim evidence. Revisit only with a decision about which path owns it.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rag import chunking

logger = logging.getLogger(__name__)

#: Every source type this module can load, in the order `pending()` sweeps
#: them. `chunking` defines the same three; a fourth needs a chunker, a loader
#: and a pending query, which is the point of keeping the list closed.
SOURCE_TYPES: tuple[str, ...] = (
    chunking.SOURCE_JD,
    chunking.SOURCE_RESUME,
    chunking.SOURCE_ASSESSMENT,
)


#: "This column holds something worth indexing", as ONE definition.
#:
#: THE FIRST VERSION HAD TWO, AND THEY DISAGREED. `pending()` asked SQL
#: `btrim(col) <> ''` while the loaders asked Python `not body.strip()`, and
#: those are not the same test: `btrim` with no second argument strips SPACES
#: only, while `str.strip()` strips every whitespace character. So a JD holding
#: "\n\n" was REPORTED as needing indexing and then LOADED as nothing.
#:
#: That disagreement is precisely the failure this module's docstring warns
#: about in the other direction: the sweep queues the document, the task
#: no-ops, the document still has no chunks, and the next hour does it again --
#: forever, with a bill as the only symptom, because every individual step
#: behaves correctly. It was caught by running the two against each other on a
#: real database rather than by reading them.
#:
#: So the test lives in SQL, once, and the loaders ASK for it rather than
#: reimplementing it. `E'...'` is an escape string literal; the class is the
#: six ASCII whitespace characters, which is what `btrim` needs spelled out.
_WHITESPACE = r"E' \t\r\n\f\v'"


def _has_text(column: str) -> str:
    return f"btrim({column}, {_WHITESPACE}) <> ''"


class UnknownSourceType(ValueError):
    """A source type with no loader.

    Raised rather than ignored, for `registry.UnknownTask`'s reason: a dispatch
    naming a type nothing can load must fail where somebody sees it, not
    succeed having indexed nothing.
    """


@dataclass(frozen=True)
class Document:
    """One document, resolved to everything the indexer needs."""

    tenant_id: uuid.UUID
    source_type: str
    source_id: uuid.UUID
    #: The whole document, used for the version fingerprint. For the assessment
    #: type it is the joined transcript, and `chunks` carries the real units.
    text: str
    #: Pre-split units, for sources whose chunk boundary is not textual. An
    #: assessment's unit is a question-and-answer PAIR: a question without its
    #: answer retrieves for the topic and proves nothing.
    chunks: list[chunking.Chunk] | None = None


async def _load_jd(session: AsyncSession, source_id: uuid.UUID) -> Document | None:
    row = (
        await session.execute(
            text(
                f"""
                SELECT tenant_id,
                       COALESCE(jd_markdown, '') AS body,
                       COALESCE({_has_text('jd_markdown')}, FALSE) AS has_text
                  FROM jobs
                 WHERE id = :id
                """
            ),
            {"id": str(source_id)},
        )
    ).first()
    if row is None or not row.has_text:
        return None
    return Document(
        tenant_id=row.tenant_id,
        source_type=chunking.SOURCE_JD,
        source_id=source_id,
        text=row.body,
    )


async def _load_resume(session: AsyncSession, source_id: uuid.UUID) -> Document | None:
    row = (
        await session.execute(
            text(
                f"""
                SELECT p.source_tenant_id                    AS source_tenant_id,
                       COALESCE(p.resume_text, '')           AS body,
                       COALESCE({_has_text('p.resume_text')}, FALSE) AS has_text,
                       (t.id IS NOT NULL)                    AS tenant_exists
                  FROM profiles p
                  LEFT JOIN tenants t ON t.id = p.source_tenant_id
                 WHERE p.id = :id
                """
            ),
            {"id": str(source_id)},
        )
    ).first()
    if row is None or not row.has_text:
        return None
    if not row.tenant_exists:
        # THE TENANT MUST EXIST, NOT MERELY BE NON-NULL, AND THAT IS A REAL
        # DISTINCTION HERE.
        #
        # `profiles.source_tenant_id` is a plain nullable UUID with NO foreign
        # key (see `models/candidate.py`), because a profile is shared across
        # tenants via the databank. `context_chunks.tenant_id` DOES have one.
        # So a profile whose tenant was deleted carries a dangling reference
        # that reads as perfectly valid until the INSERT, which then fails with
        # a foreign key violation.
        #
        # Checking only for NULL would make that document a POISON PILL: the
        # task would burn all three attempts, the sweep would find it again an
        # hour later because it still has no chunks, and it would do that
        # forever. Found by running the sweep against a real database with an
        # orphaned profile in it, which is the only way it surfaces.
        logger.warning(
            "rag.sources.unindexable source_type=resume source_id=%s "
            "tenant_id=%s reason=%s",
            source_id,
            row.source_tenant_id,
            "no_source_tenant_id" if row.source_tenant_id is None else "tenant_missing",
        )
        return None
    return Document(
        tenant_id=row.source_tenant_id,
        source_type=chunking.SOURCE_RESUME,
        source_id=source_id,
        text=row.body,
    )


async def _assessment_exchanges(
    session: AsyncSession, link_id: uuid.UUID
) -> tuple[uuid.UUID | None, list[dict[str, str]], list[uuid.UUID]]:
    """(tenant, exchanges, answer message ids) for one application's transcript.

    ONE pairing walk, shared by the loader and by `exchange_ordinals`. The
    exchange INDEX is the chunk ordinal (`chunking.chunk_exchanges` enumerates
    before it skips an empty answer), so the only way to say "the chunk that
    holds this answer" without a second, drifting copy of the walk is to ask
    the walk that produced the ordinal.
    """
    rows = (
        await session.execute(
            text(
                """
                SELECT l.tenant_id AS tenant_id,
                       m.id        AS message_id,
                       m.speaker   AS speaker,
                       m.content   AS content
                  FROM job_candidate_links l
                  JOIN assessment_conversations c ON c.job_candidate_link_id = l.id
                  JOIN assessment_messages m      ON m.conversation_id = c.id
                 WHERE l.id = :id
                 ORDER BY c.created_at, m.ordinal
                """
            ),
            {"id": str(link_id)},
        )
    ).all()
    if not rows:
        return None, [], []

    # Walk in order, never zip alternate rows: the last question of an
    # abandoned assessment has no answer, and zipping would pair it with
    # somebody else's. Same rule as `tools.implementations.pair_exchanges` and
    # as the recruiter transcript route.
    exchanges: list[dict[str, str]] = []
    answer_ids: list[uuid.UUID] = []
    pending_question: str | None = None
    for row in rows:
        if row.speaker == "agent":
            pending_question = row.content
            continue
        if pending_question is None:
            continue
        exchanges.append({"question": pending_question, "answer": row.content})
        answer_ids.append(row.message_id)
        pending_question = None
    return rows[0].tenant_id, exchanges, answer_ids


async def exchange_ordinals(
    session: AsyncSession,
    *,
    link_id: uuid.UUID,
    answer_message_ids: Sequence[uuid.UUID],
) -> frozenset[int]:
    """The assessment chunk ordinals that hold these ANSWER messages.

    Used by the `retrieve_context` tool to leave a skill's own answers out of
    the passages retrieved to judge that skill: evidence that merely restates
    the answer being graded is the answer graded twice. An id that is not an
    answer on this link (a question, another link's message, an invented id)
    maps to nothing rather than raising: exclusion can only ever remove, so an
    unmatched id costs nothing and names nothing.
    """
    wanted = {str(message_id) for message_id in answer_message_ids}
    if not wanted:
        return frozenset()
    _tenant, _exchanges, answer_ids = await _assessment_exchanges(session, link_id)
    return frozenset(
        index for index, message_id in enumerate(answer_ids)
        if str(message_id) in wanted
    )


async def _load_assessment(
    session: AsyncSession, source_id: uuid.UUID
) -> Document | None:
    """The transcript of one application, keyed on the LINK.

    The link rather than the conversation, for the reason
    `GET /assessments/transcripts/links/{link_id}` is keyed that way: the
    transcript exists from the first answer, it outlives any one conversation
    row, and every consumer of assessment evidence already holds a link id.
    """
    tenant_id, exchanges, _answer_ids = await _assessment_exchanges(
        session, source_id
    )
    if tenant_id is None:
        return None

    chunks = chunking.chunk_exchanges(exchanges)
    if not chunks:
        return None
    return Document(
        tenant_id=tenant_id,
        source_type=chunking.SOURCE_ASSESSMENT,
        source_id=source_id,
        text="\n\n".join(chunk.content for chunk in chunks),
        chunks=chunks,
    )


_LOADERS = {
    chunking.SOURCE_JD: _load_jd,
    chunking.SOURCE_RESUME: _load_resume,
    chunking.SOURCE_ASSESSMENT: _load_assessment,
}


async def load(
    session: AsyncSession, *, source_type: str, source_id: uuid.UUID
) -> Document | None:
    """Resolve one document, or None when there is nothing to index.

    None is a legitimate outcome and covers four real states: the row was
    deleted between the dispatch and the run, a JD is still a draft, a resume
    has not been parsed yet, and an assessment has no answered exchange. None
    of those is a failure, and raising on them would spend a retry budget
    against a state that is not going to change on its own.
    """
    loader = _LOADERS.get(source_type)
    if loader is None:
        raise UnknownSourceType(
            f"{source_type!r} has no loader; known types are {sorted(_LOADERS)}"
        )
    return await loader(session, source_id)


#: Documents that have text and no chunk rows at all, per source type. Pure
#: relational questions -- no hashing, no text loaded -- so the sweep stays
#: cheap enough to run hourly against a growing table.
_PENDING_SQL: dict[str, str] = {
    chunking.SOURCE_JD: f"""
        SELECT j.id AS source_id
          FROM jobs j
         WHERE j.jd_markdown IS NOT NULL
           AND {_has_text('j.jd_markdown')}
           AND NOT EXISTS (
                 SELECT 1 FROM context_chunks c
                  WHERE c.source_type = 'jd' AND c.source_id = j.id
               )
         ORDER BY j.created_at DESC
         LIMIT :limit
    """,
    chunking.SOURCE_RESUME: f"""
        SELECT p.id AS source_id
          FROM profiles p
         WHERE p.resume_text IS NOT NULL
           AND {_has_text('p.resume_text')}
           AND EXISTS (
                 SELECT 1 FROM tenants t WHERE t.id = p.source_tenant_id
               )
           AND NOT EXISTS (
                 SELECT 1 FROM context_chunks c
                  WHERE c.source_type = 'resume' AND c.source_id = p.id
               )
         ORDER BY p.created_at DESC
         LIMIT :limit
    """,
    chunking.SOURCE_ASSESSMENT: """
        SELECT DISTINCT l.id AS source_id
          FROM job_candidate_links l
          JOIN assessment_conversations c ON c.job_candidate_link_id = l.id
          JOIN assessment_messages m      ON m.conversation_id = c.id
         WHERE m.speaker = 'candidate'
           AND NOT EXISTS (
                 SELECT 1 FROM context_chunks k
                  WHERE k.source_type = 'assessment' AND k.source_id = l.id
               )
         LIMIT :limit
    """,
}


async def pending(session: AsyncSession, *, limit: int) -> list[tuple[str, uuid.UUID]]:
    """(source_type, source_id) for every document with text and no chunks.

    The budget is shared across the three types rather than applied per type,
    so one source with a large backlog cannot starve the others out of every
    sweep forever.
    """
    found: list[tuple[str, uuid.UUID]] = []
    for source_type in SOURCE_TYPES:
        remaining = limit - len(found)
        if remaining <= 0:
            break
        rows = await session.execute(
            text(_PENDING_SQL[source_type]), {"limit": remaining}
        )
        found.extend((source_type, row.source_id) for row in rows)
    return found


async def unindexable_count(session: AsyncSession) -> int:
    """Profiles with resume text that no EXISTING tenant owns.

    Counted and logged by the sweep rather than left invisible. Two ways to
    qualify, and they are different data problems: `source_tenant_id` is NULL
    (the tenant-deletion anonymisation in `api/admin.py` reached this row), or
    it names a tenant that is no longer in the table (it did not). The second
    is the one that used to crash the indexer on a foreign key violation.

    A non-zero value is a data question rather than an indexing one, so it is
    reported and never repaired here: guessing an owner for somebody's resume
    is not a decision this sweep gets to make.
    """
    return int(
        (
            await session.execute(
                text(
                    f"""
                    SELECT count(*) FROM profiles p
                     WHERE p.resume_text IS NOT NULL
                       AND {_has_text('p.resume_text')}
                       AND NOT EXISTS (
                             SELECT 1 FROM tenants t
                              WHERE t.id = p.source_tenant_id
                           )
                    """
                )
            )
        ).scalar()
        or 0
    )
