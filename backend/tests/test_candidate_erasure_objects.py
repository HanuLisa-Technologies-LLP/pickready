"""An erasure reaches the STORED FILES, and a half-finished one is findable.

WHAT WAS WRONG, AND WHY NO EXISTING TEST COULD SEE IT
-------------------------------------------------------
`services/erasure` deleted rows, vectors and cache keys and never named
`object_storage` once. So a candidate who pressed Delete My Profile, typed the
confirmation phrase and read a warning screen promising that their "complete
profile, including all background verification records and assessment data,
will be permanently deleted" kept, in the object store:

  * their resume,
  * their assessment video recording, raw and compressed,
  * any staged project original whose deletion had previously failed,
  * every file on their conversations.

Every assertion in `test_erasure_cascade.py` still passed, because every one of
them reads the DATABASE, and the database was genuinely clean. That is the
shape of this defect: the rows that NAMED those objects were the first thing
deleted, so after the erasure nothing in the product could even tell you what
had been left behind.

THE KEYS ARE READ BEFORE THE ROWS GO, AND THIS FILE ASSERTS THAT ORDERING
---------------------------------------------------------------------------
`open_deletion_request` captures the enumeration onto a record that outlives
its subject. `test_the_keys_survive_the_rows_that_named_them` is the one that
would fail if somebody moved the enumeration after the cascade, which is the
natural-looking rewrite and produces an empty list and a silent, permanent
"complete" deletion of nothing.

THE STORE IS FAKED, DELIBERATELY
----------------------------------
A real bucket would make this an integration test nobody runs, and the
behaviour under test is not S3's. It is: which keys are enumerated, that each
delete is CONFIRMED rather than assumed, that a refusal leaves the request open
and retryable, and that a retry deletes only what is left. `_FakeStore` answers
a HEAD from its own contents, so "the delete call returned" is never what
satisfies an assertion here.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.services import deletion_requests, erasure


async def _factory_or_skip():
    engine = create_async_engine(
        get_settings().database_url, pool_size=1, max_overflow=0
    )
    try:
        async with engine.connect():
            pass
    except Exception:  # noqa: BLE001
        await engine.dispose()
        pytest.skip("no database reachable, skipping erasure object test")
    return engine, async_sessionmaker(engine, expire_on_commit=False)


class _FakeStore:
    """An object store that answers a HEAD from what it actually holds.

    `refuse` names keys whose delete raises, which is an outage. `stubborn`
    names keys whose delete SUCCEEDS and which are still there afterwards,
    which is the case a return code cannot detect and the HEAD can.
    """

    def __init__(self, keys: set[str]) -> None:
        self.keys = set(keys)
        self.refuse: set[str] = set()
        self.stubborn: set[str] = set()
        self.delete_calls: list[str] = []

    def delete_verified(self, key: str) -> bool:
        self.delete_calls.append(key)
        if key in self.refuse:
            raise RuntimeError("the object store is unavailable")
        if key in self.stubborn:
            return False
        self.keys.discard(key)
        return key not in self.keys


def _install(monkeypatch, store: _FakeStore) -> None:
    """Patch the real module attribute, never `sys.modules`.

    claude.md records why: `from package import submodule` resolves the PACKAGE
    ATTRIBUTE once anything has imported the real module, after which a
    `sys.modules` entry is never consulted again, and the fake silently stops
    working the moment an earlier test touches the real thing.
    """
    from app.services.video import storage as video_storage

    monkeypatch.setattr(video_storage, "delete_verified", store.delete_verified)


class World:
    """One tenant, two candidates, and a file of every kind on the subject.

    The bystander exists because an erasure that deleted every object in the
    product would satisfy any assertion phrased only about the subject.
    """

    def __init__(self) -> None:
        self.tenant = uuid.uuid4()
        self.job = uuid.uuid4()
        self.subject = uuid.uuid4()
        self.subject_profile = uuid.uuid4()
        self.subject_link = uuid.uuid4()
        self.subject_project = uuid.uuid4()
        self.subject_assessment = uuid.uuid4()
        self.subject_conversation = uuid.uuid4()
        self.subject_message = uuid.uuid4()
        self.bystander = uuid.uuid4()
        self.bystander_profile = uuid.uuid4()

    @property
    def subject_keys(self) -> set[str]:
        return {
            f"resumes/{self.subject}.pdf",
            f"assessment-raw/{self.subject}.webm",
            f"assessment-video/{self.subject}.mp4",
            f"project-intake/{self.subject}/archive.zip",
            f"conversations/{self.subject}/letter.pdf",
        }

    @property
    def bystander_keys(self) -> set[str]:
        return {f"resumes/{self.bystander}.pdf"}


async def _seed(session, world: World) -> None:
    await session.execute(
        text(
            "INSERT INTO tenants (id, name, domain, spf_dkim_status) "
            "VALUES (:t, :n, :d, 'pending')"
        ),
        {
            "t": str(world.tenant),
            "n": f"objects-{world.tenant}",
            "d": f"{world.tenant}.objects.test",
        },
    )
    await session.execute(
        text(
            "INSERT INTO jobs (id, tenant_id, title, jd_json, status) "
            "VALUES (:j, :t, 'Backend Engineer', '{}'::jsonb, 'draft')"
        ),
        {"j": str(world.job), "t": str(world.tenant)},
    )
    for candidate, profile, key in (
        (world.subject, world.subject_profile, f"resumes/{world.subject}.pdf"),
        (
            world.bystander,
            world.bystander_profile,
            f"resumes/{world.bystander}.pdf",
        ),
    ):
        await session.execute(
            text(
                "INSERT INTO candidates (id, tenant_id, full_name, email, "
                "consent_databank) VALUES (:c, :t, 'Test Person', :e, false)"
            ),
            {
                "c": str(candidate),
                "t": str(world.tenant),
                "e": f"{candidate}@objects.test",
            },
        )
        await session.execute(
            text(
                "INSERT INTO profiles (id, candidate_id, source_tenant_id, "
                "resume_public_id, resume_storage_provider) "
                "VALUES (:p, :c, :t, :k, 's3')"
            ),
            {
                "p": str(profile),
                "c": str(candidate),
                "t": str(world.tenant),
                "k": key,
            },
        )
    await session.execute(
        text(
            "INSERT INTO job_candidate_links "
            "(id, tenant_id, job_id, candidate_id, profile_id, source) "
            "VALUES (:l, :t, :j, :c, :p, 'manual')"
        ),
        {
            "l": str(world.subject_link),
            "t": str(world.tenant),
            "j": str(world.job),
            "c": str(world.subject),
            "p": str(world.subject_profile),
        },
    )
    await session.execute(
        text(
            "INSERT INTO assessment_conversations "
            "(id, tenant_id, job_id, job_candidate_link_id, grade) "
            "VALUES (:a, :t, :j, :l, 'non_managerial')"
        ),
        {
            "a": str(world.subject_assessment),
            "t": str(world.tenant),
            "j": str(world.job),
            "l": str(world.subject_link),
        },
    )
    await session.execute(
        text(
            "INSERT INTO video_recordings (id, tenant_id, conversation_id, "
            "candidate_id, job_candidate_link_id, status, s3_raw_key, "
            "s3_compressed_key) VALUES (:i, :t, :a, :c, :l, 'ready', "
            ":raw, :compressed)"
        ),
        {
            "i": str(uuid.uuid4()),
            "t": str(world.tenant),
            "a": str(world.subject_assessment),
            "c": str(world.subject),
            "l": str(world.subject_link),
            "raw": f"assessment-raw/{world.subject}.webm",
            "compressed": f"assessment-video/{world.subject}.mp4",
        },
    )
    # A project whose staged original is STILL THERE, which is the row the
    # intake sweep exists for and exactly the one an erasure must not miss.
    await session.execute(
        text(
            "INSERT INTO candidate_projects (id, candidate_id, name, "
            "description, submission_kind, status, intake_objects_json) "
            "VALUES (:i, :c, 'Pipeline', 'A data pipeline.', 'files', "
            "'processed', CAST(:objects AS jsonb))"
        ),
        {
            "i": str(world.subject_project),
            "c": str(world.subject),
            "objects": '[{"key": "project-intake/%s/archive.zip"}]'
            % world.subject,
        },
    )
    await session.execute(
        text(
            "INSERT INTO conversations (id, tenant_id, kind, subject, "
            "candidate_id, thread_token) "
            "VALUES (:i, :t, 'candidate', 'About your application', :c, :tok)"
        ),
        {
            "i": str(world.subject_conversation),
            "t": str(world.tenant),
            "c": str(world.subject),
            "tok": uuid.uuid4().hex,
        },
    )
    await session.execute(
        text(
            "INSERT INTO conversation_messages (id, conversation_id, tenant_id, "
            "author_party, body, channel) "
            "VALUES (:i, :conv, :t, 'candidate', 'Here is the letter.', 'chat')"
        ),
        {
            "i": str(world.subject_message),
            "conv": str(world.subject_conversation),
            "t": str(world.tenant),
        },
    )
    await session.execute(
        text(
            "INSERT INTO conversation_attachments (id, message_id, tenant_id, "
            "object_key, filename, content_type, size_bytes) "
            "VALUES (:i, :m, :t, :k, 'letter.pdf', 'application/pdf', 1024)"
        ),
        {
            "i": str(uuid.uuid4()),
            "m": str(world.subject_message),
            "t": str(world.tenant),
            "k": f"conversations/{world.subject}/letter.pdf",
        },
    )


async def _cleanup(session, world: World) -> None:
    for candidate in (world.subject, world.bystander):
        await session.execute(
            text("DELETE FROM candidates WHERE id = :c"), {"c": str(candidate)}
        )
        await session.execute(
            text("DELETE FROM candidate_deletion_requests WHERE candidate_id = :c"),
            {"c": str(candidate)},
        )
    await session.execute(
        text("DELETE FROM tenants WHERE id = :t"), {"t": str(world.tenant)}
    )
    await session.commit()


@pytest.mark.asyncio
async def test_every_store_in_the_product_is_enumerated() -> None:
    """Five kinds of object, one candidate, and nobody else's.

    Named individually rather than counted, because a count passes while the
    enumeration silently drops a whole store, and dropping the video keys is
    precisely the miss this change exists to repair.
    """
    engine, factory = await _factory_or_skip()
    world = World()
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                await _seed(session, world)
                await session.commit()
                found = await erasure.candidate_object_keys(session, world.subject)

        keys = {entry["key"] for entry in found}
        assert keys == world.subject_keys, (
            "the enumeration missed a store. Every key it does not name is a "
            "file that survives an erasure whose warning screen promised it "
            f"would not: {world.subject_keys - keys}"
        )
        kinds = {entry["kind"] for entry in found}
        assert kinds == {
            erasure.KIND_RESUME,
            erasure.KIND_VIDEO_RAW,
            erasure.KIND_VIDEO_COMPRESSED,
            erasure.KIND_PROJECT_INTAKE,
            erasure.KIND_CONVERSATION_ATTACHMENT,
        }
        assert not (keys & world.bystander_keys), (
            "the enumeration reached another candidate's files"
        )
    finally:
        async with factory() as session:
            async with superadmin_scope(session):
                await _cleanup(session, world)
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_keys_survive_the_rows_that_named_them() -> None:
    """The ordering assertion, and the one a plausible rewrite would break.

    Enumerating AFTER the cascade looks tidier and returns an empty list, so
    the request would complete immediately having deleted nothing, for ever,
    with a green log line saying so.
    """
    engine, factory = await _factory_or_skip()
    world = World()
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                await _seed(session, world)
                await session.commit()

                request = await erasure.open_deletion_request(
                    session,
                    world.subject,
                    reason=deletion_requests.REASON_CANDIDATE_REQUESTED,
                )
                await erasure.cascade_erasure(session, world.subject)
                await erasure.mark_rows_erased(session, request)
                await session.commit()
                request_id = request.id

        # Read the record back from a SECOND session, because an assertion
        # against the object still in the first one's identity map cannot see
        # what was actually committed.
        async with factory() as session:
            async with superadmin_scope(session):
                row = (
                    await session.execute(
                        text(
                            "SELECT state, objects_total, object_keys_json "
                            "FROM candidate_deletion_requests WHERE id = :i"
                        ),
                        {"i": str(request_id)},
                    )
                ).first()
                gone = (
                    await session.execute(
                        text("SELECT id FROM candidates WHERE id = :c"),
                        {"c": str(world.subject)},
                    )
                ).first()

        assert gone is None, "the candidate row survived the cascade"
        assert row is not None, "the deletion record did not outlive its subject"
        state, total, stored = row
        assert state == deletion_requests.STATE_ROWS_ERASED
        assert total == len(world.subject_keys)
        assert {entry["key"] for entry in stored} == world.subject_keys, (
            "the record does not name the files that are still in the store, "
            "so nothing can ever finish deleting them"
        )
    finally:
        async with factory() as session:
            async with superadmin_scope(session):
                await _cleanup(session, world)
        await engine.dispose()


@pytest.mark.asyncio
async def test_the_objects_are_deleted_and_the_request_completes(
    monkeypatch,
) -> None:
    """The whole erasure, end to end, read back from the store itself."""
    engine, factory = await _factory_or_skip()
    world = World()
    store = _FakeStore(world.subject_keys | world.bystander_keys)
    _install(monkeypatch, store)
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                await _seed(session, world)
                await session.commit()
                request = await erasure.open_deletion_request(
                    session,
                    world.subject,
                    reason=deletion_requests.REASON_CANDIDATE_REQUESTED,
                )
                await erasure.cascade_erasure(session, world.subject)
                await erasure.mark_rows_erased(session, request)
                await erasure.run_object_deletion(session, request)
                await session.commit()
                request_id = request.id

        assert store.keys == world.bystander_keys, (
            "the erasure left the subject's files behind, or reached somebody "
            f"else's: {store.keys}"
        )
        async with factory() as session:
            async with superadmin_scope(session):
                state, deleted, total, remaining = (
                    await session.execute(
                        text(
                            "SELECT state, objects_deleted, objects_total, "
                            "jsonb_array_length(object_keys_json) "
                            "FROM candidate_deletion_requests WHERE id = :i"
                        ),
                        {"i": str(request_id)},
                    )
                ).one()
        assert state == deletion_requests.STATE_COMPLETED
        assert deleted == total == len(world.subject_keys)
        assert remaining == 0
    finally:
        async with factory() as session:
            async with superadmin_scope(session):
                await _cleanup(session, world)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_refused_delete_leaves_the_request_open_and_retryable(
    monkeypatch,
) -> None:
    """THE ASSERTION THIS ORCHESTRATION EXISTS FOR.

    An object store that refuses must not produce a completed erasure, must not
    lose the work it did manage, and must leave something for the sweep to pick
    up. A second pass then deletes ONLY what is left, which is what makes the
    hourly sweep safe to run against a request that is mostly done.
    """
    engine, factory = await _factory_or_skip()
    world = World()
    store = _FakeStore(world.subject_keys | world.bystander_keys)
    stubborn = f"assessment-video/{world.subject}.mp4"
    refused = f"resumes/{world.subject}.pdf"
    store.stubborn.add(stubborn)
    store.refuse.add(refused)
    _install(monkeypatch, store)
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                await _seed(session, world)
                await session.commit()
                request = await erasure.open_deletion_request(
                    session,
                    world.subject,
                    reason=deletion_requests.REASON_CANDIDATE_REQUESTED,
                )
                await erasure.cascade_erasure(session, world.subject)
                await erasure.mark_rows_erased(session, request)
                first = await erasure.run_object_deletion(session, request)
                await session.commit()

                assert request.state == deletion_requests.STATE_ROWS_ERASED, (
                    "a request completed while files were still in the store"
                )
                assert first.failure is not None
                assert request.objects_deleted == 3
                assert {
                    entry["key"] for entry in request.object_keys_json
                } == {stubborn, refused}
                assert request.deletion_attempts == 1

                # The outage clears and the stubborn object finally goes.
                store.refuse.clear()
                store.stubborn.clear()
                store.delete_calls.clear()
                second = await erasure.run_object_deletion(session, request)
                await session.commit()

        assert second.failure is None
        assert request.state == deletion_requests.STATE_COMPLETED
        assert request.objects_deleted == request.objects_total
        assert sorted(store.delete_calls) == sorted([stubborn, refused]), (
            "the retry re-deleted objects it had already confirmed gone, so "
            "the remaining list is not shrinking and the counters cannot be "
            "trusted"
        )
        assert store.keys == world.bystander_keys
    finally:
        async with factory() as session:
            async with superadmin_scope(session):
                await _cleanup(session, world)
        await engine.dispose()


@pytest.mark.asyncio
async def test_a_pre_migration_resume_is_refused_rather_than_reported_deleted(
    monkeypatch,
) -> None:
    """The one object this product cannot delete says so.

    A resume row written before the object-store migration carries a key that
    means nothing to the current store. Deleting it there is a no-op, the HEAD
    that follows says "absent", and an erasure would report the candidate's
    resume permanently deleted while the bytes sat untouched in the old bucket.
    """
    engine, factory = await _factory_or_skip()
    world = World()
    store = _FakeStore(set())
    _install(monkeypatch, store)
    try:
        async with factory() as session:
            async with superadmin_scope(session):
                await _seed(session, world)
                await session.execute(
                    text(
                        "UPDATE profiles SET resume_storage_provider = :legacy "
                        "WHERE candidate_id = :c"
                    ),
                    {
                        # Any provider but the current one. The CHECK still
                        # admits the pre-AWS value until a migration narrows
                        # it, which is exactly why the refusal is keyed on
                        # "not current" rather than on that name.
                        "legacy": "gcs",
                        "c": str(world.subject),
                    },
                )
                await session.commit()
                found = await erasure.candidate_object_keys(session, world.subject)

        legacy = [
            entry
            for entry in found
            if entry["kind"] == erasure.KIND_RESUME_LEGACY
        ]
        assert len(legacy) == 1
        with pytest.raises(erasure.LegacyObjectNotDeletable):
            erasure.delete_object_verified(legacy[0])
        assert not store.delete_calls, (
            "the pre-migration key was sent to the current store, where the "
            "delete is a no-op and the HEAD then reports a successful erasure"
        )
    finally:
        async with factory() as session:
            async with superadmin_scope(session):
                await _cleanup(session, world)
        await engine.dispose()
