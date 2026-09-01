"""The one S3 object store, shared by resume and compliance-document storage.

WHY THIS MODULE EXISTS AT ALL
------------------------------
`resume_storage` and `document_storage` were deliberately kept as separate
modules -- a resume is a candidate artefact with a PDF/DOCX-only rule, a
compliance record is a scan a finance team produces and a photographed PAN card
is a JPEG, and accepting one must never widen what the other will take. That
argument is about VALIDATION and it still holds.

It was never an argument about TRANSPORT, and the two modules had drifted into
carrying two near-identical copies of the same bucket handle, the same
content-addressed upload race, the same download, and the same "a lost response
must not create a second object" reasoning. Migrating that twice would have
meant maintaining two S3 clients that must agree and eventually will not. So the
transport is here, once, and the two callers keep their own validation, their
own prefixes and their own error copy.

CONTENT-ADDRESSED, AND WHAT THAT BUYS
--------------------------------------
The object key is the sha256 of the bytes. A retry after a lost response
therefore resolves to the object already stored instead of creating a second
one, which is the whole reason a browser that times out mid-upload does not
leave a duplicate behind. `put_if_absent` is a HEAD followed by a conditional
PUT, and the race between two concurrent uploads of identical bytes is settled
by `IfNoneMatch: *` -- S3's precondition equivalent of the `if_generation_match=0`
this replaces. A `PreconditionFailed` means somebody else stored the identical
bytes first, which is a SUCCESS, not an error.

NEVER NAME THE VENDOR IN USER-FACING COPY (claude.md, 2026-07-26). Every message
this module raises states what happened, not where the bytes land. The callers'
copy already follows the rule; this module raises typed errors and lets them
phrase it.

BOTO3 IS SYNCHRONOUS, AND THAT IS HANDLED AT THE BOUNDARY
-----------------------------------------------------------
Every public function here is a plain sync function, and the callers wrap them
in `run_in_threadpool` exactly as they wrapped the GCS calls. Putting the
threadpool hop inside this module would hide it from the call site, and the call
site is where somebody reasoning about a request handler's event loop needs to
see it.

CREDENTIALS
-----------
None are read here. `boto3` resolves them from the environment, and in a
deployed environment that is the ECS task role -- an IAM role scoped to exactly
this bucket, per the per-service scoping rule in spec-doc5 §D.4. A long-lived
access key in an env var is what that rule exists to avoid, so this module must
never grow a parameter for one.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings

logger = logging.getLogger(__name__)

#: The URI scheme durable database values use. `gs://` for rows written before
#: the AWS migration; both are recognised on READ so a pre-migration row can be
#: identified and reported rather than silently 404ing, and only `s3://` is ever
#: WRITTEN.
S3_SCHEME = "s3://"
LEGACY_GCS_SCHEME = "gs://"


class ObjectStorageError(RuntimeError):
    """The object store could not be reached, or answered with nothing."""


class ObjectStorageNotConfigured(ObjectStorageError):
    """No bucket is configured. Distinct from a transport failure on purpose:
    one is an operator's missing setting and the other is an outage, and a
    caller choosing between "please retry" and "contact support" needs to tell
    them apart."""


@dataclass(frozen=True)
class StoredObject:
    key: str
    uri: str
    size_bytes: int
    created_at: datetime
    #: S3's ETag. Recorded for the same reason the GCS generation was: it is the
    #: identifier that proves WHICH version of an object a database row points
    #: at, and "confirm the artefact by digest rather than by trusting the write
    #: succeeded" is this project's standing verification discipline.
    etag: str


# ── Client ───────────────────────────────────────────────────────────────────
#
# One module-level client, built lazily and guarded by a lock. boto3 clients are
# thread-safe for calls but NOT for construction, and this module is called from
# a threadpool, so two requests arriving together on a cold process could
# otherwise build two clients and race on botocore's shared session state.

_client: Any = None
_client_lock = threading.Lock()


def _bucket_name() -> str:
    bucket = (get_settings().s3_bucket or "").strip()
    if not bucket:
        raise ObjectStorageNotConfigured("No S3 bucket is configured.")
    return bucket


def client() -> Any:
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                import boto3  # noqa: PLC0415 -- optional at import time

                settings = get_settings()
                _client = boto3.client(
                    "s3",
                    region_name=settings.aws_region or None,
                    # `endpoint_url` exists for localstack and for the test
                    # suite. It is None in every real environment, and boto3
                    # then resolves the real regional endpoint.
                    endpoint_url=settings.s3_endpoint_url or None,
                )
    return _client


def reset_client() -> None:
    """Drop the cached client. Tests only -- settings are cached per process,
    so a test that changes the bucket or the endpoint must also drop this."""
    global _client
    with _client_lock:
        _client = None


def uri_for(key: str) -> str:
    return f"{S3_SCHEME}{_bucket_name()}/{key}"


def is_legacy_uri(uri: str | None) -> bool:
    """True for an object written before the AWS migration.

    Callers use this to raise a NAMED error rather than a 404. A row pointing at
    `gs://` is not corrupt, it is un-migrated, and those are different problems
    with different fixes -- `scripts/migrate_resumes_to_s3.py` is the fix for
    one and nothing is the fix for the other.
    """
    return bool(uri) and str(uri).startswith(LEGACY_GCS_SCHEME)


# ── Operations ───────────────────────────────────────────────────────────────


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        return (
            value.astimezone(timezone.utc)
            if value.tzinfo
            else value.replace(tzinfo=timezone.utc)
        )
    return datetime.now(timezone.utc)


def _head(key: str) -> dict[str, Any] | None:
    from botocore.exceptions import ClientError  # noqa: PLC0415

    try:
        return client().head_object(Bucket=_bucket_name(), Key=key)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        # S3 answers HEAD on a missing key with 404/NoSuchKey, and on a bucket
        # the caller may LIST but not GET with 403. Both mean "not there for
        # us"; neither is a transport failure worth raising over, because the
        # very next step is a conditional PUT that will settle it.
        if code in {"404", "NoSuchKey", "NotFound", "403", "AccessDenied"}:
            return None
        raise ObjectStorageError(f"Object store HEAD failed: {code}") from exc


def put_if_absent(
    *, key: str, data: bytes, content_type: str, metadata: dict[str, str]
) -> StoredObject:
    """Store `data` at `key` exactly once, and return what is there afterwards.

    The three outcomes are all successes and are deliberately not distinguished
    to the caller: the object was absent and we wrote it; the object was already
    there and we wrote nothing; a concurrent identical upload won the race and
    we wrote nothing. The key is the content hash, so all three leave the same
    bytes at the same address, and a caller that branched on which one happened
    would be branching on a race.
    """
    from botocore.exceptions import ClientError  # noqa: PLC0415

    bucket = _bucket_name()
    existing = _head(key)
    if existing is None:
        try:
            client().put_object(
                Bucket=bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                Metadata=metadata,
                # Server-side encryption is also enforced by the bucket policy
                # in Terraform. Stated here too, because a caller reading this
                # module should not have to open the IaC to learn whether the
                # bytes are encrypted, and belt-and-braces on encryption is not
                # a redundancy worth trimming.
                ServerSideEncryption="AES256",
                # The precondition. Without it two concurrent uploads of
                # identical bytes both write, which is harmless for the DATA
                # (same bytes, same key) and wasteful for the transfer.
                IfNoneMatch="*",
            )
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code not in {"PreconditionFailed", "412"}:
                raise ObjectStorageError(f"Object store PUT failed: {code}") from exc
        except TypeError:
            # `IfNoneMatch` is not accepted by every botocore version or by
            # every S3-compatible endpoint. Falling back to an unconditional
            # PUT is CORRECT here and not a compromise: the key is the content
            # hash, so the worst case is that identical bytes are written twice
            # to the same address. Silently skipping the write would not be.
            client().put_object(
                Bucket=bucket,
                Key=key,
                Body=data,
                ContentType=content_type,
                Metadata=metadata,
                ServerSideEncryption="AES256",
            )
        existing = _head(key)

    if existing is None:
        raise ObjectStorageError("The object was not readable after it was stored.")

    return StoredObject(
        key=key,
        uri=f"{S3_SCHEME}{bucket}/{key}",
        size_bytes=int(existing.get("ContentLength") or len(data)),
        created_at=_as_utc(existing.get("LastModified")),
        etag=str(existing.get("ETag") or "").strip('"'),
    )


def get_bytes(key: str) -> bytes:
    """Download one object. Raises rather than returning empty bytes.

    An empty download is treated as a failure, not as an empty file: nothing in
    this platform legitimately stores a zero-byte resume or compliance document,
    and returning `b""` would send a zero-byte PDF to a browser and look like a
    corrupt upload rather than a retrieval failure.
    """
    from botocore.exceptions import ClientError  # noqa: PLC0415

    try:
        response = client().get_object(Bucket=_bucket_name(), Key=key)
        data = response["Body"].read()
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        raise ObjectStorageError(f"Object store GET failed: {code}") from exc
    except (KeyError, OSError) as exc:
        raise ObjectStorageError("Object store GET returned no body.") from exc
    if not data:
        raise ObjectStorageError("Object store returned an empty file.")
    return data


def exists(key: str) -> bool:
    """Whether an object is currently readable at `key`.

    Added for the project-intake deletion contract (Project Evidence brief
    section 27): a temporary original may only be recorded as deleted after a
    HEAD confirms it is gone, because "the delete call returned" is the same
    class of non-evidence as "the pipeline was green".
    """
    return _head(key) is not None


def delete(key: str) -> None:
    """Best-effort removal, used to compensate a failed database write.

    Never raises. This is only ever called on the rollback path, where the
    request has already failed and the useful outcome is that the caller's error
    reaches the user rather than being replaced by a second one from the
    cleanup. An orphaned content-addressed object costs storage and nothing
    else, and the lifecycle policy in Terraform reclaims it.
    """
    try:
        client().delete_object(Bucket=_bucket_name(), Key=key)
    except Exception:  # noqa: BLE001 -- compensation must not raise
        logger.debug("object_storage.delete_failed key=%s", key, exc_info=True)


def presigned_get_url(key: str, *, ttl_seconds: int, filename: str | None = None) -> str:
    """A short-lived URL a browser may follow.

    NOT how the product currently serves resumes -- `services/resume_access`
    streams them through an authenticated, tenant-scoped, capability-checked
    endpoint, and that stays the boundary because a presigned URL is a bearer
    token that leaves no audit trail once it is copied out of a page. This
    exists for the paths where a direct download genuinely is the right shape
    (a large export, a background job handing a file to another system), and it
    is deliberately given a TTL parameter rather than a default so no caller can
    mint a long-lived one by omission.

    `filename` sets a download disposition so the browser saves rather than
    renders, which is the S3 equivalent of the delivery-path attachment flag the
    previous provider used.
    """
    params: dict[str, Any] = {"Bucket": _bucket_name(), "Key": key}
    if filename:
        # The header is quoted because a filename can contain a space, and an
        # unquoted one truncates at the first one.
        params["ResponseContentDisposition"] = f'attachment; filename="{filename}"'
    return client().generate_presigned_url(
        "get_object", Params=params, ExpiresIn=int(ttl_seconds)
    )
