"""Filtered ANN recall under the tenant predicate, measured rather than assumed.

RPN-AI-UP-001 W2.3. This is the only thing standing between the product and a
failure that raises nothing, logs nothing, and gets worse as the business grows.

THE FAILURE
-----------
Every query in `rag/retrieval` runs under the RLS policy, so
`tenant_id = current_setting('app.tenant_id')` is an implicit predicate on top
of whatever `where` the caller passed. An HNSW scan collects its candidates by
VECTOR DISTANCE first and the predicate filters SECOND. In a table holding many
tenants the scan can walk mostly other tenants' vectors and hand back a short
list, or none at all, for the tenant that asked.

Nothing errors. The caller sees a result set indistinguishable from a tenant
that genuinely has little indexed, and the effect worsens as the table grows,
worst for the smallest and newest tenants. That is the opposite of the
direction a defect should degrade in, and it is why the assertion here is
RECALL AGAINST A KNOWN RELEVANT SET rather than "rows came back". A test
asserting that rows came back passes throughout the entire failure.

WHY THIS FIXTURE IS SHARP, AND WHAT MAKES IT SO
-----------------------------------------------
The same relevant sentence is planted in EVERY tenant, not only in the one that
queries. So the global pool of nearest neighbours is `N x _RELEVANT_PER_TENANT`
rows at distance zero, of which exactly `_RELEVANT_PER_TENANT` belong to the
caller. Raising N does not make the caller's documents worse; it makes the
CONTAMINATION worse, which is precisely the axis the defect lives on. An
indexer or a scan setting that ranks first and filters afterwards loses the
caller's rows among the other tenants' copies, and it loses more of them the
larger N gets.

WHAT THIS PROVES, AND WHAT IT DOES NOT
---------------------------------------
With no `VOYAGE_CONTEXT_4` configured, `services.embeddings.embed` returns
deterministic pseudo-random unit vectors seeded from a sha256 of the text. Two
different strings are therefore near-orthogonal and the same string always
embeds to the same vector. That is what makes an offline recall test possible
at all: the relevant set is UNAMBIGUOUS, because the planted chunks carry the
query's exact text and sit at cosine distance zero from it while every filler
chunk sits at roughly one.

So this test proves that the retrieval path returns the rows it is supposed to
return, from the right tenant, under a growing multi-tenant table. It proves
NOTHING about semantic quality: it cannot, because the offline vectors carry no
semantics. Judging whether a real model ranks "message broker" above "RabbitMQ"
needs a live model and a human, exactly as `eval_interview.py` says of question
quality. Do not extend this file in that direction.

WHY THE PLANNER IS LEFT ALONE
------------------------------
An earlier version forced the ANN path with `SET LOCAL enable_seqscan = off`,
to be sure the HNSW index rather than a sequential scan was answering. It was
removed because it made the test measure something else: repeated seed and
delete cycles leave dead entries in a shared HNSW graph, and a forced index
scan over a bloated graph returns rows that depend on when the table was last
vacuumed. The test became a flaky report on autovacuum. What is asserted
instead is the property that matters to a caller either way, plus the
`retrievers` tuple below, which says whether the semantic half actually
contributed.

THE `retrievers` ASSERTION IS THE POINT, NOT DECORATION
--------------------------------------------------------
`retrieve` fuses a semantic list and a keyword list, and the planted chunks
carry the query's exact words, so the KEYWORD half finds them too. Recall alone
would therefore stay at 1.0 while the ANN half returned nothing whatsoever:
fusion would quietly cover for it, which is the mirror image of the bug the
`_tsquery` docstring records (the lexical half matched nothing and the semantic
hits made retrieval look like it worked). `RetrievedChunk.retrievers` records
which retriever surfaced each id, so asserting that "semantic" is among them is
what actually holds the ANN path to account.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

#: The query, already whitespace-normalised because `retrieve` normalises its
#: input and the planted chunks must embed to the identical vector.
_QUERY = (
    "kafka consumer group rebalance during a three region cluster migration"
)

#: Deliberately sharing not one token with the query, so the keyword retriever
#: cannot surface a filler chunk and blur the relevant set.
_FILLER = (
    "Drafted the annual stationery tender and reconciled it against last "
    "year's spend before the finance review.",
    "Coordinated the office relocation, including the furniture inventory and "
    "the lease handover paperwork.",
    "Maintained the visitor register, the reception rota and the badge audit "
    "every quarter without exception.",
    "Prepared the travel expense summary for the leadership offsite and "
    "circulated it to the finance partner.",
    "Ran the quarterly fire drill, logged the muster time and filed the "
    "safety officer's report afterwards.",
)

#: Documents per tenant carrying the planted sentence. Three rather than one so
#: recall is a fraction with room to be partial: a scan that returns one of
#: three is a 0.33, which a boolean "was anything returned" would score as a
#: pass.
_RELEVANT_DOCS_PER_TENANT = 3

#: Filler chunks inside each of those documents. Total chunks per tenant is
#: `_RELEVANT_DOCS_PER_TENANT * (1 + _FILLER_PER_DOC)`.
_FILLER_PER_DOC = 5

#: Sizes N is swept over. 50 is the acceptance figure in RPN-AI-UP-001 W2.
_TENANT_COUNTS = (5, 20, 50)


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


def _document(planted: bool) -> list:
    """One document's chunks: the planted sentence, then unrelated filler.

    `planted=False` builds a tenant that holds nothing relevant at all, which is
    what the cross-tenant test needs: a caller whose own index cannot supply the
    answer, so anything it receives from the other tenant's set is a leak rather
    than a coincidence.
    """
    from app.services.rag import chunking

    pieces: list[str] = []
    if planted:
        pieces.append(_QUERY)
    pieces.extend(_FILLER[:_FILLER_PER_DOC])
    return [
        chunking.Chunk(
            content=content,
            # `experience` carries the highest section prior in `rerank`, so the
            # planted chunk is not held back by a prior chosen for a different
            # reason. The filler never competes on terms anyway.
            section_type=(
                chunking.SECTION_EXPERIENCE if index == 0 and planted
                else chunking.SECTION_PROSE
            ),
            ordinal=index,
            source_type=chunking.SOURCE_RESUME,
        )
        for index, content in enumerate(pieces)
    ]


async def _seed_tenants(factory, count: int, *, planted: bool = True):
    """Create `count` tenants, index each one's documents, return their ids.

    Returns `(tenant_ids, relevant_by_tenant, all_by_tenant)` where the two maps
    are tenant id to the set of `context_chunks.id` values that carry the
    planted sentence, and to every chunk id that tenant owns.
    """
    from app.core.db import superadmin_scope
    from app.models import Tenant
    from app.services.rag import chunking, index

    tenant_ids = [uuid.uuid4() for _ in range(count)]
    document_ids = {tid: [uuid.uuid4() for _ in range(_RELEVANT_DOCS_PER_TENANT)]
                    for tid in tenant_ids}
    chunks = _document(planted)
    document = "\n\n".join(chunk.content for chunk in chunks)

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                for tid in tenant_ids:
                    s.add(Tenant(id=tid, name=f"Recall {tid.hex[:8]}",
                                 domain=f"{tid}.recall.test"))
                await s.flush()
                for tid in tenant_ids:
                    for document_id in document_ids[tid]:
                        await index.index_document(
                            s,
                            tenant_id=tid,
                            source_type=chunking.SOURCE_RESUME,
                            source_id=document_id,
                            document=document,
                            chunks=chunks,
                        )

    relevant: dict[uuid.UUID, set[uuid.UUID]] = {}
    owned: dict[uuid.UUID, set[uuid.UUID]] = {}
    async with factory() as s:
        async with superadmin_scope(s):
            rows = (
                await s.execute(
                    text("SELECT id, tenant_id, content FROM context_chunks "
                         " WHERE tenant_id = ANY(CAST(:t AS uuid[]))"),
                    {"t": [str(tid) for tid in tenant_ids]},
                )
            ).all()
    for row in rows:
        owned.setdefault(row.tenant_id, set()).add(row.id)
        if row.content == _QUERY:
            relevant.setdefault(row.tenant_id, set()).add(row.id)
    return tenant_ids, relevant, owned


async def _drop_tenants(factory, tenant_ids) -> None:
    """Chunks go with the tenant by foreign key, so this is the whole cleanup."""
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(
                    text("DELETE FROM tenants WHERE id = ANY(CAST(:t AS uuid[]))"),
                    {"t": [str(tid) for tid in tenant_ids]},
                )


async def _retrieve_as(factory, tenant_id, *, top_k: int):
    """Query through the RLS-aware session, exactly as a request handler does."""
    from app.core.db import tenant_scope
    from app.services.rag import retrieval

    async with factory() as s:
        async with tenant_scope(s, tenant_id):
            return await retrieval.retrieve(s, _QUERY, top_k=top_k)


@pytest.mark.asyncio
async def test_recall_is_complete_and_does_not_decay_as_tenants_are_added() -> None:
    """The measurement W2.3 asks for, swept over N.

    Two assertions, and both are needed. Complete recall at each N is the
    contract. Non-decay ACROSS N is what catches the defect in its own shape:
    filtered ANN recall does not fail at a threshold, it erodes, so a suite
    that only ever measured one table size would report a healthy number right
    up until a customer noticed their newest tenant retrieving nothing.

    Measured in one test rather than parameterised into three, because
    "recall did not decay" is a statement about the relationship between the
    runs and no single parameterised case can hold it.
    """
    engine, factory = await _factory_or_skip()
    measured: dict[int, float] = {}
    try:
        for count in _TENANT_COUNTS:
            tenant_ids, relevant, _ = await _seed_tenants(factory, count)
            try:
                caller = tenant_ids[0]
                expected = relevant[caller]
                assert len(expected) == _RELEVANT_DOCS_PER_TENANT

                found = await _retrieve_as(factory, caller,
                                           top_k=_RELEVANT_DOCS_PER_TENANT + 2)
                returned = [chunk.chunk_id for chunk in found]
                hit = expected & set(returned)
                measured[count] = len(hit) / len(expected)

                assert measured[count] == 1.0, (
                    f"recall {measured[count]:.2f} at {count} tenants: the scan "
                    f"lost {len(expected) - len(hit)} of the caller's own rows "
                    f"among {count * _RELEVANT_DOCS_PER_TENANT} identical "
                    f"vectors held by other tenants"
                )
                # The planted rows are the only ones that share a single term
                # with the query, so they must also OUTRANK the filler rather
                # than merely appear somewhere in the page.
                assert set(returned[:_RELEVANT_DOCS_PER_TENANT]) == expected
                # And the semantic half must be one of the retrievers that
                # found them. Without this the keyword half alone satisfies
                # every assertion above while the ANN scan returns nothing.
                for chunk in found:
                    if chunk.chunk_id in expected:
                        assert "semantic" in chunk.retrievers
            finally:
                await _drop_tenants(factory, tenant_ids)

        smallest, largest = _TENANT_COUNTS[0], _TENANT_COUNTS[-1]
        assert measured[largest] >= measured[smallest], (
            f"recall decayed from {measured[smallest]:.2f} at {smallest} "
            f"tenants to {measured[largest]:.2f} at {largest}"
        )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_second_tenant_retrieves_nothing_from_the_first_tenants_set() -> None:
    """The acceptance criterion stated as its own test, and stated as ZERO.

    The caller here holds no planted sentence at all, so its index cannot
    answer the query. That is deliberate: if the tenant boundary leaked, the
    other tenant's exact matches are the nearest vectors in the whole table by
    a wide margin and would be returned instantly. A caller that held its own
    copies could not tell a leak from its own rows coming back.

    Asserted in both directions. Not one of the first tenant's chunks may
    appear, and everything that does appear must belong to the caller: a
    retrieval that returned rows from a third place would satisfy the first
    assertion on its own.
    """
    engine, factory = await _factory_or_skip()
    owner_ids: list = []
    caller_ids: list = []
    try:
        owner_ids, owner_relevant, owner_owned = await _seed_tenants(factory, 1)
        caller_ids, _, caller_owned = await _seed_tenants(factory, 1, planted=False)
        owner, caller = owner_ids[0], caller_ids[0]
        assert owner_relevant[owner], "the fixture must give the owner something"

        found = await _retrieve_as(factory, caller, top_k=10)
        returned = {chunk.chunk_id for chunk in found}

        # An empty result would satisfy both assertions below vacuously, and a
        # tenant boundary that worked by returning nothing to anybody is not a
        # boundary, it is an outage. The caller's own filler is what it should
        # be reading, so it has to be reading something.
        assert returned, "the caller retrieved nothing at all, so nothing is proven"
        assert returned & owner_owned[owner] == set()
        assert returned <= caller_owned[caller]
    finally:
        await _drop_tenants(factory, owner_ids + caller_ids)
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_relevant_set_is_unambiguous_under_the_offline_metric() -> None:
    """A guard on the FIXTURE, not on the product.

    Every assertion in this file rests on one property of the offline embedding
    fallback: identical text embeds to an identical vector, and different text
    does not. If that ever stopped holding, the tests above would keep running
    and would stop meaning anything, which is the failure mode this codebase
    treats as worst. So it is checked directly, in the same place it is relied
    on, rather than left as a comment about a module three imports away.
    """
    from app.services.embeddings import embed

    vectors = await embed([_QUERY, _QUERY, _FILLER[0]])
    same = sum(a * b for a, b in zip(vectors[0], vectors[1]))
    different = sum(a * b for a, b in zip(vectors[0], vectors[2]))
    assert same == pytest.approx(1.0, abs=1e-6)
    assert abs(different) < 0.2
