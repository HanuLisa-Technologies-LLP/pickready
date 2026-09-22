"""An in-memory object store matching the slice of S3 `object_storage` uses.

WHAT IT IMPLEMENTS, AND WHY THAT IS THE WHOLE LIST
----------------------------------------------------
`app/services/object_storage.py` reaches boto3 through one function, `client()`,
and issues five calls: `head_object`, `put_object`, `get_object`,
`delete_object` and `generate_presigned_url`. Those five are here and nothing
else is. The module is the product's only door to the bucket, so the list is
closed by construction rather than by a promise to keep it up to date.

FAILURES ARE `ClientError`, BECAUSE THAT IS WHAT THE PRODUCT CATCHES
---------------------------------------------------------------------
Every error path in `object_storage` catches `botocore.exceptions.ClientError`
and reads `exc.response["Error"]["Code"]`, then branches on the code: 404,
NoSuchKey, NotFound, 403 and AccessDenied all mean "not there for us" on a HEAD,
and PreconditionFailed means a concurrent identical upload won the race. A
double that raised a plain RuntimeError would be injecting a fault the product
cannot classify, and the scenario would prove only that an unexpected exception
escapes, which is true of any exception.

CONTENT ADDRESSING IS NOT REIMPLEMENTED HERE
----------------------------------------------
`put_if_absent` derives the key from the content hash and decides for itself
what to do about a collision. This store is a plain key to bytes map underneath
that, with `IfNoneMatch` honoured because the product sends it and falls back to
an unconditional PUT when the endpoint refuses the parameter. Reproducing the
precondition is what makes the fallback path reachable in a scenario.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

from harness.doubles.clock import Clock


def _client_error(code: str, message: str, operation: str) -> Exception:
    """Build the exception the product's own handlers are written against."""
    from botocore.exceptions import ClientError  # noqa: PLC0415 -- optional import

    return ClientError(
        {"Error": {"Code": code, "Message": message}}, operation
    )


@dataclass
class _Body:
    """What `get_object` hands back under the `Body` key.

    A streaming body in boto3, and `object_storage.get_bytes` calls `.read()` on
    it once. Anything richer here would be emulating a streaming contract no
    caller uses.
    """

    data: bytes

    def read(self) -> bytes:
        return self.data


@dataclass
class _StoredBytes:
    data: bytes
    content_type: str
    metadata: Mapping[str, str]
    last_modified: datetime
    etag: str


@dataclass
class InMemoryObjectStore:
    """A working object store. Records every call so a scenario can assert.

    `calls` is a log of operation names in order, which answers a question a
    state assertion cannot: `put_if_absent` is specified to write NOTHING when
    the object is already there, and the only evidence of that is the absence of
    a PUT between the two HEADs.
    """

    clock: Clock = field(default_factory=Clock)
    objects: dict[str, _StoredBytes] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.calls.append("head_object")
        stored = self.objects.get(Key)
        if stored is None:
            raise _client_error("404", "Not Found", "HeadObject")
        return {
            "ContentLength": len(stored.data),
            "LastModified": stored.last_modified,
            "ETag": f'"{stored.etag}"',
            "ContentType": stored.content_type,
            "Metadata": dict(stored.metadata),
        }

    def put_object(
        self,
        *,
        Bucket: str,
        Key: str,
        Body: bytes,
        ContentType: str = "application/octet-stream",
        Metadata: Mapping[str, str] | None = None,
        ServerSideEncryption: str | None = None,
        IfNoneMatch: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append("put_object")
        if IfNoneMatch is not None and Key in self.objects:
            raise _client_error(
                "PreconditionFailed",
                "At least one of the pre-conditions you specified did not hold",
                "PutObject",
            )
        etag = hashlib.md5(Body, usedforsecurity=False).hexdigest()
        self.objects[Key] = _StoredBytes(
            data=Body,
            content_type=ContentType,
            metadata=dict(Metadata or {}),
            last_modified=self.clock.now(),
            etag=etag,
        )
        return {"ETag": f'"{etag}"'}

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.calls.append("get_object")
        stored = self.objects.get(Key)
        if stored is None:
            raise _client_error("NoSuchKey", "The specified key does not exist.", "GetObject")
        return {"Body": _Body(stored.data), "ContentLength": len(stored.data)}

    def delete_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.calls.append("delete_object")
        self.objects.pop(Key, None)
        # S3 answers 204 for a delete of a key that was not there. The product's
        # `delete` is a compensation path that never raises, so a double that
        # raised on a missing key would exercise a branch the real store does
        # not take.
        return {}

    def generate_presigned_url(
        self, operation: str, *, Params: Mapping[str, Any], ExpiresIn: int
    ) -> str:
        self.calls.append("generate_presigned_url")
        key = str(Params.get("Key", ""))
        return f"https://object-store.invalid/{key}?expires={int(ExpiresIn)}"


#: Fault kinds this seam serves, with the S3 error code each arrives as.
#:
#: `missing_key` is 404 rather than an exception class of its own because that
#: is what S3 answers and what `_head` already reads as "not there for us".
#: `server_error` is InternalError, which is the code S3 documents for a fault
#: on its side and which `object_storage` does NOT swallow: it raises
#: `ObjectStorageError` naming the code, which is the degradation being tested.
FAILURE_CODES: Mapping[str, str] = {
    "missing_key": "404",
    "server_error": "InternalError",
}


@dataclass
class FailingObjectStore:
    """An object store whose every operation fails one documented way.

    `missing_key` fails only the READ side. A store that also refused writes
    would be two faults wearing one name, and the interesting scenario is the
    one where the database row points at an object that is not there: a
    reference the product believes in and the bucket has never heard of.
    """

    kind: str
    calls: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.kind not in FAILURE_CODES:
            raise ValueError(
                f"unknown object store fault {self.kind!r}; "
                f"the kinds served are {sorted(FAILURE_CODES)}"
            )

    def _fail(self, operation: str) -> Exception:
        return _client_error(
            FAILURE_CODES[self.kind],
            f"harness fault: object store {self.kind}",
            operation,
        )

    def head_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.calls.append("head_object")
        raise self._fail("HeadObject")

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append("put_object")
        if self.kind == "missing_key":
            # A write succeeds and the read still misses, which is the shape of
            # a bucket the writer can reach and the reader cannot: a replication
            # lag, a lifecycle rule, a key written under the wrong prefix.
            return {"ETag": '"harness"'}
        raise self._fail("PutObject")

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.calls.append("get_object")
        raise self._fail("GetObject")

    def delete_object(self, *, Bucket: str, Key: str) -> dict[str, Any]:
        self.calls.append("delete_object")
        raise self._fail("DeleteObject")

    def generate_presigned_url(
        self, operation: str, *, Params: Mapping[str, Any], ExpiresIn: int
    ) -> str:
        self.calls.append("generate_presigned_url")
        raise self._fail("GeneratePresignedUrl")
