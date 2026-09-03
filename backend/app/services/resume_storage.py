"""Private S3-backed resume storage.

Resume bytes never persist on the application filesystem. Durable database
references use ``s3://`` object names; a raw bucket URL is never returned to a
browser -- reads go through `services/resume_access`, which is authenticated,
tenant-scoped and capability-checked.

TRANSPORT MOVED, VALIDATION DID NOT (spec-doc5 Part D). The bucket handle, the
content-addressed upload and its race are now in `services/object_storage`,
shared with compliance-document storage. Everything ABOVE that line stays here
and is unchanged: the PDF/DOCX-only rule, the 10 MB ceiling, the magic-byte
signature check, and the fact that a resume upload must never widen to accept
the JPEG a compliance record legitimately is.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, UploadFile, status
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.services import object_storage

ALLOWED_RESUME_EXTENSIONS = {".pdf", ".docx"}
ALLOWED_RESUME_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/octet-stream",
}
MAX_RESUME_BYTES = 10 * 1024 * 1024
OBJECT_PREFIX = "resumes"

#: What `profiles.resume_storage_provider` records for a row written today.
#: Rows written before the AWS migration say "gcs" and are recognised on read so
#: an un-migrated object reports as un-migrated rather than as missing.
STORAGE_PROVIDER = "s3"
LEGACY_STORAGE_PROVIDER = "gcs"


@dataclass(frozen=True)
class ResumeAsset:
    public_id: str
    secure_url: str
    original_filename: str
    mime_type: str
    size_bytes: int
    uploaded_at: datetime
    sha256: str
    metadata: dict[str, Any]


def _error(code: int, message: str, *, retryable: bool = False) -> HTTPException:
    return HTTPException(
        status_code=code,
        detail={"message": message, "retryable": retryable},
    )


def _normalise_filename(filename: str | None) -> str:
    name = os.path.basename((filename or "").strip())
    if not name or len(name) > 255:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Choose a resume file with a valid filename.",
        )
    return name


def _assert_document_signature(data: bytes, extension: str) -> None:
    if extension == ".pdf" and not data.startswith(b"%PDF-"):
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "The selected file is not a valid PDF.",
        )
    if extension == ".docx" and data[:2] != b"PK":
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "The selected file is not a valid DOCX document.",
        )


async def read_validated_resume(file: UploadFile) -> tuple[bytes, str, str]:
    filename = _normalise_filename(file.filename)
    extension = os.path.splitext(filename.lower())[1]
    content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
    if extension not in ALLOWED_RESUME_EXTENSIONS:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Resume files must be PDF or DOCX.",
        )
    if content_type and content_type not in ALLOWED_RESUME_CONTENT_TYPES:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "The selected file type does not match a PDF or DOCX resume.",
        )
    data = await file.read(MAX_RESUME_BYTES + 1)
    if not data:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "The selected resume is empty.",
        )
    if len(data) > MAX_RESUME_BYTES:
        raise _error(
            status.HTTP_413_CONTENT_TOO_LARGE,
            "Resume files must be 10 MB or smaller.",
        )
    _assert_document_signature(data, extension)
    mime_type = (
        "application/pdf"
        if extension == ".pdf"
        else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    return data, filename, mime_type


def _object_uri(object_name: str) -> str:
    return object_storage.uri_for(object_name)


def _upload_or_get_existing(
    data: bytes,
    sha256: str,
    filename: str,
    mime_type: str,
) -> dict[str, Any]:
    """Create the content-addressed object exactly once."""
    object_name = f"{OBJECT_PREFIX}/{sha256}"
    try:
        stored = object_storage.put_if_absent(
            key=object_name,
            data=data,
            content_type=mime_type,
            metadata={
                "original_filename": filename,
                "mime_type": mime_type,
                "sha256": sha256,
            },
        )
    except object_storage.ObjectStorageNotConfigured as exc:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Resume storage is not configured. Please try again shortly.",
            retryable=True,
        ) from exc
    except Exception as exc:
        # The message never names the vendor (claude.md, 2026-07-26) and never
        # quotes the underlying error, which can echo the request.
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "We could not securely store your resume. Nothing was submitted; please retry.",
            retryable=True,
        ) from exc
    return {
        "object_name": stored.key,
        "size": stored.size_bytes,
        "etag": stored.etag,
        "created_at": stored.created_at,
    }


def _as_utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        return (
            value.astimezone(timezone.utc)
            if value.tzinfo
            else value.replace(tzinfo=timezone.utc)
        )
    return datetime.now(timezone.utc)


async def store_resume(file: UploadFile) -> ResumeAsset:
    """Validate and persist a resume in the private regional bucket."""
    data, filename, mime_type = await read_validated_resume(file)
    return await store_resume_bytes(data, filename, mime_type)


async def store_resume_bytes(
    data: bytes, filename: str, mime_type: str
) -> ResumeAsset:
    """Persist already-validated bytes (used by the one-time legacy migration)."""
    sha256 = hashlib.sha256(data).hexdigest()
    result = await run_in_threadpool(
        _upload_or_get_existing, data, sha256, filename, mime_type
    )
    object_name = str(result["object_name"])
    return ResumeAsset(
        public_id=object_name,
        secure_url=_object_uri(object_name),
        original_filename=filename,
        mime_type=mime_type,
        size_bytes=int(result["size"]),
        uploaded_at=_as_utc(result.get("created_at")),
        sha256=sha256,
        metadata={
            "provider": STORAGE_PROVIDER,
            "bucket": get_settings().s3_bucket,
            "object_name": object_name,
            # The ETag is what lets somebody confirm the stored artefact by
            # digest rather than by trusting that the write returned 200 --
            # the same verification discipline this project applies to a
            # deployed image.
            "etag": result.get("etag"),
        },
    )


def apply_resume_asset(profile: Any, asset: ResumeAsset) -> None:
    profile.resume_url = asset.secure_url
    profile.resume_public_id = asset.public_id
    profile.resume_storage_provider = STORAGE_PROVIDER
    profile.resume_original_filename = asset.original_filename
    profile.resume_mime_type = asset.mime_type
    profile.resume_size_bytes = asset.size_bytes
    profile.resume_uploaded_at = asset.uploaded_at
    profile.resume_sha256 = asset.sha256
    profile.resume_metadata_json = asset.metadata


def copy_resume_metadata(source: Any, target: Any) -> None:
    if not profile_has_resume(source):
        raise ResumeStorageError("The prior resume is missing storage metadata.")
    for field in (
        "resume_url",
        "resume_public_id",
        "resume_storage_provider",
        "resume_legacy_public_id",
        "resume_original_filename",
        "resume_mime_type",
        "resume_size_bytes",
        "resume_uploaded_at",
        "resume_sha256",
        "resume_metadata_json",
    ):
        setattr(target, field, getattr(source, field))


def profile_has_resume(profile: Any) -> bool:
    return bool(
        profile.resume_public_id
        and profile.resume_url
        and profile.resume_original_filename
    )


def _fetch_object_bytes(object_name: str) -> bytes:
    try:
        return object_storage.get_bytes(object_name)
    except object_storage.ObjectStorageError as exc:
        raise ResumeStorageError(
            "Storage could not be reached while retrieving the resume."
        ) from exc


async def fetch_resume_bytes(profile: Any) -> bytes:
    if not profile_has_resume(profile):
        raise ResumeStorageError("The resume file is missing its storage metadata.")
    provider = getattr(profile, "resume_storage_provider", None)
    url = str(profile.resume_url or "")
    if provider == LEGACY_STORAGE_PROVIDER or object_storage.is_legacy_uri(url):
        # A NAMED failure, not a 404. This row is not corrupt, it is
        # un-migrated, and those are different problems with different fixes.
        # Saying "missing" would send somebody looking for a lost file.
        raise ResumeStorageError(
            "This resume was stored before the storage migration and has not "
            "been copied across yet. Run scripts/migrate_resumes_to_s3.py."
        )
    if provider != STORAGE_PROVIDER and not url.startswith(object_storage.S3_SCHEME):
        raise ResumeStorageError("The resume has not been migrated to private storage.")
    return await run_in_threadpool(_fetch_object_bytes, profile.resume_public_id)


class ResumeStorageError(RuntimeError):
    pass
