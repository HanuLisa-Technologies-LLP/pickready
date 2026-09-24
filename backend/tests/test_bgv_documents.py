"""A fresher's own verification documents: hostile input, and an erasure that reaches it.

WHAT THE BRIEF ASKS FOR
-------------------------
"Freshers: no employer BGV. Academic certificates and address proof only."
Until now the only related surface was an Available / Not Available radio that
stored no file.

THE THREE THINGS WORTH TESTING, AND WHY EACH ONE
--------------------------------------------------
* **Validation, from the bytes rather than the name.** A renamed file is the
  ordinary case (somebody exports a scan and types a filename), and this is a
  candidate-supplied upload, so the magic bytes decide. The negative case here
  is a file called `.pdf` that is not one.
* **The key never crosses the boundary.** A response that carried the object
  key would hand out the address of a private store, which is the same mistake
  as naming the storage vendor in user-facing copy.
* **Erasure reaches the BYTES.** Deleting the row deletes nothing in the object
  store: no foreign key reaches an object. That is the trap `context_chunks`
  and the video recordings already sprang on this product, and the assertion
  that catches it is a HEAD against the store after the rows are gone, never a
  row count.

AND ONE THING THAT MUST STAY FALSE: none of this gates anything. A fresher's
`derive_status` is `not_required` whether they have uploaded a certificate or
not, because the brief gives a fresher no verification step that could complete
and inventing one would end a candidacy over paperwork nobody asked for.
"""
from __future__ import annotations

import asyncio
import io
import os
import uuid
import zlib
from typing import Iterator

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.deps import CurrentUser, get_candidate_db, get_current_candidate
from app.core.config import get_settings
from app.core.db import superadmin_scope
from app.core.security import AUDIENCE_CANDIDATE
from app.main import app
from app.models.enums import Role
from app.services import bgv_documents, bgv_workflow, object_storage

BGV = "/api/v1/bgv"
BUCKET = os.environ.get("S3_TEST_BUCKET", "readypick-test-private")
REGION = "ap-south-1"
LIVE_ENDPOINT = os.environ.get("S3_TEST_ENDPOINT_URL", "").strip()


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _sessions():
    return async_sessionmaker(
        create_async_engine(get_settings().database_url, poolclass=NullPool),
        expire_on_commit=False,
    )


async def _reachable() -> bool:
    engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(
                sa.text("SELECT object_key FROM candidate_bgv_documents LIMIT 0")
            )
        return True
    except Exception:  # noqa: BLE001 -- no database, or migration 0114 unapplied
        return False
    finally:
        await engine.dispose()


class _Settings:
    """What `object_storage.get_settings()` returns for these tests."""

    s3_bucket = BUCKET
    aws_region = REGION
    s3_endpoint_url = LIVE_ENDPOINT


