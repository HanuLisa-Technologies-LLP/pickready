"""Assessment media transport, on top of the shared S3 client.

`services/object_storage` is the one S3 client and this module reuses it, its
HEAD/GET/exists/delete, and its timeouts. What it cannot reuse is
`put_if_absent`: that contract is CONTENT-ADDRESSED (the key is the sha256 of
the bytes, so a duplicate write is a success), and a media key is
ID-ADDRESSED. A re-upload must REPLACE the bytes at its key rather than
silently keeping a truncated earlier attempt, so every write here is
unconditional.

ENCRYPTION IS `aws:kms` UNDER THIS ENVIRONMENT'S KEY, AND NOTHING ELSE
------------------------------------------------------------------------
`infra/modules/s3` denies any PutObject whose `x-amz-server-side-encryption`
header is not `aws:kms`. This module sent `AES256` until 2026-09-24, so on a
bucket built from that module every recording upload would have been refused
(pilot held zero recordings when this was found, CONTRACT v3).
`object_storage.sse_arguments` is the one place the header is built (this
module's `_sse()` reads it, and so does every other bucket write). It names
the key explicitly because `aws:kms` alone selects the AWS-managed `aws/s3`
key rather than the bucket's own, and it REFUSES when `s3_kms_key_id` is
empty rather than writing under the wrong key or letting the store deny each
part.

NO WHOLE RECORDING IS EVER HELD IN MEMORY
------------------------------------------
The API forwards one part at a time into a multipart upload (at most
`video_part_max_bytes`); the worker downloads each segment to DISK and uploads
the compressed file FROM disk with the managed transfer, which splits it into
parts itself. The only bytes-in-memory writer left is `put`, for small objects.

Every function is sync, exactly as `object_storage`'s are; API callers wrap
them in `run_in_threadpool` and worker callers run them directly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import S3_MIN_PART_BYTES, get_settings
from app.services import object_storage

logger = logging.getLogger(__name__)

#: Re-exported for callers that validate part sizes; defined once in config.
MIN_PART_BYTES = S3_MIN_PART_BYTES

#: S3's own ceiling on part numbers in one multipart upload.
MAX_PART_NUMBER = 10_000


@dataclass(frozen=True)
class UploadedPart:
    """One part as the object store recorded it."""

    part_number: int
    etag: str
    size: int


def _bucket() -> str:
    bucket = (get_settings().s3_bucket or "").strip()
    if not bucket:
        raise object_storage.ObjectStorageNotConfigured(
            "No object storage bucket is configured."
        )
    return bucket


def _sse() -> dict[str, str]:
    """The encryption arguments every media write carries: `aws:kms` under
    this environment's key, or a refusal. ONE implementation, in
    `object_storage.sse_arguments`, shared with every other bucket write."""
    return object_storage.sse_arguments()


class UploadNotFound(object_storage.ObjectStorageError):
    """The store holds no multipart upload under this id any more.

    Either it was completed (the object exists) or aborted (nothing exists).
    Raised as its own class because the caller can tell those two apart with
    a HEAD and settle the row, where any other store error is a real failure
    to retry.
    """


def _error(action: str, exc: Exception) -> object_storage.ObjectStorageError:
    code = ""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str(response.get("Error", {}).get("Code", ""))
    if code == "NoSuchUpload":
        return UploadNotFound(f"Object store {action} failed: {code}")
    return object_storage.ObjectStorageError(f"Object store {action} failed: {code}")


def put(*, key: str, data: bytes, content_type: str) -> int:
    """Store small `data` at `key`, replacing whatever is there. Returns the
    size. Media of recording size goes through `upload_path` or a multipart
    upload, never through here."""
    from botocore.exceptions import ClientError  # noqa: PLC0415

    sse = _sse()
    try:
        object_storage.client().put_object(
            Bucket=_bucket(),
            Key=key,
            Body=data,
            ContentType=content_type,
            ServerSideEncryption=sse["ServerSideEncryption"],
            SSEKMSKeyId=sse["SSEKMSKeyId"],
        )
    except ClientError as exc:
        raise _error("PUT", exc) from exc
    return len(data)


def upload_path(*, key: str, path: Path, content_type: str) -> int:
    """Upload a file FROM DISK with the managed transfer (multipart above its
    own threshold), encrypted exactly as `put` is. Returns the file size."""
    from boto3.exceptions import S3UploadFailedError  # noqa: PLC0415
    from botocore.exceptions import ClientError  # noqa: PLC0415

    extra = {"ContentType": content_type, **_sse()}
    try:
        object_storage.client().upload_file(
            str(path), _bucket(), key, ExtraArgs=extra
        )
    except (ClientError, S3UploadFailedError) as exc:
        raise _error("upload", exc) from exc
    return path.stat().st_size


def download_to(key: str, path: Path) -> int:
    """Stream `key` to `path` on disk. Returns the byte count written."""
    from botocore.exceptions import ClientError  # noqa: PLC0415

    try:
        object_storage.client().download_file(_bucket(), key, str(path))
    except ClientError as exc:
        raise _error("download", exc) from exc
    return path.stat().st_size


# ── Multipart upload: the segment transport ──────────────────────────────────


def create_multipart(*, key: str, content_type: str) -> str:
    """Open a multipart upload at `key`. Returns the store's upload id.

    The encryption is declared HERE: S3 applies a multipart upload's SSE
    settings to every part and to the completed object."""
    from botocore.exceptions import ClientError  # noqa: PLC0415

    sse = _sse()
    try:
        response = object_storage.client().create_multipart_upload(
            Bucket=_bucket(),
            Key=key,
            ContentType=content_type,
            ServerSideEncryption=sse["ServerSideEncryption"],
            SSEKMSKeyId=sse["SSEKMSKeyId"],
        )
    except ClientError as exc:
        raise _error("CreateMultipartUpload", exc) from exc
    return str(response["UploadId"])


def upload_part(*, key: str, upload_id: str, part_number: int, data: bytes) -> str:
    """Upload one part. Returns its ETag, which completion needs verbatim."""
    from botocore.exceptions import ClientError  # noqa: PLC0415

    if not 1 <= part_number <= MAX_PART_NUMBER:
        raise ValueError(f"part numbers run from 1 to {MAX_PART_NUMBER}")
    try:
        response = object_storage.client().upload_part(
            Bucket=_bucket(),
            Key=key,
            UploadId=upload_id,
            PartNumber=part_number,
            Body=data,
        )
    except ClientError as exc:
        raise _error("UploadPart", exc) from exc
    return str(response["ETag"])


def list_parts(*, key: str, upload_id: str) -> list[UploadedPart]:
    """Every part the STORE holds for this upload, in part-number order.

    The sweep completes an abandoned upload from this rather than from the
    row's `parts_json`, because a part S3 accepted whose database write then
    rolled back is still a part of the object, and the store is what the
    completion is checked against.
    """
    from botocore.exceptions import ClientError  # noqa: PLC0415

    client = object_storage.client()
    parts: list[UploadedPart] = []
    marker = 0
    while True:
        try:
            response = client.list_parts(
                Bucket=_bucket(), Key=key, UploadId=upload_id,
                PartNumberMarker=marker,
            )
        except ClientError as exc:
            raise _error("ListParts", exc) from exc
        for part in response.get("Parts") or []:
            parts.append(
                UploadedPart(
                    part_number=int(part["PartNumber"]),
                    etag=str(part["ETag"]),
                    size=int(part.get("Size") or 0),
                )
            )
        if not response.get("IsTruncated"):
            break
        marker = int(response.get("NextPartNumberMarker") or 0)
    return sorted(parts, key=lambda part: part.part_number)


def complete_multipart(
    *, key: str, upload_id: str, parts: list[tuple[int, str]]
) -> None:
    """Join the parts into one object. `parts` is (part number, ETag)."""
    from botocore.exceptions import ClientError  # noqa: PLC0415

    ordered = sorted(parts)
    try:
        object_storage.client().complete_multipart_upload(
            Bucket=_bucket(),
            Key=key,
            UploadId=upload_id,
            MultipartUpload={
                "Parts": [
                    {"PartNumber": number, "ETag": etag} for number, etag in ordered
                ]
            },
        )
    except ClientError as exc:
        raise _error("CompleteMultipartUpload", exc) from exc


def abort_multipart(*, key: str, upload_id: str) -> None:
    """Discard an upload and every part it holds. A no-op answer from the
    store for an upload that no longer exists is success: the parts are gone
    either way, and the bucket's abort-incomplete rule is the backstop."""
    from botocore.exceptions import ClientError  # noqa: PLC0415

    try:
        object_storage.client().abort_multipart_upload(
            Bucket=_bucket(), Key=key, UploadId=upload_id
        )
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in {"NoSuchUpload", "404"}:
            return
        raise _error("AbortMultipartUpload", exc) from exc


