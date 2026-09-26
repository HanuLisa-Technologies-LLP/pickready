"""An in-memory S3 client for the session-recording tests.

It implements exactly the calls `services/video/storage` makes, with the
behaviour those calls rely on and nothing more:

  * a multipart upload holds parts until it is COMPLETED (one object whose
    bytes are the parts in part-number order) or ABORTED (nothing);
  * completing with a part that is not the last and is under 5 MiB is refused
    with `EntityTooSmall`, as S3 does, so a test cannot pass on a segment the
    real store would reject after the session is over;
  * a completed or aborted upload answers `NoSuchUpload`;
  * every write records the encryption arguments it carried, so a test can
    assert the header the bucket policy demands;
  * `stubborn` keys survive a delete that returned success, which is the case
    only a HEAD can detect; `deny` keys answer 403 to everything, and
    `deny_head` keys answer 403 to a HEAD alone (a role that may delete but
    not read), which a strict HEAD must never read as "gone".

`install(monkeypatch)` points `object_storage.client()` at one instance and
sets the bucket and KMS key settings the storage module refuses to run
without. The real module attribute is patched, never `sys.modules`
(claude.md, the Miti harness lesson).
"""
from __future__ import annotations

import itertools
import pathlib
from typing import Any

from botocore.exceptions import ClientError

MIN_PART = 5 * 1024 * 1024
KMS_KEY_ARN = "arn:aws:kms:xx-test-1:000000000000:key/test-key"


def _error(code: str, operation: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": code}}, operation)


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.uploads: dict[str, dict[str, Any]] = {}
        self.writes: list[dict[str, Any]] = []
        self.stubborn: set[str] = set()
        self.deny: set[str] = set()
        self.deny_head: set[str] = set()
        self.fail_complete: set[str] = set()
        self._ids = itertools.count(1)

    # ── guards ───────────────────────────────────────────────────────────
    def _check(self, key: str, operation: str) -> None:
        if key in self.deny:
            raise _error("403", operation)

    def _upload(self, key: str, upload_id: str, operation: str) -> dict[str, Any]:
        upload = self.uploads.get(upload_id)
        if upload is None or upload["key"] != key:
            raise _error("NoSuchUpload", operation)
        return upload

    # ── single objects ───────────────────────────────────────────────────
    def put_object(self, *, Bucket, Key, Body, ContentType, **sse) -> dict:
        self._check(Key, "PutObject")
        self.writes.append({"op": "PutObject", "key": Key, **sse})
        self.objects[Key] = bytes(Body)
        return {}

    def upload_file(self, filename, bucket, key, ExtraArgs=None) -> None:
        self._check(key, "PutObject")
        self.writes.append({"op": "upload_file", "key": key, **(ExtraArgs or {})})
        self.objects[key] = pathlib.Path(filename).read_bytes()

    def download_file(self, bucket, key, filename) -> None:
        self._check(key, "GetObject")
        if key not in self.objects:
            raise _error("404", "HeadObject")
        pathlib.Path(filename).write_bytes(self.objects[key])

    def head_object(self, *, Bucket, Key) -> dict:
        self._check(Key, "HeadObject")
        if Key in self.deny_head:
            raise _error("403", "HeadObject")
        if Key not in self.objects:
            raise _error("404", "HeadObject")
        return {"ContentLength": len(self.objects[Key])}

    def delete_object(self, *, Bucket, Key) -> dict:
        self._check(Key, "DeleteObject")
        if Key not in self.stubborn:
            self.objects.pop(Key, None)
        return {}

    # ── multipart ────────────────────────────────────────────────────────
    def create_multipart_upload(self, *, Bucket, Key, ContentType, **sse) -> dict:
        self._check(Key, "CreateMultipartUpload")
        upload_id = f"upload-{next(self._ids)}"
        self.writes.append({"op": "CreateMultipartUpload", "key": Key, **sse})
        self.uploads[upload_id] = {"key": Key, "parts": {}}
        return {"UploadId": upload_id}

    def upload_part(self, *, Bucket, Key, UploadId, PartNumber, Body) -> dict:
        self._check(Key, "UploadPart")
        upload = self._upload(Key, UploadId, "UploadPart")
        etag = f'"etag-{UploadId}-{PartNumber}-{len(Body)}"'
        upload["parts"][PartNumber] = (etag, bytes(Body))
        return {"ETag": etag}

    def list_parts(self, *, Bucket, Key, UploadId, PartNumberMarker=0) -> dict:
        upload = self._upload(Key, UploadId, "ListParts")
        parts = [
            {"PartNumber": number, "ETag": etag, "Size": len(body)}
            for number, (etag, body) in sorted(upload["parts"].items())
            if number > PartNumberMarker
        ]
        return {"Parts": parts, "IsTruncated": False}

    def complete_multipart_upload(self, *, Bucket, Key, UploadId, MultipartUpload) -> dict:
        if Key in self.fail_complete:
            raise _error("InternalError", "CompleteMultipartUpload")
        upload = self._upload(Key, UploadId, "CompleteMultipartUpload")
        requested = MultipartUpload["Parts"]
        chunks: list[bytes] = []
        for index, part in enumerate(requested):
            etag, body = upload["parts"].get(part["PartNumber"], (None, b""))
            if etag != part["ETag"]:
                raise _error("InvalidPart", "CompleteMultipartUpload")
            if index < len(requested) - 1 and len(body) < MIN_PART:
                raise _error("EntityTooSmall", "CompleteMultipartUpload")
            chunks.append(body)
        self.objects[Key] = b"".join(chunks)
        del self.uploads[UploadId]
        return {}

    def abort_multipart_upload(self, *, Bucket, Key, UploadId) -> dict:
        self._upload(Key, UploadId, "AbortMultipartUpload")
        del self.uploads[UploadId]
        return {}


def install(monkeypatch, store: FakeS3 | None = None) -> FakeS3:
    from app.core.config import get_settings
    from app.services import object_storage

    store = store or FakeS3()
    monkeypatch.setattr(object_storage, "client", lambda: store)
    settings = get_settings()
    monkeypatch.setattr(settings, "s3_bucket", "readypick-test-private")
    monkeypatch.setattr(settings, "s3_kms_key_id", KMS_KEY_ARN)
    return store
