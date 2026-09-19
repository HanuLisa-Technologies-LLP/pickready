"""Writing documents into `context_chunks`, and resolving what a document IS.

RPN-AI-UP-001 W2. `rag/index.index_document` and `rag/sources.load` are the two
halves of the indexing path, and until W2 neither had ever been executed against
a database. The parts asserted here are the ones whose failure is SILENT:

  * re-indexing an unchanged document must write NOTHING. An append-only
    indexer does not raise, it doubles the corpus, and retrieval then returns
    the same sentence under two ids while an agent reads the second as
    corroboration of the first.
  * a CHANGED document must replace its chunks in place. The unique index on
    (source_type, source_id, ordinal) is the mechanism, so the assertion is on
    the ROW COUNT rather than on what `index_document` says it did. A return
    value describing an upsert that did not happen is exactly the shape of
    "a timestamp is not evidence that work happened".
  * a document that got SHORTER must lose its trailing chunks. An orphan chunk
    is a sentence that is no longer in the document, still retrievable as
    though it were, and nothing anywhere reports it.
  * `source_version` must be restamped on the rows whose own text did not
    change, or the version filter in `retrieval` hides an unedited paragraph
    that still belongs to the new version of the document.
  * `load` must answer None for the four states that are not failures, and
    RAISE for the one that is. A loader that returned None for an unknown
    source type would let a dispatch naming a type nothing can load report
    success having indexed nothing.

The suite runs with no `VOYAGE_CONTEXT_4`, so `embeddings.embed` returns its
deterministic offline vectors. That is asserted rather than assumed in
`test_an_absent_embedding_credential_does_not_degrade_indexing`: the point of
keeping the fallback is that indexing works without a paid credential, and a
test that quietly tolerated `degraded=True` would stop noticing if it ever
started failing instead.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

# ── The documents ────────────────────────────────────────────────────────────
#
# Every block is comfortably over `chunking.MIN_CHARS`, so each one becomes its
# own chunk and the fragment-merging path does not silently change the ordinals
# out from under an assertion about which chunk was edited.

_JD_V1 = """
# Staff Data Engineer

We are hiring a data engineer to own the streaming ingestion platform end to
end, across three regions and two clusters, reporting to the head of platform.

## Responsibilities

Own the partition strategy for the ingestion topics, lead the migration off the
single cluster, and keep the nightly reconciliation job inside its window.

## Required skills

Kafka, Postgres and Terraform, plus a working knowledge of distributed
consensus as it behaves in production rather than as it reads in a textbook.
"""

#: The middle block only. Everything else is byte-identical, so exactly one
#: chunk should be rewritten and the rest reported unchanged.
_JD_EDITED_MIDDLE = _JD_V1.replace(
    "Own the partition strategy for the ingestion topics, lead the migration off"
    " the\nsingle cluster, and keep the nightly reconciliation job inside its"
    " window.",
    "Own the partition strategy for every ingestion topic, retire the single"
    " cluster\nentirely, and hold the nightly reconciliation job inside a two"
    " hour window.",
)

#: A new block appended. The existing blocks are untouched, which is what makes
#: this the restamping case: their `content_sha256` is unchanged and their
#: `source_version` must move anyway.
_JD_APPENDED = _JD_V1 + """
## Benefits

Private medical cover from day one, a learning budget renewed every year, and
four weeks of fully remote working for anyone based outside the two hub cities.
"""

#: The last block removed, so the document is genuinely shorter and the trailing
#: chunk has to be deleted rather than left behind.
_JD_SHORTER = _JD_V1[: _JD_V1.index("## Required skills")]

_RESUME = """
EXPERIENCE

Senior Engineer, Northwind Data. Ran the streaming ingestion platform on Kafka
across three regions, owning the partition strategy and the consumer group
rebalance through a migration from a single cluster to a federated pair.

EDUCATION