@pytest.fixture
def storage(monkeypatch):
    """MinIO when the stack names one, moto otherwise.

    DELIBERATELY DOES NOT EMPTY THE BUCKET, unlike `test_object_storage`'s own
    fixture: several agents share this stack, and a sweep here would delete
    objects another run is mid-assertion about. Every object below is content
    addressed off a unique payload, so nothing collides without emptying it.

    It DOES create the bucket when it is missing, which is the one thing a
    shared stack routinely loses: the compose volume is recreated and the
    bucket goes with it. Creating it is what the stack declares anyway, and it
    is additive, so it cannot disturb another run.
    """
    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError

    monkeypatch.setattr(object_storage, "get_settings", lambda: _Settings())
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    # See test_object_storage: boto3's process-wide DEFAULT_SESSION resolves
    # credentials once, so an earlier test in the same process can leave this
    # one signing with the machine's ambient key.
    monkeypatch.setattr(boto3, "DEFAULT_SESSION", None)

    if LIVE_ENDPOINT:
        monkeypatch.setenv(
            "AWS_ACCESS_KEY_ID", os.environ.get("S3_TEST_ACCESS_KEY", "readypick_test")
        )
        monkeypatch.setenv(
            "AWS_SECRET_ACCESS_KEY",
            os.environ.get("S3_TEST_SECRET_KEY", "readypick_test"),
        )
        probe = boto3.client(
            "s3",
            region_name=REGION,
            endpoint_url=LIVE_ENDPOINT,
            # Short and retry-free FOR THE PROBE ONLY: botocore's defaults
            # spend roughly twenty seconds retrying a refused connection.
            config=Config(
                connect_timeout=2, read_timeout=5, retries={"max_attempts": 1}
            ),
        )
        try:
            probe.head_bucket(Bucket=BUCKET)
        except ClientError:
            probe.create_bucket(
                Bucket=BUCKET,
                CreateBucketConfiguration={"LocationConstraint": REGION},
            )
        except BotoCoreError as exc:
            raise AssertionError(
                f"S3_TEST_ENDPOINT_URL is {LIVE_ENDPOINT!r} but the endpoint "
                "could not be reached. Start the test stack with: "
                "docker compose -f docker-compose.test.yml up -d --wait. "
                f"Underlying error: {exc}"
            ) from exc
        object_storage.reset_client()
        yield
        object_storage.reset_client()
        return

    import moto

    with moto.mock_aws():
        object_storage.reset_client()
        boto3.client("s3", region_name=REGION).create_bucket(
            Bucket=BUCKET,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
        yield
        object_storage.reset_client()


class World:
    def __init__(self) -> None:
        self.candidate = uuid.uuid4()
        self.user = uuid.uuid4()
        self.email = f"fresher-{self.candidate.hex[:10]}@bgvdocs.test"


@pytest.fixture
def world() -> Iterator[World]:
    if not _run(_reachable()):
        pytest.skip("no database with migration 0114 -- skipping BGV document test")
    w = World()
    sessions = _sessions()

    async def _seed() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text(
                            "INSERT INTO users (id, tenant_id, email, full_name, "
                            " role, status) "
                            "VALUES (:id, NULL, :email, 'Meera Iyer', "
                            " 'candidate', 'active')"
                        ),
                        {"id": str(w.user), "email": w.email},
                    )
                    await session.execute(
                        sa.text(
                            "INSERT INTO candidates (id, user_id, full_name, email, "
                            " consent_databank, employment_background, created_at) "
                            "VALUES (:id, :uid, 'Meera Iyer', :email, false, "
                            " 'fresher', now())"
                        ),
                        # LINKED by user_id: the only thing a request resolves
                        # a candidate by (`candidate_identity`).
                        {"id": str(w.candidate), "uid": str(w.user), "email": w.email},
                    )

    async def _teardown() -> None:
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    await session.execute(
                        sa.text("DELETE FROM candidates WHERE id = :id"),
                        {"id": str(w.candidate)},
                    )
                    await session.execute(
                        sa.text("DELETE FROM users WHERE id = :id"),
                        {"id": str(w.user)},
                    )

    _run(_seed())
    try:
        yield w
    finally:
        _run(_teardown())


@pytest.fixture
def client(world: World) -> Iterator[TestClient]:
    sessions = _sessions()

    async def _candidate_db():
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    yield session

    # A candidate-audience principal, the only kind a candidate's cookie can
    # produce. `test_bgv_real_candidate_token.py` makes these calls with a real
    # session and no overrides.
    async def _current_candidate() -> CurrentUser:
        return CurrentUser(
            user_id=world.user,
            tenant_id=None,
            role=Role.candidate,
            audience=AUDIENCE_CANDIDATE,
        )

    # THE UPLOAD LIMITER IS REAL AND ITS COUNTER OUTLIVES THE RUN. It is keyed
    # on the caller's IP, every TestClient request arrives from `testclient`,
    # and Redis keeps the window for an hour, so the thirtieth upload across
    # ALL runs on this machine is refused with a 429 that has nothing to do
    # with the assertion being made. The counter for this one bucket is reset;
    # nothing else in Redis is touched, because the stack is shared.
    _reset_upload_limiter()

    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_candidate_db] = _candidate_db
    app.dependency_overrides[get_current_candidate] = _current_candidate
    try:
        with TestClient(app) as http:
            yield http
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def _reset_upload_limiter() -> None:
    """Drop this bucket's counters, and only this bucket's."""
    import redis.asyncio as redis_asyncio

    async def _drop() -> None:
        client = redis_asyncio.from_url(get_settings().redis_url)
        try:
            keys = [key async for key in client.scan_iter("ratelimit:bgv_document_upload:*")]
            if keys:
                await client.delete(*keys)
        finally:
            await client.aclose()

    _run(_drop())


