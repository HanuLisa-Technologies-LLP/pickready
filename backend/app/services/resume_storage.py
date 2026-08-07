"""Private GCS-backed resume storage.

Resume bytes never persist on the application filesystem. Durable database
references use ``gs://`` object names; a raw bucket URL is never returned to a
browser. The Cloudinary reader is temporary and read-only so already stored
profiles remain available during the one-time production migration.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import HTTPException, UploadFile, status
from google.cloud import storage
from google.api_core.exceptions import PreconditionFailed
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings

ALLOWED_RESUME_EXTENSIONS = {".pdf", ".docx"}
ALLOWED_RESUME_CONTENT_TYPES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/octet-stream",
}
MAX_RESUME_BYTES = 10 * 1024 * 1024
GCS_PREFIX = "resumes"


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


def _bucket() -> storage.Bucket:
    settings = get_settings()
    if not settings.gcs_bucket:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Resume storage is not configured. Please try again shortly.",
            retryable=True,
        )
    return storage.Client().bucket(settings.gcs_bucket)


def _gcs_uri(object_name: str) -> str:
    return f"gs://{get_settings().gcs_bucket}/{object_name}"


def _upload_or_get_existing(
    data: bytes,
    sha256: str,
    filename: str,
    mime_type: str,
) -> dict[str, Any]:
    """Create the content-addressed object exactly once."""
    object_name = f"{GCS_PREFIX}/{sha256}"
    blob = _bucket().blob(object_name)
    try:
        if not blob.exists():
            blob.metadata = {
                "original_filename": filename,
                "mime_type": mime_type,
                "sha256": sha256,
            }
            try:
                blob.upload_from_string(
                    data,
                    content_type=mime_type,
                    if_generation_match=0,
                )
            except PreconditionFailed:
                # A concurrent content-addressed upload won the race.
                pass
        blob.reload()
    except Exception as exc:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "We could not securely store your resume. Nothing was submitted; please retry.",
            retryable=True,
        ) from exc
    return {
        "object_name": object_name,
        "size": int(blob.size or len(data)),
        "generation": str(blob.generation or ""),
        "created_at": blob.time_created,
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
    """Validate and persist a resume in the private regional GCS bucket."""
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
        secure_url=_gcs_uri(object_name),
        original_filename=filename,
        mime_type=mime_type,
        size_bytes=int(result["size"]),
        uploaded_at=_as_utc(result.get("created_at")),
        sha256=sha256,
        metadata={
            "provider": "gcs",
            "bucket": get_settings().gcs_bucket,
            "object_name": object_name,
            "generation": result.get("generation"),
        },
    )


def apply_resume_asset(profile: Any, asset: ResumeAsset) -> None:
    profile.resume_url = asset.secure_url
    profile.resume_public_id = asset.public_id
    profile.resume_storage_provider = "gcs"
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


def _fetch_gcs_bytes(object_name: str) -> bytes:
    try:
        data = _bucket().blob(object_name).download_as_bytes()
    except Exception as exc:
        raise ResumeStorageError(
            "GCS could not be reached while retrieving the resume."
        ) from exc
    if not data:
        raise ResumeStorageError("GCS returned an empty resume file.")
    return data


def _configure_cloudinary() -> None:
    settings = get_settings()
    if not settings.cloudinary_url:
        raise ResumeStorageError("Legacy resume storage is not configured.")
    import cloudinary

    cloudinary.config(cloudinary_url=settings.cloudinary_url, secure=True)


async def _fetch_legacy_cloudinary(profile: Any) -> bytes:
    """Temporary read-only bridge used until the migration reaches zero."""
    _configure_cloudinary()
    import cloudinary.utils

    metadata = profile.resume_metadata_json or {}
    download_url = cloudinary.utils.private_download_url(
        profile.resume_public_id,
        "",
        resource_type=metadata.get("resource_type") or "raw",
        type=metadata.get("delivery_type") or "upload",
    )
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
        ) as client:
            response = await client.get(download_url)
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ResumeStorageError(
            "Legacy storage could not be reached while retrieving the resume."
        ) from exc
    if not response.content:
        raise ResumeStorageError("Legacy storage returned an empty resume file.")
    return response.content


async def fetch_resume_bytes(profile: Any) -> bytes:
    if not profile_has_resume(profile):
        raise ResumeStorageError("The resume file is missing its storage metadata.")
    provider = getattr(profile, "resume_storage_provider", None)
    if provider == "gcs" or str(profile.resume_url).startswith("gs://"):
        return await run_in_threadpool(_fetch_gcs_bytes, profile.resume_public_id)
    return await _fetch_legacy_cloudinary(profile)


async def delete_resume_asset(asset: ResumeAsset) -> None:
    """Best-effort compensation after a database write failure."""
    try:
        await run_in_threadpool(_bucket().blob(asset.public_id).delete)
    except Exception:
        return


class ResumeStorageError(RuntimeError):
    pass
