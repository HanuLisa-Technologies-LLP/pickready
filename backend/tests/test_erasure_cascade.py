"""Erasure reaches the vectors and the caches, not only the rows (W9.5).

WHY THIS TEST IS WORTH ITS DATABASE
-------------------------------------
The failure it prevents does not raise and does not show up in a route test.
Deleting `candidates` cascades to nine tables and looks complete, while
`context_chunks` holds the candidate's resume, cut into pieces, each with a
1024-dimension vector, reachable by no foreign key from `candidates` at all.
Published inversion work recovers 50 to 70% of the input words from popular
sentence embeddings, and because the embedding model is public and queryable, a
dictionary attack against a stolen vector is practical. A residual vector is the
resume in a form that is inconvenient to read rather than impossible.

The two assertions the specification asks for are ZERO residual vectors and
ZERO residual cache keys, and both are made by reading rows and keys back, never
by trusting a return value.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import cache
from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.services import chunk_acl, erasure

VECTOR = "[" + ",".join(["0.01"] * 1024) + "]"


async def _factory_or_skip():
    engine = create_async_engine(
        get_settings().database_url, pool_size=1, max_overflow=0
    )
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001
        await engine.dispose()
        pytest.skip("no database reachable, skipping erasure integration test")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _redis_or_skip():
    import redis.asyncio as redis_asyncio

    return redis_asyncio.from_url(
        get_settings().redis_url,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )


class World:
    """One tenant, two candidates. The second one is the control: an erasure
    that deleted everything would pass every "zero residual" assertion."""

    def __init__(self) -> None:
        self.tenant = uuid.uuid4()
        self.job = uuid.uuid4()
        self.subject = uuid.uuid4()
        self.subject_profile = uuid.uuid4()
        self.subject_link = uuid.uuid4()
        self.subject_project = uuid.uuid4()
        self.bystander = uuid.uuid4()
        self.bystander_profile = uuid.uuid4()


async def _seed(session, world: World) -> None:
    await session.execute(
        text(
            "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
            "VALUES (:t, :n, :d, 'pending')"
        ),
        {"t": str(world.tenant), "n": f"erasure-{world.tenant}",
         "d": f"{world.tenant}.erasure.test"},
    )
    await session.execute(
        text(
            "INSERT INTO jobs (id, tenant_id, title, jd_json, status) "
            "VALUES (:j, :t, 'Backend Engineer', '{}'::jsonb, 'draft')"
        ),
        {"j": str(world.job), "t": str(world.tenant)},
    )
    for candidate, profile in (
        (world.subject, world.subject_profile),
        (world.bystander, world.bystander_profile),
    ):
        await session.execute(
            text(
                "INSERT INTO candidates (id, tenant_id, full_name, email, "
                "consent_databank) VALUES (:c, :t, 'Test Person', :e, false)"
            ),
            {"c": str(candidate), "t": str(world.tenant),
             "e": f"{candidate}@erasure.test"},
        )
        await session.execute(
            text(
                "INSERT INTO profiles (id, candidate_id, source_tenant_id, "
                "resume_text, embedding, embedding_shadow, parsed_fields_json) "
                "VALUES (:p, :c, :t, 'Kafka and Postgres', CAST(:v AS vector), "
                "CAST(:v AS vector), '{\"skills\": [\"Kafka\"]}'::jsonb)"
            ),
            {"p": str(profile), "c": str(candidate), "t": str(world.tenant),
             "v": VECTOR},
        )
    await session.execute(
        text(
            "INSERT INTO job_candidate_links "
            "(id, tenant_id, job_id, candidate_id, profile_id, source) "
            "VALUES (:l, :t, :j, :c, :p, 'manual')"
        ),
        {"l": str(world.subject_link), "t": str(world.tenant),
         "j": str(world.job), "c": str(world.subject),
         "p": str(world.subject_profile)},
    )
    await session.execute(
        text(
            "INSERT INTO candidate_projects (id, candidate_id, name, description, "
            "submission_kind, status, evidence_json) "
            "VALUES (:i, :c, 'Pipeline', 'A data pipeline.', 'files', "
            "'processed', '{}'::jsonb)"
        ),
        {"i": str(world.subject_project), "c": str(world.subject)},
    )
    # Chunks written WITHOUT the ACL columns, exactly as `rag/index` writes
    # them today. The trigger from migration 0092 is what classifies them, and
    # that is the property being relied on.
    rows = (
        ("resume", world.subject_profile),
        ("assessment", world.subject_link),
        ("resume", world.bystander_profile),
    )
    for ordinal, (source_type, source_id) in enumerate(rows):
        await session.execute(
            text(
                "INSERT INTO context_chunks (id, tenant_id, source_type, "
                "source_id, source_version, section_type, ordinal, content, "
                "content_sha256, embedding) "
                "VALUES (:i, :t, :st, :sid, 'v1', 'experience', :o, "
                "'Kafka and Postgres', :h, CAST(:v AS vector))"
            ),
            {"i": str(uuid.uuid4()), "t": str(world.tenant), "st": source_type,
             "sid": str(source_id), "o": ordinal, "h": f"{ordinal:064d}",
             "v": VECTOR},
        )
    # One JD chunk, which carries no candidate and must keep its NULL ACL.
    await session.execute(
        text(
            "INSERT INTO context_chunks (id, tenant_id, source_type, source_id, "
            "source_version, section_type, ordinal, content, content_sha256, "
            "embedding) VALUES (:i, :t, 'jd', :sid, 'v1', 'responsibilities', 0, "
            "'Own the pipeline', :h, CAST(:v AS vector))"
        ),
        {"i": str(uuid.uuid4()), "t": str(world.tenant), "sid": str(world.job),
         "h": "9" * 64, "v": VECTOR},
    )


async def _cleanup(session, world: World) -> None:
    for candidate in (world.subject, world.bystander):
        await session.execute(
            text("DELETE FROM candidates WHERE id = :c"), {"c": str(candidate)}
        )
    await session.execute(
        text("DELETE FROM context_chunks WHERE tenant_id = :t"),
        {"t": str(world.tenant)},
    )
    # The `audit_log` rows are deliberately NOT cleaned up: the application role
    # holds no DELETE grant on that table, which is the append-only property the
    # erasure receipt depends on. A test that could tidy them away would be
    # testing a database this product does not run on.
    await session.execute(
        text("DELETE FROM tenants WHERE id = :t"), {"t": str(world.tenant)}
    )
    await session.commit()


# ── The trigger that makes the erasure predicate possible ────────────────────


@pytest.mark.asyncio
async def test_the_database_classifies_a_chunk_no_writer_classified() -> None:
    """`rag/index` UPSERTs chunks and sets no ACL column. A classification that
    depended on a remembering caller would be NULL on the row that mattered, so
    migration 0092 puts it in a trigger and every writer inherits it."""
    engine, factory = await _factory_or_skip()
    world = World()
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                await _seed(session, world)
                rows = (
                    await session.execute(
                        text(
                            "SELECT source_type, acl_capability, acl_candidate_id "
                            "FROM context_chunks WHERE tenant_id = :t "
                            "ORDER BY source_type"
                        ),
                        {"t": str(world.tenant)},
                    )
                ).all()
                by_type = {row.source_type: row for row in rows}
                assert by_type["assessment"].acl_capability == "view_review_screen"
                assert by_type["assessment"].acl_candidate_id == world.subject
                assert by_type["resume"].acl_capability == "view_review_screen"
                # A JD is text the tenant itself wrote and publishes on a public
                # application page: tenant membership is the whole check.
                assert by_type["jd"].acl_capability is None
                assert by_type["jd"].acl_candidate_id is None
                await _cleanup(session, world)
    finally:
        await engine.dispose()


# ── The cascade ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_erasure_leaves_zero_residual_vectors_and_zero_cache_keys() -> None:
    engine, factory = await _factory_or_skip()
    world = World()
    redis = _redis_or_skip()
    subject_keys = [
        cache.key(erasure.CANDIDATE_CACHE_NAMESPACE, str(world.subject)),
        cache.key(
            erasure.CANDIDATE_CACHE_NAMESPACE, str(world.subject), "profile"
        ),
        cache.key(
            erasure.CANDIDATE_CACHE_NAMESPACE, str(world.subject), "projects", "1"
        ),
    ]
    bystander_key = cache.key(
        erasure.CANDIDATE_CACHE_NAMESPACE, str(world.bystander), "profile"
    )
    try:
        try:
            for key in [*subject_keys, bystander_key]:
                await redis.set(key, "cached", ex=300)
        except Exception:  # noqa: BLE001
            pytest.skip("no redis reachable, skipping erasure integration test")

        async with factory() as session:
            async with superadmin_scope(session):
                await _seed(session, world)
                receipt = await erasure.cascade_erasure(session, world.subject)
                await session.commit()

        async with factory() as session:
            async with superadmin_scope(session):
                # ZERO RESIDUAL VECTORS, read back rather than reported.
                remaining_chunks = (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM context_chunks WHERE "
                            + chunk_acl.candidate_scope_clause()
                        ),
                        chunk_acl.candidate_scope_params(world.subject),
                    )
                ).scalar_one()
                assert remaining_chunks == 0
                profile_vectors = (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM profiles WHERE candidate_id = :c "
                            "AND (embedding IS NOT NULL OR embedding_shadow IS NOT NULL)"
                        ),
                        {"c": str(world.subject)},
                    )
                ).scalar_one()
                assert profile_vectors == 0
                assert (
                    await session.execute(
                        text("SELECT count(*) FROM profiles WHERE candidate_id = :c"),
                        {"c": str(world.subject)},
                    )
                ).scalar_one() == 0
                assert (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM candidate_projects "
                            "WHERE candidate_id = :c"
                        ),
                        {"c": str(world.subject)},
                    )
                ).scalar_one() == 0
                assert (
                    await session.execute(
                        text("SELECT count(*) FROM candidates WHERE id = :c"),
                        {"c": str(world.subject)},
                    )
                ).scalar_one() == 0

                # THE CONTROL. An erasure that deleted the table would satisfy
                # every assertion above.
                assert (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM context_chunks WHERE "
                            "acl_candidate_id = :c"
                        ),
                        {"c": str(world.bystander)},
                    )
                ).scalar_one() == 1
                assert (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM context_chunks WHERE "
                            "source_type = 'jd' AND tenant_id = :t"
                        ),
                        {"t": str(world.tenant)},
                    )
                ).scalar_one() == 1

                # THE AUDIT ROW SURVIVES THE SUBJECT. `audit_log.candidate_id`
                # has no foreign key to `candidates` on purpose: an audit trail
                # a subject can delete is not an audit trail.
                assert (
                    await session.execute(
                        text(
                            "SELECT count(*) FROM audit_log WHERE candidate_id = :c "
                            "AND action = :a"
                        ),
                        {"c": str(world.subject), "a": erasure.ACTION_CANDIDATE_ERASED},
                    )
                ).scalar_one() == 1

        # ZERO RESIDUAL CACHE KEYS, read back from Redis.
        for key in subject_keys:
            assert await redis.get(key) is None
        assert await redis.get(bystander_key) == "cached"

        assert receipt.chunks_deleted == 2
        assert receipt.profile_vectors_cleared == 1
        assert receipt.projects_deleted == 1
        assert receipt.cache_keys_deleted == len(subject_keys)
    finally:
        async with factory() as session:
            async with superadmin_scope(session):
                await _cleanup(session, world)
        await redis.delete(bystander_key)
        await redis.aclose()
        await engine.dispose()


# ── The data map ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_vector_column_in_the_schema_is_classified() -> None:
    """The data map is EXECUTABLE: `cascade_erasure` iterates it, so a vector
    column added without an entry is a column an erasure silently walks past."""
    engine, factory = await _factory_or_skip()
    try:
        async with factory() as session:
            rows = (
                await session.execute(
                    text(
                        "SELECT table_name, column_name FROM information_schema.columns "
                        "WHERE udt_name = 'vector' AND table_schema = 'public'"
                    )
                )
            ).all()
    finally:
        await engine.dispose()
    in_schema = {(row.table_name, row.column_name) for row in rows}
    classified = {(entry.table, entry.column) for entry in erasure.VECTOR_COLUMNS}
    assert in_schema, "the schema should declare at least one vector column"
    assert in_schema <= classified, f"unclassified vector columns: {in_schema - classified}"


def test_every_classified_column_states_how_an_erasure_reaches_it() -> None:
    """Including the ones an erasure does not touch. "Not erased" without its
    reason is what reads as the finding in a security review."""
    assert erasure.VECTOR_COLUMNS
    for entry in erasure.VECTOR_COLUMNS:
        assert entry.embeds
        assert len(entry.erasure) > 30


def test_the_ai_reach_vector_carries_its_cross_tenant_justification() -> None:
    """`bd_leads` AI Reach is the ONE place cross-tenant vector similarity is a
    feature. Written down here, and in migration 0092, so it does not read as
    the finding in the first security review."""
    entry = next(
        item for item in erasure.VECTOR_COLUMNS
        if (item.table, item.column) == ("jobs", "reach_embedding")
    )
    assert entry.candidate_pii is False
    for phrase in ("cross-tenant", "platform staff", "no resume", "no score"):
        assert phrase in entry.erasure


# ── The retrieval-time predicate ─────────────────────────────────────────────


def test_a_chunk_needing_no_capability_is_readable_by_anyone_in_the_tenant() -> None:
    assert chunk_acl.may_read(None, set()) is True


def test_a_candidate_chunk_needs_the_capability_at_read_time() -> None:
    """Permissions change after storage: a recruiter loses the capability and
    every chunk written before that moment is still in the index."""
    assert chunk_acl.may_read("view_review_screen", {"view_review_screen"}) is True
    assert chunk_acl.may_read("view_review_screen", {"view_dashboard"}) is False
    assert chunk_acl.may_read("view_review_screen", set()) is False


@pytest.mark.asyncio
async def test_the_sql_predicate_and_the_python_predicate_agree() -> None:
    """Two implementations of one rule is the failure `tiers.py` was, so the
    pair is pinned against each other against the real database."""
    engine, factory = await _factory_or_skip()
    cases = [(None, True), ("view_review_screen", True), ("decide_profile", False)]
    held = ["view_review_screen"]
    try:
        async with factory() as session:
            for stored, expected in cases:
                allowed = (
                    await session.execute(
                        text(
                            "SELECT "
                            + chunk_acl.visibility_clause().replace(
                                "acl_capability", "CAST(:stored AS varchar)"
                            )
                        ),
                        {"stored": stored, **chunk_acl.visibility_params(held)},
                    )
                ).scalar_one()
                assert allowed is expected
                assert chunk_acl.may_read(stored, held) is expected
    finally:
        await engine.dispose()
