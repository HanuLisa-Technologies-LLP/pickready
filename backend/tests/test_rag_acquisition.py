"""W6.5: one bounded broadened retry before a thin retrieval is given up on.

WHAT IS PROVEN, AND AGAINST WHAT
----------------------------------
Real database, real index, real retrieval stack, under the RLS-aware session,
using the same offline-vector determinism `test_retrieval_tenant_recall.py`
documents: with no VOYAGE credential, `embeddings.embed` returns deterministic
pseudo-random unit vectors, so a chunk carrying the query's exact text is the
unambiguous relevant set and the keyword half finds it regardless.

The cases the brief names, each its own test:

  * thin first pass, broadened retry FINDS the material -> real chunks return
    and the outcome records that broadening did it;
  * still thin after the retry -> whatever was found returns, `sufficient`
    stays False, and nothing manufactures a sufficiency (the downstream
    EMPTY_STATE_COPY contract is untouched, asserted structurally: the module
    does not import generation_sufficiency at all);
  * the deadline refuses the retry BEFORE it starts, per the predictive rule.

WHY THE FIRST PASS IS MADE THIN WITH A SECTION FILTER
-------------------------------------------------------
The planted chunk is indexed under `experience`; the first ask filters to
`skills`. That is the honest miniature of the real failure: the material
exists, the caller's precise ask missed it, and dropping the section filter is
exactly the broadening move the module performs. No mock is involved anywhere;
every assertion is about rows a real query returned.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings

_QUERY = "orchestrated the payments ledger reconciliation service in rust"
_FILLER = [
    "organised the quarterly volunteering day and the office move",
    "maintained the internal wiki and onboarding checklists",
    "coordinated travel bookings for the sales team offsite",
]


async def _factory_or_skip():
    engine = create_async_engine(get_settings().database_url)
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001 -- no database reachable
        await engine.dispose()
        pytest.skip("no database reachable -- skipping acquisition test")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_tenant(factory, *, planted: bool = True) -> uuid.UUID:
    """One tenant, one document whose relevant chunk sits under EXPERIENCE."""
    from app.core.db import superadmin_scope
    from app.models import Tenant
    from app.services.rag import chunking, index

    tenant_id = uuid.uuid4()
    pieces = ([_QUERY] if planted else []) + _FILLER
    chunks = [
        chunking.Chunk(
            content=content,
            section_type=(
                chunking.SECTION_EXPERIENCE
                if planted and position == 0
                else chunking.SECTION_PROSE
            ),
            ordinal=position,
            source_type=chunking.SOURCE_RESUME,
        )
        for position, content in enumerate(pieces)
    ]
    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                s.add(
                    Tenant(
                        id=tenant_id,
                        name=f"Acq {tenant_id.hex[:8]}",
                        domain=f"{tenant_id}.acq.test",
                    )
                )
                await s.flush()
                await index.index_document(
                    s,
                    tenant_id=tenant_id,
                    source_type=chunking.SOURCE_RESUME,
                    source_id=uuid.uuid4(),
                    document="\n\n".join(chunk.content for chunk in chunks),
                    chunks=chunks,
                )
    return tenant_id


async def _drop_tenant(factory, tenant_id: uuid.UUID) -> None:
    from app.core.db import superadmin_scope

    async with factory() as s:
        async with s.begin():
            async with superadmin_scope(s):
                await s.execute(
                    text("DELETE FROM tenants WHERE id = :t"), {"t": str(tenant_id)}
                )


async def _acquire_as(factory, tenant_id: uuid.UUID, **kwargs):
    from app.core.db import tenant_scope
    from app.services.rag import acquisition

    async with factory() as s:
        async with tenant_scope(s, tenant_id):
            return await acquisition.acquire(s, _QUERY, **kwargs)


async def test_a_thin_first_pass_is_rescued_by_the_one_broadened_retry() -> None:
    """The material exists under `experience`; the caller asked for `skills`.

    The first pass legitimately finds nothing, the broadened pass drops the
    section filter and finds it, and the outcome says so: real content, two
    attempts, `broadened=True`, `sufficient=True`. This is the case that must
    produce real content rather than the empty state.
    """
    from app.services.rag import chunking

    engine, factory = await _factory_or_skip()
    tenant_id = await _seed_tenant(factory)
    try:
        outcome = await _acquire_as(
            factory,
            tenant_id,
            section_types=[chunking.SECTION_SKILLS],
            min_chunks=1,
        )
        assert outcome.attempts == 2
        assert outcome.broadened is True
        assert outcome.sufficient is True
        assert outcome.skipped_reason is None
        contents = [chunk.content for chunk in outcome.chunks]
        assert _QUERY in contents, contents
    finally:
        await _drop_tenant(factory, tenant_id)
        await engine.dispose()


async def test_still_insufficient_after_the_retry_stays_honest() -> None:
    """A tenant that holds nothing relevant, and a floor its filler cannot
    meet. The retry runs, and the outcome reports `sufficient=False` rather
    than inventing a result. What the caller does with a thin result stays the
    caller's contract: the fixed EMPTY_STATE_COPY path is untouched by
    construction, and the structural half of that claim is the import test at
    the bottom of this file."""
    engine, factory = await _factory_or_skip()
    tenant_id = await _seed_tenant(factory, planted=False)
    try:
        outcome = await _acquire_as(factory, tenant_id, min_chunks=4)
        assert outcome.attempts == 2
        assert outcome.broadened is True
        assert outcome.sufficient is False
    finally:
        await _drop_tenant(factory, tenant_id)
        await engine.dispose()


async def test_the_deadline_refuses_the_retry_before_it_starts() -> None:
    """The predictive rule: with a budget the first attempt has already
    consumed, the broadened attempt is REFUSED rather than started, and the
    refusal is NAMED in the record rather than left to be inferred from an
    attempt count."""
    from app.services.rag import acquisition, chunking

    engine, factory = await _factory_or_skip()
    tenant_id = await _seed_tenant(factory)
    try:
        outcome = await _acquire_as(
            factory,
            tenant_id,
            section_types=[chunking.SECTION_SKILLS],
            min_chunks=1,
            deadline_seconds=0.0,
        )
        assert outcome.attempts == 1
        assert outcome.broadened is False
        assert outcome.sufficient is False
        assert outcome.skipped_reason == acquisition.REFUSED_DEADLINE
    finally:
        await _drop_tenant(factory, tenant_id)
        await engine.dispose()


async def test_a_sufficient_first_pass_never_pays_for_a_second() -> None:
    """No filter in the way: the first pass finds the chunk and the retry is
    recorded as not needed. One attempt, not two, because the broadened pass
    costs an embedding call and two real queries."""
    engine, factory = await _factory_or_skip()
    tenant_id = await _seed_tenant(factory)
    try:
        from app.services.rag import acquisition

        outcome = await _acquire_as(factory, tenant_id, min_chunks=1)
        assert outcome.attempts == 1
        assert outcome.broadened is False
        assert outcome.sufficient is True
        assert outcome.skipped_reason == acquisition.NOT_NEEDED
    finally:
        await _drop_tenant(factory, tenant_id)
        await engine.dispose()


def test_acquisition_never_touches_the_sufficiency_gates_or_scoring() -> None:
    """Structural: the module imports neither `generation_sufficiency` (whose
    EMPTY_STATE_COPY contract it must not weaken) nor anything that scores.

    The second half restates `test_retrieval_scoring_isolation`'s rule at this
    new module: retrieval is a ranking and acquisition prior ONLY, and a
    broadened retry that could reach a scorer would be a new path for the
    sufficiency signal to move a grade."""
    import ast
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[1]
        / "app" / "services" / "rag" / "acquisition.py"
    )
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    banned = (
        "app.services.generation_sufficiency",
        "app.services.functional_assessment",
        "app.services.miti",
        "app.services.siddhi",
        "app.services.matching",
    )
    offending = sorted(
        name
        for name in imported
        for bad in banned
        if name == bad or name.startswith(bad + ".")
    )
    assert not offending, offending
