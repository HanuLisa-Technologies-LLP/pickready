"""Video object transport, on top of the shared S3 client.

`services/object_storage` is the one S3 transport and this module reuses its
client, its HEAD/GET/exists/delete and its encryption posture. What it cannot
reuse is `put_if_absent`: that contract is CONTENT-ADDRESSED (the key is the
sha256 of the bytes, so a duplicate write is a success), and a video key is
ID-ADDRESSED by spec (dual-mode spec 5: assessment-raw/{ids}). A re-upload of
a recording must REPLACE the bytes at its key rather than silently keeping a
truncated earlier attempt, so the put here is unconditional.

Every function is sync, exactly as `object_storage`'s are; API callers wrap
them in `run_in_threadpool` and worker callers run them directly.
"""
from __future__ import annotations

import logging
from typing import Any

from app.core.config import get_settings
from app.services import object_storage

logger = logging.getLogger(__name__)


def _bucket() -> str:
    bucket = (get_settings().s3_bucket or "").strip()
    if not bucket:
        raise object_storage.ObjectStorageNotConfigured(
            "No object storage bucket is configured."
        )
    return bucket


def put(*, key: str, data: bytes, content_type: str) -> int:
    """Store `data` at `key`, replacing whatever is there. Returns the size."""
    from botocore.exceptions import ClientError  # noqa: PLC0415

    try:
        object_storage.client().put_object(
            Bucket=_bucket(),
            Key=key,
            Body=data,
            ContentType=content_type,
            ServerSideEncryption="AES256",
        )
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        raise object_storage.ObjectStorageError(
            f"Object store PUT failed: {code}"
        ) from exc
    return len(data)


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
    absent afterwards.
    """
    object_storage.delete(key)
    try:
        return not object_storage.exists(key)
    except object_storage.ObjectStorageError:
        return False