B.Tech in Computer Science, with a final year project on consensus protocols
and a dissertation on partition tolerance in geographically split clusters.
"""


async def _factory_or_skip():
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import get_settings

    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001
        await engine.dispose()
        pytest.skip("no database reachable")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class _Fx:
    """Every id one seeded world needs, minted up front so cleanup is total."""

    def __init__(self) -> None:
        self.tenant_id = uuid.uuid4()
        self.job_id = uuid.uuid4()
        self.draft_job_id = uuid.uuid4()
        self.cand_id = uuid.uuid4()
        self.other_cand_id = uuid.uuid4()
        self.profile_id = uuid.uuid4()
        self.unparsed_profile_id = uuid.uuid4()
        self.orphan_profile_id = uuid.uuid4()
        self.link_id = uuid.uuid4()
        self.silent_link_id = uuid.uuid4()
        self.conv_id = uuid.uuid4()
        self.silent_conv_id = uuid.uuid4()


async def _seed(factory, fx: _Fx) -> None:
    """One tenant carrying every state `sources.load` has to distinguish.

    Seeded in one transaction rather than per test, because the None cases are
    only meaningful next to their positive twin: a resume that loads and a
    resume that does not, in the same tenant, differing only in the column the
    loader reads.
    """
    from app.core.db import superadmin_scope
    from app.models import Candidate, Job, JobStatus, LinkSource, Tenant
    from app.models.assessment import AssessmentConversation, AssessmentMessage
    from app.models.candidate import JobCandidateLink, Profile

    now = datetime.now(timezone.utc)
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                s.add(Tenant(id=fx.tenant_id, name=f"Ix {fx.tenant_id.hex[:6]}",
                             domain=f"{fx.tenant_id}.ix.test"))
                await s.flush()
                s.add(Job(id=fx.job_id, tenant_id=fx.tenant_id,
                          title="Staff Data Engineer", jd_json={},
                          jd_markdown=_JD_V1, status=JobStatus.ratified,
                          ratified_at=now, assessment_grade="non_managerial"))
                # A JD that is whitespace and nothing else. Whitespace is not
                # content, here for the same reason it is not content at Gate 1.
                s.add(Job(id=fx.draft_job_id, tenant_id=fx.tenant_id,
                          title="Still a draft", jd_json={},
                          jd_markdown="   \n\n  ", status=JobStatus.draft,
                          assessment_grade="non_managerial"))
                s.add(Candidate(id=fx.cand_id,
                                email=f"c{fx.cand_id.hex[:8]}@ix.test",
                                full_name="Indexed Candidate",
                                consent_databank=False))
                s.add(Candidate(id=fx.other_cand_id,
                                email=f"c{fx.other_cand_id.hex[:8]}@ix.test",
                                full_name="Silent Candidate",
                                consent_databank=False))
                await s.flush()
                s.add(Profile(id=fx.profile_id, candidate_id=fx.cand_id,
                              source_tenant_id=fx.tenant_id, resume_text=_RESUME))
                # Uploaded, not yet parsed. Nothing to index and nothing wrong.
                s.add(Profile(id=fx.unparsed_profile_id, candidate_id=fx.cand_id,
                              source_tenant_id=fx.tenant_id, resume_text=None))
                # Text with no owning tenant, which is what the tenant-deletion
                # anonymisation leaves behind. `context_chunks.tenant_id` is NOT
                # NULL, so this document cannot be indexed under this schema.
                s.add(Profile(id=fx.orphan_profile_id, candidate_id=fx.cand_id,
                              source_tenant_id=None, resume_text=_RESUME))
                await s.flush()
                s.add(JobCandidateLink(id=fx.link_id, tenant_id=fx.tenant_id,
                                       job_id=fx.job_id, candidate_id=fx.cand_id,
                                       source=LinkSource.fresh, status="applied"))
                s.add(JobCandidateLink(id=fx.silent_link_id, tenant_id=fx.tenant_id,
                                       job_id=fx.job_id,
                                       candidate_id=fx.other_cand_id,
                                       source=LinkSource.fresh, status="applied"))
                await s.flush()
                s.add(AssessmentConversation(
                    id=fx.conv_id, tenant_id=fx.tenant_id, job_id=fx.job_id,
                    job_candidate_link_id=fx.link_id, grade="non_managerial",
                    status="active", next_question_index=0, started_at=now))
                s.add(AssessmentConversation(
                    id=fx.silent_conv_id, tenant_id=fx.tenant_id, job_id=fx.job_id,
                    job_candidate_link_id=fx.silent_link_id,
                    grade="non_managerial", status="active",
                    next_question_index=0, started_at=now))
                await s.flush()
                for ordinal, (speaker, content) in enumerate([
                    ("agent", "How did you handle consumer group rebalance "
                              "storms during the regional migration?"),
                    ("candidate", "We drained one region at a time and pinned "
                                  "the group instance id so a rolling deploy "
                                  "stopped triggering a full rebalance."),
                    ("agent", "What did that cost you in replay time?"),
                    ("candidate", "About forty minutes on the first cutover "
                                  "and under five on every one after it."),
                ], 1):
                    s.add(AssessmentMessage(
                        tenant_id=fx.tenant_id, conversation_id=fx.conv_id,
                        ordinal=ordinal, speaker=speaker, domain="technical",
                        question_key="k", content=content))
                # Invited, opened, answered nothing. There is no exchange, so
                # there is no evidence, so there is nothing to index.
                s.add(AssessmentMessage(
                    tenant_id=fx.tenant_id, conversation_id=fx.silent_conv_id,
                    ordinal=1, speaker="agent", domain="technical",
                    question_key="k",
                    content="Tell me about the largest cluster you have run."))


async def _cleanup(factory, fx: _Fx) -> None:
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(text("DELETE FROM tenants WHERE id = :t"),
                                {"t": str(fx.tenant_id)})
                await s.execute(
                    text("DELETE FROM candidates WHERE id = ANY(CAST(:c AS uuid[]))"),
                    {"c": [str(fx.cand_id), str(fx.other_cand_id)]},
                )
                # Belt and braces: a chunk keyed on a profile whose tenant was
                # never created (the orphan case) has no cascade to ride out on.
                await s.execute(
                    text("DELETE FROM context_chunks "
                         "WHERE source_id = ANY(CAST(:s AS uuid[]))"),
                    {"s": [str(fx.job_id), str(fx.draft_job_id),
                           str(fx.profile_id), str(fx.orphan_profile_id),
                           str(fx.link_id), str(fx.silent_link_id)]},
                )


async def _index(factory, fx: _Fx, document: str, *, source_type: str = "jd",
                 source_id: uuid.UUID | None = None):
    from app.core.db import superadmin_scope
    from app.services.rag import index

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                return await index.index_document(
                    s,
                    tenant_id=fx.tenant_id,
                    source_type=source_type,
                    source_id=source_id or fx.job_id,
                    document=document,
                )


async def _rows(factory, source_id: uuid.UUID) -> list:
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with superadmin_scope(s):
            return (
                await s.execute(
                    text("SELECT ordinal, content, content_sha256, source_version, "
                         "       embedding IS NOT NULL AS has_vector "
                         "  FROM context_chunks WHERE source_id = :s "
                         " ORDER BY ordinal"),
                    {"s": str(source_id)},
                )
            ).all()


# ── the indexer ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_indexing_the_same_document_twice_writes_once() -> None:
    """Idempotence on `content_sha256`, asserted on the TABLE.

    The second pass must report every chunk unchanged AND leave the row count
    where it was. Either half alone is satisfiable by a broken indexer: one
    that reports `unchanged` while inserting duplicates passes the return-value
    assertion, and one that skips the write for the wrong reason passes the
    count assertion.
    """
    from app.services.rag import chunking

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        expected = chunking.chunk_jd(_JD_V1)
        assert len(expected) > 1, "the fixture must produce a multi-chunk document"

        first = await _index(factory, fx, _JD_V1)
        assert first.written == len(expected)
        assert first.unchanged == 0
        assert first.deleted == 0
        assert len(await _rows(factory, fx.job_id)) == len(expected)

        second = await _index(factory, fx, _JD_V1)
        assert second.written == 0
        assert second.unchanged == len(expected)
        assert second.deleted == 0
        assert second.total == len(expected)
        assert len(await _rows(factory, fx.job_id)) == len(expected)
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_edited_document_replaces_its_chunk_rather_than_duplicating() -> None:
    """The unique index on (source_type, source_id, ordinal) is the mechanism.

    Asserted as a row count and as the stored CONTENT, not as the indexer's own
    report. A duplicate chunk is the failure that turns one sentence into two
    retrievable ids, which an agent counting corroboration will find and cannot
    tell apart from two independent sources saying the same thing.
    """
    from app.services.rag import chunking

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        expected = chunking.chunk_jd(_JD_V1)
        await _index(factory, fx, _JD_V1)

        edited = await _index(factory, fx, _JD_EDITED_MIDDLE)
        assert edited.written == 1, "only the edited block should be rewritten"
        assert edited.unchanged == len(expected) - 1
        assert edited.deleted == 0

        rows = await _rows(factory, fx.job_id)
        assert len(rows) == len(expected)
        assert [row.ordinal for row in rows] == list(range(len(expected)))
        stored = "\n".join(row.content for row in rows)
        assert "retire the single cluster" in stored
        assert "lead the migration off" not in stored
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_shorter_document_deletes_its_trailing_chunks() -> None:
    """What a removed paragraph leaves behind, and why it may not stay.

    An orphan chunk is text that is no longer in the document and is still
    returned by retrieval as though it were. Nothing raises, nothing logs, and
    the evidence an agent cites is a requirement the client deleted.
    """
    from app.services.rag import chunking

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        long_chunks = chunking.chunk_jd(_JD_V1)
        short_chunks = chunking.chunk_jd(_JD_SHORTER)
        assert len(short_chunks) < len(long_chunks), "the fixture must shrink"

        await _index(factory, fx, _JD_V1)
        shortened = await _index(factory, fx, _JD_SHORTER)

        assert shortened.deleted == len(long_chunks) - len(short_chunks)
        rows = await _rows(factory, fx.job_id)
        assert len(rows) == len(short_chunks)
        assert max(row.ordinal for row in rows) == len(short_chunks) - 1
        assert "distributed\nconsensus" not in "\n".join(row.content for row in rows)
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_source_version_is_restamped_on_chunks_that_did_not_change() -> None:
    """An unedited paragraph still belongs to the new version of the document.

    `retrieval` filters on `source_version` to keep two versions of one JD from
    being blended into one answer. If an unchanged chunk kept the old stamp,
    that filter would hide the paragraph nobody touched, which reads as a JD
    that lost half its requirements the moment somebody appended a section.
    """
    from app.services.rag import chunking

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        original = chunking.chunk_jd(_JD_V1)
        await _index(factory, fx, _JD_V1)

        appended = await _index(factory, fx, _JD_APPENDED)
        assert appended.written == 1, "only the new block is new text"
        assert appended.unchanged == len(original)

        wanted = chunking.source_version(_JD_APPENDED)
        rows = await _rows(factory, fx.job_id)
        assert {row.source_version for row in rows} == {wanted}
        assert wanted != chunking.source_version(_JD_V1)
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_absent_embedding_credential_does_not_degrade_indexing() -> None:
    """The offline fallback is kept on purpose, and this is what it buys.

    With no `VOYAGE_CONTEXT_4` the embedding client returns deterministic
    vectors rather than raising, so a local run and CI index a document with a
    vector on every row. `degraded` is the honest record of the other case, and
    it must be False here or the fallback has stopped doing its job.
    """
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        result = await _index(factory, fx, _JD_V1)
        assert result.degraded is False
        assert result.embedded == result.written
        assert all(row.has_vector for row in await _rows(factory, fx.job_id))
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_deleting_the_tenant_cascades_the_chunks_away() -> None:
    """A chunk is a verbatim slice of a candidate's resume or a client's JD.

    When a tenant is deleted that text must go with it, by the foreign key
    rather than by a sweep somebody has to remember to run.
    """
    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        await _index(factory, fx, _JD_V1)
        assert await _rows(factory, fx.job_id)

        await _cleanup(factory, fx)
        assert await _rows(factory, fx.job_id) == []
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


# ── what a document IS ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_load_resolves_all_three_source_types() -> None:
    """One loader per source type, each answering "text, and whose tenant".

    The assessment case is the one worth reading: its unit is a question and
    answer PAIR, pre-split by the loader, because a question without its answer
    retrieves for the topic and proves nothing.
    """
    from app.services.rag import chunking, sources

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        from app.core.db import superadmin_scope

        async with factory() as s:
            async with superadmin_scope(s):
                jd = await sources.load(s, source_type=chunking.SOURCE_JD,
                                        source_id=fx.job_id)
                resume = await sources.load(s, source_type=chunking.SOURCE_RESUME,
                                            source_id=fx.profile_id)
                assessment = await sources.load(
                    s, source_type=chunking.SOURCE_ASSESSMENT,
                    source_id=fx.link_id)

        assert jd is not None and jd.tenant_id == fx.tenant_id
        assert "Staff Data Engineer" in jd.text
        assert jd.chunks is None, "a JD is chunked by the chunker, not the loader"

        assert resume is not None and resume.tenant_id == fx.tenant_id
        assert "Northwind Data" in resume.text

        assert assessment is not None and assessment.tenant_id == fx.tenant_id
        assert assessment.chunks is not None
        # Two answered exchanges, and the dangling question of an abandoned
        # assessment would have made a third if pairing were done by zipping.
        assert len(assessment.chunks) == 2
        assert all(chunk.content.startswith("Q: ") for chunk in assessment.chunks)
        assert all(chunk.section_type == chunking.SECTION_QA
                   for chunk in assessment.chunks)
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_load_returns_none_for_every_state_that_is_not_a_failure() -> None:
    """None is an outcome, not an error, and covers five real states.

    Raising on any of them would spend a retry budget against a state that is
    not going to change on its own: the row was deleted between the dispatch
    and the run, the JD is still a draft, the resume has not been parsed, the
    assessment has no answered exchange, and the profile has no owning tenant.

    The orphan profile is the one that must be reported rather than skipped
    silently. `context_chunks.tenant_id` is NOT NULL, so a profile whose
    `source_tenant_id` was cleared by the tenant-deletion anonymisation holds
    retrievable text that can never be retrieved, and a sweep that ignored it
    would make "nothing to do" and "not running" produce the same empty log.
    """
    from app.core.db import superadmin_scope
    from app.services.rag import chunking, sources

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        async with factory() as s:
            async with superadmin_scope(s):
                missing = await sources.load(s, source_type=chunking.SOURCE_JD,
                                             source_id=uuid.uuid4())
                blank_jd = await sources.load(s, source_type=chunking.SOURCE_JD,
                                              source_id=fx.draft_job_id)
                unparsed = await sources.load(s, source_type=chunking.SOURCE_RESUME,
                                              source_id=fx.unparsed_profile_id)
                orphan = await sources.load(s, source_type=chunking.SOURCE_RESUME,
                                            source_id=fx.orphan_profile_id)
                unanswered = await sources.load(
                    s, source_type=chunking.SOURCE_ASSESSMENT,
                    source_id=fx.silent_link_id)

        assert missing is None
        assert blank_jd is None, "whitespace is not content"
        assert unparsed is None
        assert orphan is None
        assert unanswered is None
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_pending_finds_a_document_with_no_chunks_and_then_stops() -> None:
    """The sweep asks the TABLE, and stops asking once the table answers.

    `pending` deliberately does not look for STALE chunks: recomputing the
    document fingerprint in SQL would be a second implementation of
    `chunking.source_version`, and its disagreement would be invisible, showing
    up as a sweep that re-indexes every document forever with a bill as the
    only symptom. So the whole contract is "text, and no chunk rows at all",
    and both directions of it are asserted here.

    The limit is generous rather than tight because the budget is SHARED across
    the three source types in order, and a warm database carrying other tests'
    unindexed jobs could otherwise starve the resume and assessment queries out
    of the answer entirely.
    """
    from app.core.db import superadmin_scope
    from app.services.rag import chunking, sources

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)

        async with factory() as s:
            async with superadmin_scope(s):
                before = await sources.pending(s, limit=500)
        assert (chunking.SOURCE_JD, fx.job_id) in before
        assert (chunking.SOURCE_RESUME, fx.profile_id) in before
        assert (chunking.SOURCE_ASSESSMENT, fx.link_id) in before
        # Neither of these has anything to index, so neither is pending. The
        # whitespace-only JD is deliberately NOT asserted here; it has its own
        # test below, because the two implementations of "blank" disagree.
        assert (chunking.SOURCE_RESUME, fx.orphan_profile_id) not in before
        assert (chunking.SOURCE_ASSESSMENT, fx.silent_link_id) not in before

        await _index(factory, fx, _JD_V1)
        await _index(factory, fx, _RESUME, source_type=chunking.SOURCE_RESUME,
                     source_id=fx.profile_id)

        async with factory() as s:
            async with superadmin_scope(s):
                after = await sources.pending(s, limit=500)
        assert (chunking.SOURCE_JD, fx.job_id) not in after
        assert (chunking.SOURCE_RESUME, fx.profile_id) not in after
        # Untouched, so still waiting. The sweep did not decide it was done
        # because something else was.
        assert (chunking.SOURCE_ASSESSMENT, fx.link_id) in after
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_whitespace_only_jd_is_not_pending_because_load_will_refuse_it() -> None:
    """The sweep and the loader must agree about what "blank" means.

    `sources.load` decides emptiness in Python, with `str.strip()`, which
    removes newlines and tabs. `sources.pending` decides it in SQL, with
    `btrim(...)`, whose default trim set is the SPACE CHARACTER ALONE. A JD
    holding "   \\n\\n  " is therefore content to the sweep and blank to the
    loader, and the two answers can never be reconciled by waiting.

    What that produces is not an error. The sweep lists the job, dispatches an
    index task, the task loads None, indexes nothing, and the job is listed
    again on the next pass, hourly, forever, for as long as the row exists. The
    module docstring names this exact failure as the reason `pending` refuses
    to recompute the document fingerprint in SQL: two implementations of one
    concept whose disagreement is invisible, with a bill as the only symptom.
    It arrived through the other half of the same module anyway.

    A draft JD holding only whitespace is not exotic. It is what a rich text
    editor leaves behind when a recruiter selects everything in the box and
    deletes it.
    """
    from app.core.db import superadmin_scope
    from app.services.rag import chunking, sources

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        await _seed(factory, fx)
        async with factory() as s:
            async with superadmin_scope(s):
                loaded = await sources.load(s, source_type=chunking.SOURCE_JD,
                                            source_id=fx.draft_job_id)
                listed = await sources.pending(s, limit=500)

        assert loaded is None, "the loader already agrees whitespace is not content"
        assert (chunking.SOURCE_JD, fx.draft_job_id) not in listed
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_unindexable_count_sees_text_that_no_tenant_owns() -> None:
    """Expected to be zero in production, and worth a query when it is not.

    A non-zero value is a data question rather than an indexing one, which is
    exactly why it is COUNTED instead of being skipped inside a loop.
    """
    from app.core.db import superadmin_scope
    from app.services.rag import sources

    engine, factory = await _factory_or_skip()
    fx = _Fx()
    try:
        async with factory() as s:
            async with superadmin_scope(s):
                baseline = await sources.unindexable_count(s)
        await _seed(factory, fx)
        async with factory() as s:
            async with superadmin_scope(s):
                seeded = await sources.unindexable_count(s)
        assert seeded == baseline + 1
    finally:
        await _cleanup(factory, fx)
        await engine.dispose()


@pytest.mark.asyncio
async def test_an_unknown_source_type_raises_rather_than_indexing_nothing() -> None:
    """A dispatch naming a type nothing can load must fail where somebody sees it.

    Returning None would be the silent version: the task would report success,
    the sweep would find the document pending on the next pass, and the loop
    would run forever with an empty log on both sides. No database is needed,
    because the refusal happens before the session is ever touched.
    """
    from app.services.rag import sources

    with pytest.raises(sources.UnknownSourceType) as exc:
        await sources.load(None, source_type="project_evidence",
                           source_id=uuid.uuid4())
    # The message names the types that DO exist, so the reader of the log line
    # does not have to open the module to find out what was expected.
    assert "project_evidence" in str(exc.value)
    for known in sources.SOURCE_TYPES:
        assert known in str(exc.value)
