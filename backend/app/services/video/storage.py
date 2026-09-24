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
(pilot held zero recordings when this was found, CONTRACT v3). `_sse()` is the
one place the header is built, it names the key explicitly because `aws:kms`
alone selects the AWS-managed `aws/s3` key rather than the bucket's own, and
it REFUSES when `s3_kms_key_id` is empty rather than writing under the wrong
key or letting the store deny each part.

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
    """The encryption arguments every media write carries. See the module
    docstring: `aws:kms` under this environment's key, or a refusal."""
    key_id = (get_settings().s3_kms_key_id or "").strip()
    if not key_id:
        raise object_storage.ObjectStorageNotConfigured(
            "S3_KMS_KEY_ID is not set. The bucket policy accepts only "
            "aws:kms uploads, and without the key id the object would be "
            "encrypted under the AWS-managed key instead of this "
            "environment's own."
        )
    return {"ServerSideEncryption": "aws:kms", "SSEKMSKeyId": key_id}


def _error(action: str, exc: Exception) -> object_storage.ObjectStorageError:
    code = ""
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = str(response.get("Error", {}).get("Code", ""))
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
    return object_storage.exists(key)


def head_size(key: str) -> int | None:
    """The stored object's size, or None when it is not readable."""
    from botocore.exceptions import ClientError  # noqa: PLC0415

    try:
        response: Any = object_storage.client().head_object(Bucket=_bucket(), Key=key)
    except ClientError:
        return None
    return int(response.get("ContentLength") or 0)


def delete_verified(key: str) -> bool:
    """Delete `key` and confirm with a HEAD that it is gone.

    The project-intake deletion contract: "the delete call returned" is the
    same class of non-evidence as "the pipeline was green", so the answer is
    the HEAD, not the delete. Returns True only when the object is verifiably
    absent afterwards. The bucket keeps a noncurrent version for one day
    under the lifecycle rule for these prefixes (`infra/modules/s3`), which
    is what turns this delete into a purge.
    """
    object_storage.delete(key)
    try:
        return not object_storage.exists(key)
    except object_storage.ObjectStorageError:
        return False