# ── Reads and verified deletion ──────────────────────────────────────────────


def get_bytes(key: str) -> bytes:
    return object_storage.get_bytes(key)


def exists(key: str) -> bool:
    """Whether an object is at `key`, by the strict HEAD below: a denied HEAD
    raises rather than reading as absent."""
    return head_size(key) is not None


#: The answers S3 gives a HEAD on a key that is not there. Nothing else means
#: absent: in particular a 403 is a missing GRANT, and reading it as "gone"
#: would let a worker with no permission confirm a deletion it never made.
_ABSENT_CODES = frozenset({"404", "NoSuchKey", "NotFound"})


def head_size(key: str) -> int | None:
    """The stored object's size, or None when the store says it is ABSENT.

    Stricter than `object_storage.exists` on purpose, and the difference is
    the reason this function exists: that helper serves a content-addressed
    PUT and reads a 403 as "not there for us", which is harmless before a
    conditional write and wrong after a delete. Here any answer other than
    not-found RAISES, so a denied or failed HEAD can never be mistaken for a
    confirmed absence.
    """
    from botocore.exceptions import ClientError  # noqa: PLC0415

    try:
        response: Any = object_storage.client().head_object(Bucket=_bucket(), Key=key)
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in _ABSENT_CODES:
            return None
        raise _error("HEAD", exc) from exc
    return int(response.get("ContentLength") or 0)


def delete_verified(key: str) -> bool:
    """Delete `key` and confirm with a HEAD that it is gone.

    The project-intake deletion contract: "the delete call returned" is the
    same class of non-evidence as "the pipeline was green", so the answer is
    the HEAD, not the delete. Returns True only when the store answers
    not-found afterwards; a refused DELETE or a HEAD that is anything but
    not-found RAISES `ObjectStorageError`, which every caller counts as a
    failure to retry. `object_storage.delete` is not used because it swallows
    every error (it exists to compensate a failed database write), and paired
    with a HEAD that reads 403 as absent it would have confirmed the deletion
    of an object a worker had no permission to touch.

    The bucket keeps a noncurrent version for one day under the lifecycle
    rule for these prefixes (`infra/modules/s3`), which is what turns this
    delete into a purge.
    """
    from botocore.exceptions import ClientError  # noqa: PLC0415

    try:
        object_storage.client().delete_object(Bucket=_bucket(), Key=key)
    except ClientError as exc:
        raise _error("DELETE", exc) from exc
    return head_size(key) is None