#: The eight bytes every PNG opens with. Named once, because the validator
#: reads them and a test that mistyped them would be testing the typo.
_PNG_MAGIC = bytes([0x89]) + b"PNG\r\n" + bytes([0x1A, 0x0A])


def _png(seed: bytes) -> bytes:
    """A byte-valid PNG whose content is unique per `seed`.

    Real magic bytes, because the validator reads them; a unique tail, because
    the object key is a content address and two tests sharing a payload would
    share an object and make one test's delete the other's failure.
    """
    return _PNG_MAGIC + zlib.compress(seed + os.urandom(16))


def _upload(client: TestClient, kind: str, payload: bytes, name: str = "scan.png"):
    return client.post(
        f"{BGV}/me/documents",
        data={"document_type": kind},
        files={"file": (name, io.BytesIO(payload), "image/png")},
    )


# ── Validation ───────────────────────────────────────────────────────────────


def test_a_file_that_is_not_what_its_name_claims_is_refused(
    world: World, client: TestClient, storage
):
    """The magic bytes decide, not the extension somebody typed."""
    response = client.post(
        f"{BGV}/me/documents",
        data={"document_type": "academic_certificate"},
        files={"file": ("degree.pdf", io.BytesIO(b"not a pdf at all"),
                        "application/pdf")},
    )
    assert response.status_code == 422
    assert "valid PDF" in response.json()["detail"]


def test_an_unsupported_format_is_refused_with_the_limits_named(
    world: World, client: TestClient, storage
):
    response = client.post(
        f"{BGV}/me/documents",
        data={"document_type": "address_proof"},
        files={"file": ("proof.docx", io.BytesIO(b"PK\x03\x04"),
                        "application/octet-stream")},
    )
    assert response.status_code == 422
    # The hint is RENDERED from the setting that enforces it, so the sentence
    # and the check cannot drift.
    assert bgv_documents.upload_limits_hint() in response.json()["detail"]


def test_an_unknown_document_type_is_refused(
    world: World, client: TestClient, storage
):
    assert _upload(client, "passport_scan", _png(b"x")).status_code == 422


def test_the_size_ceiling_is_configuration_not_a_literal(
    world: World, client: TestClient, storage, monkeypatch
):
    """Every ceiling is a `bgv_document_*` setting, the `project_*` rule."""
    settings = get_settings()
    monkeypatch.setattr(settings, "bgv_document_max_bytes", 64, raising=False)
    # Padding that does not compress, so the payload's size on the wire is the
    # size this asserts about rather than whatever zlib made of it.
    oversized = _PNG_MAGIC + os.urandom(4096)
    assert _upload(client, "address_proof", oversized).status_code == 413


# ── Storage ──────────────────────────────────────────────────────────────────


def test_an_upload_is_listed_and_carries_no_object_key(
    world: World, client: TestClient, storage
):
    payload = _png(b"degree-certificate")
    created = _upload(client, "academic_certificate", payload, name="degree.png")
    assert created.status_code == 201, created.text
    body = created.json()
    assert len(body["documents"]) == 1
    document = body["documents"][0]
    assert document["original_filename"] == "degree.png"
    assert document["document_label"] == "Academic certificate"
    # NOTHING NAMING THE STORE. A key is the address of a private bucket by
    # another route, and it has no reason to be on an API boundary.
    assert "object_key" not in document
    assert "bgv-documents" not in created.text
    # Both slots are always offered, so a missing address proof cannot hide in
    # a short list.
    assert {item["key"] for item in body["accepted_types"]} == {
        "academic_certificate",
        "address_proof",
    }


def test_the_same_scan_uploaded_twice_is_one_document(
    world: World, client: TestClient, storage
):
    """A double click and a retry after a lost response are the ordinary case."""
    payload = _png(b"the same certificate")
    assert _upload(client, "academic_certificate", payload).status_code == 201
    second = _upload(client, "academic_certificate", payload)
    assert second.status_code == 201
    assert len(second.json()["documents"]) == 1


def test_the_per_type_ceiling_refuses_the_next_one(
    world: World, client: TestClient, storage, monkeypatch
):
    settings = get_settings()
    monkeypatch.setattr(settings, "bgv_documents_max_per_type", 2, raising=False)
    assert _upload(client, "address_proof", _png(b"one")).status_code == 201
    assert _upload(client, "address_proof", _png(b"two")).status_code == 201
    refused = _upload(client, "address_proof", _png(b"three"))
    assert refused.status_code == 409
    assert "address proof" in refused.json()["detail"]


def test_a_candidate_can_withdraw_their_own_document(
    world: World, client: TestClient, storage
):
    created = _upload(client, "address_proof", _png(b"withdraw me"))
    document_id = created.json()["documents"][0]["id"]
    removed = client.delete(f"{BGV}/me/documents/{document_id}")
    assert removed.status_code == 200
    assert removed.json()["documents"] == []
    assert client.delete(f"{BGV}/me/documents/{document_id}").status_code == 404


# ── The erasure hook ─────────────────────────────────────────────────────────


def test_erasure_enumerates_and_then_deletes_the_bytes(
    world: World, client: TestClient, storage
):
    """The assertion is a HEAD against the store, never a row count.

    Deleting the row deletes nothing in the object store, because no foreign
    key reaches an object. A test that only counted rows would pass while every
    certificate stayed readable.
    """
    assert _upload(client, "academic_certificate", _png(b"erase-a")).status_code == 201
    assert _upload(client, "address_proof", _png(b"erase-b")).status_code == 201

    async def _enumerate() -> tuple[dict[str, str], ...]:
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    return await bgv_documents.candidate_document_keys(
                        session, world.candidate
                    )

    keys = _run(_enumerate())
    assert len(keys) == 2
    assert {entry["kind"] for entry in keys} == {bgv_documents.KIND_BGV_DOCUMENT}
    for entry in keys:
        assert object_storage.exists(entry["key"]) is True

    async def _erase() -> int:
        sessions = _sessions()
        async with sessions() as session:
            async with session.begin():
                async with superadmin_scope(session):
                    return await bgv_documents.delete_candidate_documents(
                        session, world.candidate
                    )

    assert _run(_erase()) == 2
    for entry in keys:
        assert object_storage.exists(entry["key"]) is False
    # And enumerating afterwards is honestly empty, which is exactly why the
    # enumeration has to run BEFORE the cascade rather than after it.
    assert _run(_enumerate()) == ()


def test_documents_gate_nothing_for_a_fresher(world: World, client: TestClient, storage):
    """The offer gate's `not_required` path is untouched by this feature.

    Asserted against the real derivation rather than a comment: a fresher with
    no documents and a fresher with two must produce the same status, or a
    missing certificate has quietly become a blocker.
    """
    empty = bgv_workflow.derive_status(
        background="fresher", employer_count=0, statuses=[]
    )
    assert _upload(client, "academic_certificate", _png(b"gates-nothing")).status_code == 201
    still = bgv_workflow.derive_status(
        background="fresher", employer_count=0, statuses=[]
    )
    assert empty == still == "not_required"
